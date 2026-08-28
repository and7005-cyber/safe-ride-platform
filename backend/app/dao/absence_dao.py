"""Per-date student absences (#7), scoped and provenance-tracked (U4).

Marking a student absent for a date suppresses their stop on that day's run,
so the record is self-clearing the next day. TODAY-dated marks/clears also
carry operational side-effects (R25b): a mark sets the live status to
'absent' and appends the run_absences snapshot of any active run whose
roster (run_stops) carries the student; a clear resets an 'absent' status to
'at-school' — but is rejected while the student's bus has an active run of a
covered type (the run already excluded the stop; un-absenting mid-run is
incoherent). Past/future-dated marks and clears never touch the live status.

Each row carries three separate facts, and keeping them apart is what makes the
record true about the period it claims (U8):

- ``scope`` ('day' | 'morning' | 'afternoon') is **coverage** — which runs the
  absence gates. It only ever widens; two different scopes union to 'day'.
- ``source`` ('admin' | 'parent' | 'driver') is **precedence**, in that order. A
  lower-precedence mark never re-attributes a higher one, though it still
  widens coverage. Enforced inside the single upsert/delete statement, so a
  concurrent office mark always wins.
- ``marked_period`` is the **witness** — which period someone individually
  marked. It survives the collapse to 'day' that widening causes, and it is what
  the derivation keys on.

A partial parent cancellation gates its run's roster but never writes the
displayed status: it is a statement of intent. A recorded marking does write it,
partial or not, because it is a driver saying they were at the stop and the
child was not. So 'day' coverage and a marking are the two things that set
status='absent', and either one exiting resets it — which is why clear and
withdraw check the marking rather than using whole-day scope as its proxy.
"""
from typing import Any

