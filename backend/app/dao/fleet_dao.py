import datetime as dt
import logging
from typing import Any

from app.core.db import get_connection
from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.services import geo_service

logger = logging.getLogger("saferide.fleet")

# Departure anchors (Africa/Nairobi wall clock) for the geometry ETAs (U6).
# Morning anchors on the earliest assigned pickup_time when one exists;
# afternoon ALWAYS anchors on its type default — pickup_time is a
# morning-clock attribute, so an afternoon route anchored on it would write
# dawn-clock drop-off times that look derived.
_MORNING_DEFAULT = "07:00"
_AFTERNOON_DEFAULT = "15:30"


def _stop_label(st: dict) -> str:
    """A stop is labelled by the student's home address (#1, #14); fall back to
    a surname-based label only when no address is recorded."""
    address = (st.get("home_address") or "").strip()
    if address:
        return address
    return (st["name"].split()[-1] + " Stop") if st.get("name") else "Stop"


def _group_key(home_lat, home_lng, home_address, student_id) -> str:
    """Location-group identity for a student's pickup point (U6/U7). Located
    students group by rounded coordinates (siblings share a stop); without
    coordinates the key is the address so each distinct pickup point keeps
    its own stop, with a per-student fallback when neither exists. These keys
    are server-issued: the routes payload exposes them per stop row and the
    manual reorder payload (U7) echoes them back verbatim — clients never
    derive them."""
    if home_lat is not None and home_lng is not None:
        return f"{home_lat:.6f},{home_lng:.6f}"
    addr = (home_address or "").strip().lower()
    return f"addr:{addr}" if addr else f"student:{student_id}"


def _assigned_students(conn, route_id: str) -> list[dict]:
    """A route's assigned students in canonical pickup-time-then-name order."""
    return conn.execute(
        """
        select st.id, st.name, st.home_address, st.home_lat, st.home_lng, st.pickup_time
        from live_student_routes sr
        join live_students st on st.id = sr.student_id
        where sr.route_id = %s
        order by coalesce(st.pickup_time, '99:99') asc, st.name asc
        """,
        (route_id,),
    ).fetchall()


def _group_students(
    students: list[dict],
) -> tuple[list[str], dict[str, list[dict]], dict[str, dict]]:
    """Group student rows by location identity, preserving the given order.
    Returns (keys in first-seen order, key -> students, key -> representative
    point for located groups only)."""
    location_keys: list[str] = []
    by_key: dict[str, list[dict]] = {}
    points: dict[str, dict] = {}
    for st in students:
        key = _group_key(st["home_lat"], st["home_lng"], st.get("home_address"), st["id"])
        if st["home_lat"] is not None and st["home_lng"] is not None:
            points.setdefault(key, {"key": key, "lat": st["home_lat"], "lng": st["home_lng"]})
        if key not in by_key:
            by_key[key] = []
            location_keys.append(key)
        by_key[key].append(dict(st))
    return location_keys, by_key, points


def _insert_stop(conn, route_id: str, name: str, order: int, time: str | None,
                 lat: float | None, lng: float | None, is_gate: bool, student_id) -> None:
    conn.execute(
        "insert into live_route_stops (route_id, name, stop_order, scheduled_time, lat, lng, is_school_gate, student_id) "
        "values (%s, %s, %s, %s, %s, %s, %s, %s)",
        (route_id, name, order, time, lat, lng, is_gate, student_id),
    )


