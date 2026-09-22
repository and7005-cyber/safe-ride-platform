"""Stop exceptions: the office's decision record for a run (GPS plan, U2).

An exception is a school-owned ``run_exceptions`` row keyed by kind — for a
bypassed stop, by (run, stop order) through the partial unique index — with a
``run_exception_events`` ledger underneath it. The prompt IS the event row
(``prompt_state``, ``delivered_at``, ``shown_at``, ``response``); later taps
attach as further events (U3 answers them, U9/U10 add the other kinds).

Two shapes live here on purpose:

- module functions that take the caller's connection and never open their
  own, like ``participation_dao``: the Arrive evaluation runs in a savepoint
  inside the action transaction, and the run report reads exceptions on the
  connection it already holds;
- ``ExceptionDao`` for the staff routes, opening the scoped connection the way
  every other DAO does, so RLS confines the read and a foreign run is 404.

Open or resolved is never stored. A bypassed-stop exception is *open* while
the closure gate's own per-stop predicate still names a child at that stop and
*resolved* once it names nobody — a boarding closes it and an undo reopens it
with no second write path (R15, R21). ``reviewed_at`` is stored and independent
of that: the office can review an exception that is still open, and the
runs-list badge (U4) reads the stored stamp alone.

Prompts are server-owned (U3, R23, R34). The pending events of the driver's
open run are re-delivered, priority-ordered, in every driver context read, so
a reload, a reconnect or a second device re-shows them; the driver answers by
event id through the respond route, whose conflicts are machine-readable; the
card's mount stamps ``shown_at``. Auto-resolution runs in the action DAOs on
the action's own transaction: Arrive closes *shown* remote-absent prompts as
unanswered (an unshown one stays pending; a bypassed-stop prompt is never
closed by an Arrive), End Run and force-close close every pending prompt of
the run, and Board, Drop-off and Absent close nothing — instead, a Board,
Drop-off or Absent for a child listed on an *open* bypassed-stop exception of
this run (the per-stop predicate named the child before the tap) is recorded
on that exception's ledger as a resolution tap, by membership, whether it
came through the card or from the board page and whatever the prompt's
state; the card's event id is a hint, never the key.

The custody check (U9, R14, R20) is the second writer. Every Board and
Drop-off that is not a resolution tap is classified against the child's stop
on this run's own snapshot: ``away`` upserts the per-stop ``custody-away``
row and adds one *pending prompt event per tap* naming the child, so two
children at one stop are confirmed or undone independently; ``unverified``
upserts one ``unverified`` row per (run, reason) — per (run, stop) for
``stop-unverified``, the index's key — with one silent event per tap. The
custody prompt's answers are ``confirmed`` (the respond route) and
``retracted`` (written by the reverse path when the driver undoes the tap,
from the card or the board page: a pending prompt is answered directly, a
confirmed one keeps its answer and a ``retracted`` ledger row is appended
after it). Its status is derived on read from each child's latest event:
open while any prompt is pending, confirmed if any child's latest is a
confirmation, retracted only if every child's latest is a retraction. "Bus
seen at stop" is derived on read too, from any phone fix on the run inside
the vicinity radius — informational, never a reason to clear or downgrade a
row.

The absent classification (U10, R16–R18) is the third writer. Every Absent
is classified after the absence is written: the unverified reasons first
(the same rows as the custody check, so a coarse fix at a coordinate-less
stop is one ``stop-unverified`` row and never two prompts), then the
corroborations — a parent or office absence that already covered the trip,
the phone at the child's stop, or an afternoon mark at the school while the
child's stop is still ahead — and otherwise a per-(run, child)
``absent-remote`` row (016's partial index) with one *pending three-way
prompt* per tap: ``told-me`` flips the kind to ``absent-attested`` (history
only: no live row, no incident, no call-now); ``not-at-stop``, the card's
``dismissed``, or a *shown* prompt left unanswered until the next Arrive
keeps it uncorroborated and stamps the call-now notice due on the deciding
event — at most once per child per run, judged on the child's ledger, so a
mark / undo / mark cycle cannot re-arm it. The post-commit drain
(``ExceptionDao.send_due_call_now``) sends every due-but-unsent row of the
school and stamps it sent; it runs after every driver action and every
context poll, so a fan-out lost between commit and send is re-attempted, and
the row lock plus the sent stamp plus the notification dedup make it once.
Unanswered at End Run or force-close is recorded without call-now. The undo
answers the child's pending absent prompt ``undo`` (or appends an ``undo``
row after an answer, history kept) and never touches the call-now stamps.

The plausibility safeguard (U12, R32) is the fourth writer, and it runs on
every tap that stores a fix — Start Run to End Run — before the custody and
absent checks. ``assess_plausibility`` compares the fix with the run's
previous fix-bearing trail row (by receipt, inside retention) and the run's
planned stops, and merges the flags it finds onto the tap's trail row; the
action DAO hands those flags to the two checks, so a flagged fix classifies
``unverified`` / ``implausible`` and neither raises nor clears anything.
``record_implausible_fix`` then lists the fix for the office on the run's one
``implausible-movement`` row (016's per-run partial index): the first flagged
fix creates the row with its fix columns and the distance it moved; every
flagged fix — the first included — attaches as a silent ledger event carrying
its fix columns, ``distance_m`` = how far it sits from the previous distinct
capture (None for a run's first fix) and ``student_id`` when the tap named a
child; the row's ``reason`` is the union of every flag seen on the run so
far, comma-joined in vocabulary order, so the office reads the whole picture
on one line. No prompt, no call-now, no incident: the office's second
opinion, nothing the driver has to answer.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from app.core.db import get_connection
from app.core.errors import (
    BadRequestError,
    ForbiddenError,
    NotFoundError,
    PromptConflictError,
)
from app.core.scope import SchoolScope
from app.dao import participation_dao, position_dao
from app.dao.audit_dao import masked_display_sql, record_audit
from app.dao.school_thresholds import resolve_school_thresholds
from app.services.position_rules import (
    CUSTODY_WITHIN,
    PLAUSIBILITY_FLAGS,
    REASON_STOP_UNVERIFIED,
    NormalisedFix,
    StoredFix,
    classify_absent,
    classify_custody,
    jump_distance_m,
    plausibility_flags,
    within_vicinity,
)

logger = logging.getLogger("saferide.exceptions")

STOP_BYPASSED = "stop-bypassed"
ABSENT_REMOTE = "absent-remote"
ABSENT_ATTESTED = "absent-attested"
CUSTODY_AWAY = "custody-away"
UNVERIFIED = "unverified"
IMPLAUSIBLE_MOVEMENT = "implausible-movement"
# The two absent kinds share 016's (run, student) partial index: one row per
# child per run, whichever kind it currently reads.
ABSENT_KINDS = (ABSENT_REMOTE, ABSENT_ATTESTED)

# The custody prompt's answers on the ledger (R13, R14): the respond route
# writes `confirmed`; the reverse path writes `retracted`.
CUSTODY_CONFIRMED = "confirmed"
CUSTODY_RETRACTED = "retracted"

# The remote-absent prompt's answers (R17): the respond route writes the
# first two and the card's dismiss; the reverse path writes `undo`.
ABSENT_TOLD_ME = "told-me"
ABSENT_NOT_AT_STOP = "not-at-stop"
ABSENT_UNDO = "undo"
PROMPT_DISMISSED = "dismissed"

# Delivery order of pending prompts (R13): the two safety prompts before the
# routine custody confirm, unknown kinds last; ties break on creation time.
# The same table lives in the client queue; keep the two in step.
PROMPT_PRIORITY: dict[str, int] = {STOP_BYPASSED: 0, ABSENT_REMOTE: 0, CUSTODY_AWAY: 1}
_PRIORITY_SQL = (
    "case x.kind when 'stop-bypassed' then 0 when 'absent-remote' then 0 "
    "when 'custody-away' then 1 else 2 end"
)

# What the respond route accepts per kind. A bypassed stop is answered by the
# outcome taps themselves (they arrive at the boarding/absent routes with the
# event id), so its only spoken answer is the explicit dismiss. The custody
# confirm takes `confirmed` (U9); the remote absent takes the two
# attestation answers and the card's dismiss (U10). Undo goes through the
# reverse path in every kind and is recorded there, never here.
PROMPT_ANSWERS: dict[str, tuple[str, ...]] = {
    STOP_BYPASSED: (PROMPT_DISMISSED,),
    CUSTODY_AWAY: (CUSTODY_CONFIRMED,),
    ABSENT_REMOTE: (ABSENT_TOLD_ME, ABSENT_NOT_AT_STOP, PROMPT_DISMISSED),
}
# The remote-absent answers that leave the mark uncorroborated (R17): each
# decides the call-now notice and the office incident in its own transaction.
UNCORROBORATED_ANSWERS = frozenset({ABSENT_NOT_AT_STOP, PROMPT_DISMISSED})

PROMPT_ALREADY_ANSWERED = "prompt-already-answered"
PROMPT_RESOLVED = "prompt-resolved"


def _prompt_payload(
    event: dict[str, Any], kind: str, *, stop_order: int | None, stop_name: str | None,
    students: list[dict[str, Any]], student_id: str | None = None,
    distance_m: float | None = None,
) -> dict[str, Any]:
    """The one prompt shape both deliveries use — the action response and the
    context poll — so the client queue can key and merge them by event id.
    ``distance_m`` is the tap's distance from the stop for the custody copy
    ("about 1.8 km from their stop"); None for a bypassed stop."""
    return {
        "event_id": str(event["id"]),
        "exception_id": str(event["exception_id"]),
        "kind": kind,
        "stop_order": stop_order,
        "stop_name": stop_name,
        "student_id": student_id,
        "students": students,
        "answers": list(PROMPT_ANSWERS.get(kind, ())),
        "distance_m": distance_m,
        "created_at": event.get("created_at"),
        "delivered_at": event.get("delivered_at"),
        "shown_at": event.get("shown_at"),
    }


def _stop_name_sql(event_alias: str, exception_alias: str) -> str:
    return f"""(
        select rs.name from run_stops rs
        where rs.run_id = {event_alias}.run_id and rs.stop_order = {exception_alias}.stop_order
        order by rs.is_school_gate desc, rs.name asc
        limit 1
    )"""


def evaluate_bypassed_stop(
    conn, run: dict[str, Any], passed_order: int
) -> dict[str, Any] | None:
    """Evaluate the stop an Arrive just moved past (R15, F3).

    Called by ``RunDao.arrive_next_stop`` inside a savepoint after progress
    moved to N; ``passed_order`` is N−1. Nothing is evaluated when there is no
    such stop, when it is the school gate, when it carries no students, or for
    the last stop, which End Run's gate owns. The students are the closure
    gate's own per-stop predicate, so the check can never flag a cross-bus
    rider or a covered absence the gate would let through.

    Writes at most one exception row per (run, stop order) — the partial
    unique index is the key, so a catch-up Arrive re-evaluates the same stop
    idempotently — and at most one *pending* prompt event on it at a time. The
    row carries ``student_id`` NULL: a bypassed stop is per stop, and the
    children it concerns are derived at read time against current outcomes,
    which is also what makes it read open or resolved.

    Returns None when nobody at the stop is unrecorded (an existing exception
    then simply reads resolved), otherwise::

        {"exception_id", "stop_order", "stop_name", "students", "raised", "prompt"}

    ``raised`` is True only when this call inserted the exception row — the
    router's dedup for the office alert, so a reopen or a catch-up Arrive never
    re-alerts. ``prompt`` is the pending event created here, or None when one
    was already pending.
    """
    run_id = str(run["id"])
    total_stops = run.get("total_stops") or 0
    if passed_order < 1 or passed_order >= total_stops:
        return None
    stop_rows = conn.execute(
        """
        select name, is_school_gate, student_id from run_stops
        where run_id = %s and stop_order = %s
        order by is_school_gate desc, name asc
        """,
        (run_id, passed_order),
    ).fetchall()
    if not stop_rows or any(r["is_school_gate"] for r in stop_rows):
        return None
    if all(r["student_id"] is None for r in stop_rows):
        return None
    students = participation_dao.unaccounted_at_stop(conn, run_id, run["type"], passed_order)
    if not students:
        return None

    inserted = conn.execute(
        """
        insert into run_exceptions (school_id, run_id, stop_order, kind)
        values (%s, %s, %s, %s)
        on conflict (run_id, stop_order) where kind = 'stop-bypassed' do nothing
        returning id
        """,
        (str(run["school_id"]), run_id, passed_order, STOP_BYPASSED),
    ).fetchone()
    raised = inserted is not None
    if raised:
        exception_id = inserted["id"]
    else:
        # Lock the existing row so two catch-up Arrives serialise here and the
        # pending-event check below cannot both see "none" and insert twice.
        existing = conn.execute(
            """
            select id from run_exceptions
            where run_id = %s and stop_order = %s and kind = %s
            for update
            """,
            (run_id, passed_order, STOP_BYPASSED),
        ).fetchone()
        if not existing:
            return None
        exception_id = existing["id"]

    pending = conn.execute(
        """
        select id from run_exception_events
        where exception_id = %s and prompt_state = 'pending'
        limit 1
        """,
        (exception_id,),
    ).fetchone()
    listed = [{"id": str(s["id"]), "name": s["name"]} for s in students]
    stop_name = stop_rows[0]["name"]
    prompt = None
    if not pending:
        event = conn.execute(
            """
            insert into run_exception_events (exception_id, school_id, run_id, prompt_state)
            values (%s, %s, %s, 'pending')
            returning id, exception_id, created_at, delivered_at, shown_at
            """,
            (exception_id, str(run["school_id"]), run_id),
        ).fetchone()
        # The driver-facing prompt, in the shape the context poll re-delivers
        # (U3). delivered_at stays null here: the response carries it, and the
        # first context read stamps the delivery.
        prompt = _prompt_payload(
            dict(event), STOP_BYPASSED, stop_order=passed_order, stop_name=stop_name,
            students=listed,
        )
    return {
        "exception_id": str(exception_id),
        "stop_order": passed_order,
        "stop_name": stop_name,
        "students": listed,
        "raised": raised,
        "prompt": prompt,
    }


def pending_prompts(conn, run: dict[str, Any]) -> list[dict[str, Any]]:
    """The run's pending prompts, priority-ordered, for the driver context (R34).

    Stamps ``delivered_at`` on every pending prompt of the run that has none —
    once, on the first read that returns it — then reads the list. Each entry
    is ``_prompt_payload``'s shape. For a bypassed stop the children are the
    per-stop predicate's current answer, so a card shrinks as the driver
    records them. A pending bypassed-stop prompt whose stop names nobody is
    not delivered — an empty question is not a question. Driver outcomes
    answer the prompt by membership before this can happen; the guard covers
    the other ways a child stops being unaccounted (an office absence recorded
    mid-run, a confirmed boarding on another bus's run), after which End Run
    or force-close closes the prompt as unanswered.
    """
    run_id = str(run["id"])
    conn.execute(
        """
        update run_exception_events set delivered_at = now()
        where run_id = %s and prompt_state = 'pending' and delivered_at is null
        """,
        (run_id,),
    )
    rows = conn.execute(
        f"""
        select e.id, e.exception_id, e.student_id as event_student_id, e.created_at,
               e.delivered_at, e.shown_at, e.distance_m,
               x.kind, x.stop_order, x.student_id as exception_student_id,
               s.name as student_name,
               {_stop_name_sql("e", "x")} as stop_name
        from run_exception_events e
        join run_exceptions x on x.id = e.exception_id
        left join live_students s on s.id = coalesce(e.student_id, x.student_id)
        where e.run_id = %s and e.prompt_state = 'pending'
        order by {_PRIORITY_SQL}, e.created_at asc, e.id asc
        """,
        (run_id,),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        kind = row["kind"]
        student_id = row["event_student_id"] or row["exception_student_id"]
        if kind == STOP_BYPASSED and row["stop_order"] is not None:
            listed = participation_dao.unaccounted_at_stop(
                conn, run_id, run["type"], row["stop_order"]
            )
            if not listed:
                continue
            students = [{"id": str(s["id"]), "name": s["name"]} for s in listed]
        elif student_id:
            students = [{"id": str(student_id), "name": row["student_name"]}]
        else:
            students = []
        out.append(
            _prompt_payload(
                dict(row), kind, stop_order=row["stop_order"], stop_name=row["stop_name"],
                students=students, student_id=str(student_id) if student_id else None,
                distance_m=row["distance_m"],
            )
        )
    return out


def listed_on_open_bypassed(
    conn, run: dict[str, Any], stop_order: int | None, student_id: str
) -> bool:
    """Does an open bypassed-stop exception of this run list this child?
    (R14, R15 — the membership the custody exemption keys on.)

    Read by the Board, Drop-off and Absent DAOs *before* the outcome is
    written: "listed" is the closure gate's own per-stop predicate naming the
    child at that stop, which is exactly what makes the exception read open
    — a prompt's state (pending, dismissed, answered) plays no part. After
    the tap the child has an outcome, so the question can only be asked
    beforehand.
    """
    if stop_order is None:
        return False
    run_id = str(run["id"])
    exists = conn.execute(
        "select 1 from run_exceptions where run_id = %s and stop_order = %s and kind = %s",
        (run_id, stop_order, STOP_BYPASSED),
    ).fetchone()
    if not exists:
        return False
    listed = participation_dao.unaccounted_at_stop(conn, run_id, run["type"], stop_order)
    return any(str(s["id"]) == str(student_id) for s in listed)


def record_resolution(
    conn, run: dict[str, Any], student_id: str, *,
    event_id: str | None = None, listed_before: bool = False,
) -> dict[str, Any] | None:
    """Attach an outcome tap to the open bypassed-stop exception at the
    child's stop (R14, R15, F3; the plan's "keyed by the tap" decision).

    Called by the Board, Drop-off and Absent DAOs after the outcome is written,
    inside their savepoint, for every tap. Membership is the key, judged
    before the tap: ``listed_before`` is the caller's answer from
    ``listed_on_open_bypassed`` — whether the tap came through the card or
    from the board page, and whether the exception's prompt is pending,
    dismissed or already answered. ``event_id`` is the client's hint:
    accepted, never required, never preferred over membership (a hint naming
    another prompt is logged and ignored), and never a reason to refuse the
    tap (R23).

    Records one ``resolution`` ledger event naming the child on that
    exception. A prompt still pending is answered ``resolution`` once the stop
    names nobody, so a two-child card stays up, shorter, until the second
    child is recorded; a dismissed prompt keeps its answer while the exception
    still resolves on read.

    Returns ``{"exception_id", "event_id", "answered", "hint_ignored"}`` when
    the tap was attached — the caller then skips the custody check (R14) —
    else None (the child was not listed, or no exception sits at the stop).
    """
    if not listed_before:
        return None
    run_id = str(run["id"])
    at_stop = conn.execute(
        "select stop_order from run_stops where run_id = %s and student_id = %s "
        "order by stop_order asc limit 1",
        (run_id, student_id),
    ).fetchone()
    if not at_stop:
        return None
    exception = conn.execute(
        """
        select id, school_id, stop_order from run_exceptions
        where run_id = %s and stop_order = %s and kind = %s
        for update
        """,
        (run_id, at_stop["stop_order"], STOP_BYPASSED),
    ).fetchone()
    if not exception:
        return None
    pending = conn.execute(
        """
        select id from run_exception_events
        where exception_id = %s and prompt_state = 'pending'
        order by created_at asc, id asc
        limit 1
        for update
        """,
        (exception["id"],),
    ).fetchone()
    hint_ignored = bool(event_id) and (pending is None or str(pending["id"]) != str(event_id))
    if hint_ignored:
        logger.info(
            "resolution tap: card hint %s ignored, membership selects exception %s (run=%s)",
            event_id, exception["id"], run_id,
        )
    conn.execute(
        """
        insert into run_exception_events
            (exception_id, school_id, run_id, student_id, response)
        values (%s, %s, %s, %s, 'resolution')
        """,
        (exception["id"], str(exception["school_id"]), run_id, student_id),
    )
    remaining = participation_dao.unaccounted_at_stop(
        conn, run_id, run["type"], exception["stop_order"]
    )
    answered = False
    if pending and not remaining:
        conn.execute(
            """
            update run_exception_events
            set prompt_state = 'answered', response = 'resolution'
            where id = %s
            """,
            (pending["id"],),
        )
        answered = True
    return {
        "exception_id": str(exception["id"]),
        "event_id": str(pending["id"]) if pending else None,
        "answered": answered,
        "hint_ignored": hint_ignored,
    }


# --- the custody check (U9: R14, R20, R23; F2, F5) ------------------------------

# The partial unique indexes of 016 that key a per-stop row, verbatim, so the
# upsert's conflict target matches the index it relies on.
_STOP_KEYED_CONFLICT: dict[tuple[str, str | None], str] = {
    (CUSTODY_AWAY, None): "on conflict (run_id, stop_order) where kind = 'custody-away'",
    (UNVERIFIED, REASON_STOP_UNVERIFIED): (
        "on conflict (run_id, stop_order) "
        "where kind = 'unverified' and reason = 'stop-unverified'"
    ),
}

_FixColumns = tuple[float | None, float | None, float | None, Any]


def _fix_columns(fix: NormalisedFix) -> _FixColumns:
    stored = fix.fix
    if stored is None:
        return (None, None, None, None)
    return (stored.lat, stored.lng, stored.accuracy_m, stored.captured_at)


def _upsert_stop_keyed_exception(
    conn, run: dict[str, Any], *, kind: str, reason: str | None, stop_order: int,
    fix_columns: _FixColumns, distance_m: float | None,
) -> str:
    """One row per (run, stop order) for the kind — the first offending tap
    creates it with its fix and distance, later taps attach (R14). The row is
    locked when it already exists so two taps for one stop serialise their
    event inserts behind it."""
    inserted = conn.execute(
        f"""
        insert into run_exceptions
            (school_id, run_id, stop_order, kind, reason,
             fix_lat, fix_lng, fix_accuracy_m, fix_captured_at, distance_m)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        {_STOP_KEYED_CONFLICT[(kind, reason)]} do nothing
        returning id
        """,
        (str(run["school_id"]), str(run["id"]), stop_order, kind, reason, *fix_columns,
         distance_m),
    ).fetchone()
    if inserted:
        return str(inserted["id"])
    existing = conn.execute(
        """
        select id from run_exceptions
        where run_id = %s and stop_order = %s and kind = %s
          and reason is not distinct from %s
        for update
        """,
        (str(run["id"]), stop_order, kind, reason),
    ).fetchone()
    return str(existing["id"])


def _upsert_run_keyed_unverified(
    conn, run: dict[str, Any], *, reason: str, fix_columns: _FixColumns,
    distance_m: float | None,
) -> tuple[str, bool]:
    """One ``unverified`` row per (run, reason) for ``no-fix``,
    ``too-coarse`` (R20) and ``session-mismatch`` (U14): a GPS-denied run
    must not spawn a row per tap. No index keys these, so the (run, reason)
    pair is serialised on a transaction-scoped advisory lock before the
    select-for-update, and the first tap inside the lock inserts. Returns
    ``(id, inserted)`` so a once-per-run caller can tell the first time."""
    run_id = str(run["id"])
    conn.execute(
        "select pg_advisory_xact_lock(hashtext(%s))", (f"unverified:{run_id}:{reason}",)
    )
    existing = conn.execute(
        """
        select id from run_exceptions
        where run_id = %s and kind = %s and reason = %s and stop_order is null
        for update
        """,
        (run_id, UNVERIFIED, reason),
    ).fetchone()
    if existing:
        return str(existing["id"]), False
    inserted = conn.execute(
        """
        insert into run_exceptions
            (school_id, run_id, kind, reason,
             fix_lat, fix_lng, fix_accuracy_m, fix_captured_at, distance_m)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        returning id
        """,
        (str(run["school_id"]), run_id, UNVERIFIED, reason, *fix_columns, distance_m),
    ).fetchone()
    return str(inserted["id"]), True


# The `reason` of the unverified row a refused ping stream leaves for the
# office (GPS plan U14/R28): pings arrived from an auth session other than the
# one bound to the run. Not a check reason — no tap is classified by it — so
# it lives here, beside the writer, rather than in position_rules.
REASON_SESSION_MISMATCH = "session-mismatch"


def record_session_mismatch(conn, run: dict[str, Any]) -> tuple[str, bool]:
    """Flag the run once for pings from a second session (U14/R28): one
    ``unverified`` / ``session-mismatch`` row per run with one silent ledger
    event written the first time only — no student, no fix, no prompt, no
    call-now, no incident — so a second phone looping on the endpoint adds
    nothing after the first refusal. Returns ``(exception_id, first_time)``.
    Logs the run id only."""
    exception_id, inserted = _upsert_run_keyed_unverified(
        conn, run, reason=REASON_SESSION_MISMATCH, fix_columns=(None, None, None, None),
        distance_m=None,
    )
    if inserted:
        conn.execute(
            """
            insert into run_exception_events (exception_id, school_id, run_id)
            values (%s, %s, %s)
            """,
            (exception_id, str(run["school_id"]), str(run["id"])),
        )
        logger.info(
            "pings from a second session refused; run flagged for review (run=%s exception=%s)",
            run["id"], exception_id,
        )
    return exception_id, inserted


def record_custody_check(
    conn,
    run: dict[str, Any],
    *,
    stop: dict[str, Any],
    student_id: str,
    student_name: str,
    fix: NormalisedFix,
    action_key: str | None,
    custody_threshold_m: float | None = None,
    accuracy_cap_m: float | None = None,
    flags: Iterable[str] = (),
) -> dict[str, Any]:
    """Classify one Board or Drop-off against the child's stop and record the
    verdict (R14, R20; AE3, AE4, AE17).

    Called by ``toggle_boarding`` and ``dropoff_student`` inside their
    savepoint, after the outcome and the trail row are written and only when
    the tap was not a bypassed-stop resolution (those are exempt, R14).
    ``stop`` is the child's ``run_stops`` row on this run — the snapshot's
    coordinates, never the live route's. The thresholds are the school's own
    (U11): the action DAO passes the ones it resolved for the tap; a caller
    that passes none has them resolved here from ``run["school_id"]``.
    ``flags`` are the fix's plausibility flags from ``assess_plausibility``
    (U12): any flag makes the verdict ``unverified`` / ``implausible``.

    - ``within``: nothing written.
    - ``away``: the per-stop ``custody-away`` row (created by the first far
      tap with its fix and distance; later taps attach) and one *pending
      prompt event* naming the child with the fix columns and distance, so
      each tap is confirmed or undone on its own (R13). Returns the prompt.
    - ``unverified``: one ``unverified`` row per (run, reason) — per (run,
      stop) for ``stop-unverified`` — and one silent ledger event per tap
      naming the child (no prompt, no call-now; F5).

    Returns ``{"classification", "reason", "distance_m", "exception_id",
    "prompt"}``. Never logs a coordinate.
    """
    if custody_threshold_m is None or accuracy_cap_m is None:
        thresholds = resolve_school_thresholds(conn, str(run["school_id"]))
        custody_threshold_m = (
            thresholds.custody_threshold_m if custody_threshold_m is None else custody_threshold_m
        )
        accuracy_cap_m = thresholds.fix_accuracy_cap_m if accuracy_cap_m is None else accuracy_cap_m
    verdict = classify_custody(
        fix.fix, (stop.get("lat"), stop.get("lng")),
        custody_threshold_m=custody_threshold_m, accuracy_cap_m=accuracy_cap_m,
        flags=tuple(flags),
    )
    outcome: dict[str, Any] = {
        "classification": verdict.classification,
        "reason": verdict.reason,
        "distance_m": verdict.distance_m,
        "exception_id": None,
        "prompt": None,
    }
    if verdict.classification == CUSTODY_WITHIN:
        return outcome

    run_id = str(run["id"])
    school_id = str(run["school_id"])
    stop_order = int(stop["stop_order"])
    fix_columns = _fix_columns(fix)

    if verdict.away:
        exception_id = _upsert_stop_keyed_exception(
            conn, run, kind=CUSTODY_AWAY, reason=None, stop_order=stop_order,
            fix_columns=fix_columns, distance_m=verdict.distance_m,
        )
        event = conn.execute(
            """
            insert into run_exception_events
                (exception_id, school_id, run_id, student_id, action_key,
                 fix_lat, fix_lng, fix_accuracy_m, fix_captured_at, distance_m, prompt_state)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending')
            returning id, exception_id, created_at, delivered_at, shown_at
            """,
            (exception_id, school_id, run_id, student_id, action_key, *fix_columns,
             verdict.distance_m),
        ).fetchone()
        outcome["exception_id"] = exception_id
        # Delivered in the context poll like every prompt (U3); delivered_at
        # stays null until the first read that returns it.
        outcome["prompt"] = _prompt_payload(
            dict(event), CUSTODY_AWAY, stop_order=stop_order, stop_name=stop.get("name"),
            students=[{"id": str(student_id), "name": student_name}],
            student_id=str(student_id), distance_m=verdict.distance_m,
        )
        logger.info(
            "custody-away recorded (run=%s stop_order=%s exception=%s)",
            run_id, stop_order, exception_id,
        )
        return outcome

    outcome["exception_id"] = _record_unverified(
        conn, run, reason=verdict.reason, stop_order=stop_order, student_id=student_id,
        action_key=action_key, fix_columns=fix_columns, distance_m=verdict.distance_m,
    )
    return outcome


def _record_unverified(
    conn, run: dict[str, Any], *, reason: str, stop_order: int, student_id: str,
    action_key: str | None, fix_columns: _FixColumns, distance_m: float | None,
) -> str:
    """One ``unverified`` row per (run, reason) — per (run, stop) for
    ``stop-unverified`` — with one silent ledger event per tap naming the
    child (no prompt, no call-now; R20, F5). Shared by the custody check and
    the absent classification: a coarse fix at a coordinate-less stop is one
    ``stop-unverified`` row whichever action tapped it."""
    if reason == REASON_STOP_UNVERIFIED:
        exception_id = _upsert_stop_keyed_exception(
            conn, run, kind=UNVERIFIED, reason=reason, stop_order=stop_order,
            fix_columns=fix_columns, distance_m=None,
        )
    else:
        exception_id, _inserted = _upsert_run_keyed_unverified(
            conn, run, reason=reason, fix_columns=fix_columns, distance_m=distance_m,
        )
    conn.execute(
        """
        insert into run_exception_events
            (exception_id, school_id, run_id, student_id, action_key,
             fix_lat, fix_lng, fix_accuracy_m, fix_captured_at, distance_m)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (exception_id, str(run["school_id"]), str(run["id"]), student_id, action_key,
         *fix_columns, distance_m),
    )
    logger.info(
        "unverified check recorded (run=%s stop_order=%s reason=%s exception=%s)",
        run["id"], stop_order, reason, exception_id,
    )
    return exception_id


