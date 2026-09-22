"""The per-run position trail and the served bus position (GPS plan U7).

Two shapes, like ``exception_dao``:

- module functions on the caller's connection, used by the action DAOs
  inside the action transaction: append the tap's trail row, write the five
  position columns as one statement, clear them, flag a row, and read the
  previous fix and the planned stops the plausibility safeguard (U12)
  compares a new fix against;
- ``PositionDao`` for the one piece of work that opens its own scoped
  connection — the bounded purge after Start Run — so RLS confines it to the
  school like any request-path DAO.

The served position is ``live_buses.current_lat/lng``; ``position_source``,
``position_at`` and ``position_accuracy_m`` describe that pair and are never
read while it is null. Every writer here sets all five in one statement (the
plan's one-authority rule): a fix writes source ``action`` with the capture
time and accuracy, a stop or the school writes ``checkpoint`` with ``now()``
and no accuracy, End Run and force-close null all five (R7, R8, R11).

Retention (R12) keys on ``received_at`` — the server's stamp — never on the
client's capture time, and every pass is bounded (row cap, statement
timeout, one try at the per-school advisory lock) because on this runtime
"not in the transaction" still means "in the driver's wait". Exceptions and
their events keep kind, distance, responses and review state past retention;
only the copied coordinates are nulled. Nothing here logs a coordinate.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from typing import Any

from psycopg import sql

from app.core.config import (
    GPS_ACTION_KEY_TTL_DAYS,
    GPS_CLOCK_SKEW_TOLERANCE_S,
    GPS_PING_FUTURE_WINDOW_S,
    GPS_PING_RUN_START_GRACE_S,
    GPS_PURGE_BATCH_ROWS,
    GPS_PURGE_STATEMENT_TIMEOUT_MS,
    get_settings,
)
from app.core.db import get_connection
from app.core.errors import (
    ForbiddenError,
    NotFoundError,
    PingPacedError,
    PingRefusedError,
)
from app.core.scope import SchoolScope
from app.dao import idempotency_dao
from app.dao.school_thresholds import resolve_school_thresholds
from app.services.position_rules import (
    FIX_REASON_COARSE,
    FLAG_CLASSIFICATION_FAILED,
    FLAG_REPEAT_COORDINATES,
    PLAUSIBILITY_FLAGS,
    NormalisedFix,
    StoredFix,
    normalise_fix,
)

logger = logging.getLogger("saferide.positions")

SOURCE_ACTION = "action"
SOURCE_CHECKPOINT = "checkpoint"
SOURCE_PING = "ping"

# pg_try_advisory_xact_lock(class, hashtext(school_id)): the class number is
# the migration that introduced the tables, so no other lock in the product
# can collide with a purge.
PURGE_LOCK_CLASS = 16

# "Older than N days" as an absolute instant: `now()` minus N × 86,400 s.
# One %s placeholder — the day count. The verify set's trail-rows-past-
# retention observation uses the same expression so the two never disagree.
RETENTION_CUTOFF_SQL = "now() - make_interval(secs => %s * 86400)"


# --- trail --------------------------------------------------------------------


def append_action_row(
    conn,
    *,
    school_id: str,
    run_id: str,
    bus_id: str,
    action_kind: str,
    action_key: str | None,
    session_id: str | None,
    device_id: str | None,
    fix: NormalisedFix,
) -> str:
    """One ``run_positions`` row per tap — with the fix when there is one,
    with the reason when there is not. The row is the audit that the tap
    happened, so a fix-less action writes it too (AE2). Returns the row id
    so the savepoint tier can flag it (``classification-failed``)."""
    stored = fix.fix
    row = conn.execute(
        """
        insert into run_positions
            (school_id, run_id, bus_id, source, action_kind, action_key, session_id,
             device_id, lat, lng, accuracy_m, captured_at, fix_reason, flags)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        returning id
        """,
        (
            school_id, run_id, bus_id, SOURCE_ACTION, action_kind, action_key, session_id,
            device_id,
            stored.lat if stored else None,
            stored.lng if stored else None,
            stored.accuracy_m if stored else None,
            stored.captured_at if stored else None,
            fix.reason,
            list(fix.flags),
        ),
    ).fetchone()
    return str(row["id"])


def append_ping_row(
    conn,
    *,
    school_id: str,
    run_id: str,
    bus_id: str,
    session_id: str | None,
    device_id: str | None,
    fix: NormalisedFix,
) -> str | None:
    """One ``run_positions`` row per accepted ping (GPS plan U14): source
    ``ping``, no action kind or key, the session and device that sent it,
    the fix and its reason (``coarse`` above the school's cap, else
    ``none``) and the boundary's flags. Keyed on (run, capture time) by
    016's partial unique index: a capture time the run already holds — a
    retried batch, two phones on one session — inserts nothing and returns
    None, which the batch counts as a duplicate. ``fix.fix`` must be set."""
    stored = fix.fix
    assert stored is not None
    row = conn.execute(
        """
        insert into run_positions
            (school_id, run_id, bus_id, source, session_id, device_id,
             lat, lng, accuracy_m, captured_at, fix_reason, flags)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        on conflict (run_id, captured_at) where source = 'ping' do nothing
        returning id
        """,
        (
            school_id, run_id, bus_id, SOURCE_PING, session_id, device_id,
            stored.lat, stored.lng, stored.accuracy_m, stored.captured_at,
            fix.reason, list(fix.flags),
        ),
    ).fetchone()
    return str(row["id"]) if row else None


def newest_ping_age_s(conn, run_id: str) -> float | None:
    """Seconds since the run's newest ping row was received (server clock
    both sides), or None when the run has no ping yet — the pacing input
    (U14/R28). Taps are not counted: the pace bounds the ping stream, never
    the driver's actions."""
    row = conn.execute(
        """
        select extract(epoch from now() - max(received_at)) as age_s
        from run_positions
        where run_id = %s and source = %s
        """,
        (run_id, SOURCE_PING),
    ).fetchone()
    age = row["age_s"] if row else None
    return float(age) if age is not None else None


