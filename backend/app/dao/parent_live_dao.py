from typing import Any

from app.core.db import get_connection
from app.dao.status_sql import display_status_case


def _mask_stop_name(name: str, is_own: bool, is_gate: bool) -> str:
    """Privacy: strip leading house-number digits for stops that aren't the
    family's own or the school gate (matches live /parent/track)."""
    if is_own or is_gate or not name:
        return name
    stripped = name.lstrip("0123456789 ").strip()
    return stripped or "Stop"


class ParentLiveDao:
    def _child_ids(self, conn, parent_id: str) -> list[str]:
        rows = conn.execute(
            "select student_id from live_parent_students where parent_id = %s", (parent_id,)
        ).fetchall()
        return [r["student_id"] for r in rows]

    def list_children(self, parent_id: str) -> list[dict[str, Any]]:
        """The parent's children, each with a derived ``display_status``.

        ``display_status`` is computed at read time, never stored (the raw
        ``status`` field stays untouched in the payload — admin keeps the
        operational value). The branch-by-branch derivation lives in
        ``app.dao.status_sql``, shared with the admin students list.

        Each row also carries ``cancellation`` (U5): today's parent-sourced
        absence as ``{scope, withdrawable}``, else None (a staff-sourced
        absence is not a cancellation — it surfaces through display_status
        when whole-day). ``withdrawable`` mirrors the withdraw guard: some
        covered run type still has NO run row today involving the child
        (run-row existence, not the active-run predicate — completion must
        not reopen withdrawal). For a merged 'day' row that means at least
        one half is still withdrawable, which is exactly when the UI should
        offer the action (U13's dialog picks the half).
        """
        with get_connection() as conn:
            ids = self._child_ids(conn, parent_id)
            if not ids:
                return []
            rows = conn.execute(
                f"""
                select s.*, b.name as bus_name, b.driver_name, b.driver_phone,
                       b.current_lat as bus_current_lat, b.current_lng as bus_current_lng,
                       sc.name as school_name,
                       {display_status_case("s")} as display_status,
                       a.scope as cancel_scope, a.source as cancel_source,
                       case when a.source = 'parent' then exists (
                           select 1
                           from (values ('morning'), ('afternoon')) as covered(run_type)
                           where (a.scope = 'day' or a.scope = covered.run_type)
                             and not exists (
                                 select 1 from live_runs r
                                 where r.date = (now() at time zone 'Africa/Nairobi')::date
                                   and r.type = covered.run_type
                                   and (
                                       exists (select 1 from run_stops rs
                                               where rs.run_id = r.id
                                                 and rs.student_id = s.id)
                                       or exists (select 1 from live_student_routes sr
                                                  where sr.route_id = r.route_id
                                                    and sr.student_id = s.id)
                                   )
                             )
                       ) end as cancel_withdrawable
                from live_students s
                left join live_buses b on b.id = s.bus_id
                left join live_schools sc on sc.id = s.school_id
                left join live_student_absences a
                    on a.student_id = s.id
                   and a.absence_date = (now() at time zone 'Africa/Nairobi')::date
                where s.id = any(%s)
                order by s.name asc
                """,
                (ids,),
            ).fetchall()
        children = []
        for r in rows:
            child = dict(r)
            scope = child.pop("cancel_scope")
            source = child.pop("cancel_source")
            withdrawable = child.pop("cancel_withdrawable")
            child["cancellation"] = (
                {"scope": scope, "withdrawable": bool(withdrawable)}
                if source == "parent"
                else None
            )
            children.append(child)
        return children

    def cancel_ride_context(self, parent_id: str, student_id: str) -> dict[str, Any] | None:
        """Ownership check + guard snapshot for Cancel-a-Ride (U5), one read.

        None means the student is not linked to this parent — the 404
        boundary, matching get_track. Ownership is the SOLE boundary and it
        evaluates before any guard: student UUIDs are harvestable by any
        authenticated token, and a guard's 409 fired first would leak another
        child's live on-bus state. Otherwise returns:

          student      {id, name, status, bus_id}
          absence      today's absence row {scope, source, reason} or None
          runs         today's run rows involving the child, each
                       {type, status} — involvement is run_stops membership
                       OR the run's route being one of the child's routes
                       (the child may have been excluded from run_stops at
                       snapshot time by this very absence)
          route_buses  {'morning'|'afternoon': {bus_id, bus_name}} from the
                       child's route assignments (first route per type)

        Guards evaluated over this snapshot are best-effort pre-reads for
        friendly messages; the atomic set_scope / withdraw_scope statements
        stay the authority under concurrency.
        """
        with get_connection() as conn:
            ids = [str(cid) for cid in self._child_ids(conn, parent_id)]
            if str(student_id) not in ids:
                return None  # ownership: not this parent's child
            student = conn.execute(
                "select id, name, status, bus_id from live_students where id = %s",
                (student_id,),
            ).fetchone()
            if not student:
                return None  # link row outlived the student: same 404
            absence = conn.execute(
                """
                select scope, source, reason from live_student_absences
                where student_id = %s
                  and absence_date = (now() at time zone 'Africa/Nairobi')::date
                """,
                (student_id,),
            ).fetchone()
            runs = conn.execute(
                """
                select r.type, r.status from live_runs r
                where r.date = (now() at time zone 'Africa/Nairobi')::date
                  and (
                      exists (select 1 from run_stops rs
                              where rs.run_id = r.id and rs.student_id = %s)
                      or exists (select 1 from live_student_routes sr
                                 where sr.route_id = r.route_id and sr.student_id = %s)
                  )
                """,
                (student_id, student_id),
            ).fetchall()
            route_rows = conn.execute(
                """
                select r.type, b.id as bus_id, b.name as bus_name
                from live_student_routes sr
                join live_routes r on r.id = sr.route_id
                left join live_buses b on b.id = r.bus_id
                where sr.student_id = %s
                order by r.created_at asc
                """,
                (student_id,),
            ).fetchall()
        route_buses: dict[str, dict[str, Any]] = {}
        for r in route_rows:
            route_buses.setdefault(
                r["type"], {"bus_id": r["bus_id"], "bus_name": r["bus_name"]}
            )
        return {
            "student": dict(student),
            "absence": dict(absence) if absence else None,
            "runs": [dict(r) for r in runs],
            "route_buses": route_buses,
        }

    def get_track(self, parent_id: str, student_id: str) -> dict[str, Any] | None:
        with get_connection() as conn:
            ids = [str(cid) for cid in self._child_ids(conn, parent_id)]
            if str(student_id) not in ids:
                return None  # ownership: not this parent's child
            student = conn.execute(
                """
                select s.*, b.name as bus_name, b.driver_name, b.driver_phone,
                       b.current_lat as bus_current_lat, b.current_lng as bus_current_lng
                from live_students s left join live_buses b on b.id = s.bus_id
                where s.id = %s
                """,
                (student_id,),
            ).fetchone()
            if not student:
                return None
            # The student's current (morning) route + its stops.
            route = conn.execute(
                """
                select r.* from live_routes r
                join live_student_routes sr on sr.route_id = r.id
                where sr.student_id = %s and r.bus_id = %s
                order by (r.type <> 'morning') asc, r.type asc limit 1
                """,
                (student_id, student["bus_id"]),
            ).fetchone()
            stops = []
            if route:
                raw = conn.execute(
                    "select * from live_route_stops where route_id = %s order by stop_order asc, name asc",
                    (route["id"],),
                ).fetchall()
                # Siblings can share a stop_order (one row per student): prefer
                # the requesting student's own row so is_own survives the dedup.
                raw = sorted(
                    raw,
                    key=lambda r: (
                        r["stop_order"],
                        str(r["student_id"]) != str(student_id),
                        r["name"] or "",
                    ),
                )
                seen_orders = set()
                for s in raw:
                    if s["stop_order"] in seen_orders:
                        continue
                    seen_orders.add(s["stop_order"])
                    is_own = str(s["student_id"]) == str(student_id)
                    stops.append({
                        "stop_order": s["stop_order"],
                        "name": _mask_stop_name(s["name"], is_own, s["is_school_gate"]),
                        "is_school_gate": s["is_school_gate"],
                        "is_own": is_own,
                        "lat": s["lat"],
                        "lng": s["lng"],
                    })
            run = None
            if student["bus_id"]:
                r = conn.execute(
                    """
                    select * from live_runs
                    where bus_id = %s and date = (now() at time zone 'Africa/Nairobi')::date
                    order by created_at desc limit 1
                    """,
                    (student["bus_id"],),
                ).fetchone()
                run = dict(r) if r else None
        return {"student": dict(student), "stops": stops, "run": run}

    def list_alerts(
        self, parent_id: str, window_hours: int | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Incidents on the children's buses, newest first.

        window_hours, when set, keeps only rows newer than that rolling window
        (24 = Recent, 168 = History); limit is hard-capped at 200 (R35/R36).
        """
        limit = max(1, min(int(limit), 200))
        with get_connection() as conn:
            ids = self._child_ids(conn, parent_id)
            if not ids:
                return []
            bus_rows = conn.execute(
                "select distinct bus_id from live_students where id = any(%s) and bus_id is not null",
                (ids,),
            ).fetchall()
            bus_ids = [r["bus_id"] for r in bus_rows]
            if not bus_ids:
                return []
            window_sql = ""
            params: list = [bus_ids]
            if window_hours is not None:
                window_sql = " and created_at > now() - (%s || ' hours')::interval"
                params.append(int(window_hours))
            params.append(limit)
            # student_id-stamped incidents are child-specific (absence reports
            # for the school): only the admin Alerts page may see them — no
            # other parent on the bus learns a named child's absence here.
            rows = conn.execute(
                f"""
                select id, driver_name, bus_id, bus_name, type, run_type, description, created_at
                from live_incidents
                where bus_id = any(%s) and student_id is null{window_sql}
                order by created_at desc
                limit %s
                """,
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    def get_profile(self, parent_id: str) -> dict[str, Any]:
        with get_connection() as conn:
            user = conn.execute(
                "select id, email, full_name, phone from app_users where id = %s", (parent_id,)
            ).fetchone()
            children = self.list_children(parent_id)
        return {"profile": dict(user) if user else None, "children": children}