# --- the absent classification (U10: R16, R17, R18, R20; F4) --------------------

_ABSENT_CONFLICT = (
    "on conflict (run_id, student_id) where kind in ('absent-remote', 'absent-attested') "
    "do nothing"
)


def record_absent_check(
    conn,
    run: dict[str, Any],
    *,
    stop: dict[str, Any],
    school: tuple[Any, Any] | None,
    student_id: str,
    student_name: str,
    fix: NormalisedFix,
    action_key: str | None,
    prior_absence_covers: bool,
    vicinity_m: float | None = None,
    accuracy_cap_m: float | None = None,
    flags: Iterable[str] = (),
) -> dict[str, Any]:
    """Classify one Absent mark and record the verdict (R16, R17, R20; AE6,
    AE7, AE8, AE16). ``flags`` are the fix's plausibility flags (U12): a
    flagged fix is ``unverified`` / ``implausible`` and corroborates nothing.

    Called by ``mark_student_absent`` inside its savepoint, after the absence,
    the snapshot and the trail row are written — the mark itself is never
    held back (R17, R23). ``stop`` is the child's ``run_stops`` row on this
    run; ``school`` the run's gate coordinates (or the school's pin);
    ``prior_absence_covers`` whether a parent or office absence covered this
    trip *before* the driver's upsert. The vicinity radius and accuracy cap
    are the school's own (U11): the action DAO passes the ones it resolved
    for the tap; a caller that passes none has them resolved here from
    ``run["school_id"]``.

    - ``corroborated``: nothing written — the standard notice already went
      out and the trail row carries the fix.
    - ``unverified``: the shared unverified rows (one per reason, per stop
      for ``stop-unverified``), one silent event per tap, never a prompt.
    - ``remote``: the per-(run, child) ``absent-remote`` row — created by the
      first remote mark with its fix and distance; a later remote mark on the
      same child attaches to it, and a row that ``told-me`` had flipped to
      ``absent-attested`` reads ``absent-remote`` again while the new
      question is open — and one *pending three-way prompt event* carrying
      the fix and the distance. When a prompt is already pending on the row
      the tap attaches as a silent event instead (one question at a time).

    Returns ``{"classification", "reason", "corroborated_by", "distance_m",
    "exception_id", "prompt"}``. Never logs a coordinate.
    """
    if vicinity_m is None or accuracy_cap_m is None:
        thresholds = resolve_school_thresholds(conn, str(run["school_id"]))
        vicinity_m = thresholds.vicinity_radius_m if vicinity_m is None else vicinity_m
        accuracy_cap_m = thresholds.fix_accuracy_cap_m if accuracy_cap_m is None else accuracy_cap_m
    verdict = classify_absent(
        fix.fix, (stop.get("lat"), stop.get("lng")),
        school=school, run_type=run["type"], stop_order=stop.get("stop_order"),
        stops_completed=int(run.get("stops_completed") or 0),
        prior_absence_covers=prior_absence_covers,
        vicinity_m=vicinity_m, accuracy_cap_m=accuracy_cap_m,
        flags=tuple(flags),
    )
    outcome: dict[str, Any] = {
        "classification": verdict.classification,
        "reason": verdict.reason,
        "corroborated_by": verdict.corroborated_by,
        "distance_m": verdict.distance_m,
        "exception_id": None,
        "prompt": None,
    }
    run_id = str(run["id"])
    school_id = str(run["school_id"])
    stop_order = int(stop["stop_order"])
    fix_columns = _fix_columns(fix)

    if verdict.corroborated:
        logger.info(
            "absent corroborated by %s (run=%s stop_order=%s)",
            verdict.corroborated_by, run_id, stop_order,
        )
        return outcome
    if verdict.unverified:
        outcome["exception_id"] = _record_unverified(
            conn, run, reason=verdict.reason, stop_order=stop_order, student_id=student_id,
            action_key=action_key, fix_columns=fix_columns, distance_m=verdict.distance_m,
        )
        return outcome

    inserted = conn.execute(
        f"""
        insert into run_exceptions
            (school_id, run_id, stop_order, student_id, kind,
             fix_lat, fix_lng, fix_accuracy_m, fix_captured_at, distance_m)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        {_ABSENT_CONFLICT}
        returning id
        """,
        (school_id, run_id, stop_order, str(student_id), ABSENT_REMOTE, *fix_columns,
         verdict.distance_m),
    ).fetchone()
    if inserted:
        exception_id = str(inserted["id"])
    else:
        # Lock the child's row so two marks serialise their event inserts
        # behind it; a row told-me had attested is a live question again.
        existing = conn.execute(
            """
            select id, kind from run_exceptions
            where run_id = %s and student_id = %s and kind = any(%s)
            for update
            """,
            (run_id, str(student_id), list(ABSENT_KINDS)),
        ).fetchone()
        exception_id = str(existing["id"])
        if existing["kind"] != ABSENT_REMOTE:
            conn.execute(
                "update run_exceptions set kind = %s where id = %s", (ABSENT_REMOTE, exception_id)
            )
    outcome["exception_id"] = exception_id

    pending = conn.execute(
        """
        select id from run_exception_events
        where exception_id = %s and prompt_state = 'pending'
        limit 1
        """,
        (exception_id,),
    ).fetchone()
    event = conn.execute(
        """
        insert into run_exception_events
            (exception_id, school_id, run_id, student_id, action_key,
             fix_lat, fix_lng, fix_accuracy_m, fix_captured_at, distance_m, prompt_state)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        returning id, exception_id, created_at, delivered_at, shown_at
        """,
        (exception_id, school_id, run_id, str(student_id), action_key, *fix_columns,
         verdict.distance_m, None if pending else "pending"),
    ).fetchone()
    if not pending:
        # Delivered in the context poll like every prompt (U3); delivered_at
        # stays null until the first read that returns it.
        outcome["prompt"] = _prompt_payload(
            dict(event), ABSENT_REMOTE, stop_order=stop_order, stop_name=stop.get("name"),
            students=[{"id": str(student_id), "name": student_name}],
            student_id=str(student_id), distance_m=verdict.distance_m,
        )
    logger.info(
        "absent-remote recorded (run=%s stop_order=%s exception=%s prompt=%s)",
        run_id, stop_order, exception_id, "pending" if not pending else "attached",
    )
    return outcome


