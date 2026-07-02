from typing import Any

from app.core.db import get_connection
from app.dao.absence_dao import absent_student_ids


class RunDao:
    # --- admin runs --------------------------------------------------------

    def list_runs(self, active: bool = False) -> list[dict[str, Any]]:
        """All runs, newest first. active=True narrows to today's (Africa/
        Nairobi) non-completed runs — the dashboard's Active Runs card (R5),
        same predicate shape as find_active_run_today."""
        where = (
            "where r.status <> 'completed' "
            "and r.date = (now() at time zone 'Africa/Nairobi')::date"
            if active
            else ""
        )
        with get_connection() as conn:
            rows = conn.execute(
                f"""
                select r.*, b.name as bus_name, b.plate_number, rt.name as route_name
                from live_runs r
                left join live_buses b on b.id = r.bus_id
                left join live_routes rt on rt.id = r.route_id
                {where}
                order by r.date desc, r.created_at desc
                """
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

    def create_run(self, data: dict) -> dict[str, Any]:
        with get_connection() as conn:
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
                data,
            ).fetchone()
        return dict(row)

    def update_run(self, run_id: str, data: dict) -> dict[str, Any] | None:
        with get_connection() as conn:
            current = conn.execute(
                "select date from live_runs where id = %s", (run_id,)
            ).fetchone()
            if not current:
                return None
            # Re-run the create_run conflict check when the resulting state is
            # non-completed with a bus, excluding this run, so admins get the
            # friendly 409 instead of the raw unique-violation message (R3).
            # The resulting date falls back to the run's current date,
            # matching coalesce(%(date)s, date) in the update below.
            if data.get("bus_id") and (data.get("status") or "in-progress") != "completed":
                self._assert_no_active_run_conflict(
                    conn, data["bus_id"], data.get("date") or current["date"],
                    exclude_run_id=run_id,
                )
            row = conn.execute(
                """
                update live_runs set
                    bus_id=%(bus_id)s, route_id=%(route_id)s, type=coalesce(%(type)s,'morning'),
                    date=coalesce(%(date)s, date), start_time=%(start_time)s, end_time=%(end_time)s,
                    status=coalesce(%(status)s,'in-progress'), total_stops=coalesce(%(total_stops)s,0),
                    stops_completed=coalesce(%(stops_completed)s,0),
                    total_students=coalesce(%(total_students)s,0),
                    students_boarded=coalesce(%(students_boarded)s,0), incidents=coalesce(%(incidents)s,0)
                where id=%(id)s returning *
                """,
                {**data, "id": run_id},
            ).fetchone()
        return dict(row) if row else None

    def delete_run(self, run_id: str) -> None:
        """Delete a run. Deleting a NON-completed run also resets its roster's
        'on-bus' students back to 'at-school' in the same transaction — R28's
        recovery path (admin deletes a mistakenly started run) must not strand
        an auto-boarded afternoon roster on a phantom bus."""
        with get_connection() as conn:
            run = conn.execute(
                "select status from live_runs where id = %s", (run_id,)
            ).fetchone()
            if run and run["status"] != "completed":
                conn.execute(
                    """
                    update live_students set status = 'at-school'
                    where status = 'on-bus'
                      and id in (
                          select student_id from run_stops
                          where run_id = %s and student_id is not null
                      )
                    """,
                    (run_id,),
                )
            conn.execute("delete from live_runs where id = %s", (run_id,))

    def run_report(self, run_id: str) -> dict[str, Any]:
        """Post-run report (R14-R16): the run row + bus/route/driver names +
        the absence snapshot taken at start_run. Legacy runs that predate the
        snapshot (a route but no run_absences rows and no run_stops — e.g.
        admin-created) fall back to live_student_absences on the run's date
        intersected with the route's membership, flagged approximate=True.
        Runs with no route report an empty list, approximate=False."""
        from app.core.errors import NotFoundError

        with get_connection() as conn:
            run = conn.execute(
                """
                select r.*, b.name as bus_name, b.plate_number, rt.name as route_name,
                       u.full_name as driver_name
                from live_runs r
                left join live_buses b on b.id = r.bus_id
                left join live_routes rt on rt.id = r.route_id
                left join app_users u on u.id = r.driver_id
                where r.id = %s
                """,
                (run_id,),
            ).fetchone()
            if not run:
                raise NotFoundError("Run was not found")
            absent = conn.execute(
                """
                select student_id, student_name, reason from run_absences
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
                    absent = conn.execute(
                        """
                        select a.student_id, s.name as student_name, a.reason
                        from live_student_absences a
                        join live_students s on s.id = a.student_id
                        join live_student_routes sr
                            on sr.student_id = a.student_id and sr.route_id = %s
                        where a.absence_date = %s
                        order by s.name asc
                        """,
                        (run["route_id"], run["date"]),
                    ).fetchall()
                    approximate = True
        report = dict(run)
        report["absent_students"] = [dict(a) for a in absent]
        report["approximate"] = approximate
        return report

    # --- driver context ----------------------------------------------------

    def get_driver_context(self, driver_id: str) -> dict[str, Any]:
        with get_connection() as conn:
            bus = conn.execute(
                "select * from live_buses where driver_id = %s order by name asc limit 1", (driver_id,)
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
            active_dict = dict(active) if active else None
            absent_flag_sql = """
                select s.*, exists (
                    select 1 from live_student_absences a
                    where a.student_id = s.id
                      and a.absence_date = (now() at time zone 'Africa/Nairobi')::date
                ) as absent
                from live_students s
            """
            if active_dict:
                students = conn.execute(
                    absent_flag_sql
                    + """
                    where s.id in (
                        select rs.student_id from run_stops rs
                        where rs.run_id = %s and rs.student_id is not null
                    )
                    order by s.name asc
                    """,
                    (active_dict["id"],),
                ).fetchall()
            else:
                students = conn.execute(
                    absent_flag_sql + " where s.bus_id = %s order by s.name asc",
                    (bus["id"],),
                ).fetchall()
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
            "completed_route_ids_today": [str(r["route_id"]) for r in completed_today],
        }

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

    def start_run(self, driver_id: str, route_id: str) -> dict[str, Any]:
        """Atomic: validate, snapshot stops, create the in-progress run."""
        from app.core.errors import ConflictError, ForbiddenError

        with get_connection() as conn:
            bus = conn.execute(
                "select * from live_buses where driver_id = %s limit 1", (driver_id,)
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
            # Drop stops for students marked absent today (#7), then renumber so
            # the snapshot's stop_orders stay contiguous (arrive_next_stop walks
            # them one at a time).
            absent = absent_student_ids(conn)
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
                    "insert into run_stops (run_id, stop_order, name, scheduled_time, lat, lng, is_school_gate, student_id) "
                    "values (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (run["id"], order_map[s["stop_order"]], s["name"], s["scheduled_time"], s["lat"],
                     s["lng"], s["is_school_gate"], s["student_id"]),
                )
            # Both run types operate on the RUN's roster (the run_stops student
            # set) — never the derived live_students.bus_id (see KTDs).
            #
            # Stale-'absent' self-heal: absences are per-date and nothing else
            # clears the status, so a roster student still 'absent' from a
            # previous day (no today-absence row) must not stay stuck.
            if route["type"] == "afternoon":
                # Auto-board the roster (R32): every roster student without a
                # today-absence goes 'on-bus' directly in SQL — no per-student
                # 'student-boarded' pushes; the run-level 'on-way-home'
                # notification covers the start. Stale-'absent' students join
                # the auto-board set here.
                conn.execute(
                    """
                    update live_students set status = 'on-bus'
                    where id in (
                        select student_id from run_stops
                        where run_id = %s and student_id is not null
                    )
                      and id not in (
                          select student_id from live_student_absences
                          where absence_date = (now() at time zone 'Africa/Nairobi')::date
                      )
                    """,
                    (run["id"],),
                )
            else:
                # Morning: reset stale-'absent' roster students to 'at-school'
                # so they board normally.
                conn.execute(
                    """
                    update live_students set status = 'at-school'
                    where status = 'absent'
                      and id in (
                          select student_id from run_stops
                          where run_id = %s and student_id is not null
                      )
                      and id not in (
                          select student_id from live_student_absences
                          where absence_date = (now() at time zone 'Africa/Nairobi')::date
                      )
                    """,
                    (run["id"],),
                )
            # Recount students_boarded (never increment): morning counts who is
            # on the bus, afternoon counts confirmed drop-offs — both 0 at
            # start in the normal case, but recomputing keeps the counter
            # honest even for roster students carried over in an odd state.
            count_status = "dropped-off" if route["type"] == "afternoon" else "on-bus"
            boarded_count = self._count_run_students_with_status(conn, run["id"], count_status)
            run = conn.execute(
                "update live_runs set students_boarded = %s where id = %s returning *",
                (boarded_count, run["id"]),
            ).fetchone()
            # Snapshot today's absences for the run report (R14-R16): today's
            # absent students intersected with the ROUTE's membership — never
            # the derived bus roster (see KTDs). student_name is denormalized
            # because run_absences.student_id is ON DELETE SET NULL; a
            # name-less snapshot would rot after student deletion.
            conn.execute(
                """
                insert into run_absences (run_id, student_id, student_name, reason)
                select %s, s.id, s.name, a.reason
                from live_student_absences a
                join live_students s on s.id = a.student_id
                join live_student_routes sr
                    on sr.student_id = a.student_id and sr.route_id = %s
                where a.absence_date = (now() at time zone 'Africa/Nairobi')::date
                on conflict (run_id, student_id) do nothing
                """,
                (run["id"], route_id),
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

    def arrive_next_stop(self, driver_id: str, run_id: str) -> dict[str, Any]:
        from app.core.errors import ConflictError, ForbiddenError

        with get_connection() as conn:
            run = conn.execute("select * from live_runs where id = %s", (run_id,)).fetchone()
            if not run or str(run["driver_id"]) != str(driver_id):
                raise ForbiddenError("Run is not owned by this driver")
            if run["status"] == "completed":
                raise ConflictError("Run is already completed")
            new_completed = min(run["stops_completed"] + 1, run["total_stops"])
            conn.execute(
                "update live_runs set stops_completed = %s where id = %s", (new_completed, run_id)
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
                    inc = conn.execute(
                        """
                        insert into live_incidents
                            (run_id, driver_id, driver_name, bus_id, bus_name, type, description)
                        values (%s, %s, %s, %s, %s, 'arrival', %s)
                        returning *
                        """,
                        (run_id, driver_id, bus["driver_name"] if bus else None, run["bus_id"],
                         bus["name"] if bus else None,
                         f"{bus['name'] if bus else 'Bus'} has arrived at {gate['name']}."),
                    ).fetchone()
                    conn.execute(
                        "update live_runs set incidents = incidents + 1 where id = %s", (run_id,)
                    )
                    arrival_incident = dict(inc)
            updated = conn.execute("select * from live_runs where id = %s", (run_id,)).fetchone()
        return {"run": dict(updated), "arrival_incident": arrival_incident}

    def end_run(self, driver_id: str, run_id: str) -> dict[str, Any]:
        from app.core.errors import ConflictError, ForbiddenError

        with get_connection() as conn:
            run = conn.execute("select * from live_runs where id = %s", (run_id,)).fetchone()
            if not run or str(run["driver_id"]) != str(driver_id):
                raise ForbiddenError("Run is not owned by this driver")
            if run["status"] == "completed":
                raise ConflictError("Run is already completed")
            # Capture who was actually on the bus before the sweep wipes it;
            # notifications must only assert arrival for boarded students.
            boarded = conn.execute(
                """
                select distinct s.id from live_students s
                where s.id in (select student_id from run_stops where run_id = %s and student_id is not null)
                  and s.status = 'on-bus'
                """,
                (run_id,),
            ).fetchall()
            # Persist students_boarded as the final pre-sweep count over the
            # run's own roster (run_stops), per run type: morning counts who
            # is on the bus; afternoon counts who was dropped off at their
            # stop (tap-time drop-offs) before the sweep rewrites statuses.
            final_status = "dropped-off" if run["type"] == "afternoon" else "on-bus"
            final_count = self._count_run_students_with_status(conn, run_id, final_status)
            conn.execute(
                "update live_runs set status='completed', stops_completed=total_stops, "
                "students_boarded=%s, "
                "end_time=to_char(now() at time zone 'Africa/Nairobi','HH24:MI') where id=%s",
                (final_count, run_id),
            )
            # Sweep the run's roster (run_stops student_ids), skipping absent.
            sweep_status = "dropped-off" if run["type"] == "afternoon" else "at-school"
            conn.execute(
                """
                update live_students set status = %s
                where id in (select distinct student_id from run_stops where run_id = %s and student_id is not null)
                    and status <> 'absent'
                """,
                (sweep_status, run_id),
            )
            # Clear the bus live position.
            conn.execute(
                "update live_buses set current_lat = null, current_lng = null where id = %s",
                (run["bus_id"],),
            )
            updated = conn.execute("select * from live_runs where id = %s", (run_id,)).fetchone()
        result = dict(updated)
        result["boarded_student_ids"] = [str(b["id"]) for b in boarded]
        return result

    def write_position(self, driver_id: str, lat: float, lng: float) -> dict[str, Any]:
        """Record the bus position; returns the active run snapshot."""
        from app.core.errors import ForbiddenError

        with get_connection() as conn:
            run = self.find_active_run_today(conn, self._bus_id_for_driver(conn, driver_id))
            if not run or str(run["driver_id"]) != str(driver_id):
                raise ForbiddenError("No active run for this driver")
            conn.execute(
                "update live_buses set current_lat = %s, current_lng = %s where id = %s",
                (lat, lng, run["bus_id"]),
            )
        return dict(run)

    def toggle_boarding(
        self, driver_id: str, student_id: str, on_bus: bool
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Board a student on a morning run; returns (student, run snapshot).

        Boarding is morning-only (afternoon runs auto-board and confirm via
        dropoff_student) and one-way: un-boarding is disabled because a
        boarded push already went out and silently retracting a safety
        assertion is worse than routing the fix through the office (R26).
        """
        from app.core.errors import ConflictError, ForbiddenError

        with get_connection() as conn:
            bus_id = self._bus_id_for_driver(conn, driver_id)
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
            # Recount students_boarded from the run's own roster in the SAME
            # transaction — never increment/decrement, so repeated taps and
            # board/unboard cycles can't drift the counter (R15).
            boarded_count = self._count_run_students_with_status(conn, run["id"], "on-bus")
            run = conn.execute(
                "update live_runs set students_boarded = %s where id = %s returning *",
                (boarded_count, run["id"]),
            ).fetchone()
        return dict(row), dict(run)

    def dropoff_student(
        self, driver_id: str, student_id: str
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

        with get_connection() as conn:
            bus_id = self._bus_id_for_driver(conn, driver_id)
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
            student = conn.execute(
                "select * from live_students where id = %s", (student_id,)
            ).fetchone()
            if not student or student["status"] != "on-bus":
                raise ConflictError("Student is not on the bus")
            row = conn.execute(
                "update live_students set status = 'dropped-off' where id = %s returning *",
                (student_id,),
            ).fetchone()
            dropped_count = self._count_run_students_with_status(conn, run["id"], "dropped-off")
            run = conn.execute(
                "update live_runs set students_boarded = %s where id = %s returning *",
                (dropped_count, run["id"]),
            ).fetchone()
        return dict(row), dict(run)

    def mark_student_absent(
        self, driver_id: str, student_id: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Driver marks a roster student absent mid-run (R30); returns
        (student, run snapshot enriched with route_name/bus_name for the
        caller's post-commit notification + incident tasks).

        Single transaction: upsert today's live_student_absences row
        (marked_by = the driver; a repeat mark is a reason edit, never a
        500), append the run_absences snapshot (on conflict do nothing —
        one row per run+student), set status 'absent', recount
        students_boarded from the run's roster.
        """
        from app.core.errors import ForbiddenError

        reason = "Marked absent by driver at stop"
        with get_connection() as conn:
            bus_id = self._bus_id_for_driver(conn, driver_id)
            run = self.find_active_run_today(conn, bus_id) if bus_id else None
            if not run or str(run["driver_id"]) != str(driver_id):
                raise ForbiddenError("No active run for this driver")
            stop = conn.execute(
                "select 1 from run_stops where run_id = %s and student_id = %s limit 1",
                (run["id"], student_id),
            ).fetchone()
            if not stop:
                raise ForbiddenError("Student is not on this run")
            conn.execute(
                """
                insert into live_student_absences (student_id, absence_date, reason, marked_by)
                values (%s, (now() at time zone 'Africa/Nairobi')::date, %s, %s)
                on conflict (student_id, absence_date)
                do update set reason = excluded.reason, marked_by = excluded.marked_by
                """,
                (student_id, reason, driver_id),
            )
            student = conn.execute(
                "update live_students set status = 'absent' where id = %s returning *",
                (student_id,),
            ).fetchone()
            inserted = conn.execute(
                """
                insert into run_absences (run_id, student_id, student_name, reason)
                values (%s, %s, %s, %s)
                on conflict (run_id, student_id) do nothing
                returning id
                """,
                (run["id"], student_id, student["name"], reason),
            ).fetchone()
            count_status = "dropped-off" if run["type"] == "afternoon" else "on-bus"
            boarded_count = self._count_run_students_with_status(conn, run["id"], count_status)
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
        return dict(student), run

    def _count_run_students_with_status(self, conn, run_id: str, status: str) -> int:
        """Distinct students on the run's own roster (run_stops) currently in
        ``status``. The run-scoped roster, never the derived bus roster."""
        row = conn.execute(
            """
            select count(distinct s.id) as n from live_students s
            where s.id in (
                select student_id from run_stops where run_id = %s and student_id is not null
            ) and s.status = %s
            """,
            (run_id, status),
        ).fetchone()
        return row["n"]

    def _bus_id_for_driver(self, conn, driver_id: str) -> str | None:
        row = conn.execute(
            "select id from live_buses where driver_id = %s limit 1", (driver_id,)
        ).fetchone()
        return row["id"] if row else None