def add_flag(conn, row_id: str, flag: str) -> None:
    """Append a flag to a trail row once (idempotent). The seam U9/U10 use to
    mark ``classification-failed`` after their savepoint rolled back."""
    add_flags(conn, row_id, (flag,))


def add_flags(conn, row_id: str, flags: Iterable[str]) -> None:
    """Merge flags onto a trail row in one statement — each once, in the
    given order after whatever the row already carries (``clock-skew`` from
    the boundary stays first). The plausibility step (U12) writes its flags
    through here."""
    new = list(flags)
    if not new:
        return
    conn.execute(
        """
        update run_positions
        set flags = flags || array(
            select f from unnest(%s::text[]) with ordinality as n(f, ord)
            where not (f = any(flags))
            order by ord
        )
        where id = %s
        """,
        (new, row_id),
    )


# Every fix reader below keeps the same shape: phone fixes only (a checkpoint
# row is the planned stop coordinate stamped by a fix-less Arrive, and counting
# it would make every stop "seen" by construction), the four fix columns
# present, and receipt inside the school's retention (R12) — a purge that has
# not run yet is invisible here too.
_FIX_ROW_SQL = """
    p.source <> %(checkpoint)s
    and p.lat is not null and p.lng is not null
    and p.accuracy_m is not null and p.captured_at is not null
"""
_RETENTION_SQL = """
    p.received_at >= now() - make_interval(secs => %(retention_days)s * 86400)
"""


def _stored(row) -> StoredFix:
    return StoredFix(
        lat=row["lat"], lng=row["lng"], accuracy_m=row["accuracy_m"],
        captured_at=row["captured_at"],
    )


def run_fixes(
    conn, run_id: str, *, default_retention_days: int | None = None
) -> list[StoredFix]:
    """Every *plausible* phone fix on a run's trail inside the school's
    retention, oldest first — the read behind "bus seen at stop" (U9, R13).

    A fix carrying any plausibility flag (U12, R32) is left out: a flagged
    fix vouches for nothing, so a fix posted at the planned stop's pin or
    teleported onto it can never make "bus seen at stop" read yes — the
    fabricated corroboration the plan warns about. A row flagged
    ``classification-failed`` is left out too: its plausibility may never
    have been judged, and an unjudged fix must not vouch for a stop either.
    The school's own retention applies; the default beneath it is Settings'
    (U11), read per call.
    """
    if default_retention_days is None:
        default_retention_days = int(get_settings().gps_position_retention_days)
    rows = conn.execute(
        f"""
        select p.lat, p.lng, p.accuracy_m, p.captured_at
        from run_positions p
        join live_runs r on r.id = p.run_id
        left join live_schools s on s.id = r.school_id
        where p.run_id = %(run_id)s
          and {_FIX_ROW_SQL}
          and not (p.flags && %(excluded_flags)s::text[])
          and p.received_at >= now() - make_interval(
                secs => coalesce(s.position_retention_days, %(retention_days)s) * 86400)
        order by p.received_at asc, p.id asc
        """,
        {
            "run_id": run_id, "checkpoint": SOURCE_CHECKPOINT,
            "excluded_flags": [*PLAUSIBILITY_FLAGS, FLAG_CLASSIFICATION_FAILED],
            "retention_days": default_retention_days,
        },
    ).fetchall()
    return [_stored(row) for row in rows]


