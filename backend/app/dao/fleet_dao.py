from typing import Any

from app.core.db import get_connection


def _stop_label(st: dict) -> str:
    """A stop is labelled by the student's home address (#1, #14); fall back to
    a surname-based label only when no address is recorded."""
    address = (st.get("home_address") or "").strip()
    if address:
        return address
    return (st["name"].split()[-1] + " Stop") if st.get("name") else "Stop"


def regenerate_route_stops(conn, route_id: str) -> None:
    """Rebuild a route's stops from its assigned students + the school gate.

    One stop per unique home location (siblings share an order). Stops are
    named by home address. Direction matters:

    - **Morning** routes are ordered by pickup time, with the selected school
      as the final stop.
    - **Afternoon** routes start at the school and then visit the student
      stops in reverse pickup order (the morning route, run backwards).

    A student with no coordinates still gets their own stop, labelled by home
    address (so it shows in lists even without a map position) rather than
    collapsing into a generic "School Pickup". A route with no school
    generates no gate stop.
    """
    route = conn.execute(
        "select r.id, r.type, r.school_id, s.name as school_name, s.lat as school_lat, s.lng as school_lng "
        "from live_routes r left join live_schools s on s.id = r.school_id where r.id = %s",
        (route_id,),
    ).fetchone()
    if not route:
        return

    students = conn.execute(
        """
        select st.id, st.name, st.home_address, st.home_lat, st.home_lng, st.pickup_time
        from live_student_routes sr
        join live_students st on st.id = sr.student_id
        where sr.route_id = %s
        order by coalesce(st.pickup_time, '99:99') asc, st.name asc
        """,
        (route_id,),
    ).fetchall()

    conn.execute("delete from live_route_stops where route_id = %s", (route_id,))

    is_afternoon = route["type"] == "afternoon"
    has_gate = route["school_id"] is not None

    # Group students by location, preserving morning pickup order.
    location_keys: list[str] = []
    by_key: dict[str, list[dict]] = {}
    for st in students:
        if st["home_lat"] is None or st["home_lng"] is None:
            # No map coordinates: key by address so each distinct pickup point
            # keeps its own stop (labelled by address) instead of collapsing
            # into one generic stop. Falls back to a per-student key.
            addr = (st.get("home_address") or "").strip().lower()
            key = f"addr:{addr}" if addr else f"student:{st['id']}"
        else:
            key = f"{st['home_lat']:.6f},{st['home_lng']:.6f}"
        if key not in by_key:
            by_key[key] = []
            location_keys.append(key)
        by_key[key].append(dict(st))

    n_locations = len(location_keys)
    # Reserve order 1 for the school gate on afternoon routes; otherwise the
    # gate trails the student stops.
    student_base = 2 if (is_afternoon and has_gate) else 1
    gate_order = 1 if (is_afternoon and has_gate) else (n_locations + 1 if has_gate else None)

    def location_order(idx: int) -> int:
        if is_afternoon:
            # Reverse the morning order: first morning pickup is dropped last.
            return student_base + (n_locations - 1 - idx)
        return student_base + idx

    rows: list[tuple] = []
    for idx, key in enumerate(location_keys):
        order = location_order(idx)
        for st in by_key[key]:
            # Labelled by home address; coordinate-less students simply have no
            # map point (lat/lng stay null) but still appear as a named stop.
            name = _stop_label(st)
            rows.append((route_id, name, order, st["pickup_time"], st["home_lat"], st["home_lng"], False, st["id"]))

    for row in rows:
        conn.execute(
            "insert into live_route_stops (route_id, name, stop_order, scheduled_time, lat, lng, is_school_gate, student_id) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s)",
            row,
        )

    if has_gate:
        conn.execute(
            "insert into live_route_stops (route_id, name, stop_order, scheduled_time, lat, lng, is_school_gate, student_id) "
            "values (%s, %s, %s, %s, %s, %s, true, null)",
            (route_id, route["school_name"] or "School", gate_order, None, route["school_lat"], route["school_lng"]),
        )


def _check_route_bus_conflict(
    conn, bus_id: str | None, route_type: str, exclude_route_id: str | None = None
) -> None:
    """One route per (bus, type) (R1). This friendly pre-check names the
    conflicting route and bus; the partial unique index
    live_routes_bus_type_key (migration 007) is the race-proof backstop."""
    from app.core.errors import ConflictError

    if not bus_id:
        return
    exclude_sql = " and r.id <> %s" if exclude_route_id else ""
    params: list = [bus_id, route_type]
    if exclude_route_id:
        params.append(exclude_route_id)
    existing = conn.execute(
        "select r.name as route_name, b.name as bus_name "
        "from live_routes r join live_buses b on b.id = r.bus_id "
        f"where r.bus_id = %s and r.type = %s{exclude_sql} limit 1",
        params,
    ).fetchone()
    if existing:
        raise ConflictError(
            f"Bus {existing['bus_name']} already has a {route_type} route "
            f"({existing['route_name']})"
        )


