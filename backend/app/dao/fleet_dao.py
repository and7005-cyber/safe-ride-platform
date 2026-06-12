from typing import Any

from app.core.db import get_connection


def regenerate_route_stops(conn, route_id: str) -> None:
    """Rebuild a route's stops from its assigned students + the school gate.

    One stop per unique home location (siblings share an order); students with
    no coordinates get a stop at the school; the school gate is always last.
    A route with no school generates no gate stop.
    """
    route = conn.execute(
        "select r.id, r.school_id, s.name as school_name, s.lat as school_lat, s.lng as school_lng "
        "from live_routes r left join live_schools s on s.id = r.school_id where r.id = %s",
        (route_id,),
    ).fetchone()
    if not route:
        return

    students = conn.execute(
        """
        select st.id, st.name, st.home_lat, st.home_lng, st.pickup_time
        from live_student_routes sr
        join live_students st on st.id = sr.student_id
        where sr.route_id = %s
        order by coalesce(st.pickup_time, '99:99') asc, st.name asc
        """,
        (route_id,),
    ).fetchall()

    conn.execute("delete from live_route_stops where route_id = %s", (route_id,))

    # Group students into ordered stops by location key (coords, or 'school'
    # for coordinate-less students).
    order_by_key: dict[str, int] = {}
    next_order = 0
    rows: list[tuple] = []
    for st in students:
        if st["home_lat"] is None or st["home_lng"] is None:
            key = "school"
            lat, lng = route["school_lat"], route["school_lng"]
            name = "School Pickup"
        else:
            key = f"{st['home_lat']:.6f},{st['home_lng']:.6f}"
            lat, lng = st["home_lat"], st["home_lng"]
            name = (st["name"].split()[-1] + " Stop") if st["name"] else "Stop"
        if key not in order_by_key:
            next_order += 1
            order_by_key[key] = next_order
        rows.append(
            (route_id, name, order_by_key[key], st["pickup_time"], lat, lng, False, st["id"])
        )

    for row in rows:
        conn.execute(
            "insert into live_route_stops (route_id, name, stop_order, scheduled_time, lat, lng, is_school_gate, student_id) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s)",
            row,
        )

    if route["school_id"] is not None:
        gate_order = next_order + 1
        conn.execute(
            "insert into live_route_stops (route_id, name, stop_order, scheduled_time, lat, lng, is_school_gate, student_id) "
            "values (%s, %s, %s, %s, %s, %s, true, null)",
            (route_id, route["school_name"] or "School", gate_order, None, route["school_lat"], route["school_lng"]),
        )


class FleetDao:
    # --- buses -------------------------------------------------------------

    def list_buses(self) -> list[dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute("select * from live_buses order by name asc").fetchall()
        return [dict(r) for r in rows]

    def create_bus(self, data: dict) -> dict[str, Any]:
        with get_connection() as conn:
            row = conn.execute(
                """
                insert into live_buses (name, plate_number, driver_id, driver_name, driver_phone, capacity, status)
                values (%(name)s, %(plate_number)s, %(driver_id)s, %(driver_name)s, %(driver_phone)s,
                        coalesce(%(capacity)s, 45), coalesce(%(status)s, 'idle'))
                returning *
                """,
                data,
            ).fetchone()
        return dict(row)

    def update_bus(self, bus_id: str, data: dict) -> dict[str, Any] | None:
        with get_connection() as conn:
            row = conn.execute(
                """
                update live_buses set
                    name = %(name)s, plate_number = %(plate_number)s, driver_id = %(driver_id)s,
                    driver_name = %(driver_name)s, driver_phone = %(driver_phone)s,
                    capacity = coalesce(%(capacity)s, 45), status = coalesce(%(status)s, 'idle')
                where id = %(id)s returning *
                """,
                {**data, "id": bus_id},
            ).fetchone()
        return dict(row) if row else None

    def delete_bus(self, bus_id: str) -> None:
        with get_connection() as conn:
            conn.execute("delete from live_buses where id = %s", (bus_id,))

    # --- schools -----------------------------------------------------------

    def list_schools(self) -> list[dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute("select * from live_schools order by name asc").fetchall()
        return [dict(r) for r in rows]

    def create_school(self, data: dict) -> dict[str, Any]:
        with get_connection() as conn:
            row = conn.execute(
                "insert into live_schools (name, address, phone, lat, lng) "
                "values (%(name)s, %(address)s, %(phone)s, %(lat)s, %(lng)s) returning *",
                data,
            ).fetchone()
        return dict(row)

    def update_school(self, school_id: str, data: dict) -> dict[str, Any] | None:
        with get_connection() as conn:
            row = conn.execute(
                "update live_schools set name=%(name)s, address=%(address)s, phone=%(phone)s, "
                "lat=%(lat)s, lng=%(lng)s where id=%(id)s returning *",
                {**data, "id": school_id},
            ).fetchone()
            if row:
                route_ids = conn.execute(
                    "select id from live_routes where school_id = %s", (school_id,)
                ).fetchall()
                for r in route_ids:
                    regenerate_route_stops(conn, r["id"])
        return dict(row) if row else None

    def delete_school(self, school_id: str) -> None:
        with get_connection() as conn:
            conn.execute("delete from live_schools where id = %s", (school_id,))

    # --- routes ------------------------------------------------------------

    def list_routes(self) -> list[dict[str, Any]]:
        with get_connection() as conn:
            routes = conn.execute("select * from live_routes order by name asc").fetchall()
            result = []
            for route in routes:
                stops = conn.execute(
                    "select * from live_route_stops where route_id = %s order by stop_order asc, name asc",
                    (route["id"],),
                ).fetchall()
                item = dict(route)
                item["route_stops"] = [dict(s) for s in stops]
                result.append(item)
        return result

    def create_route(self, data: dict) -> dict[str, Any]:
        with get_connection() as conn:
            row = conn.execute(
                "insert into live_routes (name, type, bus_id, school_id) "
                "values (%(name)s, coalesce(%(type)s,'morning'), %(bus_id)s, %(school_id)s) returning *",
                data,
            ).fetchone()
            regenerate_route_stops(conn, row["id"])
        return dict(row)

    def update_route(self, route_id: str, data: dict) -> dict[str, Any] | None:
        with get_connection() as conn:
            row = conn.execute(
                "update live_routes set name=%(name)s, type=coalesce(%(type)s,'morning'), "
                "bus_id=%(bus_id)s, school_id=%(school_id)s where id=%(id)s returning *",
                {**data, "id": route_id},
            ).fetchone()
            if row:
                regenerate_route_stops(conn, route_id)
        return dict(row) if row else None

    def delete_route(self, route_id: str) -> None:
        with get_connection() as conn:
            conn.execute("delete from live_routes where id = %s", (route_id,))
