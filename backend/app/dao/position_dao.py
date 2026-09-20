"""The per-run position trail and the served bus position (GPS plan U7).

Two shapes, like ``exception_dao``:

- module functions on the caller's connection, used by the action DAOs
  inside the action transaction: append the tap's trail row, write the five
  position columns as one statement, clear them, flag a row;
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
from typing import Any

from psycopg import sql

from app.core.config import (
    GPS_ACTION_KEY_TTL_DAYS,
    GPS_PURGE_BATCH_ROWS,
    GPS_PURGE_STATEMENT_TIMEOUT_MS,
    get_settings,
)
from app.core.db import get_connection
from app.core.scope import SchoolScope
from app.dao import idempotency_dao
from app.services.position_rules import NormalisedFix, StoredFix

logger = logging.getLogger("saferide.positions")

SOURCE_ACTION = "action"
SOURCE_CHECKPOINT = "checkpoint"

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


def add_flag(conn, row_id: str, flag: str) -> None:
    """Append a flag to a trail row once (idempotent). The seam U9/U10 use to
    mark ``classification-failed`` after their savepoint rolled back."""
    conn.execute(
        """
        update run_positions set flags = array_append(flags, %s)
        where id = %s and not (%s = any(flags))
        """,
        (flag, row_id, flag),
    )


def run_fixes(
    conn, run_id: str, *, default_retention_days: int | None = None
) -> list[StoredFix]:
    """Every phone fix on a run's trail inside the school's retention, oldest
    first — the read behind "bus seen at stop" (U9, R13).

    Phone fixes only: a checkpoint row is the planned stop coordinate stamped
    by a fix-less Arrive, and counting it would make every stop "seen" by
    construction — the fabricated corroboration the plan warns about. Rows
    past retention are never served (R12), so a purge that has not run yet
    is invisible here too. The school's own retention applies; the default
    beneath it is Settings' (U11), read per call.
    """
    if default_retention_days is None:
        default_retention_days = int(get_settings().gps_position_retention_days)
    rows = conn.execute(
        """
        select p.lat, p.lng, p.accuracy_m, p.captured_at
        from run_positions p
        join live_runs r on r.id = p.run_id
        left join live_schools s on s.id = r.school_id
        where p.run_id = %s
          and p.source <> %s
          and p.lat is not null and p.lng is not null
          and p.accuracy_m is not null and p.captured_at is not null
          and p.received_at >= now() - make_interval(
                secs => coalesce(s.position_retention_days, %s) * 86400)
        order by p.received_at asc, p.id asc
        """,
        (run_id, SOURCE_CHECKPOINT, default_retention_days),
    ).fetchall()
    return [
        StoredFix(
            lat=row["lat"], lng=row["lng"], accuracy_m=row["accuracy_m"],
            captured_at=row["captured_at"],
        )
        for row in rows
    ]


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


class PositionDao:
    def purge_after_start(self, scope: SchoolScope) -> dict[str, Any] | None:
        """The post-Start-Run pass (R12): one batch on its own scoped
        connection so RLS confines it to the school. Returns the counts, or
        None when another pass holds the school's lock. Raises on timeout or
        failure — the router's wrapper logs and swallows, never the driver."""
        with get_connection(scope) as conn:
            return purge_batch(conn, scope.school_id)