# --- the plausibility safeguard (U12: R32, R20's implausible reason) -------------


@dataclass(frozen=True)
class PlausibilityVerdict:
    """What ``assess_plausibility`` found: the flags (``()`` for a plausible
    fix or no fix at all), the previous distinct capture it was judged
    against, and how far the fix sits from it (None without one)."""

    flags: tuple[str, ...]
    previous: StoredFix | None
    jump_m: float | None

    @property
    def flagged(self) -> bool:
        return bool(self.flags)


_PREVIOUS_FROM_TRAIL = object()


def assess_plausibility(
    conn, run: dict[str, Any], *, trail_row_id: str, fix: NormalisedFix, retention_days: int,
    previous: StoredFix | None | object = _PREVIOUS_FROM_TRAIL,
    stops: list[tuple[Any, Any]] | None = None,
    exempt: Iterable[str] = (),
) -> PlausibilityVerdict:
    """Judge the tap's fix against the run's previous fix and planned stops
    and merge the flags onto its trail row (U12, R32).

    Called by every action DAO right after the trail row is written, in its
    own savepoint, before the custody or absent check reads the result. The
    previous fix is the run's newest fix-bearing trail row by receipt inside
    the school's retention (``retention_days``, resolved by the tap), the
    stops the run's own snapshot. A fix-less tap has nothing to judge.
    Merges through ``position_dao.add_flags`` so ``clock-skew`` from the
    boundary stays and nothing is written twice. Never logs a coordinate.

    A ping batch (U14) passes ``previous`` and ``stops`` itself: its rows
    share one receipt time, so the trail cannot order them, and the batch is
    judged in capture order against the ping before it. ``exempt`` names
    flags the caller does not apply (the batch exempts ``repeat-coordinates``
    — a parked phone's stream legitimately repeats).
    """
    stored = fix.fix
    if stored is None:
        return PlausibilityVerdict(flags=(), previous=None, jump_m=None)
    run_id = str(run["id"])
    if previous is _PREVIOUS_FROM_TRAIL:
        previous = position_dao.previous_fix(
            conn, run_id, before_row_id=trail_row_id, retention_days=retention_days
        )
    if stops is None:
        stops = position_dao.planned_stops(conn, run_id)
    skipped = set(exempt)
    flags = tuple(
        flag for flag in plausibility_flags(fix, previous=previous, stops=stops)
        if flag not in skipped
    )
    if flags:
        position_dao.add_flags(conn, trail_row_id, flags)
    return PlausibilityVerdict(
        flags=flags, previous=previous, jump_m=jump_distance_m(stored, previous),
    )