def previous_fix(
    conn, run_id: str, *, before_row_id: str, retention_days: int
) -> StoredFix | None:
    """The run's previous fix-bearing trail row — by receipt order, inside
    the school's retention (the resolved days, U11), excluding the tap's own
    row — for the plausibility safeguard (U12) to compare the new fix
    against. Flagged or not: a fix after an implausible one is judged against
    what the phone last claimed. None for the first fix of a run, or when
    every earlier fix is past retention (a row the purge will delete is not
    evidence either)."""
    row = conn.execute(
        f"""
        select p.lat, p.lng, p.accuracy_m, p.captured_at
        from run_positions p
        where p.run_id = %(run_id)s
          and p.id <> %(before_row_id)s
          and {_FIX_ROW_SQL}
          and {_RETENTION_SQL}
        order by p.received_at desc, p.id desc
        limit 1
        """,
        {
            "run_id": run_id, "before_row_id": before_row_id,
            "checkpoint": SOURCE_CHECKPOINT, "retention_days": int(retention_days),
        },
    ).fetchone()
    return _stored(row) if row else None


def planned_stops(conn, run_id: str) -> list[tuple[float, float]]:
    """The run's planned stop coordinates (``run_stops`` for this run, gate
    included, pins only) — the second input of the plausibility safeguard."""
    rows = conn.execute(
        "select lat, lng from run_stops where run_id = %s "
        "and lat is not null and lng is not null",
        (run_id,),
    ).fetchall()
    return [(row["lat"], row["lng"]) for row in rows]


# --- the served position (five columns, one statement) -------------------------


def write_action_position(conn, bus_id: str, fix: StoredFix) -> None:
    conn.execute(
        """
        update live_buses
        set current_lat = %s, current_lng = %s, position_source = %s,
            position_at = %s, position_accuracy_m = %s
        where id = %s
        """,
        (fix.lat, fix.lng, SOURCE_ACTION, fix.captured_at, fix.accuracy_m, bus_id),
    )


def write_ping_position(conn, bus_id: str, fix: StoredFix) -> bool:
    """The served position from a ping (U14, R7): the five columns in one
    statement with source ``ping`` — and only forward in capture time. A
    ping captured before the bus's current ``position_at`` (a late batch, a
    tap that landed in between) changes nothing; a pair with no time (the
    legacy shape) is overwritten. Returns whether the position moved."""
    return conn.execute(
        """
        update live_buses
        set current_lat = %s, current_lng = %s, position_source = %s,
            position_at = %s, position_accuracy_m = %s
        where id = %s and (position_at is null or position_at < %s)
        """,
        (
            fix.lat, fix.lng, SOURCE_PING, fix.captured_at, fix.accuracy_m,
            bus_id, fix.captured_at,
        ),
    ).rowcount == 1


def write_checkpoint_position(conn, bus_id: str, lat: float, lng: float) -> None:
    conn.execute(
        """
        update live_buses
        set current_lat = %s, current_lng = %s, position_source = %s,
            position_at = now(), position_accuracy_m = null
        where id = %s
        """,
        (lat, lng, SOURCE_CHECKPOINT, bus_id),
    )


def clear_position(conn, bus_id: str | None) -> None:
    """Null all five columns. A run without a bus (admin-created) has no
    position to clear — force-close reaches here with ``None``."""
    if bus_id is None:
        return
    conn.execute(
        """
        update live_buses
        set current_lat = null, current_lng = null, position_source = null,
            position_at = null, position_accuracy_m = null
        where id = %s
        """,
        (bus_id,),
    )


# --- retention --------------------------------------------------------------