def regenerate_route_stops(conn, route_id: str) -> bool:
    """Rebuild a route's stops from its assigned students + the school gate.

    One stop per unique home location (siblings share an order). Stops are
    named by home address. Direction matters:

    - **Morning** routes end at the school gate; **afternoon** routes start
      at it (the morning route, run backwards).

    Opens with ``select … for update`` on the route row: every stop-rewrite
    path (assignment, pickup-time edit, school edit, handover, recalculate)
    contends on the same lock as the manual reorder transaction (U7), so a
    concurrent rebuild can never interleave with a renumber.

    Ordering authority (R9–R11, one authority per route):

    - ``custom_stops`` routes are untouched: the planner's saved option is
      authoritative until a student assignment flips the flag off (R18).
      Early return — no delete, no rebuild.
    - Auto routes attempt the geometry path: when every location group has
      coordinates, the school gate is located, and BOTH provider signals are
      Google (``optimized_order_with_provider`` for the order,
      ``route_geometry`` — provider ``'google-routes'`` — for the legs), the
      optimizer order is written with per-group times from cumulative leg
      ETAs. The departure anchor is read from the students BEFORE the stop
      delete: morning = earliest assigned ``pickup_time`` (else 07:00),
      afternoon = always 15:30 (never student pickup times — they are
      morning-clock values). The gate row carries the computed school
      arrival (morning) / departure (afternoon), which doubles as the
      durable "previously computed" marker below. ``last_recalc_degraded``
      clears on success.
    - Anything else falls back. Previously computed routes (their prior gate
      row carries a scheduled_time — only the geometry path writes one, and
      the fallback re-writes it as-is) preserve surviving location groups'
      previous relative order AND ``scheduled_time`` from the pre-delete
      snapshot: re-sorting by pickup time would pair a computed order's
      times with a contradictory sequence. Genuinely new groups append
      (before the gate on morning routes, last on afternoon) ordered by
      pickup-time-then-name among themselves. Never-computed routes take the
      pickup-time-then-name build wholesale — origin R10's literal fallback,
      and byte-identical to the pre-U6 behavior. Mixed provider signals
      (order without geometry or vice versa) take this same fallback: never
      a partial write.
    - Manual routes (``manual_stop_order``, U7) never call geometry and take
      the preservation path unconditionally: an admin-frozen order is a
      choice, not a degradation, so the degraded flag is not raised.

    A degraded auto recalculation (fallback taken with students assigned)
    persists ``last_recalc_degraded = true`` and logs a WARNING — silent
    degradation is banned (R10). Returns ``False`` exactly in that case, so
    callers can thread ``stops_recalculated`` into mutation responses;
    ``True`` otherwise (geometry success, custom/authoritative, empty).

    In-progress runs are untouched by construction: runs operate on their own
    ``run_stops`` snapshot (R12).
    """
    route = conn.execute(
        "select r.id, r.type, r.school_id, r.custom_stops, r.manual_stop_order, "
        "s.name as school_name, s.lat as school_lat, s.lng as school_lng "
        "from live_routes r left join live_schools s on s.id = r.school_id "
        "where r.id = %s for update of r",
        (route_id,),
    ).fetchone()
    if not route or route["custom_stops"]:
        return True

    students = _assigned_students(conn, route_id)

    is_afternoon = route["type"] == "afternoon"
    has_gate = route["school_id"] is not None
    gate_located = has_gate and route["school_lat"] is not None and route["school_lng"] is not None

    # The departure anchor is derived from the (pre-delete) student rows, never
    # from the stop rows about to be deleted — else every recalc would reset a
    # morning route to the 07:00 default.
    if is_afternoon:
        departure = geo_service.next_departure(None, default=_AFTERNOON_DEFAULT)
    else:
        earliest = next((st["pickup_time"] for st in students if st["pickup_time"]), None)
        departure = geo_service.next_departure(earliest, default=_MORNING_DEFAULT)

    # Group students by location, preserving morning pickup order.
    location_keys, by_key, points = _group_students(students)

    # Pre-delete snapshot keyed by location-group identity (the keys above),
    # feeding the preservation fallback. Rows are read in display order so the
    # first row of a group defines its previous position and time.
    prev_rows = conn.execute(
        "select name, stop_order, scheduled_time, lat, lng, is_school_gate, student_id "
        "from live_route_stops where route_id = %s order by stop_order asc, name asc",
        (route_id,),
    ).fetchall()
    prev_groups: dict[str, dict] = {}
    prev_gate_time: str | None = None
    for row in prev_rows:
        if row["is_school_gate"]:
            if prev_gate_time is None:
                prev_gate_time = row["scheduled_time"]
            continue
        if row["lat"] is not None and row["lng"] is not None:
            key = f"{row['lat']:.6f},{row['lng']:.6f}"
        else:
            # Coordinate-less rows were keyed by address, and the row name IS
            # that address (or a surname fallback — covered by the student
            # alias below).
            label = (row["name"] or "").strip().lower()
            key = f"addr:{label}" if label else None
        rec = None
        if key is not None:
            rec = prev_groups.setdefault(
                key, {"order": row["stop_order"], "time": row["scheduled_time"], "student_times": {}}
            )
        if row["student_id"] is not None:
            if rec is None:
                rec = {"order": row["stop_order"], "time": row["scheduled_time"], "student_times": {}}
            rec["student_times"][str(row["student_id"])] = row["scheduled_time"]
            # The student link is the stable alias when labels or coordinates
            # drift (setdefault: the location key stays the primary identity).
            prev_groups.setdefault(f"student:{row['student_id']}", rec)

    conn.execute("delete from live_route_stops where route_id = %s", (route_id,))

    n_locations = len(location_keys)
    is_auto = not route["manual_stop_order"]
    gate_name = route["school_name"] or "School"

    # --- Geometry path (auto routes with something to compute) ---------------
    degraded_reason = None
    if is_auto and n_locations > 0:
        if not all(key in points for key in location_keys):
            degraded_reason = "location group(s) without coordinates"
        elif not gate_located:
            degraded_reason = "school gate has no coordinates" if has_gate else "route has no school gate"
        else:
            gate_point = {"lat": route["school_lat"], "lng": route["school_lng"]}
            ordering = geo_service.optimized_order_with_provider(
                [points[key] for key in location_keys], gate_point
            )
            # A single group has no ordering problem — 'trivial' is exact, not
            # a fallback. Everything else must be the Google optimiser.
            order_ok = ordering["provider"] == "google" or (
                n_locations == 1 and ordering["provider"] == "trivial"
            )
            if not order_ok:
                degraded_reason = f"order provider '{ordering['provider']}'"
            else:
                ordered_keys = [p["key"] for p in ordering["ordered"]]
                if is_afternoon:
                    # Existing afternoon semantics: the school gate leads and
                    # the computed order runs backwards (first pickup of the
                    # morning direction is dropped last).
                    seq_keys = list(reversed(ordered_keys))
                    seq = [gate_point] + [points[k] for k in seq_keys]
                else:
                    seq_keys = ordered_keys
                    seq = [points[k] for k in seq_keys] + [gate_point]
                geom = geo_service.route_geometry(seq, departure=departure)
                if geom["provider"] != "google-routes" or len(geom["legs"]) != len(seq) - 1:
                    # Mixed signals (order ok, geometry degraded/misshapen):
                    # full fallback below — never a partial write.
                    degraded_reason = f"geometry provider '{geom['provider']}'"
                else:
                    etas = [departure.strftime("%H:%M")]
                    cumulative = 0
                    for leg in geom["legs"]:
                        cumulative += leg.get("duration_s") or 0
                        etas.append((departure + dt.timedelta(seconds=cumulative)).strftime("%H:%M"))
                    if is_afternoon:
                        gate_time, group_times = etas[0], dict(zip(seq_keys, etas[1:]))
                        orders = {key: 2 + i for i, key in enumerate(seq_keys)}
                        gate_order = 1
                    else:
                        gate_time, group_times = etas[-1], dict(zip(seq_keys, etas[:-1]))
                        orders = {key: 1 + i for i, key in enumerate(seq_keys)}
                        gate_order = n_locations + 1
                    for key in seq_keys:
                        for st in by_key[key]:
                            _insert_stop(conn, route_id, _stop_label(st), orders[key],
                                         group_times[key], st["home_lat"], st["home_lng"], False, st["id"])
                    _insert_stop(conn, route_id, gate_name, gate_order, gate_time,
                                 route["school_lat"], route["school_lng"], True, None)
                    conn.execute(
                        "update live_routes set last_recalc_degraded = false where id = %s",
                        (route_id,),
                    )
                    return True

    # --- Fallback / preservation ---------------------------------------------
    # "Previously computed" is read off the prior gate row's scheduled_time:
    # only the geometry path writes one, and this fallback re-writes it as-is,
    # so the marker survives degraded rebuilds in between. Manual routes (U7)
    # preserve unconditionally — the admin's order is authoritative.
    previously_computed = prev_gate_time is not None
    preserve = previously_computed or not is_auto
    if preserve:
        surviving = sorted(
            (k for k in location_keys if k in prev_groups),
            key=lambda k: prev_groups[k]["order"],
        )
        new_keys = [k for k in location_keys if k not in prev_groups]
        if is_afternoon:
            # Direction semantics for the appended block too: the earliest
            # new pickup is dropped last.
            new_keys.reverse()
        final_keys = surviving + new_keys
    else:
        # Never computed: the original pickup-time-then-name build, wholesale
        # (reversed for afternoon routes — first pickup is dropped last),
        # writing current pickup times so time edits keep applying.
        final_keys = list(reversed(location_keys)) if is_afternoon else list(location_keys)

    student_base = 2 if (is_afternoon and has_gate) else 1
    gate_order = 1 if (is_afternoon and has_gate) else (len(final_keys) + 1 if has_gate else None)
    for idx, key in enumerate(final_keys):
        rec = prev_groups.get(key) if preserve else None
        for st in by_key[key]:
            if rec is None:
                time = st["pickup_time"]
            else:
                # Surviving students keep their own previous time; a new
                # sibling joining a surviving group inherits the group's.
                fallback_time = rec["time"] if rec["time"] is not None else st["pickup_time"]
                time = rec["student_times"].get(str(st["id"]), fallback_time)
            _insert_stop(conn, route_id, _stop_label(st), student_base + idx, time,
                         st["home_lat"], st["home_lng"], False, st["id"])
    if has_gate:
        # "Gate stays as-is": the preserved time doubles as the previously-
        # computed marker above (None on never-computed routes, as before).
        _insert_stop(conn, route_id, gate_name, gate_order, prev_gate_time,
                     route["school_lat"], route["school_lng"], True, None)

    if is_auto and n_locations > 0:
        # Observable degradation (R10): the durable flag drives the route
        # card's warning badge and survives reloads; the return value threads
        # stops_recalculated: false into the mutation response.
        conn.execute(
            "update live_routes set last_recalc_degraded = true where id = %s", (route_id,)
        )
        logger.warning(
            "route %s stop recalculation degraded (%s); fell back to %s",
            route_id,
            degraded_reason,
            "preserved previous order and times" if previously_computed else "pickup-time order",
        )
        return False
    if is_auto:
        # Empty auto route: nothing to compute is not a degradation.
        conn.execute(
            "update live_routes set last_recalc_degraded = false where id = %s", (route_id,)
        )
    return True