def _merge_reason(existing: str | None, flags: Iterable[str]) -> str:
    """The row's ``reason``: every flag seen on the run so far, comma-joined
    in vocabulary order (an unknown stored token is kept, last)."""
    seen = {token for token in (existing or "").split(",") if token}
    seen.update(flags)
    ordered = [flag for flag in PLAUSIBILITY_FLAGS if flag in seen]
    ordered += sorted(token for token in seen if token not in PLAUSIBILITY_FLAGS)
    return ",".join(ordered)


def record_implausible_fix(
    conn, run: dict[str, Any], *, fix: NormalisedFix, verdict: PlausibilityVerdict,
    action_key: str | None, student_id: str | None,
) -> str:
    """List a flagged fix for the office on the run's ``implausible-movement``
    row (U12, R32) — one row per run, 016's partial index.

    The first flagged fix creates the row with its fix columns and ``jump_m``
    as ``distance_m``; the row is locked when it already exists so two
    flagged taps serialise their event inserts behind it, and its ``reason``
    becomes the union of every flag seen so far. Every flagged fix — the
    first included — attaches as one silent ledger event (``prompt_state``
    NULL): the fix columns, ``distance_m`` = how far it moved from the
    previous distinct capture, ``student_id`` when the tap named a child, the
    action key. No prompt, no call-now, no incident. Returns the row id.
    """
    run_id = str(run["id"])
    school_id = str(run["school_id"])
    fix_columns = _fix_columns(fix)
    reason = _merge_reason(None, verdict.flags)
    inserted = conn.execute(
        """
        insert into run_exceptions
            (school_id, run_id, kind, reason,
             fix_lat, fix_lng, fix_accuracy_m, fix_captured_at, distance_m)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        on conflict (run_id) where kind = 'implausible-movement' do nothing
        returning id
        """,
        (school_id, run_id, IMPLAUSIBLE_MOVEMENT, reason, *fix_columns, verdict.jump_m),
    ).fetchone()
    if inserted:
        exception_id = str(inserted["id"])
    else:
        existing = conn.execute(
            """
            select id, reason from run_exceptions
            where run_id = %s and kind = %s
            for update
            """,
            (run_id, IMPLAUSIBLE_MOVEMENT),
        ).fetchone()
        exception_id = str(existing["id"])
        merged = _merge_reason(existing["reason"], verdict.flags)
        if merged != (existing["reason"] or ""):
            conn.execute(
                "update run_exceptions set reason = %s where id = %s", (merged, exception_id)
            )
    conn.execute(
        """
        insert into run_exception_events
            (exception_id, school_id, run_id, student_id, action_key,
             fix_lat, fix_lng, fix_accuracy_m, fix_captured_at, distance_m)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (exception_id, school_id, run_id, str(student_id) if student_id else None, action_key,
         *fix_columns, verdict.jump_m),
    )
    logger.info(
        "implausible fix recorded (run=%s flags=%s exception=%s)",
        run_id, ",".join(verdict.flags), exception_id,
    )
    return exception_id


def stamp_call_now_due(conn, *, event_id: str, run_id: str, student_id: str) -> bool:
    """Decide the call-now notice on this event (R18): due now, unless the
    child's absent ledger on this run already carries a due stamp — at most
    once per child per run, whatever later marks, answers or undos do.

    Called inside the deciding transaction (the respond route for
    ``not-at-stop`` and ``dismissed``, Arrive's auto-resolution of a shown
    prompt). Serialised per (run, child) on an advisory lock so two deciders
    cannot both read "none yet". Returns True when this call stamped it.
    """
    conn.execute(
        "select pg_advisory_xact_lock(hashtext(%s))", (f"call-now:{run_id}:{student_id}",)
    )
    prior = conn.execute(
        """
        select 1 from run_exception_events e
        join run_exceptions x on x.id = e.exception_id
        where e.run_id = %s and x.kind = any(%s)
          and coalesce(e.student_id, x.student_id) = %s
          and e.call_now_due_at is not null
        limit 1
        """,
        (run_id, list(ABSENT_KINDS), str(student_id)),
    ).fetchone()
    if prior:
        return False
    stamped = conn.execute(
        """
        update run_exception_events set call_now_due_at = now()
        where id = %s and call_now_due_at is null
        returning id
        """,
        (event_id,),
    ).fetchone()
    return stamped is not None


def due_call_now(conn, school_id: str) -> list[dict[str, Any]]:
    """The school's due-but-unsent call-now rows, oldest due first, locked
    for this drain (``skip locked``: a concurrent drain leaves them to us).
    Each row carries what ``notify_absent_call_now`` needs — the child and
    the run — and nothing more."""
    rows = conn.execute(
        """
        select e.id as event_id, e.run_id,
               coalesce(e.student_id, x.student_id) as student_id,
               s.name as student_name,
               r.bus_id, r.type as run_type, r.school_id
        from run_exception_events e
        join run_exceptions x on x.id = e.exception_id
        join live_runs r on r.id = e.run_id
        left join live_students s on s.id = coalesce(e.student_id, x.student_id)
        where e.school_id = %s
          and e.call_now_due_at is not null and e.call_now_sent_at is null
        order by e.call_now_due_at asc, e.id asc
        for update of e skip locked
        """,
        (school_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def mark_call_now_sent(conn, event_id: str) -> bool:
    row = conn.execute(
        """
        update run_exception_events set call_now_sent_at = now()
        where id = %s and call_now_sent_at is null
        returning id
        """,
        (event_id,),
    ).fetchone()
    return row is not None


def retract_absent_prompts(
    conn, run: dict[str, Any], student_id: str, *, event_id: str | None = None
) -> list[str]:
    """The undo's answer on the child's absent ledger of this run (R17, R35).

    Called by ``reverse_own_action`` when it withdraws an absence mark,
    whether the undo came from the board page or a card. Membership (run,
    child) is the key; the card's event id is a hint, logged when it names
    something else, never required. History is kept, as the custody
    retraction keeps it:

    - a prompt event of the child still pending is answered ``undo``
      directly;
    - on the child's absent exception whose latest event is an answer
      (told-me, not-at-stop, dismissed) or a prompt closed unanswered, one
      ``undo`` ledger row is appended (``prompt_state`` NULL) — the answer
      stays on the record and the withdrawal after it;
    - a child whose latest event is already ``undo`` is left alone.

    The call-now stamps are never touched (R18): an undo cannot re-arm the
    notice, and the family keeps the message they were sent.

    Returns the ids of the events it answered or appended.
    """
    run_id = str(run["id"])
    rows = conn.execute(
        """
        select e.id, e.exception_id, e.prompt_state, e.response, x.school_id
        from run_exception_events e
        join run_exceptions x on x.id = e.exception_id
        where e.run_id = %s and coalesce(e.student_id, x.student_id) = %s
          and x.kind = any(%s)
        order by e.created_at asc, e.id asc
        for update of e
        """,
        (run_id, str(student_id), list(ABSENT_KINDS)),
    ).fetchall()
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        latest[str(row["exception_id"])] = dict(row)
    touched: list[str] = []
    for row in rows:
        if row["prompt_state"] == "pending":
            conn.execute(
                "update run_exception_events set prompt_state = 'answered', response = %s "
                "where id = %s",
                (ABSENT_UNDO, row["id"]),
            )
            touched.append(str(row["id"]))
    for exception_id, last in latest.items():
        if last["prompt_state"] == "pending" or last["response"] == ABSENT_UNDO:
            continue
        if last["prompt_state"] is None and last["response"] is None:
            # A silent attachment (a second mark while a prompt was pending)
            # is not an answer to withdraw.
            continue
        appended = conn.execute(
            """
            insert into run_exception_events
                (exception_id, school_id, run_id, student_id, response)
            values (%s, %s, %s, %s, %s)
            returning id
            """,
            (exception_id, str(last["school_id"]), run_id, str(student_id), ABSENT_UNDO),
        ).fetchone()
        touched.append(str(appended["id"]))
    if event_id and event_id not in touched:
        logger.info(
            "undo: card hint %s not among the absent events answered by membership (run=%s)",
            event_id, run_id,
        )
    return touched


def retract_custody_prompts(
    conn, run: dict[str, Any], student_id: str, *, event_id: str | None = None
) -> list[str]:
    """The undo's answer on the child's custody ledger of this run (R14, R35).

    Called by ``reverse_own_action`` when it withdraws a boarding or a
    drop-off, whether the undo came from the card or from the board page.
    Membership (run, child) is the key; the card's event id is a hint, logged
    when it names something else, never required. History is kept:

    - every prompt event of the child still pending is answered
      ``retracted`` directly;
    - on each custody exception where the child's latest event is a
      ``confirmed`` answer, one ``retracted`` ledger event is appended
      (``prompt_state`` NULL) — the confirmation stays on the record and the
      correction after it. The reverse path carries no fix today, so the
      event's fix columns are null;
    - a child whose latest event is already ``retracted`` is left alone.

    Returns the ids of the events it answered or appended (possibly none: an
    undo of a tap made at the stop has no prompt to retract).
    """
    run_id = str(run["id"])
    rows = conn.execute(
        """
        select e.id, e.exception_id, e.prompt_state, e.response, x.school_id
        from run_exception_events e
        join run_exceptions x on x.id = e.exception_id
        where e.run_id = %s and e.student_id = %s and x.kind = %s
        order by e.created_at asc, e.id asc
        for update of e
        """,
        (run_id, str(student_id), CUSTODY_AWAY),
    ).fetchall()
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        latest[str(row["exception_id"])] = dict(row)
    touched: list[str] = []
    for row in rows:
        if row["prompt_state"] == "pending":
            conn.execute(
                "update run_exception_events set prompt_state = 'answered', response = %s "
                "where id = %s",
                (CUSTODY_RETRACTED, row["id"]),
            )
            touched.append(str(row["id"]))
    for exception_id, last in latest.items():
        if last["prompt_state"] == "pending" or last["response"] != CUSTODY_CONFIRMED:
            continue
        appended = conn.execute(
            """
            insert into run_exception_events
                (exception_id, school_id, run_id, student_id, response)
            values (%s, %s, %s, %s, %s)
            returning id
            """,
            (exception_id, str(last["school_id"]), run_id, str(student_id), CUSTODY_RETRACTED),
        ).fetchone()
        touched.append(str(appended["id"]))
    if event_id and event_id not in touched:
        logger.info(
            "undo: card hint %s not among the custody events retracted by membership (run=%s)",
            event_id, run_id,
        )
    return touched


_CLOSED_RETURNING = f"""
        returning e.id, e.exception_id, e.run_id, x.kind, x.stop_order,
                  coalesce(e.student_id, x.student_id) as student_id,
                  (select s.name from live_students s
                   where s.id = coalesce(e.student_id, x.student_id)) as student_name,
                  {_stop_name_sql("e", "x")} as stop_name
"""


def resolve_pending_on_arrive(conn, run_id: str) -> list[dict[str, Any]]:
    """Arrive's auto-resolution (R17, R34): a *shown* pending remote-absent
    prompt becomes unanswered; an unshown one stays pending; a bypassed-stop
    prompt is never touched here — its resolution is the outcome taps, which
    a catch-up sequence delivers after the next Arrive, not before.

    Left unanswered until the next Arrive counts as "not at the stop" (R17,
    F4), so this is a deciding transaction for the call-now notice (R18):
    each closed prompt is stamped due here, once per child per run, and the
    returned entries carry ``call_now_due`` so the router can drain the
    outbox and raise the office incident from the return value alone.
    """
    rows = conn.execute(
        f"""
        update run_exception_events e
        set prompt_state = 'unanswered'
        from run_exceptions x
        where x.id = e.exception_id
          and e.run_id = %s and e.prompt_state = 'pending' and e.shown_at is not null
          and x.kind = %s
        {_CLOSED_RETURNING}
        """,
        (run_id, ABSENT_REMOTE),
    ).fetchall()
    closed = []
    for row in rows:
        entry = _closed(row)
        if entry["student_id"]:
            entry["call_now_due"] = stamp_call_now_due(
                conn, event_id=entry["event_id"], run_id=str(run_id),
                student_id=entry["student_id"],
            )
        closed.append(entry)
    return closed


def resolve_all_pending(conn, run_id: str) -> list[dict[str, Any]]:
    """End Run's and force-close's auto-resolution (R17, R34): every pending
    prompt of the run, shown or not, becomes unanswered. No call-now follows
    from here (the closure gate already owns the child); the return is the
    ledger of what closed."""
    rows = conn.execute(
        f"""
        update run_exception_events e
        set prompt_state = 'unanswered'
        from run_exceptions x
        where x.id = e.exception_id and e.run_id = %s and e.prompt_state = 'pending'
        {_CLOSED_RETURNING}
        """,
        (run_id,),
    ).fetchall()
    return [_closed(r) for r in rows]


def _closed(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": str(row["id"]),
        "exception_id": str(row["exception_id"]),
        "run_id": str(row["run_id"]),
        "kind": row["kind"],
        "student_id": str(row["student_id"]) if row["student_id"] else None,
        "student_name": row.get("student_name"),
        "stop_order": row.get("stop_order"),
        "stop_name": row.get("stop_name"),
        "prompt_state": "unanswered",
        "call_now_due": False,
    }


def _prompt_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": str(row["id"]),
        "exception_id": str(row["exception_id"]),
        "kind": row["kind"],
        "prompt_state": row["prompt_state"],
        "response": row["response"],
        "delivered_at": row["delivered_at"],
        "shown_at": row["shown_at"],
    }


def list_exceptions(
    conn, run: dict[str, Any], *, exception_id: str | None = None
) -> list[dict[str, Any]]:
    """A run's exceptions with their derived status and event ledger.

    ``run`` needs ``id`` and ``type``. Each row carries the stored columns
    (kind, reason, stop order, the denormalised fix, distance, seen_at_stop,
    created/reviewed), ``stop_name`` from the run's own stop snapshot,
    ``student_name`` for student-keyed kinds, a masked ``reviewed_by_display``,
    ``students`` — the children still without an outcome — and ``status``.

    For ``stop-bypassed`` the status is 'open' while the per-stop predicate
    names anyone and 'resolved' once it names nobody. For ``custody-away`` it
    is derived from the prompt events: 'open' while any tap is pending,
    'confirmed' if any tap was confirmed, 'retracted' only if every tap was
    retracted, else None (no prompt yet, or closed unanswered at End Run).
    ``seen_at_stop`` on a custody row is derived on read from the run's phone
    fixes inside retention — any fix inside the vicinity radius, cap first —
    falling back to the stored column when the trail carries no fix (purged,
    or a row written by hand); it is informational and never changes the
    status. For every other kind the status is None; ``students`` lists the
    children named on the row's ledger (the tapped children of a custody or
    unverified row). ``events`` is the ledger oldest first, with prompt_state,
    response, delivered_at, shown_at and the call-now stamps.
    """
    run_id = str(run["id"])
    only = " and e.id = %(exception_id)s" if exception_id else ""
    rows = conn.execute(
        f"""
        select e.*,
               s.name as student_name,
               (
                   select rs.name from run_stops rs
                   where rs.run_id = e.run_id and rs.stop_order = e.stop_order
                   order by rs.is_school_gate desc, rs.name asc
                   limit 1
               ) as stop_name,
               (
                   select rs.lat from run_stops rs
                   where rs.run_id = e.run_id and rs.stop_order = e.stop_order
                   order by rs.is_school_gate desc, rs.name asc
                   limit 1
               ) as stop_lat,
               (
                   select rs.lng from run_stops rs
                   where rs.run_id = e.run_id and rs.stop_order = e.stop_order
                   order by rs.is_school_gate desc, rs.name asc
                   limit 1
               ) as stop_lng,
               {masked_display_sql("u", "p")} as reviewed_by_display
        from run_exceptions e
        left join live_students s on s.id = e.student_id
        left join app_users u on u.id = e.reviewed_by
        left join provider_accounts p on p.user_id = e.reviewed_by and p.removed_at is null
        where e.run_id = %(run_id)s{only}
        order by e.created_at asc, e.stop_order asc nulls last
        """,
        {"run_id": run_id, "exception_id": exception_id},
    ).fetchall()
    events = conn.execute(
        """
        select ev.*, s.name as student_name
        from run_exception_events ev
        left join live_students s on s.id = ev.student_id
        where ev.run_id = %s
        order by ev.created_at asc, ev.id asc
        """,
        (run_id,),
    ).fetchall()
    ledger: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        ledger.setdefault(str(event["exception_id"]), []).append(dict(event))
    # Read once, only when a custody row needs them: the run's fixes and the
    # school's vicinity radius and accuracy cap (U11) that "seen at stop"
    # is derived against.
    fixes: list[StoredFix] | None = None
    thresholds = None

    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        stop_point = (item.pop("stop_lat", None), item.pop("stop_lng", None))
        own = ledger.get(str(item["id"]), [])
        if item["kind"] == STOP_BYPASSED and item["stop_order"] is not None:
            students = participation_dao.unaccounted_at_stop(
                conn, run_id, run["type"], item["stop_order"]
            )
            item["students"] = [{"id": str(s["id"]), "name": s["name"]} for s in students]
            item["status"] = "open" if students else "resolved"
        else:
            item["students"] = _children_on(own)
            if item["kind"] == CUSTODY_AWAY:
                item["status"] = _custody_status(own)
            elif item["kind"] in ABSENT_KINDS:
                item["status"] = _absent_status(own)
            else:
                item["status"] = None
        if item["kind"] == CUSTODY_AWAY:
            if fixes is None:
                fixes = position_dao.run_fixes(conn, run_id)
            if thresholds is None:
                thresholds = resolve_school_thresholds(conn, _school_id_of(conn, run))
            seen = _seen_at_stop(
                fixes, stop_point,
                vicinity_m=thresholds.vicinity_radius_m,
                accuracy_cap_m=thresholds.fix_accuracy_cap_m,
            )
            if seen is not None:
                item["seen_at_stop"] = seen
        # The event shape stays the ledger row's; the name served the
        # children list above.
        item["events"] = [{k: v for k, v in e.items() if k != "student_name"} for e in own]
        out.append(item)
    return out


def _children_on(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The distinct children named on a ledger, in first-tap order."""
    seen: dict[str, dict[str, Any]] = {}
    for event in events:
        student_id = event.get("student_id")
        if student_id and str(student_id) not in seen:
            seen[str(student_id)] = {"id": str(student_id), "name": event.get("student_name")}
    return list(seen.values())


