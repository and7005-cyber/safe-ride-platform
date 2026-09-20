import logging
from typing import Any

from app.core.db import get_connection
from app.core.scope import SchoolScope
from app.dao.audit_dao import actor_display, record_audit
from app.dao.absence_dao import AbsenceDao, absent_student_ids
from app.dao import exception_dao, participation_dao
from app.dao.status_sql import display_status_case, no_progress_case, scope_covers

logger = logging.getLogger("saferide.runs")


class RunDao:
    # --- admin runs --------------------------------------------------------

    def list_runs(self, scope: SchoolScope, active: bool = False) -> list[dict[str, Any]]:
        """The ACTIVE school's runs, newest first (U7). active=True narrows to
        non-completed runs up to and including today (Africa/Nairobi) — the
        dashboard's Active Runs card (R5). A driver-role scope narrows
        further to their own bus's runs: the list must never hand a driver
        the school-wide operational picture.

        Deliberately *not* the same predicate as find_active_run_today, which
        stays pinned to today so a stale run is invisible to every driver write
        that resolves through it (R15; arrive and end take a run_id instead and
        are guarded by _assert_service_day).

        That pinning is what creates the problem this widening solves: a run
        still open at midnight falls out of the driver lookup and out of the
        per-date uniqueness index, and — before this — out of the office's view
        too, so it sat in progress forever with nobody told. Prior-day runs
        surface here flagged `stale`, which is where the office force-closes
        them.
        """
        where = "where r.school_id = %(school_id)s"
        if active:
            where += (
                " and r.status <> 'completed' "
                "and r.date <= (now() at time zone 'Africa/Nairobi')::date"
            )
        if scope.role == "driver":
            where += (
                " and r.bus_id in (select id from live_buses "
                "where driver_id = %(driver_id)s and school_id = %(school_id)s)"
            )
        with get_connection(scope) as conn:
            rows = conn.execute(
                f"""
                select r.*, b.name as bus_name, b.plate_number, rt.name as route_name,
                       {no_progress_case("r")} as no_progress,
                       (r.status <> 'completed'
                        and r.date < (now() at time zone 'Africa/Nairobi')::date) as stale,
                       -- Children this run left unaccounted whose parents have
                       -- not been phoned yet (U14/R14). On the list, not only in
                       -- the force-close dialog: an office user pulled away
                       -- mid-task would otherwise have no way to rediscover
                       -- which runs still owe a family a call, which is the
                       -- entire reason the obligation is recorded.
                       (
                           select count(*) from run_participation p
                           where p.run_id = r.id
                             and p.unaccounted_at is not null
                             and p.contacted_at is null
                       ) as contact_pending
                from live_runs r
                left join live_buses b on b.id = r.bus_id
                left join live_routes rt on rt.id = r.route_id
                {where}
                order by r.date desc, r.created_at desc
                """,
                {"school_id": scope.school_id, "driver_id": scope.user_id},
            ).fetchall()
        return [dict(r) for r in rows]

    def _assert_no_active_run_conflict(
        self, conn, bus_id: str, date, exclude_run_id: str | None = None
    ) -> None:
        """No two non-completed runs for the same bus on the same date (#12).
        Friendly message here; the partial unique index
        live_runs_active_bus_date_key is the race-proof backstop.
        date=None means today (Africa/Nairobi)."""
        from app.core.errors import ConflictError

        exclude_sql = " and id <> %s" if exclude_run_id else ""
        params: list = [bus_id, date]
        if exclude_run_id:
            params.append(exclude_run_id)
        existing = conn.execute(
            f"""
            select 1 from live_runs
            where bus_id = %s and status <> 'completed'
              and date = coalesce(%s::date, (now() at time zone 'Africa/Nairobi')::date)
              {exclude_sql}
            limit 1
            """,
            params,
        ).fetchone()
        if existing:
            raise ConflictError("This bus already has an active run on that date")

    @staticmethod
    def _assert_run_refs_in_scope(conn, scope: SchoolScope, data: dict) -> None:
        """A run payload's bus and route must belong to the scope school
        (U7/AE25's run-side twin): a foreign id "does not exist" — 404."""
        from app.core.errors import NotFoundError

        if data.get("bus_id"):
            if not conn.execute(
                "select 1 from live_buses where id = %s and school_id = %s",
                (data["bus_id"], scope.school_id),
            ).fetchone():
                raise NotFoundError("Bus not found")
        if data.get("route_id"):
            if not conn.execute(
                "select 1 from live_routes where id = %s and school_id = %s",
                (data["route_id"], scope.school_id),
            ).fetchone():
                raise NotFoundError("Route not found")

    def create_run(self, scope: SchoolScope, data: dict, actor: dict) -> dict[str, Any]:
        with get_connection(scope) as conn:
            # school_id comes from the scope, never the client (U7); the
            # payload's bus AND route must be this school's own (404 foreign).
            self._assert_run_refs_in_scope(conn, scope, data)
            # No two non-completed runs for the same bus on the same date (#12).
            if data.get("bus_id") and (data.get("status") or "in-progress") != "completed":
                self._assert_no_active_run_conflict(conn, data["bus_id"], data.get("date"))
            row = conn.execute(
                """
                insert into live_runs
                    (bus_id, route_id, school_id, type, date, start_time, end_time, status,
                     total_stops, stops_completed, total_students, students_boarded, incidents)
                values
                    (%(bus_id)s, %(route_id)s, %(school_id)s, coalesce(%(type)s,'morning'),
                     coalesce(%(date)s, (now() at time zone 'Africa/Nairobi')::date),
                     %(start_time)s, %(end_time)s, coalesce(%(status)s,'in-progress'),
                     coalesce(%(total_stops)s,0), coalesce(%(stops_completed)s,0),
                     coalesce(%(total_students)s,0), coalesce(%(students_boarded)s,0), coalesce(%(incidents)s,0))
                returning *
                """,
                {**data, "school_id": scope.school_id},
            ).fetchone()
            record_audit(
                conn, action="run-created", actor=actor, scope=scope,
                resource_type="run", resource_id=row["id"], detail={},
            )
        return dict(row)

    def update_run(
        self, scope: SchoolScope, run_id: str, data: dict, actor: dict
    ) -> dict[str, Any] | None:
        """Edit a run's plan. Cannot complete one (R16).

        Completion is a claim about children, not a field: it means every child
        on the roster has a recorded outcome. Only the gated driver end and the
        office force-close can make it, because only those two evaluate the
        roster — one refusing until the driver accounts for everyone, the other
        recording the unresolved children as unaccounted. Setting the column
        here would produce a completed run with neither guarantee behind it.
        """
        from app.core.errors import ConflictError, NotFoundError

        with get_connection(scope) as conn:
            current = conn.execute(
                "select date, status from live_runs where id = %s and school_id = %s",
                (run_id, scope.school_id),
            ).fetchone()
            if not current:
                # Another school's run does not exist here (U7/R3) — and a
                # genuinely unknown id answers the same 404, not a 200 null.
                raise NotFoundError("Run not found")
            # A foreign bus or route in the edit is 404 too (AE25's twin).
            self._assert_run_refs_in_scope(conn, scope, data)
            if data.get("status") == "completed" and current["status"] != "completed":
                raise ConflictError(
                    "A run cannot be marked finished here. The driver ends it once "
                    "every child is accounted for, or you can force-close it if they "
                    "cannot."
                )
            # Reopening is blocked as well, and not only for symmetry: a finished
            # run reopened here is no longer 'completed', which is what the
            # delete refusal keys on — so reopen-then-delete would cascade the
            # participation this unit exists to protect.
            if (
                current["status"] == "completed"
                and data.get("status") not in (None, "completed")
            ):
                raise ConflictError(
                    "This run is finished and cannot be reopened. Start a new run "
                    "if the bus is going out again."
                )
            # Re-run the create_run conflict check when the resulting state is
            # non-completed with a bus, excluding this run, so admins get the
            # friendly 409 instead of the raw unique-violation message (R3).
            # The resulting date and status fall back to the run's current ones,
            # matching the coalesce(..., <column>) pairs in the update below.
            if data.get("bus_id") and (data.get("status") or current["status"]) != "completed":
                self._assert_no_active_run_conflict(
                    conn, data["bus_id"], data.get("date") or current["date"],
                    exclude_run_id=run_id,
                )
            row = conn.execute(
                """
                update live_runs set
                    bus_id=%(bus_id)s, route_id=%(route_id)s, type=coalesce(%(type)s,'morning'),
                    date=coalesce(%(date)s, date), start_time=%(start_time)s, end_time=%(end_time)s,
                    -- Preserve, don't default: 'in-progress' here meant any edit
                    -- that omitted the status silently reopened a completed run,
                    -- which then blocked its own bus through the (bus, date)
                    -- uniqueness index.
                    status=coalesce(%(status)s, status), total_stops=coalesce(%(total_stops)s,0),
                    stops_completed=coalesce(%(stops_completed)s,0),
                    total_students=coalesce(%(total_students)s,0),
                    students_boarded=coalesce(%(students_boarded)s,0), incidents=coalesce(%(incidents)s,0)
                where id=%(id)s and school_id=%(scope_school)s returning *
                """,
                {**data, "id": run_id, "scope_school": scope.school_id},
            ).fetchone()
            if row:
                record_audit(
                    conn, action="run-updated", actor=actor, scope=scope,
                    resource_type="run", resource_id=run_id, detail={},
                )
        return dict(row) if row else None

    def delete_run(self, scope: SchoolScope, run_id: str, actor: dict) -> None:
        """Delete a run — the admin's recovery path for a run started in error
        (R16). U7/AE27: a coordinator may delete a run only while it is NOT
        completed (open-run cleanup — a driver's wrong-route start); deleting
        a COMPLETED run is a director-only act, and the completed-today-with-
        evidence refusal below stands for directors too.

        Deleting a non-completed run no longer writes any child a status. It
        used to bulk-set the roster's 'on-bus' children to 'at-school', which
        was a guess dressed as a fact: an afternoon roster is auto-boarded on
        presumption, so that sweep asserted a whole busload arrived somewhere
        nobody observed. Participation cascades with the run instead, leaving
        each child with nothing recorded today — which derives to their true
        pre-run state rather than an invented one.

        The absence rows this run's driver created go with it, for the same
        reason: they were marked in the course of a run that is being undone,
        and leaving them would keep children absent for a trip that no longer
        exists. Office- and parent-created rows are untouched — those are
        statements from outside the run and the admin did not ask to revoke them.

        Deleting a *completed* run dated today whose children have recorded
        participation is refused. Those rows are now the only evidence anyone
        boarded, so cascading them would flip a whole roster from at school to
        at home in the middle of the day.

        The refusal keys on the evidence rather than on the status, because the
        two are not the same thing: an admin-created bookkeeping row carries no
        roster and no participation, and blocking its deletion would cost the
        office a legitimate correction to protect nothing. Older completed runs
        keep their participation as history and delete normally — their day is
        over, so nothing on a live surface moves.
        """
        from app.core.errors import ConflictError, ForbiddenError, NotFoundError

        with get_connection(scope) as conn:
            run = conn.execute(
                """
                select r.status, r.driver_id, r.date,
                       r.date = (now() at time zone 'Africa/Nairobi')::date as is_today,
                       exists (
                           select 1 from run_participation p
                           where p.run_id = r.id and p.student_id is not null
                       ) as has_evidence
                from live_runs r where r.id = %s and r.school_id = %s
                """,
                (run_id, scope.school_id),
            ).fetchone()
            if not run:
                raise NotFoundError("Run not found")
            if run["status"] == "completed" and scope.role != "director":
                # AE27: the coordinator's delete covers open-run cleanup only;
                # a finished run's record is director territory (R8).
                raise ForbiddenError("Only a director can delete a completed run")
            if run["status"] == "completed" and run["is_today"] and run["has_evidence"]:
                raise ConflictError(
                    "This run is finished, and its record is the only evidence of "
                    "who was on the bus today. Deleting it would show those children "
                    "as never having travelled. Edit the run instead."
                )
            if run["status"] != "completed" and run["driver_id"]:
                # Scoped to this run's own roster: a driver runs a morning and an
                # afternoon route on the same date, and undoing one must not
                # revoke the absences they marked on the other.
                conn.execute(
                    """
                    delete from live_student_absences
                    where absence_date = %(date)s
                      and source = 'driver'
                      and marked_by = %(driver_id)s
                      and student_id in (
                          select student_id from run_stops
                          where run_id = %(run_id)s and student_id is not null
                      )
                    """,
                    {"date": run["date"], "driver_id": run["driver_id"], "run_id": run_id},
                )
            # run_participation and run_stops cascade on the run's own delete.
            conn.execute("delete from live_runs where id = %s", (run_id,))
            record_audit(
                conn, action="run-deleted", actor=actor, scope=scope,
                resource_type="run", resource_id=run_id,
                detail={"status": run["status"]},
            )

    def run_report(self, scope: SchoolScope, run_id: str) -> dict[str, Any]:
        """Post-run report (R14-R16): the run row + bus/route/driver names +
        the absence snapshot taken at start_run. Legacy runs that predate the
        snapshot (a route but no run_absences rows and no run_stops — e.g.
        admin-created) fall back to live_student_absences on the run's date
        intersected with the route's membership, flagged approximate=True.
        Runs with no route report an empty list, approximate=False."""
        from app.core.errors import NotFoundError

        with get_connection(scope) as conn:
            run = conn.execute(
                """
                select r.*, b.name as bus_name, b.plate_number, rt.name as route_name,
                       u.full_name as driver_name,
                       (select case when aa.actor_kind = 'provider' then 'SafeRide'
                               else aa.actor_name end
                        from live_admin_audit aa
                        where aa.action = 'run-force-closed'
                          and aa.resource_id = r.id::text
                        order by aa.created_at desc limit 1) as force_closed_by_display
                from live_runs r
                left join live_buses b on b.id = r.bus_id
                left join live_routes rt on rt.id = r.route_id
                left join app_users u on u.id = r.driver_id
                where r.id = %s and r.school_id = %s
                """,
                (run_id, scope.school_id),
            ).fetchone()
            if not run:
                raise NotFoundError("Run was not found")
            # period tells an afternoon non-boarding from a morning no-show
            # (U8/R21) — the same child, two different failures, and the office
            # cannot act on the report without knowing which.
            absent = conn.execute(
                """
                select student_id, student_name, reason, period from run_absences
                where run_id = %s order by student_name asc
                """,
                (run_id,),
            ).fetchall()
            approximate = False
            if not absent and run["route_id"] is not None:
                has_stops = conn.execute(
                    "select 1 from run_stops where run_id = %s limit 1", (run_id,)
                ).fetchone()
                if not has_stops:
                    # Scope-aware (U4): only absences COVERING this run's type
                    # count — a morning-cancelled child is not absent from the
                    # afternoon run.
                    absent = conn.execute(
                        f"""
                        select a.student_id, s.name as student_name, a.reason,
                               a.marked_period as period
                        from live_student_absences a
                        join live_students s on s.id = a.student_id
                        join live_student_routes sr
                            on sr.student_id = a.student_id and sr.route_id = %s
                        where a.absence_date = %s
                          and {scope_covers("a.scope", "%s")}
                        order by s.name asc
                        """,
                        (run["route_id"], run["date"], run["type"]),
                    ).fetchall()
                    approximate = True
            # The contact obligation is re-readable per run (U14/R14), so the
            # office can reopen a force-closed run days later and still see who
            # was never accounted for and whether anyone rang their family.
            outstanding = participation_dao.unaccounted_children(conn, str(run_id))
            # Stop exceptions with their derived status (GPS plan U2/R21): the
            # same read the staff list route serves, on this connection.
            exceptions = exception_dao.list_exceptions(conn, dict(run))
        report = dict(run)
        report["absent_students"] = [dict(a) for a in absent]
        report["unaccounted"] = outstanding
        report["approximate"] = approximate
        report["exceptions"] = exceptions
        return report

    # --- driver context ----------------------------------------------------
    # Every driver-surface method takes the driver's SchoolScope (U7): the
    # driver id is scope.user_id and the bus resolves through the driver's
    # own school, so a driver can only ever act on their school's rows.

    def get_driver_context(self, scope: SchoolScope) -> dict[str, Any]:
        driver_id = scope.user_id
        with get_connection(scope) as conn:
            bus = conn.execute(
                "select * from live_buses where driver_id = %s and school_id = %s "
                "order by name asc limit 1",
                (driver_id, scope.school_id),
            ).fetchone()
            if not bus:
                return {
                    "bus": None, "routes": [], "active_run": None, "students": [],
                    "completed_route_ids_today": [],
                }
            routes = conn.execute(
                "select * from live_routes where bus_id = %s order by type asc", (bus["id"],)
            ).fetchall()
            # Routes of this bus already completed today (any creator): the
            # driver UI greys them out because start_run rejects them (R24).
            completed_today = conn.execute(
                """
                select distinct r.route_id from live_runs r
                join live_routes rt on rt.id = r.route_id
                where rt.bus_id = %s and r.status = 'completed'
                  and r.date = (now() at time zone 'Africa/Nairobi')::date
                """,
                (bus["id"],),
            ).fetchall()
            active = conn.execute(
                """
                select * from live_runs
                where bus_id = %s and status <> 'completed'
                    and date = (now() at time zone 'Africa/Nairobi')::date
                order by created_at desc limit 1
                """,
                (bus["id"],),
            ).fetchone()
            # Today-absent students stay visible with an `absent` flag (R25b):
            # the driver still sees the stop instead of it silently vanishing
            # mid-run, and stop numbering never shifts under their feet.
            #
            # With an active run the actionable list is the RUN's roster
            # (run_stops membership) — never the derived bus roster, which
            # diverges for students whose afternoon route rides another bus.
            # Without a run, the bus roster is the natural pre-run view.
            #
            # Scope-aware flag (U4): with an active run, a partial absence
            # flags only when it covers the RUN's type; pre-run there is no
            # run to match, so only whole-day rows flag (the %s arm is NULL
            # then, and `a.scope = null` matches nothing).
            active_dict = dict(active) if active else None
            # display_status travels with the roster (U3) so the driver phone
            # reads the same derived value as the admin list and the parent app.
            # It used to project the raw status column and show a stale on-bus
            # child differently from every other surface.
            def roster_sql(scope_param: str, undo: str) -> str:
                return f"""
                select s.*, {display_status_case("s")} as display_status, exists (
                    select 1 from live_student_absences a
                    where a.student_id = s.id
                      and a.absence_date = (now() at time zone 'Africa/Nairobi')::date
                      and {scope_covers("a.scope", scope_param)}
                ) as absent{undo}
                from live_students s
                """

            if active_dict:
                # can_undo drives the row's undo affordance (U13/R10) and mirrors
                # reverse_own_action's conditions exactly: this run, this login's
                # own action, still reversible. Deriving it on the phone would
                # mean the button appears on rows the server then refuses — or,
                # worse, hides on rows it would have allowed. Every terminal
                # badge looking reversible invites accidental taps; none of them
                # looking reversible makes the path undiscoverable.
                #
                # The absence arm matches reverse_driver_absence's own guard
                # (source 'driver', marked by this login), so a driver mark that
                # U8's precedence left attributed to the office or a parent
                # correctly offers no undo here.
                undo_sql = """, (
                        exists (
                            select 1 from run_participation p
                            where p.run_id = %(run_id)s and p.student_id = s.id
                              and (p.dropped_off_at is not null or p.handover_at is not null)
                              and p.acting_driver_id = %(driver_id)s
                        )
                        or exists (
                            select 1 from live_student_absences a2
                            where a2.student_id = s.id
                              and a2.absence_date = (now() at time zone 'Africa/Nairobi')::date
                              and a2.source = 'driver'
                              and a2.marked_by = %(driver_id)s
                        )
                    ) as can_undo"""
                students = conn.execute(
                    roster_sql("%(run_type)s", undo_sql)
                    + """
                    where s.id in (
                        select rs.student_id from run_stops rs
                        where rs.run_id = %(run_id)s and rs.student_id is not null
                    )
                    order by s.name asc
                    """,
                    {
                        "run_type": active_dict["type"],
                        "run_id": active_dict["id"],
                        "driver_id": driver_id,
                    },
                ).fetchall()
            else:
                # No run, so nothing of this driver's to undo on one.
                students = conn.execute(
                    roster_sql("%s", ", false as can_undo")
                    + " where s.bus_id = %s order by s.name asc",
                    (None, bus["id"]),
                ).fetchall()
            # The closure gate's blocking set, as data rather than only as the
            # text of a 409 (U13/R11). The driver's end-run screen lists these
            # names live, so they disappear one by one as each child is
            # resolved; recomputing the rule on the phone instead would be a
            # second implementation of the gate, free to drift from the one that
            # actually refuses.
            blocking = (
                participation_dao.unaccounted_on_run(
                    conn, str(active_dict["id"]), active_dict["type"]
                )
                if active_dict
                else []
            )
            run_stops = []
            if active_dict:
                run_stops = [
                    dict(s)
                    for s in conn.execute(
                        "select rs.* from run_stops rs where rs.run_id = %s "
                        "order by rs.stop_order asc",
                        (active_dict["id"],),
                    ).fetchall()
                ]
        return {
            "bus": dict(bus),
            "routes": [dict(r) for r in routes],
            "active_run": active_dict,
            "run_stops": run_stops,
            "students": [dict(s) for s in students],
            "blocking": [{"id": str(b["id"]), "name": b["name"]} for b in blocking],
            "completed_route_ids_today": [str(r["route_id"]) for r in completed_today],
        }

    def _assert_service_day(self, conn, run: dict) -> None:
        """No driver action operates on a run from a past service day (R15).

        The student-level paths get this free: they resolve the run through
        find_active_run_today, so a stale run is simply not found. The two paths
        that take a run_id straight from the client — arrive and end — do not,
        and without this a driver opening the app the next morning could arrive
        stops and close yesterday's run with today's timestamps.

        Ending is blocked too, not just extending. A day-late close writes
        today's end_time and today's counts onto yesterday's run, and any child
        still unaccounted would be swept past by a gate that only sees the
        roster. The office force-close is the designed exit precisely because it
        records those children as unaccounted rather than closing over them.
        """
        from app.core.errors import ConflictError

        row = conn.execute(
            "select %s::date < (now() at time zone 'Africa/Nairobi')::date as stale",
            (run["date"],),
        ).fetchone()
        if row and row["stale"]:
            raise ConflictError(
                "This run is from a previous day and can no longer be changed. "
                "Ask the office to close it."
            )

    def find_active_run_today(self, conn, bus_id: str) -> dict[str, Any] | None:
        row = conn.execute(
            """
            select * from live_runs
            where bus_id = %s and status <> 'completed'
                and date = (now() at time zone 'Africa/Nairobi')::date
            limit 1
            """,
            (bus_id,),
        ).fetchone()
        return dict(row) if row else None

    def start_run(self, scope: SchoolScope, route_id: str) -> dict[str, Any]:
        """Atomic: validate, snapshot stops, create the in-progress run."""
        from app.core.errors import ConflictError, ForbiddenError

        driver_id = scope.user_id
        with get_connection(scope) as conn:
            bus = conn.execute(
                "select * from live_buses where driver_id = %s and school_id = %s limit 1",
                (driver_id, scope.school_id),
            ).fetchone()
            if not bus:
                raise ForbiddenError("No bus is assigned to this driver")
            route = conn.execute(
                "select * from live_routes where id = %s and bus_id = %s", (route_id, bus["id"])
            ).fetchone()
            if not route:
                raise ForbiddenError("Route is not assigned to this driver's bus")
            # A route runs at most once per day, whoever created the run (R24).
            completed_today = conn.execute(
                """
                select 1 from live_runs
                where route_id = %s and status = 'completed'
                  and date = (now() at time zone 'Africa/Nairobi')::date
                limit 1
                """,
                (route_id,),
            ).fetchone()
            if completed_today:
                raise ConflictError("This route has already been completed today")
            # Planner-saved routes carry authored stops with no students; a
            # student assignment flips the flag and makes them startable (R18).
            if route["custom_stops"]:
                raise ConflictError("No students are assigned to this route yet")
            if route["school_id"] is None:
                raise ConflictError("Route has no school")
            if self.find_active_run_today(conn, bus["id"]):
                raise ConflictError("A run is already in progress for this bus today")

            all_stops = conn.execute(
                "select * from live_route_stops where route_id = %s order by stop_order asc, name asc",
                (route_id,),
            ).fetchall()
            # Drop stops for students marked absent today (#7) with a scope
            # covering THIS run's type (U4 — a morning-only cancellation keeps
            # the afternoon stop), then renumber so the snapshot's stop_orders
            # stay contiguous (arrive_next_stop walks them one at a time).
            absent = absent_student_ids(conn, run_type=route["type"])
            kept = [
                s for s in all_stops
                if s["student_id"] is None or str(s["student_id"]) not in absent
            ]
            distinct_orders = sorted({s["stop_order"] for s in kept})
            if not distinct_orders:
                raise ConflictError("Route has no stops")
            order_map = {orig: i + 1 for i, orig in enumerate(distinct_orders)}
            # total_students is the RUN's roster size (distinct non-absent
            # students in the snapshot) — never the derived bus roster, which
            # diverges for cross-bus riders and would let dropped-off counts
            # exceed the total.
            roster_size = len({str(s["student_id"]) for s in kept if s["student_id"]})

            run = conn.execute(
                """
                insert into live_runs
                    (bus_id, route_id, school_id, driver_id, type, date, start_time, status,
                     total_stops, stops_completed, total_students, students_boarded, incidents)
                values
                    (%s, %s, %s, %s, %s, (now() at time zone 'Africa/Nairobi')::date,
                     to_char(now() at time zone 'Africa/Nairobi', 'HH24:MI'), 'in-progress',
                     %s, 0, %s, 0, 0)
                returning *
                """,
                (bus["id"], route_id, route["school_id"], driver_id, route["type"],
                 len(distinct_orders), roster_size),
            ).fetchone()
            for s in kept:
                conn.execute(
                    "insert into run_stops (run_id, stop_order, name, scheduled_time, lat, lng, is_school_gate, student_id, school_id) "
                    "values (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (run["id"], order_map[s["stop_order"]], s["name"], s["scheduled_time"], s["lat"],
                     s["lng"], s["is_school_gate"], s["student_id"], route["school_id"]),
                )
            # Both run types operate on the RUN's roster (the run_stops student
            # set) — never the derived live_students.bus_id (see KTDs).
            #
            # Stale-'absent' self-heal: absences are per-date and nothing else
            # clears the status, so a roster student still 'absent' from a
            # previous day (no today-absence row) must not stay stuck.
            if route["type"] == "afternoon":
                # Auto-board the roster (R32): every roster student without a
                # today-absence COVERING the afternoon (U4 — a morning-only
                # cancellation still boards) goes 'on-bus' directly in SQL —
                # no per-student 'student-boarded' pushes; the run-level
                # 'on-way-home' notification covers the start. Stale-'absent'
                # students join the auto-board set here.
                conn.execute(
                    f"""
                    update live_students set status = 'on-bus'
                    where id in (
                        select student_id from run_stops
                        where run_id = %s and student_id is not null
                    )
                      and id not in (
                          select student_id from live_student_absences
                          where absence_date = (now() at time zone 'Africa/Nairobi')::date
                            and {scope_covers("scope", "%s")}
                      )
                    """,
                    (run["id"], route["type"]),
                )
            else:
                # Morning: reset stale-'absent' roster students to 'at-school'
                # so they board normally. Heal only when no absence COVERS
                # this run (U4): an afternoon-only cancellation does not make
                # the child absent from the morning, so it never blocks the
                # heal — and a covering row means genuinely absent, no heal.
                conn.execute(
                    f"""
                    update live_students set status = 'at-school'
                    where status = 'absent'
                      and id in (
                          select student_id from run_stops
                          where run_id = %s and student_id is not null
                      )
                      and id not in (
                          select student_id from live_student_absences
                          where absence_date = (now() at time zone 'Africa/Nairobi')::date
                            and {scope_covers("scope", "%s")}
                      )
                    """,
                    (run["id"], route["type"]),
                )
            if route["type"] == "afternoon":
                # Record the auto-board as participation, flagged presumed (U2):
                # the app assumed these children are aboard, nobody observed it.
                # The distinction is what stops the arrival notification and the
                # parent card asserting a boarding no actor recorded.
                for row in conn.execute(
                    """
                    select rs.student_id, s.name
                    from run_stops rs
                    join live_students s on s.id = rs.student_id
                    where rs.run_id = %s and rs.student_id is not null
                      and s.status = 'on-bus'
                    """,
                    (run["id"],),
                ).fetchall():
                    participation_dao.record_boarding(
                        conn, str(run["id"]), str(row["student_id"]), row["name"],
                        str(driver_id), presumed=True,
                    )
            # Recount students_boarded (never increment): morning counts who is
            # aboard, afternoon counts confirmed drop-offs — both 0 at start in
            # the normal case, but recomputing keeps the counter honest even for
            # roster students carried over in an odd state.
            boarded_count = (
                participation_dao.count_dropped_off(conn, str(run["id"]))
                if route["type"] == "afternoon"
                else participation_dao.count_boarded(conn, str(run["id"]))
            )
            run = conn.execute(
                "update live_runs set students_boarded = %s where id = %s returning *",
                (boarded_count, run["id"]),
            ).fetchone()
            # Snapshot today's absences for the run report (R14-R16): today's
            # absent students intersected with the ROUTE's membership — never
            # the derived bus roster (see KTDs) — and only absences COVERING
            # this run's type (U4). student_name is denormalized because
            # run_absences.student_id is ON DELETE SET NULL; a name-less
            # snapshot would rot after student deletion.
            conn.execute(
                f"""
                insert into run_absences (run_id, student_id, student_name, reason, period,
                                          school_id)
                select %s, s.id, s.name, a.reason,
                       -- What was individually marked, falling back to the
                       -- row's coverage for absences nobody witnessed at a
                       -- stop (an office mark, a parent cancellation).
                       coalesce(a.marked_period, a.scope),
                       %s
                from live_student_absences a
                join live_students s on s.id = a.student_id
                join live_student_routes sr
                    on sr.student_id = a.student_id and sr.route_id = %s
                where a.absence_date = (now() at time zone 'Africa/Nairobi')::date
                  and {scope_covers("a.scope", "%s")}
                on conflict (run_id, student_id) do nothing
                """,
                (run["id"], route["school_id"], route_id, route["type"]),
            )
            # Position the bus at the school when the run starts; from here the
            # position is the last stop the driver arrives at (no device GPS).
            school = conn.execute(
                "select lat, lng from live_schools where id = %s", (route["school_id"],)
            ).fetchone()
            if school and school["lat"] is not None and school["lng"] is not None:
                conn.execute(
                    "update live_buses set current_lat = %s, current_lng = %s where id = %s",
                    (school["lat"], school["lng"], bus["id"]),
                )
        return dict(run)

    def arrive_next_stop(self, scope: SchoolScope, run_id: str) -> dict[str, Any]:
        from app.core.errors import ConflictError, ForbiddenError

        driver_id = scope.user_id
        with get_connection(scope) as conn:
            run = conn.execute(
                "select * from live_runs where id = %s and school_id = %s",
                (run_id, scope.school_id),
            ).fetchone()
            if not run or str(run["driver_id"]) != str(driver_id):
                raise ForbiddenError("Run is not owned by this driver")
            if run["status"] == "completed":
                raise ConflictError("Run is already completed")
            self._assert_service_day(conn, run)
            new_completed = min(run["stops_completed"] + 1, run["total_stops"])
            conn.execute(
                "update live_runs set stops_completed = %s where id = %s", (new_completed, run_id)
            )
            # Record WHEN the stop was reached, not just how many have been (U10).
            # The no-progress flag measures elapsed time between arrivals, and the
            # office force-close needs evidence the bus actually reached the school
            # gate before it asserts a child arrived safely. Every row at this
            # stop_order is stamped: a shared stop carries one row per student.
            #
            # Deliberately NOT idempotent. arrive_next_stop takes no stop
            # identifier — every tap means "arrive at next" — so repeat tapping is
            # the only way a driver catches progress up to a child's stop_order,
            # and reaching it is a precondition for confirming their drop-off.
            # Suppressing repeats would leave a driver who missed a tap unable to
            # confirm, pushing them toward marking the child absent instead.
            conn.execute(
                "update run_stops set arrived_at = now() "
                "where run_id = %s and stop_order = %s",
                (run_id, new_completed),
            )
            arrival_incident = None
            gate = conn.execute(
                "select * from run_stops where run_id = %s and stop_order = %s order by is_school_gate desc limit 1",
                (run_id, new_completed),
            ).fetchone()
            # The bus's live position is the stop it just arrived at (no GPS).
            # Coordinate-less stops leave the position at the previous stop.
            if gate and gate["lat"] is not None and gate["lng"] is not None:
                conn.execute(
                    "update live_buses set current_lat = %s, current_lng = %s where id = %s",
                    (gate["lat"], gate["lng"], run["bus_id"]),
                )
            is_last = new_completed >= run["total_stops"]
            if gate and (gate["is_school_gate"] or is_last):
                # Idempotent per run: only the first arrival at the gate emits.
                existing = conn.execute(
                    "select 1 from live_incidents where run_id = %s and type = 'arrival'", (run_id,)
                ).fetchone()
                if not existing:
                    bus = conn.execute(
                        "select * from live_buses where id = %s", (run["bus_id"],)
                    ).fetchone()
                    # Stamped with the run's school (U7): incidents are
                    # school-owned rows from here on.
                    inc = conn.execute(
                        """
                        insert into live_incidents
                            (run_id, driver_id, driver_name, bus_id, bus_name, type,
                             description, school_id)
                        values (%s, %s, %s, %s, %s, 'arrival', %s, %s)
                        returning *
                        """,
                        (run_id, driver_id, bus["driver_name"] if bus else None, run["bus_id"],
                         bus["name"] if bus else None,
                         f"{bus['name'] if bus else 'Bus'} has arrived at {gate['name']}.",
                         run["school_id"]),
                    ).fetchone()
                    conn.execute(
                        "update live_runs set incidents = incidents + 1 where id = %s", (run_id,)
                    )
                    arrival_incident = dict(inc)
            # Bypassed-stop check (GPS plan U2/R15). Progress moved to N, so the
            # stop the bus just left behind is N−1, and the closure gate's own
            # per-stop predicate decides whether anyone there is unrecorded.
            #
            # The check, the exception upsert and the prompt event run in a
            # savepoint on this same connection (psycopg opens one because the
            # transaction is already in progress). Everything above is the
            # atomic core and commits exactly as before; a failure in here rolls
            # back to the savepoint, is logged with the run and stop order only
            # — never a coordinate — and swallowed, so a prompt-side bug can
            # never block a driver's tap (R23, R40). No trail row exists yet
            # (U7), so 'classification-failed' is this log line for now.
            bypassed = None
            passed_order = new_completed - 1
            try:
                with conn.transaction():
                    bypassed = exception_dao.evaluate_bypassed_stop(
                        conn, dict(run), passed_order
                    )
            except Exception:
                logger.exception(
                    "stop-bypassed evaluation failed; the Arrive still commits "
                    "(run=%s stop_order=%s)",
                    run_id, passed_order,
                )
            updated = conn.execute("select * from live_runs where id = %s", (run_id,)).fetchone()
        result: dict[str, Any] = {
            "run": dict(updated), "arrival_incident": arrival_incident, "prompts": [],
        }
        if bypassed:
            if bypassed["prompt"]:
                result["prompts"].append(bypassed["prompt"])
            if bypassed["raised"]:
                # For the router's office alert only; it pops this before the
                # response leaves.
                result["bypassed_stop"] = {
                    key: bypassed[key]
                    for key in ("exception_id", "stop_order", "stop_name", "students")
                }
        return result

    def end_run(self, scope: SchoolScope, run_id: str) -> dict[str, Any]:
        """Complete a run — refused while any roster child is unaccounted (U4).

        The end-of-run sweep is gone. It used to write a terminal status to
        every non-absent roster child, which meant a child who never boarded was
        recorded as safely at school and a child the driver never confirmed off
        the bus was recorded as dropped off. With the gate in place no sweep is
        needed: every child already has an individually recorded outcome, or the
        run does not close.

        The run row is locked for the duration so a concurrent driver-end and
        office force-close cannot both pass their completed check and each write
        a different outcome for the same children.
        """
        from app.core.errors import ClosureRefusedError, ConflictError, ForbiddenError

        driver_id = scope.user_id
        with get_connection(scope) as conn:
            run = conn.execute(
                "select * from live_runs where id = %s and school_id = %s for update",
                (run_id, scope.school_id),
            ).fetchone()
            if not run or str(run["driver_id"]) != str(driver_id):
                raise ForbiddenError("Run is not owned by this driver")
            if run["status"] == "completed":
                raise ConflictError("Run is already completed")
            self._assert_service_day(conn, run)

            blocking = participation_dao.unaccounted_on_run(conn, str(run_id), run["type"])
            if blocking:
                names = ", ".join(b["name"] for b in blocking)
                action = (
                    "Confirm their drop-off, record an off-route hand-over, or mark them absent"
                    if run["type"] == "afternoon"
                    else "Board them or mark them absent"
                )
                # Typed so the router can alert the office naming the same
                # children the driver was just told about (U11).
                raise ClosureRefusedError(
                    f"Not everyone is accounted for: {names}. {action} before ending the run.",
                    run_id=str(run_id),
                    blocking=[dict(b) for b in blocking],
                )
            # Who the driver actually observed boarding (U2). Read from
            # participation, not from the status column: a presumed afternoon
            # board is not evidence a child rode, and the arrival notification
            # must never assert arrival for one. This also no longer needs to
            # run "before the sweep wipes it" — the record outlives the run.
            boarded_ids = participation_dao.confirmed_boarded_ids(conn, str(run_id))
            # Persist students_boarded per run type: morning counts who is
            # aboard; afternoon counts confirmed drop-offs and hand-overs.
            final_count = (
                participation_dao.count_dropped_off(conn, str(run_id))
                if run["type"] == "afternoon"
                else participation_dao.count_boarded(conn, str(run_id))
            )
            conn.execute(
                "update live_runs set status='completed', stops_completed=total_stops, "
                "students_boarded=%s, "
                "end_time=to_char(now() at time zone 'Africa/Nairobi','HH24:MI') where id=%s",
                (final_count, run_id),
            )
            # No sweep. Every roster child reached this point with a recorded
            # outcome — that is what the gate above guarantees — so writing one
            # here could only manufacture a claim nobody made.
            #
            # live_students.status is still updated for children who boarded, so
            # the vestigial column does not drift while it survives; nothing
            # derives from it since U3.
            column_status = "dropped-off" if run["type"] == "afternoon" else "at-school"
            conn.execute(
                """
                update live_students set status = %s
                where id in (
                    select p.student_id from run_participation p
                    where p.run_id = %s and p.student_id is not null
                      and p.boarded_at is not null
                )
                """,
                (column_status, run_id),
            )
            # Clear the bus live position.
            conn.execute(
                "update live_buses set current_lat = null, current_lng = null where id = %s",
                (run["bus_id"],),
            )
            updated = conn.execute("select * from live_runs where id = %s", (run_id,)).fetchone()
        result = dict(updated)
        result["boarded_student_ids"] = boarded_ids
        return result

    def force_close_run(
        self, scope: SchoolScope, run_id: str, actor: dict[str, Any]
    ) -> dict[str, Any]:
        """Close a run no driver can resolve (U6/R12-R14).

        A driver whose phone dies, whose shift ends, or who simply forgets leaves
        the run open — and the partial unique index on (bus, date) then blocks
        that bus from starting its next route for the day. Past midnight it gets
        worse: every driver action resolves the run through a date-scoped lookup,
        so the driver loses even their own release. Without this the only exit
        is deleting the run, which destroys its report.

        This is not a sweep. Children without an outcome are recorded
        **unaccounted** — the app saying plainly that nobody knows — rather than
        given a terminal status nobody observed. That distinction is the whole
        reason the sweep was removed.

        Returns the run plus the children the office now owes a phone call.
        """
        from app.core.errors import ConflictError, NotFoundError

        with get_connection(scope) as conn:
            run = conn.execute(
                "select * from live_runs where id = %s and school_id = %s for update",
                (run_id, scope.school_id),
            ).fetchone()
            if not run:
                raise NotFoundError("Run not found")
            if run["status"] == "completed":
                raise ConflictError("Run is already completed")

            blocking = participation_dao.unaccounted_on_run(conn, str(run_id), run["type"])
            participation_dao.record_unaccounted(conn, str(run_id), blocking)

            gate_reached = participation_dao.gate_arrival_recorded(conn, str(run_id))
            boarded_ids = (
                participation_dao.confirmed_boarded_ids(conn, str(run_id))
                if gate_reached and run["type"] == "morning"
                else []
            )
            final_count = (
                participation_dao.count_dropped_off(conn, str(run_id))
                if run["type"] == "afternoon"
                else participation_dao.count_boarded(conn, str(run_id))
            )
            # No force_closed_by column: the audit row below records who did it
            # (U9), and a force-closed run stays identifiable by its
            # unaccounted participation rows.
            conn.execute(
                """
                update live_runs
                set status = 'completed', stops_completed = total_stops,
                    students_boarded = %s,
                    end_time = to_char(now() at time zone 'Africa/Nairobi', 'HH24:MI')
                where id = %s
                """,
                (final_count, run_id),
            )
            conn.execute(
                "update live_buses set current_lat = null, current_lng = null where id = %s",
                (run["bus_id"],),
            )
            updated = conn.execute("select * from live_runs where id = %s", (run_id,)).fetchone()
            outstanding = participation_dao.unaccounted_children(conn, str(run_id))
            record_audit(
                conn,
                action="run-force-closed",
                actor=actor,
                scope=scope,
                resource_type="run",
                resource_id=str(run_id),
                detail={"unaccounted_count": len(outstanding)},
            )

        result = dict(updated)
        # Only children the driver was recorded as observing aboard, and only
        # when the gate arrival was itself recorded (R13). The office was not on
        # the bus; a notification on its say-so would be the manufactured claim
        # this work removes.
        result["boarded_student_ids"] = boarded_ids
        result["gate_arrival_recorded"] = gate_reached
        result["unaccounted"] = outstanding
        result["force_closed_by_display"] = actor_display(actor, scope)
        return result

    def record_parent_contact(
        self, scope: SchoolScope, run_id: str, student_id: str
    ) -> dict[str, Any]:
        """Record that the office phoned an unaccounted child's parents (R14)."""
        from app.core.errors import NotFoundError

        with get_connection(scope) as conn:
            run = conn.execute(
                "select 1 from live_runs where id = %s and school_id = %s",
                (run_id, scope.school_id),
            ).fetchone()
            if not run:
                raise NotFoundError("Run not found")
            if not participation_dao.record_contact(
                conn, str(run_id), student_id, scope.user_id
            ):
                raise NotFoundError("No unaccounted child on this run to record contact for")
            outstanding = participation_dao.unaccounted_children(conn, str(run_id))
        return {"run_id": str(run_id), "unaccounted": outstanding}

    def toggle_boarding(
        self, scope: SchoolScope, student_id: str, on_bus: bool
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Board a student on a morning run; returns (student, run snapshot).

        Boarding is morning-only (afternoon runs auto-board and confirm via
        dropoff_student) and one-way: un-boarding is disabled because a
        boarded push already went out and silently retracting a safety
        assertion is worse than routing the fix through the office (R26).
        """
        from app.core.errors import ConflictError, ForbiddenError

        driver_id = scope.user_id
        with get_connection(scope) as conn:
            bus_id = self._bus_id_for_driver(conn, scope)
            run = self.find_active_run_today(conn, bus_id) if bus_id else None
            if not run or str(run["driver_id"]) != str(driver_id):
                raise ForbiddenError("No active run for this driver")
            if run["type"] == "afternoon":
                raise ConflictError("Use drop-off on afternoon runs")
            if not on_bus:
                raise ConflictError(
                    "Un-boarding is disabled — refresh the app and contact the "
                    "office to correct a mistake"
                )
            stop = conn.execute(
                "select * from run_stops where run_id = %s and student_id = %s limit 1",
                (run["id"], student_id),
            ).fetchone()
            if not stop:
                raise ForbiddenError("Student is not on this run")
            if stop["stop_order"] > run["stops_completed"]:
                raise ConflictError("Stop has not been reached yet")
            row = conn.execute(
                "update live_students set status = 'on-bus' where id = %s returning *",
                (student_id,),
            ).fetchone()
            # The observed fact, recorded against this run with its actor (U2).
            # The status write above stays for now: participation and the column
            # coexist until the follow-up drops it.
            participation_dao.record_boarding(
                conn, str(run["id"]), student_id, row["name"], str(driver_id), presumed=False
            )
            # Recount students_boarded from participation in the SAME
            # transaction — never increment/decrement, so repeated taps can't
            # drift the counter (R15), and it no longer reads a status column
            # that has stopped tracking boarding.
            boarded_count = participation_dao.count_boarded(conn, str(run["id"]))
            run = conn.execute(
                "update live_runs set students_boarded = %s where id = %s returning *",
                (boarded_count, run["id"]),
            ).fetchone()
        return dict(row), dict(run)

    def dropoff_student(
        self, scope: SchoolScope, student_id: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Confirm a drop-off at a reached stop on the driver's active
        afternoon run; returns (student, run snapshot).

        The student must sit on the run's own roster (run_stops), their stop
        must already be reached, and they must still be 'on-bus' (R32).
        students_boarded is recounted as the roster's dropped-off count in the
        same transaction — never incremented — so mobile retries can't drift
        it.
        """
        from app.core.errors import ConflictError, ForbiddenError

        driver_id = scope.user_id
        with get_connection(scope) as conn:
            bus_id = self._bus_id_for_driver(conn, scope)
            run = self.find_active_run_today(conn, bus_id) if bus_id else None
            if not run or str(run["driver_id"]) != str(driver_id):
                raise ForbiddenError("No active run for this driver")
            if run["type"] != "afternoon":
                raise ConflictError("Drop-off is only available on afternoon runs")
            stop = conn.execute(
                "select * from run_stops where run_id = %s and student_id = %s limit 1",
                (run["id"], student_id),
            ).fetchone()
            if not stop:
                raise ForbiddenError("Student is not on this run")
            if stop["stop_order"] > run["stops_completed"]:
                raise ConflictError("Stop has not been reached yet")
            # Precondition re-keyed onto participation (U2), not the status
            # column. The afternoon board is presumed, so a child may legitimately
            # be mid-transition; what matters is that this run records them
            # aboard. Keying on status would refuse every afternoon confirmation
            # the moment boarding stops writing it, and no afternoon run could
            # then pass the closure gate.
            aboard = participation_dao.get_for_student(conn, str(run["id"]), student_id)
            if not aboard or aboard["boarded_at"] is None:
                raise ConflictError("Student is not on the bus")
            if aboard["dropped_off_at"] is not None or aboard["handover_at"] is not None:
                raise ConflictError("This drop-off is already confirmed")
            row = conn.execute(
                "update live_students set status = 'dropped-off' where id = %s returning *",
                (student_id,),
            ).fetchone()
            participation_dao.record_dropoff(conn, str(run["id"]), student_id, str(driver_id))
            dropped_count = participation_dao.count_dropped_off(conn, str(run["id"]))
            run = conn.execute(
                "update live_runs set students_boarded = %s where id = %s returning *",
                (dropped_count, run["id"]),
            ).fetchone()
        return dict(row), dict(run)

    def record_handover(
        self, scope: SchoolScope, student_id: str, note: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Record a hand-over away from the child's own stop (U4/R12).

        A breakdown, a closed road, a guardian collecting at the roadside — the
        child left the bus, just not where the route said. Without this the
        driver's only release is marking them absent, which tells the family the
        child was never on the bus home: false, and the exact class of claim
        this work removes. Records an accounted outcome with the driver's note
        and no absence row.

        Unlike a drop-off this does not require the child's stop to have been
        reached, because by definition it did not happen there.
        """
        from app.core.errors import BadRequestError, ConflictError, ForbiddenError

        note = (note or "").strip()
        if not note:
            raise BadRequestError("A note is required — say where the child was handed over.")

        driver_id = scope.user_id
        with get_connection(scope) as conn:
            bus_id = self._bus_id_for_driver(conn, scope)
            run = self.find_active_run_today(conn, bus_id) if bus_id else None
            if not run or str(run["driver_id"]) != str(driver_id):
                raise ForbiddenError("No active run for this driver")
            stop = conn.execute(
                "select 1 from run_stops where run_id = %s and student_id = %s limit 1",
                (run["id"], student_id),
            ).fetchone()
            if not stop:
                raise ForbiddenError("Student is not on this run")
            aboard = participation_dao.get_for_student(conn, str(run["id"]), student_id)
            if not aboard or aboard["boarded_at"] is None:
                raise ConflictError("Student is not on the bus")
            if aboard["dropped_off_at"] is not None or aboard["handover_at"] is not None:
                raise ConflictError("This child is already accounted for")
            participation_dao.record_handover(
                conn, str(run["id"]), student_id, str(driver_id), note
            )
            row = conn.execute(
                "update live_students set status = 'dropped-off' where id = %s returning *",
                (student_id,),
            ).fetchone()
            dropped_count = participation_dao.count_dropped_off(conn, str(run["id"]))
            run = conn.execute(
                "update live_runs set students_boarded = %s where id = %s returning *",
                (dropped_count, run["id"]),
            ).fetchone()
        return dict(row), dict(run)

    def reverse_own_action(
        self, scope: SchoolScope, student_id: str
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        """Undo this driver's own drop-off, hand-over or absence mark while the
        run is still open (U5/R10). Returns (student, run, what_was_reversed).

        The closure gate makes a mis-tap consequential. A drop-off confirmed on
        the wrong child sends that family a false assurance and cannot be taken
        back; an absence marked in error both misrecords the child and blocks
        their real drop-off from ever being confirmed, because the drop-off path
        requires them to be aboard.

        This is a dedicated path, never a relaxation of the boarding toggle.
        That endpoint's rejection of un-boarding is a stale-client concurrency
        guard with a justification independent of the finality argument, and
        relaxing it would regress that protection while appearing to change only
        UX.

        Scope is deliberately narrow: this driver's account, this run, still
        open. The driver assistant shares the login, so "their own action" means
        this login's action — enough to correct a mis-tap, not an audit trail.
        """
        from app.core.errors import ConflictError, ForbiddenError

        driver_id = scope.user_id
        with get_connection(scope) as conn:
            bus_id = self._bus_id_for_driver(conn, scope)
            run = self.find_active_run_today(conn, bus_id) if bus_id else None
            if not run or str(run["driver_id"]) != str(driver_id):
                raise ForbiddenError("No active run for this driver")
            stop = conn.execute(
                "select 1 from run_stops where run_id = %s and student_id = %s limit 1",
                (run["id"], student_id),
            ).fetchone()
            if not stop:
                raise ForbiddenError("Student is not on this run")

            record = participation_dao.get_for_student(conn, str(run["id"]), student_id)
            reversed_what: str | None = None

            if record and (record["dropped_off_at"] or record["handover_at"]):
                if str(record["acting_driver_id"] or "") != str(driver_id):
                    raise ForbiddenError("That was recorded by a different driver")
                reversed_what = (
                    "handover" if record["handover_at"] and not record["dropped_off_at"]
                    else "dropoff"
                )
                participation_dao.reverse_outcome(conn, str(run["id"]), student_id)
                conn.execute(
                    "update live_students set status = 'on-bus' where id = %s", (student_id,)
                )
            elif AbsenceDao().reverse_driver_absence(conn, student_id, str(driver_id)):
                reversed_what = "absence"
                # Back onto the run as boarded: the child was aboard, which is
                # why the driver could mark them absent from this run at all.
                name = conn.execute(
                    "select name from live_students where id = %s", (student_id,)
                ).fetchone()
                participation_dao.record_boarding(
                    conn, str(run["id"]), student_id, name["name"], str(driver_id),
                    presumed=(run["type"] == "afternoon"),
                )

            if not reversed_what:
                raise ConflictError(
                    "Nothing of yours to undo for this child on this run."
                )

            counted = (
                participation_dao.count_dropped_off(conn, str(run["id"]))
                if run["type"] == "afternoon"
                else participation_dao.count_boarded(conn, str(run["id"]))
            )
            run = conn.execute(
                "update live_runs set students_boarded = %s where id = %s returning *",
                (counted, run["id"]),
            ).fetchone()
            student = conn.execute(
                "select * from live_students where id = %s", (student_id,)
            ).fetchone()
        return dict(student), dict(run), reversed_what

    def mark_student_absent(
        self, scope: SchoolScope, student_id: str, *, whole_day: bool = False
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Driver marks a roster student absent mid-run (U8/R17-R20); returns
        (student, run snapshot enriched with route_name/bus_name for the
        caller's post-commit notification + incident tasks).

        Scoped to the period the driver actually witnessed. It used to write a
        whole-day absence from a single run, which said something the driver was
        in no position to know: a child who is not at their morning stop may
        well be riding home that afternoon, and the whole-day row struck them off
        the afternoon roster too — so the bus never stopped for them, and the
        record blamed the child for a stop nobody made. Whole-day coverage now
        needs the driver to say so (`whole_day`), which is a claim they can only
        make from something a parent told them.

        Three fields move independently on a repeat mark, and the distinction is
        the point:

        - **scope** is coverage, and only ever widens (R19). Morning marked, then
          afternoon, means the child was absent all day; nothing here may narrow
          an existing row, because the earlier writer saw something this one did
          not.
        - **source** is precedence: office, then parent, then driver (R20). A
          driver mark no longer re-attributes an office or parent row. The old
          ratchet ran the other way and let the last actor at the stop overwrite
          a cancellation the office had recorded deliberately.
        - **marked_period** is the witness: which period someone individually
          marked, surviving the collapse to 'day' that widening causes. Without
          it the run report cannot tell an afternoon non-boarding from a morning
          no-show, and the derivation cannot tell a driver's period observation
          from a parent's partial cancellation — only the first of which is
          evidence the child is not travelling at all.

        marked_by follows source rather than the last write: it is the row's
        actor, and reverse_driver_absence keys on the pair.
        """
        from app.core.errors import ForbiddenError

        reason = "Marked absent by driver at stop"
        driver_id = scope.user_id
        with get_connection(scope) as conn:
            bus_id = self._bus_id_for_driver(conn, scope)
            run = self.find_active_run_today(conn, bus_id) if bus_id else None
            if not run or str(run["driver_id"]) != str(driver_id):
                raise ForbiddenError("No active run for this driver")
            stop = conn.execute(
                "select 1 from run_stops where run_id = %s and student_id = %s limit 1",
                (run["id"], student_id),
            ).fetchone()
            if not stop:
                raise ForbiddenError("Student is not on this run")
            period = "day" if whole_day else run["type"]
            # school_id stamped from the driver's scope (U7) — the roster
            # check above proved the student rides this school's run.
            conn.execute(
                """
                insert into live_student_absences as a
                    (student_id, absence_date, reason, marked_by, scope, source,
                     marked_period, school_id)
                values (
                    %(student_id)s, (now() at time zone 'Africa/Nairobi')::date,
                    %(reason)s, %(driver)s, %(period)s, 'driver', %(period)s,
                    %(school_id)s
                )
                on conflict (student_id, absence_date) do update set
                    reason = excluded.reason,
                    -- Coverage widens, never narrows: two different scopes union
                    -- to the whole day (R19).
                    scope = case
                        when a.scope = excluded.scope then a.scope
                        else 'day'
                    end,
                    -- Office and parent rows keep their attribution (R20).
                    source = case
                        when a.source in ('admin', 'parent') then a.source
                        else excluded.source
                    end,
                    marked_by = case
                        when a.source in ('admin', 'parent') then a.marked_by
                        else excluded.marked_by
                    end,
                    -- Both periods individually witnessed means the whole day
                    -- was, which is a stronger claim than either mark alone.
                    marked_period = case
                        when a.marked_period is null then excluded.marked_period
                        when a.marked_period = excluded.marked_period then a.marked_period
                        else 'day'
                    end,
                    -- Heal a NULL left by a pre-U7 writer; never re-stamp.
                    school_id = coalesce(a.school_id, excluded.school_id)
                """,
                {
                    "student_id": student_id,
                    "reason": reason,
                    "driver": driver_id,
                    "period": period,
                    "school_id": scope.school_id,
                },
            )
            student = conn.execute(
                "update live_students set status = 'absent' where id = %s returning *",
                (student_id,),
            ).fetchone()
            inserted = conn.execute(
                """
                insert into run_absences (run_id, student_id, student_name, reason, period,
                                          school_id)
                values (%s, %s, %s, %s, %s,
                        (select school_id from live_runs where id = %s))
                on conflict (run_id, student_id) do nothing
                returning id
                """,
                (run["id"], student_id, student["name"], reason, period, run["id"]),
            ).fetchone()
            # A child marked absent was not aboard, so their participation goes
            # (U2). On an afternoon run this retracts the presumed board the
            # auto-board wrote — the correction that presumption exists to allow.
            participation_dao.clear_for_student(conn, str(run["id"]), student_id)
            boarded_count = (
                participation_dao.count_dropped_off(conn, str(run["id"]))
                if run["type"] == "afternoon"
                else participation_dao.count_boarded(conn, str(run["id"]))
            )
            run = conn.execute(
                "update live_runs set students_boarded = %s where id = %s returning *",
                (boarded_count, run["id"]),
            ).fetchone()
            names = conn.execute(
                """
                select b.name as bus_name, rt.name as route_name
                from live_runs r
                left join live_buses b on b.id = r.bus_id
                left join live_routes rt on rt.id = r.route_id
                where r.id = %s
                """,
                (run["id"],),
            ).fetchone()
        run = dict(run)
        run["bus_name"] = names["bus_name"] if names else None
        run["route_name"] = names["route_name"] if names else None
        # newly_recorded lets the API layer keep the school-side incident
        # idempotent per run+student: repeat taps re-notify nothing.
        run["newly_recorded"] = inserted is not None
        # What the parent message may claim (U8/R21). Not run["type"]: an
        # explicit whole-day confirmation says more than this run does.
        run["absence_period"] = period
        return dict(student), run

    # _count_run_students_with_status is gone (U2): every counter now reads
    # participation. Counting a status column that no longer tracks boarding
    # would have frozen students_boarded at whatever the last sweep left.

    def _bus_id_for_driver(self, conn, scope: SchoolScope) -> str | None:
        # The driver's bus at the driver's OWN school (U7): a same-named
        # assignment at another school can never resolve here.
        row = conn.execute(
            "select id from live_buses where driver_id = %s and school_id = %s limit 1",
            (scope.user_id, scope.school_id),
        ).fetchone()
        return row["id"] if row else None