def reorder_route_stops(conn, route_id: str, ordered_keys: list[str]) -> None:
    """Persist an admin's manual stop order and flip the route to manual mode
    (U7, R11).

    ``ordered_keys`` is the FULL ordered list of the route's location-group
    keys — the ``group_key`` values the routes payload exposes per stop row.
    Validation is set-equality against the server-derived current keys, so
    missing, extra, duplicate and foreign keys (e.g. a ``student:<uuid>`` key
    from another route) are all rejected; the renumber is positional — a
    client key never targets a row for update, it only says where the
    server's own group lands. Everything runs under the route-row
    ``select … for update`` that every regeneration path takes (U6), so a
    concurrent rebuild serializes instead of interleaving with the renumber.

    The school-gate row is never touched: its position (first on afternoon
    routes, last on morning) is invariant under a permutation of the student
    groups, and its ``scheduled_time`` doubles as the previously-computed
    marker the preservation fallback reads.

    Custom routes 409 — the planner's saved order is the ordering authority
    (custom_stops > manual_stop_order > auto).
    """
    route = conn.execute(
        "select id, type, custom_stops from live_routes where id = %s for update",
        (route_id,),
    ).fetchone()
    if not route:
        raise NotFoundError("Route not found")
    if route["custom_stops"]:
        raise ConflictError(
            "This route uses planner-saved stops — change its order by re-saving "
            "from the route planner"
        )

    location_keys, by_key, _ = _group_students(_assigned_students(conn, route_id))
    if len(ordered_keys) != len(set(ordered_keys)):
        raise BadRequestError("Duplicate stop in the requested order")
    if set(ordered_keys) != set(location_keys):
        raise BadRequestError(
            "Stop order does not match the route's current stops — refresh and try again"
        )

    is_afternoon = route["type"] == "afternoon"
    has_gate_row = conn.execute(
        "select 1 from live_route_stops where route_id = %s and is_school_gate limit 1",
        (route_id,),
    ).fetchone() is not None
    base = 2 if (is_afternoon and has_gate_row) else 1
    for idx, key in enumerate(ordered_keys):
        conn.execute(
            "update live_route_stops set stop_order = %s "
            "where route_id = %s and student_id = any(%s)",
            (base + idx, route_id, [st["id"] for st in by_key[key]]),
        )
    conn.execute(
        "update live_routes set manual_stop_order = true where id = %s", (route_id,)
    )


