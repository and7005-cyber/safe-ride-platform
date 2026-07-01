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
        with get_connection() as conn:
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
                return {"bus": None, "routes": [], "active_run": None, "students": []}
            routes = conn.execute(
                "select * from live_routes where bus_id = %s order by type asc", (bus["id"],)
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
            students = conn.execute(
                "select * from live_students where bus_id = %s order by name asc", (bus["id"],)
            ).fetchall()
            active_dict = dict(active) if active else None
            run_stops = []
            if active_dict:
                # Skip stops for students marked absent today so the driver
                # doesn't stop for them (#7). The school gate (student_id null)
                # always remains.
                run_stops = [
                    dict(s)
                    for s in conn.execute(
                        """
                        select rs.* from run_stops rs
                        where rs.run_id = %s
                          and (rs.student_id is null or rs.student_id not in (
                              select student_id from live_student_absences
                              where absence_date = (now() at time zone 'Africa/Nairobi')::date
                          ))
                        order by rs.stop_order asc
                        """,
                        (active_dict["id"],),
                    ).fetchall()
                ]
        return {
            "bus": dict(bus),
            "routes": [dict(r) for r in routes],
            "active_run": active_dict,
            "run_stops": run_stops,
            "students": [dict(s) for s in students],
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
            students = conn.execute(
                """
                select count(*) as n from live_students
                where bus_id = %s and id not in (
                    select student_id from live_student_absences
                    where absence_date = (now() at time zone 'Africa/Nairobi')::date
                )
                """,
                (bus["id"],),
            ).fetchone()

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
                 len(distinct_orders), students["n"]),
            ).fetchone()
            for s in kept:
                conn.execute(
                    "insert into run_stops (run_id, stop_order, name, scheduled_time, lat, lng, is_school_gate, student_id) "
                    "values (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (run["id"], order_map[s["stop_order"]], s["name"], s["scheduled_time"], s["lat"],
                     s["lng"], s["is_school_gate"], s["student_id"]),
                )
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
        """Toggle a student's boarding state; returns (student, run snapshot)."""
        from app.core.errors import ConflictError, ForbiddenError

        with get_connection() as conn:
            bus_id = self._bus_id_for_driver(conn, driver_id)
            run = self.find_active_run_today(conn, bus_id) if bus_id else None
            if not run or str(run["driver_id"]) != str(driver_id):
                raise ForbiddenError("No active run for this driver")
            stop = conn.execute(
                "select * from run_stops where run_id = %s and student_id = %s limit 1",
                (run["id"], student_id),
            ).fetchone()
            if not stop:
                raise ForbiddenError("Student is not on this run")
            if stop["stop_order"] > run["stops_completed"]:
                raise ConflictError("Stop has not been reached yet")
            new_status = "on-bus" if on_bus else "at-school"
            row = conn.execute(
                "update live_students set status = %s where id = %s returning *",
                (new_status, student_id),
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
