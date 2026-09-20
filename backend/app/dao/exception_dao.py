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
"""

import logging
from typing import Any

from app.core.db import get_connection
from app.core.errors import NotFoundError
from app.core.scope import SchoolScope
from app.dao import participation_dao
from app.dao.audit_dao import masked_display_sql, record_audit

logger = logging.getLogger("saferide.exceptions")

STOP_BYPASSED = "stop-bypassed"


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
            returning id
            """,
            (exception_id, str(run["school_id"]), run_id),
        ).fetchone()
        # The driver-facing prompt (U3 delivers it through the context poll and
        # answers it by event id); the copy inputs travel with it.
        prompt = {
            "event_id": str(event["id"]),
            "exception_id": str(exception_id),
            "kind": STOP_BYPASSED,
            "stop_order": passed_order,
            "stop_name": stop_name,
            "students": listed,
        }
    return {
        "exception_id": str(exception_id),
        "stop_order": passed_order,
        "stop_name": stop_name,
        "students": listed,
        "raised": raised,
        "prompt": prompt,
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