def recalculate_route_stops(conn, route_id: str) -> bool:
    """Explicit return to automatic ordering (U7, R11): clear
    ``manual_stop_order`` under the route-row lock, then regenerate
    immediately through the U6 path. Returns its ``stops_recalculated``
    signal (False = the rebuild fell back instead of computing geometry).

    Custom routes 409: regeneration early-returns on them, and answering 200
    to a recalculation that did nothing is exactly the silent no-op R10 bans.
    """
    route = conn.execute(
        "select id, custom_stops from live_routes where id = %s for update",
        (route_id,),
    ).fetchone()
    if not route:
        raise NotFoundError("Route not found")
    if route["custom_stops"]:
        raise ConflictError(
            "This route uses planner-saved stops — recalculation applies only to "
            "student-based routes"
        )
    conn.execute(
        "update live_routes set manual_stop_order = false where id = %s", (route_id,)
    )
    return regenerate_route_stops(conn, route_id)


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


def _write_custom_stops(conn, route_id: str, stops: list[dict]) -> None:
    """Persist planner-provided stops verbatim (R17): the given order becomes
    stop_order 1..n, pickup times land in scheduled_time, school entries are
    gate stops, and no stop is student-linked (student_id NULL)."""
    conn.execute("delete from live_route_stops where route_id = %s", (route_id,))
    for order, stop in enumerate(stops, start=1):
        conn.execute(
            "insert into live_route_stops (route_id, name, stop_order, scheduled_time, lat, lng, is_school_gate, student_id) "
            "values (%s, %s, %s, %s, %s, %s, %s, null)",
            (
                route_id,
                stop.get("label") or "Stop",
                order,
                stop.get("pickup_time"),
                stop.get("lat"),
                stop.get("lng"),
                bool(stop.get("is_school")),
            ),
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
                    """
                    select rs.*, st.home_lat as _home_lat, st.home_lng as _home_lng,
                           st.home_address as _home_address
                    from live_route_stops rs
                    left join live_students st on st.id = rs.student_id
                    where rs.route_id = %s
                    order by rs.stop_order asc, rs.name asc
                    """,
                    (route["id"],),
                ).fetchall()
                item = dict(route)
                item["route_stops"] = []
                for s in stops:
                    stop = dict(s)
                    home = (stop.pop("_home_lat"), stop.pop("_home_lng"), stop.pop("_home_address"))
                    # Server-issued location-group key (U7): the manual reorder
                    # payload echoes these back verbatim. Derived from the
                    # student row — the same source the reorder validation
                    # reads — never from the stop row's own lat/lng. NULL on
                    # gate and planner-authored (student-less) stops.
                    stop["group_key"] = (
                        _group_key(*home, stop["student_id"])
                        if stop["student_id"] is not None
                        else None
                    )
                    item["route_stops"].append(stop)
                result.append(item)
        return result

    def create_route(self, data: dict) -> dict[str, Any]:
        with get_connection() as conn:
            _check_route_bus_conflict(conn, data.get("bus_id"), data.get("type") or "morning")
            # A planner save carries its own stops: flag the route custom,
            # keep the polyline/totals, write the stops verbatim and skip
            # student-based regeneration (R17/R18).
            custom = bool(data.get("stops"))
            row = conn.execute(
                "insert into live_routes (name, type, bus_id, school_id, "
                "custom_stops, polyline, total_distance_m, total_duration_s) "
                "values (%(name)s, coalesce(%(type)s,'morning'), %(bus_id)s, %(school_id)s, "
                "%(custom_stops)s, %(polyline)s, %(total_distance_m)s, %(total_duration_s)s) returning *",
                {
                    **data,
                    "custom_stops": custom,
                    "polyline": data.get("polyline") if custom else None,
                    "total_distance_m": data.get("total_distance_m") if custom else None,
                    "total_duration_s": data.get("total_duration_s") if custom else None,
                },
            ).fetchone()
            if custom:
                _write_custom_stops(conn, row["id"], data["stops"])
            else:
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
            custom = bool(data.get("stops"))
            stops_recalculated = True
            if custom:
                # Re-saving from the planner replaces the custom stops
                # wholesale — same write path as create (R17). The SAME UPDATE
                # clears manual_stop_order (one ordering authority per route,
                # U7 — the 008 CHECK is the race-proof backstop, never the
                # mechanism) and last_recalc_degraded (custom routes never
                # regenerate, so a stale degradation badge could otherwise
                # never clear).
                row = conn.execute(
                    "update live_routes set name=%(name)s, type=coalesce(%(type)s,'morning'), "
                    "bus_id=%(bus_id)s, school_id=%(school_id)s, custom_stops=true, "
                    "manual_stop_order=false, last_recalc_degraded=false, "
                    "polyline=%(polyline)s, total_distance_m=%(total_distance_m)s, "
                    "total_duration_s=%(total_duration_s)s where id=%(id)s returning *",
                    {**data, "id": route_id},
                ).fetchone()
                if row:
                    _write_custom_stops(conn, route_id, data["stops"])
            else:
                # Metadata-only edit: leave custom_stops/polyline/totals and the
                # saved stops alone on a custom route (regenerate early-returns
                # for it); normal routes rebuild from students as before.
                row = conn.execute(
                    "update live_routes set name=%(name)s, type=coalesce(%(type)s,'morning'), "
                    "bus_id=%(bus_id)s, school_id=%(school_id)s where id=%(id)s returning *",
                    {**data, "id": route_id},
                ).fetchone()
                if row:
                    stops_recalculated = regenerate_route_stops(conn, route_id)
            if row:
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
        if not row:
            return None
        # Observable degradation (U6/R10): false when the rebuild fell back.
        return {**dict(row), "stops_recalculated": stops_recalculated}

    def delete_route(self, route_id: str) -> None:
        with get_connection() as conn:
            conn.execute("delete from live_routes where id = %s", (route_id,))

    # --- stop-level edits (#1) --------------------------------------------

    def remove_student_from_route(self, route_id: str, student_id: str) -> bool:
        """Cancel a stop by removing its student from the route, then rebuild.
        Returns the regeneration's ``stops_recalculated`` signal (U6/R10)."""
        from app.dao.student_live_dao import _derive_student_bus

        with get_connection() as conn:
            conn.execute(
                "delete from live_student_routes where route_id = %s and student_id = %s",
                (route_id, student_id),
            )
            stops_recalculated = regenerate_route_stops(conn, route_id)
            _derive_student_bus(conn, student_id)
        return stops_recalculated

    def set_student_pickup_time(self, student_id: str, pickup_time: str | None) -> bool:
        """Edit a stop's pickup time (a student attribute). The effect depends
        on each affected route's ordering mode, decided under the same
        route-row lock every stop rewrite takes (U6):

        - auto: regenerate — on google routes the new time re-anchors the
          morning departure (pickup_time is otherwise input-only there);
          never-computed routes re-sort by it as before.
        - manual (U7, R13): write the new time through to that student's own
          stop row only — the admin's order is authoritative, so no re-sort
          and no regeneration; every other stop and the gate row stay as-is.
        - custom: nothing to touch — planner stops are not student-linked.

        Returns False when any auto regeneration degraded."""
        with get_connection() as conn:
            conn.execute(
                "update live_students set pickup_time = %s where id = %s",
                (pickup_time, student_id),
            )
            # order by r.id: a student can sit on several routes — take their
            # row locks in a stable order so two concurrent edits cannot
            # deadlock across routes.
            routes = conn.execute(
                "select r.id, r.custom_stops, r.manual_stop_order "
                "from live_student_routes sr join live_routes r on r.id = sr.route_id "
                "where sr.student_id = %s order by r.id for update of r",
                (student_id,),
            ).fetchall()
            ok = True
            for route in routes:
                if route["custom_stops"]:
                    continue
                if route["manual_stop_order"]:
                    conn.execute(
                        "update live_route_stops set scheduled_time = %s "
                        "where route_id = %s and student_id = %s",
                        (pickup_time, route["id"], student_id),
                    )
                else:
                    ok = regenerate_route_stops(conn, route["id"]) and ok
        return ok

    # --- manual ordering (U7) -----------------------------------------------

    def set_route_stop_order(self, route_id: str, ordered_keys: list[str]) -> None:
        with get_connection() as conn:
            reorder_route_stops(conn, route_id, ordered_keys)

    def recalculate_route(self, route_id: str) -> bool:
        with get_connection() as conn:
            return recalculate_route_stops(conn, route_id)