def purge_batch(
    conn,
    school_id: str,
    *,
    batch_rows: int = GPS_PURGE_BATCH_ROWS,
    key_ttl_days: int = GPS_ACTION_KEY_TTL_DAYS,
    statement_timeout_ms: int = GPS_PURGE_STATEMENT_TIMEOUT_MS,
    default_retention_days: int | None = None,
) -> dict[str, Any] | None:
    """One bounded retention pass for one school, on the caller's transaction.

    Tries the school's advisory lock (never waits) and returns None when
    another pass holds it. Otherwise, under a statement timeout: deletes at
    most ``batch_rows`` trail rows older than the school's retention by
    ``received_at`` (oldest first), nulls the copied coordinates on
    exceptions and events older than retention by ``created_at``, and
    deletes at most ``batch_rows`` idempotency keys older than
    ``key_ttl_days``. Returns the counts. The advisory lock and the SET LOCAL
    end with the caller's transaction; a timeout raises and aborts it, which
    the callers treat as "skipped". The retention is the school's own, else
    Settings' default (U11), read per call.
    """
    if default_retention_days is None:
        default_retention_days = int(get_settings().gps_position_retention_days)
    locked = conn.execute(
        "select pg_try_advisory_xact_lock(%s, hashtext(%s)) as locked",
        (PURGE_LOCK_CLASS, str(school_id)),
    ).fetchone()["locked"]
    if not locked:
        return None
    conn.execute(
        sql.SQL("set local statement_timeout = {}").format(
            sql.Literal(f"{statement_timeout_ms}ms")
        )
    )
    school = conn.execute(
        "select coalesce(position_retention_days, %s) as days from live_schools where id = %s",
        (default_retention_days, school_id),
    ).fetchone()
    days = int(school["days"]) if school else default_retention_days

    # The cutoff is `now()` minus retention in SECONDS: an interval's day
    # field is calendar arithmetic in the session time zone (a DST zone moves
    # it by an hour), while seconds are absolute — the same instant under
    # any zone, Nairobi's included (see RETENTION_CUTOFF_SQL).
    trail = conn.execute(
        f"""
        with victims as (
            select id from run_positions
            where school_id = %s and received_at < {RETENTION_CUTOFF_SQL}
            order by received_at
            limit %s
            for update skip locked
        )
        delete from run_positions p using victims v where p.id = v.id
        """,
        (school_id, days, batch_rows),
    ).rowcount
    exceptions = conn.execute(
        f"""
        update run_exceptions
        set fix_lat = null, fix_lng = null, fix_accuracy_m = null, fix_captured_at = null
        where school_id = %s and created_at < {RETENTION_CUTOFF_SQL}
          and (fix_lat is not null or fix_lng is not null
               or fix_accuracy_m is not null or fix_captured_at is not null)
        """,
        (school_id, days),
    ).rowcount
    events = conn.execute(
        f"""
        update run_exception_events
        set fix_lat = null, fix_lng = null, fix_accuracy_m = null, fix_captured_at = null
        where school_id = %s and created_at < {RETENTION_CUTOFF_SQL}
          and (fix_lat is not null or fix_lng is not null
               or fix_accuracy_m is not null or fix_captured_at is not null)
        """,
        (school_id, days),
    ).rowcount
    keys = idempotency_dao.purge_expired(
        conn, school_id, ttl_days=key_ttl_days, limit=batch_rows
    )
    return {
        "school_id": str(school_id),
        "retention_days": days,
        "trail_rows_deleted": trail,
        "exceptions_nulled": exceptions,
        "events_nulled": events,
        "keys_deleted": keys,
        "batch_rows": batch_rows,
    }


def _flag_classification_failed(conn, row_id: str) -> None:
    """Mark a ping's trail row after one of its savepoints rolled back — its
    own savepoint, so the bookkeeping never takes the batch down with it."""
    try:
        with conn.transaction():
            add_flag(conn, row_id, FLAG_CLASSIFICATION_FAILED)
    except Exception:
        logger.exception("could not flag trail row %s", row_id)


# "Judge the first ping of a batch against the trail" — the batch passes an
# explicit previous fix for every ping after it.
_PREVIOUS_FROM_TRAIL = object()


