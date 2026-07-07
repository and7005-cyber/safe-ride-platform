"""Per-date student absences (#7), scoped and provenance-tracked (U4).

Marking a student absent for a date suppresses their stop on that day's run,
so the record is self-clearing the next day. TODAY-dated marks/clears also
carry operational side-effects (R25b): a mark sets the live status to
'absent' and appends the run_absences snapshot of any active run whose
roster (run_stops) carries the student; a clear resets an 'absent' status to
'at-school' — but is rejected while the student's bus has an active run of a
covered type (the run already excluded the stop; un-absenting mid-run is
incoherent). Past/future-dated marks and clears never touch the live status.

Each row carries a ``scope`` ('day' = whole day; 'morning'/'afternoon' =
that run only) and a ``source`` ('parent'/'driver'/'admin'). Partial scopes
gate rosters for their run type but never write the displayed status — only
'day' ever writes status='absent', and any exit from 'day' (downgrade or
delete) resets an 'absent' status to 'at-school'. Provenance is a one-way
ratchet: staff writers escalate any row to a whole-day staff mark; parent
writers (set_scope / withdraw_scope) only ever touch source='parent' rows,
enforced inside the single upsert/delete statement so a concurrent staff
mark always wins.
"""
from typing import Any

from app.core.db import get_connection
from app.dao.status_sql import scope_covers


def absent_student_ids(conn, date: str | None = None, run_type: str | None = None) -> set[str]:
    """Set of student ids marked absent on ``date`` (defaults to today, Nairobi).

    ``run_type=None`` counts every absence row (whole-day view — the
    pre-scope behavior). Passing a run type ('morning'/'afternoon') narrows
    to absences COVERING that run: scope 'day' always covers, a partial
    scope only covers its own run type.
    """
    rows = conn.execute(
        f"""
        select student_id from live_student_absences
        where absence_date = coalesce(%s::date, (now() at time zone 'Africa/Nairobi')::date)
          and (%s::text is null or {scope_covers("scope", "%s")})
        """,
        (date, run_type, run_type),
    ).fetchall()
    return {str(r["student_id"]) for r in rows}


def _validate_scope(scope: str) -> None:
    """Reject unknown scopes before the SQL runs: on the upsert's conflict
    path the merge expression would otherwise fold ANY unequal value into
    'day' (the stored result passes the CHECK, so the constraint never
    fires) — an invalid input must not silently become a whole-day absence."""
    if scope not in ("day", "morning", "afternoon"):
        from app.core.errors import BadRequestError

        raise BadRequestError("Scope must be one of: day, morning, afternoon")


