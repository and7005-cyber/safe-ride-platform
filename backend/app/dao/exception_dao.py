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
Drop-off or Absent for a child at the stop of a pending bypassed-stop prompt
of this run is recorded on that exception's ledger as a resolution tap, by
membership, whether it came through the card or from the board page; the
card's event id is a hint, never the key.
"""

import logging
from typing import Any

from app.core.db import get_connection
from app.core.errors import (
    BadRequestError,
    ForbiddenError,
    NotFoundError,
    PromptConflictError,
)
from app.core.scope import SchoolScope
from app.dao import participation_dao
from app.dao.audit_dao import masked_display_sql, record_audit

logger = logging.getLogger("saferide.exceptions")

STOP_BYPASSED = "stop-bypassed"
ABSENT_REMOTE = "absent-remote"
CUSTODY_AWAY = "custody-away"

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
# event id), so its only spoken answer is the explicit dismiss. U9 adds the
# custody confirm, U10 the attestation answers; undo goes through the reverse
# path in both and is recorded there, never here.
PROMPT_ANSWERS: dict[str, tuple[str, ...]] = {STOP_BYPASSED: ("dismissed",)}

PROMPT_ALREADY_ANSWERED = "prompt-already-answered"
PROMPT_RESOLVED = "prompt-resolved"


def _prompt_payload(
    event: dict[str, Any], kind: str, *, stop_order: int | None, stop_name: str | None,
    students: list[dict[str, Any]], student_id: str | None = None,
) -> dict[str, Any]:
    """The one prompt shape both deliveries use — the action response and the
    context poll — so the client queue can key and merge them by event id."""
    return {
        "event_id": str(event["id"]),
        "exception_id": str(event["exception_id"]),
        "kind": kind,
        "stop_order": stop_order,
        "stop_name": stop_name,
        "student_id": student_id,
        "students": students,
        "answers": list(PROMPT_ANSWERS.get(kind, ())),
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
               e.delivered_at, e.shown_at,
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
            )
        )
    return out


def record_resolution(
    conn, run: dict[str, Any], student_id: str, *, event_id: str | None = None
) -> dict[str, Any] | None:
    """Attach an outcome tap to the bypassed-stop prompt for the child's stop
    (R15, F3; the plan's "keyed by the tap" decision).

    Called by the Board, Drop-off and Absent DAOs after the outcome is written,
    inside their savepoint, for every tap. Membership is the key: the child's
    own stop order on this run (``run_stops``) selects the pending
    ``stop-bypassed`` prompt whose exception sits at that stop — whether the
    tap came through the card or from the board page, and whether or not the
    card is still on screen. ``event_id`` is the client's hint: accepted,
    never required, never preferred over membership (a hint naming another
    prompt is logged and ignored), and never a reason to refuse the tap (R23).

    Records one ``resolution`` ledger event naming the child, and answers the
    prompt itself as ``resolution`` once the stop names nobody, so a two-child
    card stays up, shorter, until the second child is recorded.

    Returns ``{"exception_id", "event_id", "answered", "hint_ignored"}`` when
    the tap was attached, else None (no pending prompt at the child's stop).
    """
    run_id = str(run["id"])
    at_stop = conn.execute(
        "select stop_order from run_stops where run_id = %s and student_id = %s "
        "order by stop_order asc limit 1",
        (run_id, student_id),
    ).fetchone()
    if not at_stop:
        return None
    event = conn.execute(
        """
        select e.id, e.exception_id, x.stop_order, x.school_id
        from run_exception_events e
        join run_exceptions x on x.id = e.exception_id
        where e.run_id = %s and e.prompt_state = 'pending'
          and x.kind = %s and x.stop_order = %s
        order by e.created_at asc, e.id asc
        limit 1
        for update of e
        """,
        (run_id, STOP_BYPASSED, at_stop["stop_order"]),
    ).fetchone()
    if not event:
        return None
    hint_ignored = bool(event_id) and str(event["id"]) != str(event_id)
    if hint_ignored:
        logger.info(
            "resolution tap: card hint %s ignored, membership selects prompt %s (run=%s)",
            event_id, event["id"], run_id,
        )
    conn.execute(
        """
        insert into run_exception_events
            (exception_id, school_id, run_id, student_id, response)
        values (%s, %s, %s, %s, 'resolution')
        """,
        (event["exception_id"], str(event["school_id"]), run_id, student_id),
    )
    remaining = participation_dao.unaccounted_at_stop(
        conn, run_id, run["type"], event["stop_order"]
    )
    answered = not remaining
    if answered:
        conn.execute(
            """
            update run_exception_events
            set prompt_state = 'answered', response = 'resolution'
            where id = %s
            """,
            (event["id"],),
        )
    return {
        "exception_id": str(event["exception_id"]),
        "event_id": str(event["id"]),
        "answered": answered,
        "hint_ignored": hint_ignored,
    }


def resolve_pending_on_arrive(conn, run_id: str) -> list[dict[str, Any]]:
    """Arrive's auto-resolution (R17, R34): a *shown* pending remote-absent
    prompt becomes unanswered; an unshown one stays pending; a bypassed-stop
    prompt is never touched here — its resolution is the outcome taps, which
    a catch-up sequence delivers after the next Arrive, not before. Nothing
    produces the kind until U10; the rule is keyed by kind so it is already
    in place when it does. Returns the closed prompts for the router (U10's
    call-now decision reads them)."""
    rows = conn.execute(
        """
        update run_exception_events e
        set prompt_state = 'unanswered'
        from run_exceptions x
        where x.id = e.exception_id
          and e.run_id = %s and e.prompt_state = 'pending' and e.shown_at is not null
          and x.kind = %s
        returning e.id, e.exception_id, x.kind, coalesce(e.student_id, x.student_id) as student_id
        """,
        (run_id, ABSENT_REMOTE),
    ).fetchall()
    return [_closed(r) for r in rows]


def resolve_all_pending(conn, run_id: str) -> list[dict[str, Any]]:
    """End Run's and force-close's auto-resolution (R17, R34): every pending
    prompt of the run, shown or not, becomes unanswered. No call-now follows
    from here (the closure gate already owns the child); the return is the
    ledger of what closed."""
    rows = conn.execute(
        """
        update run_exception_events e
        set prompt_state = 'unanswered'
        from run_exceptions x
        where x.id = e.exception_id and e.run_id = %s and e.prompt_state = 'pending'
        returning e.id, e.exception_id, x.kind, coalesce(e.student_id, x.student_id) as student_id
        """,
        (run_id,),
    ).fetchall()
    return [_closed(r) for r in rows]


def _closed(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": str(row["id"]),
        "exception_id": str(row["exception_id"]),
        "kind": row["kind"],
        "student_id": str(row["student_id"]) if row["student_id"] else None,
        "prompt_state": "unanswered",
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
    names anyone and 'resolved' once it names nobody. The kinds later units
    write (custody-away, absent-*, unverified, implausible-movement) define
    their own derivations there; until then their status is None and
    ``students`` is empty. ``events`` is the ledger oldest first, with
    prompt_state, response, delivered_at, shown_at and the call-now stamps.
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
        "select * from run_exception_events where run_id = %s order by created_at asc, id asc",
        (run_id,),
    ).fetchall()
    ledger: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        ledger.setdefault(str(event["exception_id"]), []).append(dict(event))

    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        if item["kind"] == STOP_BYPASSED and item["stop_order"] is not None:
            students = participation_dao.unaccounted_at_stop(
                conn, run_id, run["type"], item["stop_order"]
            )
            item["students"] = [{"id": str(s["id"]), "name": s["name"]} for s in students]
            item["status"] = "open" if students else "resolved"
        else:
            item["students"] = []
            item["status"] = None
        item["events"] = ledger.get(str(item["id"]), [])
        out.append(item)
    return out


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
        return _prompt_summary(updated)

    @staticmethod
    def _kind_of(conn, exception_id: str) -> str:
        return conn.execute(
            "select kind from run_exceptions where id = %s", (exception_id,)
        ).fetchone()["kind"]