from app.core.db import UNSET, get_connection
from app.core.errors import NotFoundError
from app.core.scope import SchoolScope
from app.dao.audit_dao import record_audit
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
    def list_absences(self, scope: SchoolScope, date: str | None = None) -> list[dict[str, Any]]:
        # Scoped through the STUDENT's school (U6): the absence row's own
        # school_id stays unfiltered because parent/driver writers stamp it
        # only from U7 — filtering on it would hide their fresh rows.
        with get_connection(scope) as conn:
            rows = conn.execute(
                """
                select a.id, a.student_id, a.absence_date, a.reason, a.created_at,
                       a.scope, a.source, a.marked_period, a.marked_by,
                       s.name as student_name, s.grade,
                       case when a.marked_by is null then null
                            when p.user_id is not null then 'SafeRide'
                            else coalesce(u.full_name, u.email) end
                           as marked_by_display
                from live_student_absences a
                join live_students s on s.id = a.student_id and s.school_id = %s
                left join app_users u on u.id = a.marked_by
                left join provider_accounts p
                    on p.user_id = a.marked_by and p.removed_at is null
                where %s::date is null or a.absence_date = %s::date
                order by a.absence_date desc, s.name asc
                """,
                (scope.school_id, date, date),
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_absent(
        self, scope: SchoolScope, student_id: str, date: str | None,
        reason: str | None, *, actor: dict,
    ) -> dict[str, Any]:
        """Upsert an absence (date=None → today, Nairobi). A TODAY-dated mark
        also sets the live status to 'absent' and appends the run_absences
        snapshot of any active run whose run_stops roster carries the student
        (run-scoped — never the derived bus_id roster), all in one
        transaction. Other dates have no status side-effects.

        Staff transition rule (U4): an admin mark is always a whole-day
        absence, so the conflict branch escalates any existing row — a
        parent's partial cancellation included — to scope='day' and stamps
        source='admin' (the provenance ratchet's one-way direction).

        U6: another school's student → 404 up front; the row is stamped with
        the school (healing any NULL left by pre-U7 parent/driver writers,
        which is safe because the student's school was just verified)."""
        with get_connection(scope) as conn:
            student = conn.execute(
                "select 1 from live_students where id = %s and school_id = %s",
                (student_id, scope.school_id),
            ).fetchone()
            if not student:
                raise NotFoundError("Student not found")
            row = conn.execute(
                """
                insert into live_student_absences
                    (student_id, absence_date, reason, marked_by, scope, source, school_id)
                values (
                    %s,
                    coalesce(%s::date, (now() at time zone 'Africa/Nairobi')::date),
                    %s, %s, 'day', 'admin', %s
                )
                on conflict (student_id, absence_date)
                do update set reason = excluded.reason, marked_by = excluded.marked_by,
                              scope = 'day', source = 'admin',
                              school_id = excluded.school_id
                returning *,
                    (absence_date = (now() at time zone 'Africa/Nairobi')::date) as is_today
                """,
                (student_id, date, reason, actor["id"], scope.school_id),
            ).fetchone()
            if row["is_today"]:
                conn.execute(
                    "update live_students set status = 'absent' "
                    "where id = %s and school_id = %s",
                    (student_id, scope.school_id),
                )
                conn.execute(
                    """
                    insert into run_absences (run_id, student_id, student_name, reason, period,
                                              school_id)
                    select r.id, s.id, s.name, %s, 'day', r.school_id
                    from live_runs r
                    join live_students s on s.id = %s and s.school_id = %s
                    where r.status <> 'completed'
                      and r.date = (now() at time zone 'Africa/Nairobi')::date
                      and exists (
                          select 1 from run_stops rs
                          where rs.run_id = r.id and rs.student_id = s.id
                      )
                    on conflict (run_id, student_id) do nothing
                    """,
                    (reason, student_id, scope.school_id),
                )
            record_audit(
                conn, action="absence-marked", actor=actor, scope=scope,
                resource_type="absence", resource_id=row["id"],
                detail={"student_id": str(student_id), "date": str(row["absence_date"])},
            )
        result = dict(row)
        result.pop("is_today", None)
        return result

    def set_scope(
        self, student_id: str, scope: str, actor_user_id: str, reason: str | None = None,
        *, parent_scope: object = UNSET,
    ) -> dict[str, Any] | None:
        """Parent transition (U4): upsert a TODAY absence at ``scope`` as ONE
        atomic statement. Merge rule in the DO UPDATE expression: same scope
        is idempotent; any two different scopes union to 'day' (morning +
        afternoon → day; a partial under an existing 'day' stays 'day').
        ``reason`` (Cancel-a-Ride passes "Cancelled by parent", U5) lands on
        the row and on any run_absences snapshot taken here, so the admin
        absence list and run reports say why the child is off the roster.

        Precedence lives in the statement's WHERE clause — office, then parent,
        then driver (U8/R20). The conflict branch fires on parent- and
        driver-sourced rows, so an office mark committed at any point (even
        between this statement's snapshot and its conflict resolution — ON
        CONFLICT re-checks the WHERE on the locked current row) makes the parent
        lose. Returns None on that refusal (the caller maps it to a friendly
        409). Otherwise returns the stored row plus ``changed``: whether the
        stored scope actually moved (the ``prior`` CTE shares the statement's
        snapshot, so no separate read-then-write window exists).

        A driver row no longer blocks the parent: the ordering used to be
        staff-over-parent, which meant a driver's mark at the stop outranked the
        office's own record. Taking over the row keeps ``marked_period``
        untouched, so the driver's observation survives the change of source and
        the child still reads absent.

        Only a resulting 'day' scope writes status='absent' (partial scopes
        gate rosters, never the displayed status), and only on an actual
        transition — an idempotent re-cancel has zero side effects. A real
        transition also appends the run_absences snapshot of any active run
        of a COVERED type whose run_stops roster carries the student
        (mirroring mark_absent, U5): the run started while the child was
        still expected, and the completed report must list who never
        boarded. Boarded children never reach this point — the API layer
        rejects an on-bus child on an active covered run (R16).

        ``parent_scope`` (U11): the caller's ParentScope, threaded explicitly
        into the connection seam like every converted DAO; UNSET keeps the
        context-var fallback for legacy call sites.
        """
        _validate_scope(scope)
        with get_connection(parent_scope) as conn:
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
                    where a.source in ('parent', 'driver')
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
                    insert into run_absences (run_id, student_id, student_name, reason, period,
                                              school_id)
                    select r.id, s.id, s.name, %s, %s, r.school_id
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
                    (reason, result["scope"], student_id, result["scope"], result["scope"]),
                )
        return result

    def withdraw_scope(
        self, student_id: str, scope: str, actor_user_id: str,
        *, parent_scope: object = UNSET,
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

        Withdrawal also cannot cut below a recorded period marking (U8/R18-R19).
        A driver's mark is an observation — they were at the stop and the child
        was not — and a parent cancelling their side of the day does not make
        that untrue. So a downgrade must leave the marked period still covered,
        and a delete is refused outright while a marking exists. Coverage only
        ever widens; this is the same rule seen from the other end.

        Returns None when nothing was withdrawn (no row today, an office-sourced
        row, non-matching scope, a covered run just started, or a marking that
        withdrawal would cut below — the caller distinguishes for messaging),
        else {'deleted': bool, 'scope': remaining scope or None}. An exit from
        'day' resets an 'absent' status to 'at-school' (clear_absence's reset),
        unless a marking survives the withdrawal and is still writing that
        status — the parent withdrew their half, not the driver's.
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
        with get_connection(parent_scope) as conn:
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
                      -- The half left behind must still cover any marked period.
                      and (
                        a.marked_period is null
                        or a.marked_period = case
                               when %(scope)s = 'morning' then 'afternoon'
                               else 'morning'
                           end
                      )
                      and {run_guard.format(type_predicate="r.type = %(scope)s")}
                    returning a.scope, a.marked_period
                ),
                deleted as (
                    delete from live_student_absences a
                    where a.student_id = %(student_id)s
                      and a.absence_date = (now() at time zone 'Africa/Nairobi')::date
                      and a.source = 'parent'
                      and a.scope = %(scope)s
                      and a.marked_period is null
                      and not exists (select 1 from downgraded)
                      and {run_guard.format(type_predicate=scope_covers("%(scope)s", "r.type"))}
                    returning a.scope
                )
                select (select scope from downgraded) as downgraded_to,
                       (select marked_period from downgraded) as surviving_marking,
                       (select scope from deleted) as deleted_scope
                """,
                {"student_id": student_id, "scope": scope, "actor": actor_user_id},
            ).fetchone()
            downgraded_to, deleted_scope = row["downgraded_to"], row["deleted_scope"]
            if downgraded_to is None and deleted_scope is None:
                return None  # nothing this parent may withdraw
            # A downgrade only ever fires FROM 'day'; a delete exits 'day'
            # when the deleted row's scope says so. A marking that survived the
            # downgrade is still writing 'absent', so the reset would contradict
            # the row that is left.
            exits_day = downgraded_to is not None or deleted_scope == "day"
            if exits_day and row["surviving_marking"] is None:
                conn.execute(
                    "update live_students set status = 'at-school' "
                    "where id = %s and status = 'absent'",
                    (student_id,),
                )
        return {"deleted": deleted_scope is not None, "scope": downgraded_to}

    def clear_absence(self, scope: SchoolScope, absence_id: str, *, actor: dict) -> None:
        """Delete an absence. Clearing a TODAY-dated absence is rejected while
        an active run of a COVERED type involves the student ('End the run
        first' — the run either contains their stop or excluded it at
        snapshot time; a partial absence never blocks on the other run type);
        otherwise it resets the live status to 'at-school', but only when the
        current status is 'absent' and the row actually wrote it. Clearing
        past/future-dated absences never touches the status.

        "Wrote the status" is whole-day coverage OR a recorded period marking
        (U8/R18): since a driver's period mark displays the child as absent, it
        has to clear like one. Whole-day scope alone was the proxy for this and
        is no longer sufficient — a driver-marked afternoon absence would have
        been deleted while the child stayed 'absent' on every surface, with no
        row left to explain why.

        U6: scoped through the student — another school's absence row answers
        404 and is left untouched.
        """
        from app.core.errors import ConflictError

        with get_connection(scope) as conn:
            row = conn.execute(
                """
                select a.student_id, a.scope, a.marked_period, a.absence_date,
                       (a.absence_date = (now() at time zone 'Africa/Nairobi')::date) as is_today,
                       s.status
                from live_student_absences a
                join live_students s on s.id = a.student_id and s.school_id = %s
                where a.id = %s
                """,
                (scope.school_id, absence_id),
            ).fetchone()
            if not row:
                raise NotFoundError("Absence not found")
            if row["is_today"]:
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
                if row["status"] == "absent" and (
                    row["scope"] == "day" or row["marked_period"] is not None
                ):
                    conn.execute(
                        "update live_students set status = 'at-school' where id = %s",
                        (row["student_id"],),
                    )
            conn.execute("delete from live_student_absences where id = %s", (absence_id,))
            record_audit(
                conn, action="absence-cleared", actor=actor, scope=scope,
                resource_type="absence", resource_id=absence_id,
                detail={
                    "student_id": str(row["student_id"]),
                    "date": str(row["absence_date"]),
                },
            )

    def reverse_driver_absence(self, conn, student_id: str, driver_id: str) -> bool:
        """Undo today's absence, but only the one this driver marked (U5).

        The general clear path refuses while a covered run is active — a guard
        with its own justification, since clearing mid-run puts a child back on
        a roster the run has already snapshotted. This is deliberately narrower:
        same day, source 'driver', marked by this account, and the caller has
        already checked the run is still open and belongs to them.

        Guarded inside the statement rather than read-then-write, matching the
        provenance ratchet: a concurrent staff escalation flips source away from
        'driver' and this returns False rather than clobbering it.

        Takes the caller's connection — the reversal, the participation change
        and the status reset are one transaction or none of them.
        """
        deleted = conn.execute(
            """
            delete from live_student_absences
            where student_id = %s
              and absence_date = (now() at time zone 'Africa/Nairobi')::date
              and source = 'driver'
              and marked_by = %s
            returning id
            """,
            (student_id, driver_id),
        ).fetchone()
        if not deleted:
            return False
        conn.execute(
            "update live_students set status = 'at-school' where id = %s and status = 'absent'",
            (student_id,),
        )
        return True

    def clear_for_student_date(self, student_id: str, date: str) -> None:
        with get_connection() as conn:
            conn.execute(
                "delete from live_student_absences where student_id = %s and absence_date = %s::date",
                (student_id, date),
            )