class AbsenceDao:
    def list_absences(self, date: str | None = None) -> list[dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                select a.id, a.student_id, a.absence_date, a.reason, a.created_at,
                       a.scope, a.source,
                       s.name as student_name, s.grade
                from live_student_absences a
                join live_students s on s.id = a.student_id
                where %s::date is null or a.absence_date = %s::date
                order by a.absence_date desc, s.name asc
                """,
                (date, date),
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_absent(
        self, student_id: str, date: str | None, reason: str | None, marked_by: str | None
    ) -> dict[str, Any]:
        """Upsert an absence (date=None → today, Nairobi). A TODAY-dated mark
        also sets the live status to 'absent' and appends the run_absences
        snapshot of any active run whose run_stops roster carries the student
        (run-scoped — never the derived bus_id roster), all in one
        transaction. Other dates have no status side-effects.

        Staff transition rule (U4): an admin mark is always a whole-day
        absence, so the conflict branch escalates any existing row — a
        parent's partial cancellation included — to scope='day' and stamps
        source='admin' (the provenance ratchet's one-way direction)."""
        with get_connection() as conn:
            row = conn.execute(
                """
                insert into live_student_absences
                    (student_id, absence_date, reason, marked_by, scope, source)
                values (
                    %s,
                    coalesce(%s::date, (now() at time zone 'Africa/Nairobi')::date),
                    %s, %s, 'day', 'admin'
                )
                on conflict (student_id, absence_date)
                do update set reason = excluded.reason, marked_by = excluded.marked_by,
                              scope = 'day', source = 'admin'
                returning *,
                    (absence_date = (now() at time zone 'Africa/Nairobi')::date) as is_today
                """,
                (student_id, date, reason, marked_by),
            ).fetchone()
            if row["is_today"]:
                conn.execute(
                    "update live_students set status = 'absent' where id = %s",
                    (student_id,),
                )
                conn.execute(
                    """
                    insert into run_absences (run_id, student_id, student_name, reason)
                    select r.id, s.id, s.name, %s
                    from live_runs r
                    join live_students s on s.id = %s
                    where r.status <> 'completed'
                      and r.date = (now() at time zone 'Africa/Nairobi')::date
                      and exists (
                          select 1 from run_stops rs
                          where rs.run_id = r.id and rs.student_id = s.id
                      )
                    on conflict (run_id, student_id) do nothing
                    """,
                    (reason, student_id),
                )
        result = dict(row)
        result.pop("is_today", None)
        return result

    def set_scope(
        self, student_id: str, scope: str, actor_user_id: str, reason: str | None = None
    ) -> dict[str, Any] | None:
        """Parent transition (U4): upsert a TODAY absence at ``scope`` as ONE
        atomic statement. Merge rule in the DO UPDATE expression: same scope
        is idempotent; any two different scopes union to 'day' (morning +
        afternoon → day; a partial under an existing 'day' stays 'day').
        ``reason`` (Cancel-a-Ride passes "Cancelled by parent", U5) lands on
        the row and on any run_absences snapshot taken here, so the admin
        absence list and run reports say why the child is off the roster.

        The provenance ratchet lives in the statement's WHERE clause — the
        conflict branch only fires on source='parent' rows, so a staff mark
        committed at any point (even between this statement's snapshot and
        its conflict resolution — ON CONFLICT re-checks the WHERE on the
        locked current row) makes the parent lose. Returns None on that
        refusal (the caller maps it to a friendly 409). Otherwise returns the
        stored row plus ``changed``: whether the stored scope actually moved
        (the ``prior`` CTE shares the statement's snapshot, so no separate
        read-then-write window exists).

        Only a resulting 'day' scope writes status='absent' (partial scopes
        gate rosters, never the displayed status), and only on an actual
        transition — an idempotent re-cancel has zero side effects. A real
        transition also appends the run_absences snapshot of any active run
        of a COVERED type whose run_stops roster carries the student
        (mirroring mark_absent, U5): the run started while the child was
        still expected, and the completed report must list who never
        boarded. Boarded children never reach this point — the API layer
        rejects an on-bus child on an active covered run (R16).
        """
        _validate_scope(scope)
        with get_connection() as conn:
            row = conn.execute(
                """
                with prior as (
                    select scope from live_student_absences
                    where student_id = %(student_id)s
                      and absence_date = (now() at time zone 'Africa/Nairobi')::date
                )
                insert into live_student_absences as a
                    (student_id, absence_date, reason, marked_by, scope, source)
                values (
                    %(student_id)s, (now() at time zone 'Africa/Nairobi')::date,
                    %(reason)s, %(actor)s, %(scope)s, 'parent'
                )
                on conflict (student_id, absence_date) do update
                    set scope = case
                            when a.scope = excluded.scope then a.scope
                            else 'day'
                        end,
                        reason = excluded.reason,
                        marked_by = excluded.marked_by,
                        source = 'parent'
                    where a.source = 'parent'
                returning a.*, (select scope from prior) as prior_scope
                """,
                {
                    "student_id": student_id,
                    "scope": scope,
                    "actor": actor_user_id,
                    "reason": reason,
                },
            ).fetchone()
            if row is None:
                return None  # staff-sourced row: the ratchet refused the write
            result = dict(row)
            prior_scope = result.pop("prior_scope")
            result["changed"] = prior_scope is None or prior_scope != result["scope"]
            if result["changed"]:
                if result["scope"] == "day":
                    conn.execute(
                        "update live_students set status = 'absent' where id = %s",
                        (student_id,),
                    )
                conn.execute(
                    f"""
                    insert into run_absences (run_id, student_id, student_name, reason)
                    select r.id, s.id, s.name, %s
                    from live_runs r
                    join live_students s on s.id = %s
                    where r.status <> 'completed'
                      and r.date = (now() at time zone 'Africa/Nairobi')::date
                      and {scope_covers("%s", "r.type")}
                      and exists (
                          select 1 from run_stops rs
                          where rs.run_id = r.id and rs.student_id = s.id
                      )
                    on conflict (run_id, student_id) do nothing
                    """,
                    (reason, student_id, result["scope"], result["scope"]),
                )
        return result

    def withdraw_scope(
        self, student_id: str, scope: str, actor_user_id: str
    ) -> dict[str, Any] | None:
        """Parent withdrawal (U4), the same single-statement atomicity as
        set_scope: withdrawing one half of a merged 'day' downgrades the row
        to the other half; withdrawing the row's own scope deletes it; a
        scope the row does not carry is a no-op. Only source='parent' rows
        qualify (both sub-statements re-check on the locked row, so a
        concurrent staff escalation wins the same way as in set_scope).

        Both sub-statements also re-check that no ACTIVE (non-completed) run
        of a type the withdrawn scope covers exists today for the student
        (run_stops membership OR route membership — clear_absence's shape):
        the API's run-row pre-read can race a driver starting the run, and a
        withdrawal landing after that start would contradict the roster the
        run already snapshotted without the child. The check is per-CTE so
        withdrawing the not-yet-started half of a merged 'day' row stays
        allowed while the OTHER half's run is active.

        Returns None when nothing was withdrawn (no row today, staff-sourced
        row, non-matching scope, or a covered run just started — the caller
        distinguishes for messaging), else {'deleted': bool, 'scope':
        remaining scope or None}. Any exit from 'day' — downgrade or delete —
        resets an 'absent' status to 'at-school' (clear_absence's reset), in
        the same transaction.
        """
        _validate_scope(scope)
        run_guard = """not exists (
                          select 1 from live_runs r
                          where r.status <> 'completed'
                            and r.date = (now() at time zone 'Africa/Nairobi')::date
                            and {type_predicate}
                            and (
                              exists (select 1 from run_stops rs
                                      where rs.run_id = r.id and rs.student_id = a.student_id)
                              or exists (select 1 from live_student_routes sr
                                         where sr.route_id = r.route_id
                                           and sr.student_id = a.student_id)
                            )
                      )"""
        with get_connection() as conn:
            row = conn.execute(
                f"""
                with downgraded as (
                    update live_student_absences a
                    set scope = case
                            when %(scope)s = 'morning' then 'afternoon'
                            else 'morning'
                        end,
                        marked_by = %(actor)s
                    where a.student_id = %(student_id)s
                      and a.absence_date = (now() at time zone 'Africa/Nairobi')::date
                      and a.source = 'parent'
                      and a.scope = 'day'
                      and %(scope)s in ('morning', 'afternoon')
                      and {run_guard.format(type_predicate="r.type = %(scope)s")}
                    returning a.scope
                ),
                deleted as (
                    delete from live_student_absences a
                    where a.student_id = %(student_id)s
                      and a.absence_date = (now() at time zone 'Africa/Nairobi')::date
                      and a.source = 'parent'
                      and a.scope = %(scope)s
                      and not exists (select 1 from downgraded)
                      and {run_guard.format(type_predicate=scope_covers("%(scope)s", "r.type"))}
                    returning a.scope
                )
                select (select scope from downgraded) as downgraded_to,
                       (select scope from deleted) as deleted_scope
                """,
                {"student_id": student_id, "scope": scope, "actor": actor_user_id},
            ).fetchone()
            downgraded_to, deleted_scope = row["downgraded_to"], row["deleted_scope"]
            if downgraded_to is None and deleted_scope is None:
                return None  # no matching parent-sourced row to withdraw
            # A downgrade only ever fires FROM 'day'; a delete exits 'day'
            # when the deleted row's scope says so.
            if downgraded_to is not None or deleted_scope == "day":
                conn.execute(
                    "update live_students set status = 'at-school' "
                    "where id = %s and status = 'absent'",
                    (student_id,),
                )
        return {"deleted": deleted_scope is not None, "scope": downgraded_to}

    def clear_absence(self, absence_id: str) -> None:
        """Delete an absence. Clearing a TODAY-dated absence is rejected while
        an active run of a COVERED type involves the student ('End the run
        first' — the run either contains their stop or excluded it at
        snapshot time; a partial absence never blocks on the other run type);
        otherwise it resets the live status to 'at-school', but only when the
        current status is 'absent' and the row was whole-day — partial scopes
        never wrote the status, so deleting one never touches it. Clearing
        past/future-dated absences never touches the status."""
        from app.core.errors import ConflictError

        with get_connection() as conn:
            row = conn.execute(
                """
                select a.student_id, a.scope,
                       (a.absence_date = (now() at time zone 'Africa/Nairobi')::date) as is_today,
                       s.status
                from live_student_absences a
                join live_students s on s.id = a.student_id
                where a.id = %s
                """,
                (absence_id,),
            ).fetchone()
            if row and row["is_today"]:
                # Run-scoped guard (never the derived bus roster, which
                # diverges for cross-bus afternoon riders): the student is
                # mid-run when a non-completed run today of a type this
                # absence COVERS contains them in run_stops, or belongs to a
                # route they are on — their stop was excluded at snapshot
                # time by this absence.
                active = conn.execute(
                    f"""
                    select 1 from live_runs r
                    where r.status <> 'completed'
                      and r.date = (now() at time zone 'Africa/Nairobi')::date
                      and {scope_covers("%s", "r.type")}
                      and (
                        exists (select 1 from run_stops rs
                                where rs.run_id = r.id and rs.student_id = %s)
                        or exists (select 1 from live_student_routes sr
                                   where sr.route_id = r.route_id and sr.student_id = %s)
                      )
                    limit 1
                    """,
                    (row["scope"], row["scope"], row["student_id"], row["student_id"]),
                ).fetchone()
                if active:
                    raise ConflictError("End the run first")
                if row["status"] == "absent" and row["scope"] == "day":
                    conn.execute(
                        "update live_students set status = 'at-school' where id = %s",
                        (row["student_id"],),
                    )
            conn.execute("delete from live_student_absences where id = %s", (absence_id,))

    def clear_for_student_date(self, student_id: str, date: str) -> None:
        with get_connection() as conn:
            conn.execute(
                "delete from live_student_absences where student_id = %s and absence_date = %s::date",
                (student_id, date),
            )