def _custody_status(events: list[dict[str, Any]]) -> str | None:
    """Open while any prompt is pending; otherwise from each child's latest
    event on the ledger (a prompt answer or the undo's appended row): any
    child confirmed → confirmed; every child retracted → retracted; else
    None (no prompt yet, or closed unanswered at End Run)."""
    if any(e.get("prompt_state") == "pending" for e in events):
        return "open"
    latest: dict[str, dict[str, Any]] = {}
    for event in events:  # oldest first, so the last write wins
        if event.get("student_id"):
            latest[str(event["student_id"])] = event
    if not latest:
        return None
    if any(e.get("response") == CUSTODY_CONFIRMED for e in latest.values()):
        return CUSTODY_CONFIRMED
    if all(e.get("response") == CUSTODY_RETRACTED for e in latest.values()):
        return CUSTODY_RETRACTED
    return None


def _absent_status(events: list[dict[str, Any]]) -> str | None:
    """Open while a prompt is pending; otherwise from the child's latest
    prompt answer or appended row (R17): ``undo`` → retracted; ``told-me`` →
    attested; ``not-at-stop``, ``dismissed`` or a prompt closed unanswered →
    uncorroborated; else None (nothing decided yet)."""
    if any(e.get("prompt_state") == "pending" for e in events):
        return "open"
    decided = [
        e for e in events  # oldest first, so the last write wins
        if e.get("response") is not None or e.get("prompt_state") == "unanswered"
    ]
    if not decided:
        return None
    last = decided[-1]
    if last.get("response") == ABSENT_UNDO:
        return "retracted"
    if last.get("response") == ABSENT_TOLD_ME:
        return "attested"
    return "uncorroborated"