class PositionDao:
    def purge_after_start(self, scope: SchoolScope) -> dict[str, Any] | None:
        """The post-Start-Run pass (R12): one batch on its own scoped
        connection so RLS confines it to the school. Returns the counts, or
        None when another pass holds the school's lock. Raises on timeout or
        failure — the router's wrapper logs and swallows, never the driver."""
        with get_connection(scope) as conn:
            return purge_batch(conn, scope.school_id)

    # --- Phase 2 pings (GPS plan U14: R24, R26, R27, R28; F6) ----------------

    def record_pings(
        self, scope: SchoolScope, run_id: str, fixes: list[Any], device_id: Any = None,
    ) -> dict[str, Any]:
        """One ping batch for the caller's own in-progress run, on one scoped
        connection; the trail insert is the whole of it (the plan's "a ping
        is one insert" — no fan-out, no purge, no prompt).

        Refusals, in order, and every one inserts nothing: a run the scope
        cannot see is 404 (another school's, through RLS); a run this driver
        does not own is 403; a completed or prior-day run is 409
        ``run-not-active`` (AE15); a caller whose auth session is not the
        one bound to the run (``started_session_id`` — Start Run stamps it,
        any later tap re-binds it) is 409 ``session-mismatch``, and the run
        is flagged for the office once per run when it is bound to a
        different session (a run nobody's session started — created by the
        office — is refused the same way but flags nothing, there being no
        second session to report); a batch arriving while the run's newest
        ping is younger than half the school's ping interval is 429
        ``ping-too-soon``.

        The batch: each fix is normalised leniently — a malformed one is
        dropped and counted ``invalid``, never a 422; a capture time outside
        the run's window (a minute before the run row was created … receipt
        plus ``GPS_PING_FUTURE_WINDOW_S``) is dropped and counted
        ``outside_window``; inside the window but ahead of receipt by more
        than the skew tolerance is stored flagged ``clock-skew``. Rows are
        inserted in capture order under the (run, capture time) key —
        duplicates counted, not stored — with source ``ping``, the session,
        the device id, ``coarse`` when above the school's cap, and judged by
        the plausibility safeguard (U12) against the previous stored fix
        (the ping before it in the batch, the trail for the first) in the
        taps' savepoint style: flags on the row, a flagged ping listed on
        the run's ``implausible-movement`` review row, a failed step marks
        the row ``classification-failed`` and the batch goes on.
        ``repeat-coordinates`` is not applied to pings: a parked phone's
        stream legitimately repeats.

        The served position moves forward only, by capture time: after the
        batch, the newest accepted ping that is plain — judged, unflagged,
        not coarse — is written as source ``ping`` when it is later than the
        bus's current ``position_at`` (or that is null). Coarse, skewed,
        flagged and unjudged pings are stored and never served; a dense
        stream loses nothing by it.

        Returns ``{accepted, dropped: {invalid, outside_window, duplicate},
        flagged, served_at}``; ``served_at`` is the capture time now served,
        or None when this batch did not move the position. The log line
        carries the run id and counts — never a coordinate.
        """
        from app.dao import exception_dao  # lazy: exception_dao imports this module

        driver_id = str(scope.user_id)
        mismatch: str | None = None
        outcome: dict[str, Any] | None = None
        with get_connection(scope) as conn:
            run = conn.execute(
                """
                select id, school_id, bus_id, driver_id, status, created_at, started_session_id,
                       date = (now() at time zone 'Africa/Nairobi')::date as is_today
                from live_runs
                where id = %s and school_id = %s
                for update
                """,
                (run_id, scope.school_id),
            ).fetchone()
            if not run:
                raise NotFoundError("Run not found")
            if (
                run["driver_id"] is None
                or str(run["driver_id"]) != driver_id
                or run["bus_id"] is None
            ):
                raise ForbiddenError("Run is not owned by this driver")
            if run["status"] == "completed" or not run["is_today"]:
                raise PingRefusedError("Run is not in progress", code="run-not-active")
            bound = run["started_session_id"]
            if not scope.session_id or bound is None or str(bound) != str(scope.session_id):
                if bound is not None and scope.session_id:
                    # Flag first, refuse after: the refusal must not roll
                    # the flag back, and a failing flag must not turn the
                    # refusal into a 500.
                    try:
                        with conn.transaction():
                            exception_dao.record_session_mismatch(
                                conn, {"id": str(run["id"]), "school_id": str(run["school_id"])}
                            )
                    except Exception:
                        logger.exception(
                            "session-mismatch flag not recorded (run=%s)", run["id"]
                        )
                mismatch = (
                    "bound to another sign-in" if bound is not None else "not bound to a sign-in"
                )
            else:
                outcome = self._record_batch(conn, scope, dict(run), fixes, device_id)
        if mismatch:
            raise PingRefusedError(
                f"This run is {mismatch}; a tapped action from this one re-binds it",
                code="session-mismatch",
            )
        assert outcome is not None
        return outcome

    @staticmethod
    def _record_batch(
        conn, scope: SchoolScope, run: dict[str, Any], fixes: list[Any], device_id: Any,
    ) -> dict[str, Any]:
        from app.dao import exception_dao  # lazy, as above

        run_id = str(run["id"])
        school_id = str(run["school_id"])
        bus_id = str(run["bus_id"])
        thresholds = resolve_school_thresholds(conn, school_id)

        # Pace the stream, not the taps: the newest PING row by receipt.
        pace_s = thresholds.ping_interval_s / 2
        age = newest_ping_age_s(conn, run_id)
        if age is not None and age < pace_s:
            raise PingPacedError(
                "Pings are arriving faster than half the school's ping interval",
                retry_after_s=pace_s - age,
            )

        now = datetime.now(timezone.utc)
        window_start = run["created_at"] - timedelta(seconds=GPS_PING_RUN_START_GRACE_S)
        window_end = now + timedelta(seconds=GPS_PING_FUTURE_WINDOW_S)
        dropped = {"invalid": 0, "outside_window": 0, "duplicate": 0}
        usable: list[NormalisedFix] = []
        for raw in fixes:
            normalised = normalise_fix(
                raw, now=now, accuracy_cap_m=thresholds.fix_accuracy_cap_m,
                skew_tolerance_s=GPS_CLOCK_SKEW_TOLERANCE_S,
            )
            if normalised.fix is None:
                # Malformed, out of range, or a reason-only object: a ping
                # without coordinates is not a ping.
                dropped["invalid"] += 1
                continue
            if not (window_start <= normalised.fix.captured_at <= window_end):
                dropped["outside_window"] += 1
                continue
            usable.append(normalised)
        usable.sort(key=lambda item: item.fix.captured_at)  # type: ignore[union-attr]

        run_ref = {"id": run_id, "school_id": school_id}
        stops = planned_stops(conn, run_id)
        device = idempotency_dao.device_id_of(device_id)
        previous: Any = _PREVIOUS_FROM_TRAIL
        accepted = flagged = unjudged = 0
        servable: StoredFix | None = None
        for normalised in usable:
            stored = normalised.fix
            assert stored is not None
            row_id = append_ping_row(
                conn, school_id=school_id, run_id=run_id, bus_id=bus_id,
                session_id=scope.session_id, device_id=device, fix=normalised,
            )
            if row_id is None:
                dropped["duplicate"] += 1
                continue
            accepted += 1
            verdict = None
            try:
                with conn.transaction():
                    explicit = {} if previous is _PREVIOUS_FROM_TRAIL else {"previous": previous}
                    verdict = exception_dao.assess_plausibility(
                        conn, run_ref, trail_row_id=row_id, fix=normalised,
                        retention_days=thresholds.position_retention_days, stops=stops,
                        exempt=(FLAG_REPEAT_COORDINATES,), **explicit,
                    )
            except Exception:
                logger.exception(
                    "ping plausibility not assessed; the row stays unserved (run=%s)", run_id
                )
                _flag_classification_failed(conn, row_id)
                unjudged += 1
            if verdict is not None and verdict.flagged:
                flagged += 1
                try:
                    with conn.transaction():
                        exception_dao.record_implausible_fix(
                            conn, run_ref, fix=normalised, verdict=verdict,
                            action_key=None, student_id=None,
                        )
                except Exception:
                    logger.exception(
                        "implausible ping not listed for review (run=%s flags=%s)",
                        run_id, ",".join(verdict.flags),
                    )
                    _flag_classification_failed(conn, row_id)
            previous = stored
            if (
                verdict is not None
                and not verdict.flagged
                and normalised.reason != FIX_REASON_COARSE
            ):
                servable = stored  # capture order: the last one standing is the newest

        served_at = None
        if servable is not None and write_ping_position(conn, bus_id, servable):
            served_at = servable.captured_at
        logger.info(
            "ping batch recorded (run=%s accepted=%s flagged=%s unjudged=%s "
            "invalid=%s outside_window=%s duplicate=%s served=%s)",
            run_id, accepted, flagged, unjudged, dropped["invalid"],
            dropped["outside_window"], dropped["duplicate"], served_at is not None,
        )
        return {
            "accepted": accepted,
            "dropped": dropped,
            "flagged": flagged,
            "served_at": served_at.isoformat() if served_at else None,
        }