class FleetDao:
    # --- buses -------------------------------------------------------------

    def list_buses(self) -> list[dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute("select * from live_buses order by name asc").fetchall()
            buses = [dict(r) for r in rows]
            # Derive a live position status from the bus's active run (no GPS):
            # at-school / at-stop / starting. Position itself lives in
            # current_lat/lng, set on start (school) and each arrival (stop).
            for b in buses:
                b["position_state"] = "idle"
                b["position_label"] = None
                run = conn.execute(
                    """
                    select id, stops_completed, total_stops from live_runs
                    where bus_id = %s and status <> 'completed'
                      and date = (now() at time zone 'Africa/Nairobi')::date
                    order by created_at desc limit 1
                    """,
                    (b["id"],),
                ).fetchone()
                if not run:
                    continue
                completed = run["stops_completed"] or 0
                if completed <= 0:
                    b["position_state"] = "starting"
                    b["position_label"] = "Starting — at school"
                    continue
                stop = conn.execute(
                    "select name, is_school_gate from run_stops "
                    "where run_id = %s and stop_order = %s order by is_school_gate desc limit 1",
                    (run["id"], completed),
                ).fetchone()
                if stop and stop["is_school_gate"]:
                    b["position_state"] = "at-school"
                    b["position_label"] = "At school"
                elif stop:
                    nxt = conn.execute(
                        "select 1 from run_stops where run_id = %s and stop_order = %s limit 1",
                        (run["id"], completed + 1),
                    ).fetchone()
                    b["position_state"] = "at-stop"
                    b["position_label"] = f"At {stop['name']}" + (" · en route to next" if nxt else "")
        return buses

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
            _check_route_bus_conflict(conn, data.get("bus_id"), data.get("type") or "morning")
            row = conn.execute(
                "insert into live_routes (name, type, bus_id, school_id) "
                "values (%(name)s, coalesce(%(type)s,'morning'), %(bus_id)s, %(school_id)s) returning *",
                data,
            ).fetchone()
            regenerate_route_stops(conn, row["id"])
        return dict(row)

    def update_route(self, route_id: str, data: dict) -> dict[str, Any] | None:
        from app.dao.student_live_dao import _derive_student_bus

        with get_connection() as conn:
            current = conn.execute(
                "select bus_id, type from live_routes where id = %s", (route_id,)
            ).fetchone()
            if not current:
                return None
            _check_route_bus_conflict(
                conn, data.get("bus_id"), data.get("type") or "morning",
                exclude_route_id=route_id,
            )
            row = conn.execute(
                "update live_routes set name=%(name)s, type=coalesce(%(type)s,'morning'), "
                "bus_id=%(bus_id)s, school_id=%(school_id)s where id=%(id)s returning *",
                {**data, "id": route_id},
            ).fetchone()
            if row:
                regenerate_route_stops(conn, route_id)
                # A bus reassignment (incl. to/from NULL) — or a type flip,
                # since derivation prefers morning routes — invalidates the
                # denormalised live_students.bus_id of everyone on this route
                # (R2); re-derive with the canonical rule.
                if current["bus_id"] != row["bus_id"] or current["type"] != row["type"]:
                    students = conn.execute(
                        "select student_id from live_student_routes where route_id = %s",
                        (route_id,),
                    ).fetchall()
                    for s in students:
                        _derive_student_bus(conn, s["student_id"])
        return dict(row) if row else None

    def delete_route(self, route_id: str) -> None:
        with get_connection() as conn:
            conn.execute("delete from live_routes where id = %s", (route_id,))

    # --- stop-level edits (#1) --------------------------------------------

    def remove_student_from_route(self, route_id: str, student_id: str) -> None:
        """Cancel a stop by removing its student from the route, then rebuild."""
        from app.dao.student_live_dao import _derive_student_bus

        with get_connection() as conn:
            conn.execute(
                "delete from live_student_routes where route_id = %s and student_id = %s",
                (route_id, student_id),
            )
            regenerate_route_stops(conn, route_id)
            _derive_student_bus(conn, student_id)

    def set_student_pickup_time(self, student_id: str, pickup_time: str | None) -> None:
        """Edit a stop's pickup time (a student attribute); reorder every route
        the student is on, since ordering is by pickup time."""
        with get_connection() as conn:
            conn.execute(
                "update live_students set pickup_time = %s where id = %s",
                (pickup_time, student_id),
            )
            route_ids = [
                r["route_id"]
                for r in conn.execute(
                    "select route_id from live_student_routes where student_id = %s", (student_id,)
                ).fetchall()
            ]
            for route_id in route_ids:
                regenerate_route_stops(conn, route_id)