def _school_id_of(conn, run: dict[str, Any]) -> str:
    """The run's school: from the dict when the caller's query carried it,
    else one read — ``list_exceptions`` only promises ``id`` and ``type``."""
    if run.get("school_id"):
        return str(run["school_id"])
    row = conn.execute("select school_id from live_runs where id = %s", (run["id"],)).fetchone()
    return str(row["school_id"]) if row else ""


def _seen_at_stop(
    fixes: list[StoredFix], stop: tuple[Any, Any], *, vicinity_m: float, accuracy_cap_m: float
) -> bool | None:
    """Did any phone fix on the run place the bus at this stop, by the
    school's vicinity radius and accuracy cap (U11)? None when the trail
    carries no fix or the stop has no coordinates (the stored column then
    stands)."""
    if not fixes or stop[0] is None or stop[1] is None:
        return None
    return any(
        within_vicinity(fix, stop, vicinity_m=vicinity_m, accuracy_cap_m=accuracy_cap_m)
        for fix in fixes
    )


class ExceptionDao:
    """The staff surface: list a run's exceptions, mark one reviewed (R21)."""

    @staticmethod
    def _run_in_scope(conn, scope: SchoolScope, run_id: str) -> dict[str, Any]:
        run = conn.execute(
            "select id, type, school_id, total_stops from live_runs "
            "where id = %s and school_id = %s",
            (run_id, scope.school_id),
        ).fetchone()
        if not run:
            # Foreign and nonexistent are indistinguishable by design.
            raise NotFoundError("Run not found")
        return dict(run)

    def list_for_run(self, scope: SchoolScope, run_id: str) -> list[dict[str, Any]]:
        with get_connection(scope) as conn:
            run = self._run_in_scope(conn, scope, run_id)
            return list_exceptions(conn, run)

    def mark_reviewed(
        self, scope: SchoolScope, run_id: str, exception_id: str, actor: dict[str, Any]
    ) -> dict[str, Any]:
        """Stamp ``reviewed_at``/``reviewed_by`` once and write the audit row.

        Idempotent: a repeat answers the same row without moving the stamp or
        adding a second audit row. The audit detail carries the exception id
        only — never a child's name. Review is independent of open/resolved.
        """
        with get_connection(scope) as conn:
            run = self._run_in_scope(conn, scope, run_id)
            row = conn.execute(
                """
                select id, reviewed_at from run_exceptions
                where id = %s and run_id = %s and school_id = %s
                for update
                """,
                (exception_id, run_id, scope.school_id),
            ).fetchone()
            if not row:
                raise NotFoundError("Exception not found")
            if row["reviewed_at"] is None:
                conn.execute(
                    "update run_exceptions set reviewed_at = now(), reviewed_by = %s "
                    "where id = %s",
                    (scope.user_id, exception_id),
                )
                record_audit(
                    conn,
                    action="exception-reviewed",
                    actor=actor,
                    scope=scope,
                    resource_type="exception",
                    resource_id=str(exception_id),
                    detail={"exception_id": str(exception_id)},
                )
            rows = list_exceptions(conn, run, exception_id=str(exception_id))
        return rows[0]

    # --- the driver's prompt routes (U3) -----------------------------------

    @staticmethod
    def _prompt_for_driver(conn, scope: SchoolScope, event_id: str) -> dict[str, Any]:
        """The prompt event with its kind and its run's ownership, locked.

        Not found — including any event of another school, which RLS hides —
        is 404; an event whose run is not this driver's is 403; a ledger row
        that was never a prompt is 404 too.
        """
        row = conn.execute(
            """
            select e.*, x.kind, r.driver_id as run_driver_id, r.status as run_status
            from run_exception_events e
            join run_exceptions x on x.id = e.exception_id
            join live_runs r on r.id = e.run_id
            where e.id = %s and e.school_id = %s
            for update of e
            """,
            (event_id, scope.school_id),
        ).fetchone()
        if not row or row["prompt_state"] is None:
            raise NotFoundError("Prompt not found")
        if str(row["run_driver_id"]) != str(scope.user_id):
            raise ForbiddenError("Prompt belongs to another driver's run")
        return dict(row)

    def acknowledge_shown(self, scope: SchoolScope, event_id: str) -> dict[str, Any]:
        """The card mounted (U3): stamp ``shown_at`` once; the first stamp wins.

        Idempotent and state-independent — a prompt answered from another
        device a moment earlier still records that this screen showed it,
        which is what Arrive's shown-only rule reads.
        """
        with get_connection(scope) as conn:
            event = self._prompt_for_driver(conn, scope, event_id)
            if event["shown_at"] is None:
                event = dict(conn.execute(
                    "update run_exception_events set shown_at = now() where id = %s "
                    "returning *",
                    (event_id,),
                ).fetchone())
                event["kind"] = self._kind_of(conn, event["exception_id"])
        return _prompt_summary(event)

    def respond(self, scope: SchoolScope, event_id: str, answer: str) -> dict[str, Any]:
        """Record the driver's answer on the prompt (R23, R34).

        Pending → answered with the answer. The same answer again replays
        (200, unchanged). A different answer after answering is 409
        ``prompt-already-answered``; any answer after auto-resolution is 409
        ``prompt-resolved`` — both carry the recorded state, and the client
        treats both as "remove the card, refresh". The run must still be
        open (403 otherwise); an answer the kind does not take is 400.

        The remote-absent answers have effects in this same transaction
        (U10, R17, R18): ``told-me`` flips the exception's kind to
        ``absent-attested``; ``not-at-stop`` and ``dismissed`` stamp the
        call-now notice due on the event — once per child per run — and the
        return then carries two router-only keys, ``call_now_due`` (whether
        this answer armed it) and ``uncorroborated`` (the child and stop for
        the office incident). A replay carries neither, so nothing is
        dispatched twice.
        """
        with get_connection(scope) as conn:
            event = self._prompt_for_driver(conn, scope, event_id)
            state = event["prompt_state"]
            if state == "unanswered":
                raise PromptConflictError(
                    "This prompt was closed before it was answered",
                    code=PROMPT_RESOLVED, prompt_state=state, response=event["response"],
                )
            if state == "answered":
                if event["response"] == answer:
                    return _prompt_summary(event)
                raise PromptConflictError(
                    "This prompt was already answered",
                    code=PROMPT_ALREADY_ANSWERED, prompt_state=state,
                    response=event["response"],
                )
            if event["run_status"] == "completed":
                raise ForbiddenError("This run is no longer open")
            if answer not in PROMPT_ANSWERS.get(event["kind"], ()):
                raise BadRequestError("That answer is not available for this prompt")
            updated = dict(conn.execute(
                """
                update run_exception_events
                set prompt_state = 'answered', response = %s
                where id = %s
                returning *
                """,
                (answer, event_id),
            ).fetchone())
            updated["kind"] = event["kind"]
            extra: dict[str, Any] = {}
            if event["kind"] == ABSENT_REMOTE:
                extra = self._decide_absent_answer(conn, event, updated, answer)
        return {**_prompt_summary(updated), **extra}

    @staticmethod
    def _decide_absent_answer(
        conn, event: dict[str, Any], updated: dict[str, Any], answer: str
    ) -> dict[str, Any]:
        """The remote-absent answer's effects, inside the respond transaction."""
        exception = conn.execute(
            f"""
            select x.id, x.run_id, x.stop_order, x.student_id, s.name as student_name,
                   {_stop_name_sql("x", "x")} as stop_name
            from run_exceptions x
            left join live_students s on s.id = x.student_id
            where x.id = %s
            for update of x
            """,
            (event["exception_id"],),
        ).fetchone()
        student_id = str(updated.get("student_id") or exception["student_id"])
        if answer == ABSENT_TOLD_ME:
            conn.execute(
                "update run_exceptions set kind = %s where id = %s and kind = %s",
                (ABSENT_ATTESTED, exception["id"], ABSENT_REMOTE),
            )
            logger.info("absent attested (run=%s exception=%s)", exception["run_id"], exception["id"])
            return {}
        if answer not in UNCORROBORATED_ANSWERS:
            return {}
        due = stamp_call_now_due(
            conn, event_id=str(updated["id"]), run_id=str(exception["run_id"]),
            student_id=student_id,
        )
        logger.info(
            "absent uncorroborated by %s (run=%s exception=%s call_now_due=%s)",
            answer, exception["run_id"], exception["id"], due,
        )
        return {
            "call_now_due": due,
            "uncorroborated": {
                "run_id": str(exception["run_id"]),
                "student_id": student_id,
                "student_name": exception["student_name"],
                "stop_order": exception["stop_order"],
                "stop_name": exception["stop_name"],
            },
        }

    def send_due_call_now(self, scope: SchoolScope, notify) -> int:
        """Drain the school's call-now outbox (R18): every event stamped due
        and not yet sent is handed to ``notify(student, run)`` — the push
        service's ``notify_absent_call_now`` — and stamped sent.

        Runs post-commit after every driver action, every prompt answer and
        every context poll, so a fan-out lost between the deciding commit and
        the send is re-attempted within seconds. Once is guaranteed three
        ways: the rows are locked ``skip locked`` so two drains never hold the
        same one; the send precedes the stamp, so a crash between them leaves
        the row due and the retry's feed insert meets the (parent, run,
        student, type) dedup index and sends nothing; each row is its own
        savepoint, so one failure neither blocks the others nor re-sends
        them. Returns how many rows were stamped sent.
        """
        sent = 0
        with get_connection(scope) as conn:
            for row in due_call_now(conn, str(scope.school_id)):
                try:
                    with conn.transaction():
                        if row["student_id"]:
                            delivered = notify(
                                {"id": str(row["student_id"]), "name": row["student_name"]},
                                {
                                    "id": str(row["run_id"]), "bus_id": row["bus_id"],
                                    "type": row["run_type"], "school_id": row["school_id"],
                                },
                            )
                            if delivered is False:
                                # The fan-out failed: leave the row due for
                                # the next drain rather than pretend it went.
                                logger.warning(
                                    "call-now fan-out failed; the row stays due (run=%s event=%s)",
                                    row["run_id"], row["event_id"],
                                )
                                continue
                        if mark_call_now_sent(conn, str(row["event_id"])):
                            sent += 1
                except Exception:
                    logger.exception(
                        "call-now send not stamped; it stays due (run=%s event=%s)",
                        row["run_id"], row["event_id"],
                    )
        return sent

    @staticmethod
    def _kind_of(conn, exception_id: str) -> str:
        return conn.execute(
            "select kind from run_exceptions where id = %s", (exception_id,)
        ).fetchone()["kind"]
