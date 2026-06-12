from typing import Any

from app.core.db import get_connection


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
        with get_connection() as conn:
            ids = self._child_ids(conn, parent_id)
            if not ids:
                return []
            rows = conn.execute(
                """
                select s.*, b.name as bus_name, b.driver_name, b.driver_phone,
                       b.current_lat as bus_current_lat, b.current_lng as bus_current_lng,
                       sc.name as school_name
                from live_students s
                left join live_buses b on b.id = s.bus_id
                left join live_schools sc on sc.id = s.school_id
                where s.id = any(%s)
                order by s.name asc
                """,
                (ids,),
            ).fetchall()
        return [dict(r) for r in rows]

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

    def list_alerts(self, parent_id: str) -> list[dict[str, Any]]:
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
            rows = conn.execute(
                """
                select id, driver_name, bus_id, bus_name, type, description, created_at
                from live_incidents
                where bus_id = any(%s)
                order by created_at desc
                """,
                (bus_ids,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_profile(self, parent_id: str) -> dict[str, Any]:
        with get_connection() as conn:
            user = conn.execute(
                "select id, email, full_name, phone from app_users where id = %s", (parent_id,)
            ).fetchone()
            children = self.list_children(parent_id)
        return {"profile": dict(user) if user else None, "children": children}
