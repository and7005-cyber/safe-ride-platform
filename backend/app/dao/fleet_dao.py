import datetime as dt
import logging
import re
from typing import Any

from app.core.db import get_connection
from app.core.errors import BadRequestError, ConflictError, NotFoundError, SafeRideError
from app.core.scope import SchoolScope
from app.dao.audit_dao import record_audit
from app.dao.status_sql import bus_status_case
from app.services import geo_service
# The ONE server-side stop-cap authority (U8): the solver's cap, shared with
# the frontend's PLANNER_STOPS_CAP meaning. plan_solver is pure logic (stdlib +
# haversine) — importing the constant does not couple fleet paths to the plan
# DAO machinery.
from app.services.plan_solver import DEFAULT_STOP_CAP

logger = logging.getLogger("saferide.fleet")

# Live-edit hard-constraint names (U8: R4/R14). These MIRROR the review-edit
# vocabulary in fleet_plan_dao (CONSTRAINT_CAPACITY / CONSTRAINT_STOP_CAP):
# the 422 detail leads with one of these tokens verbatim. Deliberately
# duplicated, not imported — fleet paths must not couple to plan machinery.
CONSTRAINT_CAPACITY = "capacity"
CONSTRAINT_STOP_CAP = "stop cap"


class RouteConstraintError(SafeRideError):
    """A live-route edit violates a hard constraint (U8: R4/R14; AE7 family):
    422 with the constraint NAMED at the head of the detail, matching the
    review-edit shape (fleet_plan_dao.PlanConstraintError — mirrored, never
    imported). Raised under the route-row lock inside the mutating
    transaction, so the whole edit (link writes included) rolls back.

    Only an edit that INTRODUCES OR WORSENS a violation raises: a route
    already over a limit stays editable toward compliance — removals are
    always allowed, and a reorder (same counts) can never trip either check;
    the caps apply to additions."""

    status_code = 422

# HH:MM (00:00–23:59) — the only shape resolve_gate_anchor / _hhmm_to_min accept.
_HHMM_RE = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")

# System-default gate anchors (Africa/Nairobi wall clock), the fallback of last
# resort beneath the one authority (route.gate_anchor override -> school bell ->
# these). Under bell-anchoring (U4) the gate time is an INPUT the schedule is
# solved against, not an output derived from pickup times.
_MORNING_DEFAULT = "07:00"
_AFTERNOON_DEFAULT = "15:30"

def resolve_gate_anchor(route: dict) -> str:
    """The route's gate anchor (HH:MM), one authority: the route-level override
    if set, else the school's bell for the direction, else the system default.
    ``route`` carries gate_anchor + morning_bell/afternoon_bell (joined in
    ``_ROUTE_GEOMETRY_INPUTS_SQL``). ALWAYS returns a valid HH:MM: a malformed
    stored anchor/bell (the columns are free-text) is skipped rather than
    propagated, so downstream _hhmm_to_min / next_departure never crash on
    legacy bad data (payload validation rejects new bad values up front)."""
    is_afternoon = route["type"] == "afternoon"
    anchor = route.get("gate_anchor")
    if anchor and _HHMM_RE.match(anchor):
        return anchor
    bell = route.get("afternoon_bell") if is_afternoon else route.get("morning_bell")
    if bell and _HHMM_RE.match(bell):
        return bell
    return _AFTERNOON_DEFAULT if is_afternoon else _MORNING_DEFAULT


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


_ROUTE_GEOMETRY_INPUTS_SQL = (
    "select r.id, r.type, r.school_id, r.custom_stops, r.manual_stop_order, r.plan_ordered, "
    "r.stops_computed, "
    "r.gate_anchor, r.bus_id, r.trip_index, s.morning_bell, s.afternoon_bell, "
    "s.name as school_name, s.lat as school_lat, s.lng as school_lng, "
    "b.depot_lat, b.depot_lng, b.capacity as bus_capacity, b.name as bus_name "
    "from live_routes r left join live_schools s on s.id = r.school_id "
    "left join live_buses b on b.id = r.bus_id "
    "where r.id = %s"
)


def _geometry_fingerprint(route, location_keys: list[str], points: dict[str, dict]) -> tuple:
    """Everything a computed geometry depends on: route direction, ordering
    flags, gate identity/coordinates, and the location-group set with
    coordinates. The locked phase of ``regenerate_route_stops`` discards the
    unlocked phase's computed result unless this matches exactly — a stale
    order/ETA set must never be written over drifted inputs."""
    groups = frozenset(
        (key, points[key]["lat"], points[key]["lng"]) if key in points else (key, None, None)
        for key in location_keys
    )
    return (
        route["type"],
        str(route["school_id"]) if route["school_id"] is not None else None,
        route["school_lat"], route["school_lng"],
        bool(route["custom_stops"]), bool(route["manual_stop_order"]),
        # plan_ordered (U8): the flag flipping between the phases (an explicit
        # recalculate, or an apply) changes which compute is valid — discard.
        bool(route.get("plan_ordered")),
        # trip_index + depot (U7): a depot move or trip-boundary change alters the
        # prepended/appended leg, so phase 2 must discard a stale phase-1 compute.
        route.get("trip_index"), route.get("depot_lat"), route.get("depot_lng"),
        groups,
    )


def _depot_leg(conn, route) -> dict | None:
    """The depot geometry point for a route, or None (U7/R12-R14). The depot is a
    bus attribute that enters geometry ONLY as a boundary leg — an origin on the
    bus's FIRST morning trip (min trip_index) and a destination on its LAST
    afternoon trip (max trip_index) — never a stop row. Returns {'lat','lng'}."""
    if not route.get("bus_id") or route.get("depot_lat") is None or route.get("depot_lng") is None:
        return None
    is_afternoon = route["type"] == "afternoon"
    agg = "max" if is_afternoon else "min"
    boundary = conn.execute(
        f"select {agg}(trip_index) as ti from live_routes where bus_id = %s and type = %s",
        (route["bus_id"], route["type"]),
    ).fetchone()
    if boundary and boundary["ti"] == route["trip_index"]:
        return {"lat": route["depot_lat"], "lng": route["depot_lng"]}
    return None


def _prev_stop_rows(conn, route_id: str) -> list[dict]:
    """The route's current stop rows in display order — the pre-delete
    snapshot that feeds the preservation fallback, the plan-ordered fixed
    sequence (U8), and the 'before' side of the live-edit constraint guard."""
    return conn.execute(
        "select name, stop_order, scheduled_time, lat, lng, is_school_gate, student_id "
        "from live_route_stops where route_id = %s order by stop_order asc, name asc",
        (route_id,),
    ).fetchall()


def _prev_group_snapshot(prev_rows: list[dict]) -> tuple[dict[str, dict], str | None]:
    """Key the pre-delete stop rows by location-group identity. Rows arrive in
    display order, so the first row of a group defines its previous position
    and time. Returns ``(prev_groups, prev_gate_time)``."""
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
    return prev_groups, prev_gate_time


def _surviving_plus_new_keys(
    location_keys: list[str],
    by_key: dict[str, list[dict]],
    prev_groups: dict[str, dict],
    is_afternoon: bool,
) -> tuple[list[str], dict[str, dict]]:
    """Resolve the current location groups against the pre-delete snapshot:
    surviving groups keep their previous relative order, genuinely new groups
    append — before the gate on morning routes, last on afternoon (reversed
    among themselves: the earliest new pickup is dropped last). Returns
    ``(final_keys, resolved)``. The ONE order authority for both the
    preservation fallback and the plan-ordered fixed sequence (U8), so the
    two paths cannot disagree about where a new student lands.

    A group resolves to its pre-delete record by location key first, then via
    the per-student alias: a coordinate edit re-keys the group while the stale
    stop rows still carry the old coords, and without the alias the group
    would be treated as new and lose its preserved order and time. Claim-once
    (exact location matches first, then alias claims in current display
    order): a record shared by former siblings can never seed two post-split
    groups — the loser is genuinely new."""
    resolved: dict[str, dict] = {}
    claimed: set[int] = set()
    for key in location_keys:
        rec = prev_groups.get(key)
        if rec is not None and id(rec) not in claimed:
            claimed.add(id(rec))
            resolved[key] = rec
    for key in location_keys:
        if key in resolved:
            continue
        for st in by_key[key]:
            rec = prev_groups.get(f"student:{st['id']}")
            if rec is not None and id(rec) not in claimed:
                claimed.add(id(rec))
                resolved[key] = rec
                break
    surviving = sorted(
        (k for k in location_keys if k in resolved),
        key=lambda k: resolved[k]["order"],
    )
    new_keys = [k for k in location_keys if k not in resolved]
    if is_afternoon:
        # Direction semantics for the appended block too: the earliest
        # new pickup is dropped last.
        new_keys.reverse()
    return surviving + new_keys, resolved


def _check_live_edit_constraints(
    route: dict, students: list[dict], location_keys: list[str], prev_rows: list[dict]
) -> None:
    """U8 hard-constraint guard on live-route edits (R4/R14): seat capacity
    counts children against the bus's capacity, the stop cap counts location
    groups against the shared ``DEFAULT_STOP_CAP``. Runs in regeneration's
    locked phase — after the mutation rewrote the links, before any stop
    write — so every path that can add riders or stops (assignment, student
    create/update, bulk link) meets one guard, serialized by the route-row
    lock; raising rolls the whole edit back.

    Blocks ONLY an edit that introduces or worsens a violation, comparing the
    post-edit load against the pre-edit stop rows: removals always pass, a
    reorder (same counts) can never trip either check, and a route already
    over a limit stays editable toward compliance."""
    prev_student_rows = [
        r for r in prev_rows if not r["is_school_gate"] and r["student_id"] is not None
    ]
    before_children = len(prev_student_rows)
    before_stops = len({r["stop_order"] for r in prev_student_rows})
    after_children = len(students)
    after_stops = len(location_keys)

    capacity = route.get("bus_capacity")
    if (
        capacity is not None
        and after_children > capacity
        and after_children > before_children
    ):
        bus_name = route.get("bus_name") or "the assigned bus"
        raise RouteConstraintError(
            f"{CONSTRAINT_CAPACITY}: bus {bus_name} would carry {after_children} "
            f"children on this route — its capacity is {capacity}; the change "
            "was not applied"
        )
    if after_stops > DEFAULT_STOP_CAP and after_stops > before_stops:
        raise RouteConstraintError(
            f"{CONSTRAINT_STOP_CAP}: this route would have {after_stops} stops "
            f"— the cap is {DEFAULT_STOP_CAP} stops per route; the change "
            "was not applied"
        )


def _plan_fixed_compute(conn, route: dict, seq_keys: list[str], points: dict[str, dict]) -> dict:
    """Geometry + times for a plan-ordered route along its FIXED sequence
    (U8): one ``geo_service.fixed_sequence_geometry`` call — U5's helper,
    never an ordering call — then wall-clock arithmetic anchored on the gate
    exactly like the auto path: the gate arrival (morning) / departure
    (afternoon) IS the resolved anchor, each stop one cumulative leg away.
    The depot enters as the shipped boundary leg (origin on the first morning
    trip, destination on the last afternoon trip), shifting the ETA mapping
    but never appearing as a stop row.

    Returns the auto path's ``computed`` shape plus ``degraded``: True when
    the offline duration estimate was used. Degraded values are still written
    by the caller — the non-convergent-solve precedent: computed times land,
    flagged through the durable channel, never silently."""
    is_afternoon = route["type"] == "afternoon"
    anchor_hhmm = resolve_gate_anchor(route)
    default = _AFTERNOON_DEFAULT if is_afternoon else _MORNING_DEFAULT
    gate_point = {"lat": route["school_lat"], "lng": route["school_lng"]}
    depot = _depot_leg(conn, route)
    stop_points = [points[key] for key in seq_keys]
    if is_afternoon:
        seq = [gate_point] + stop_points + ([depot] if depot else [])
    else:
        seq = ([depot] if depot else []) + stop_points + [gate_point]

    geom = geo_service.fixed_sequence_geometry(seq)
    durations = [leg.get("duration_s") or 0 for leg in geom["legs"]]

    # The SHARED direction-aware ride walk (geo_service.cumulative_ride_seconds
    # — the one implementation the plan review recompute and the slot-in
    # materializer also use): rides[i] is stop i's cumulative drive to/from
    # the gate, the depot entering only as the shipped boundary leg (leading
    # on morning, trailing on afternoon — never in a ride). The anchor IS the
    # gate arrival (morning) / departure (afternoon), so each stop's ETA is
    # one signed ride away from it — identical arithmetic to the previous
    # departure-plus-cumulative walk, minus the zip truncation that could
    # silently misalign ETAs when the provider returned fewer legs than the
    # sequence implies: a short leg list now pads observably (missing legs
    # count 0) and raises the degraded flag instead.
    rides, short = geo_service.cumulative_ride_seconds(
        durations, len(seq_keys), is_afternoon=is_afternoon, has_depot=depot is not None
    )
    anchor_dt = geo_service.next_departure(anchor_hhmm, default=default)
    sign = 1 if is_afternoon else -1
    gate_time = anchor_dt.strftime("%H:%M")
    group_times = {
        key: (anchor_dt + dt.timedelta(seconds=sign * rides[i])).strftime("%H:%M")
        for i, key in enumerate(seq_keys)
    }
    if is_afternoon:
        orders = {key: 2 + i for i, key in enumerate(seq_keys)}
        gate_order = 1
    else:
        orders = {key: 1 + i for i, key in enumerate(seq_keys)}
        gate_order = len(seq_keys) + 1
    return {
        "seq_keys": list(seq_keys),
        "orders": orders,
        "group_times": group_times,
        "gate_time": gate_time,
        "gate_order": gate_order,
        "total_duration_s": geom.get("total_duration_s"),
        "degraded": bool(geom.get("degraded")) or short,
    }


def _compute_route_geometry(conn, route_id: str) -> dict[str, Any]:
    """Phase 1 of ``regenerate_route_stops``: read the route + students
    WITHOUT the route-row lock and make the (slow) geo-provider calls.

    Returns ``{fingerprint, computed, degraded_reason}``. ``computed``
    carries the optimizer order and cumulative-ETA times only when BOTH
    provider signals were Google (or the exact single-group 'trivial'
    order); ``fingerprint`` is what phase 2 must re-derive identically for
    the computed result to be written (None when nothing was attempted —
    missing/custom/manual/empty route); ``degraded_reason`` names the
    failing precondition or provider signal otherwise. Reads only — never
    writes, never locks.

    Plan-ordered routes (U8) take their own branch: no ordering call — the
    fixed sequence is derived from the current stop rows (surviving order +
    appended new groups) and extended onto the fingerprint, and ``computed``
    comes from :func:`_plan_fixed_compute`, present even on the degraded
    (offline) duration path.
    """
    result: dict[str, Any] = {"fingerprint": None, "computed": None, "degraded_reason": None}
    route = conn.execute(_ROUTE_GEOMETRY_INPUTS_SQL, (route_id,)).fetchone()
    if not route or route["custom_stops"] or route["manual_stop_order"]:
        return result

    students = _assigned_students(conn, route_id)
    location_keys, by_key, points = _group_students(students)
    if not location_keys:
        return result
    result["fingerprint"] = _geometry_fingerprint(route, location_keys, points)

    is_afternoon = route["type"] == "afternoon"
    has_gate = route["school_id"] is not None
    gate_located = has_gate and route["school_lat"] is not None and route["school_lng"] is not None
    if not all(key in points for key in location_keys):
        result["degraded_reason"] = "location group(s) without coordinates"
        return result
    if not gate_located:
        result["degraded_reason"] = (
            "school gate has no coordinates" if has_gate else "route has no school gate"
        )
        return result

    if route["plan_ordered"]:
        # Plan order is authoritative (U8): the fixed sequence — surviving
        # groups in their current stop order, new groups appended — joins the
        # fingerprint, so a concurrent reorder or roster drift between the
        # phases discards this compute exactly like any other drift.
        prev_groups, _ = _prev_group_snapshot(_prev_stop_rows(conn, route_id))
        seq_keys, _ = _surviving_plus_new_keys(location_keys, by_key, prev_groups, is_afternoon)
        result["fingerprint"] = (result["fingerprint"], tuple(seq_keys))
        result["computed"] = _plan_fixed_compute(conn, route, seq_keys, points)
        return result

    # Bell-time anchor, one authority (U4): route override -> school bell ->
    # system default. The schedule is solved AGAINST this gate time; it is never
    # derived from student pickup times (the pre-U4 model, now retired). Student
    # churn shifts the departure earlier/later — never the gate.
    anchor_hhmm = resolve_gate_anchor(route)

    n_locations = len(location_keys)
    gate_point = {"lat": route["school_lat"], "lng": route["school_lng"]}
    ordering = geo_service.optimized_order_with_provider(
        [points[key] for key in location_keys], gate_point
    )
    # A single group has no ordering problem — 'trivial' is exact, not a
    # fallback. Everything else must be the Google optimiser.
    order_ok = ordering["provider"] == "google" or (
        n_locations == 1 and ordering["provider"] == "trivial"
    )
    if not order_ok:
        result["degraded_reason"] = f"order provider '{ordering['provider']}'"
        return result

    ordered_keys = [p["key"] for p in ordering["ordered"]]
    # Depot (U7): a boundary leg only — prepended origin on the first morning
    # trip, appended destination on the last afternoon trip. Never a stop row.
    depot = _depot_leg(conn, route)
    if is_afternoon:
        # The school gate leads and the computed order runs backwards (first
        # pickup of the morning direction is dropped last). The anchor IS the
        # gate departure, so this is a single forward solve — no iteration.
        seq_keys = list(reversed(ordered_keys))
        seq = [gate_point] + [points[k] for k in seq_keys] + ([depot] if depot else [])
        departure = geo_service.next_departure(anchor_hhmm, default=_AFTERNOON_DEFAULT)
        geom = geo_service.route_geometry(seq, departure=departure)
        converged = True
    else:
        # Morning: the gate is the LAST point; backward-solve the departure so
        # the gate-arrival ETA lands on the anchor (fixed-point iteration).
        seq_keys = ordered_keys
        seq = ([depot] if depot else []) + [points[k] for k in seq_keys] + [gate_point]
        anchor_dt = geo_service.next_departure(anchor_hhmm, default=_MORNING_DEFAULT)
        departure, geom, converged = geo_service.solve_morning_departure(seq, anchor_dt)
    if geom["provider"] != "google-routes" or len(geom["legs"]) != len(seq) - 1:
        # Mixed signals (order ok, geometry degraded/misshapen): full
        # fallback — never a partial write.
        result["degraded_reason"] = f"geometry provider '{geom['provider']}'"
        return result

    etas = [departure.strftime("%H:%M")]
    cumulative = 0
    for leg in geom["legs"]:
        cumulative += leg.get("duration_s") or 0
        etas.append((departure + dt.timedelta(seconds=cumulative)).strftime("%H:%M"))
    if is_afternoon:
        # Append is zip-safe: seq_keys is shorter than etas[1:] (the trailing
        # depot arrival), so zip() truncates the depot ETA off the tail.
        gate_time, group_times = etas[0], dict(zip(seq_keys, etas[1:]))
        orders = {key: 2 + i for i, key in enumerate(seq_keys)}
        gate_order = 1
    else:
        # Morning prepend is NOT zip-symmetric: a leading depot-departure ETA
        # shifts the stop mapping by one, so stops map to etas[1:-1] (skip the
        # depot departure AND the trailing gate) instead of etas[:-1] (U7/F10).
        stop_etas = etas[1:-1] if depot else etas[:-1]
        gate_time, group_times = etas[-1], dict(zip(seq_keys, stop_etas))
        orders = {key: 1 + i for i, key in enumerate(seq_keys)}
        gate_order = n_locations + 1
    result["computed"] = {
        "seq_keys": seq_keys, "orders": orders, "group_times": group_times,
        "gate_time": gate_time, "gate_order": gate_order,
        # Persisted on the auto route (U3): the turnaround feasibility gate (U6)
        # needs the drive duration of auto routes, not just planner-saved ones.
        "total_duration_s": geom.get("total_duration_s"),
        # False when the morning backward solve exhausted its iteration budget
        # (U4): the best-iterate times are still written, but flagged degraded.
        "converged": converged,
    }
    return result


def regenerate_route_stops(conn, route_id: str) -> bool:
    """Rebuild a route's stops from its assigned students + the school gate.

    One stop per unique home location (siblings share an order). Stops are
    named by home address. Direction matters:

    - **Morning** routes end at the school gate; **afternoon** routes start
      at it (the morning route, run backwards).

    Two phases. Phase 1 (``_compute_route_geometry``) reads the inputs and
    makes the geo-provider calls WITHOUT any lock — Google round-trips must
    not extend the critical section. Phase 2 takes ``select … for update``
    on the route row — the same lock every stop-rewrite path (assignment,
    pickup-time edit, school edit, handover, recalculate) and the manual
    reorder transaction (U7) contend on — re-reads the route and students,
    and writes. The computed geometry is applied only when the re-read
    fingerprint (flags, direction, gate, location groups + coords) matches
    phase 1 exactly; any drift discards it and takes the degraded fallback
    below — never a partial or stale write (the mutation that caused the
    drift regenerates again with fresh inputs).

    Ordering authority (R9–R11 + fleet-plan U8, one authority per route:
    ``custom_stops`` > ``manual_stop_order`` > ``plan_ordered`` > auto):

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
      times with a contradictory sequence. Survival is matched by location
      key, then by the per-student alias with a claim-once guard (a
      coordinate edit re-keys a group; a sibling split must not clone a
      shared record). Genuinely new groups append
      (before the gate on morning routes, last on afternoon) ordered by
      pickup-time-then-name among themselves. Never-computed routes take the
      pickup-time-then-name build wholesale — origin R10's literal fallback,
      and byte-identical to the pre-U6 behavior. Mixed provider signals
      (order without geometry or vice versa) take this same fallback: never
      a partial write.
    - Manual routes (``manual_stop_order``, U7) never call geometry and take
      the preservation path unconditionally: an admin-frozen order is a
      choice, not a degradation, so the degraded flag is not raised.
    - Plan-ordered routes (``plan_ordered``, fleet-plan U8) preserve the
      applied plan's stop order — never an ordering call — while times and
      geometry RECOMPUTE along the fixed sequence
      (``geo_service.fixed_sequence_geometry``): newly assigned students
      append (before the gate on morning routes, last on afternoon, the
      frozen-route convention) and every stop's time re-anchors on the gate.
      Degradation is observable exactly like the auto path: the offline
      duration estimate still writes times but persists
      ``last_recalc_degraded`` and returns ``False``. Only the explicit
      ``recalculate_route_stops`` clears the flag and hands the order back
      to the optimiser.

    Hard-constraint guard (U8: R4/R14): before any write, the locked phase
    compares the post-edit load against the pre-edit stop rows and raises
    :class:`RouteConstraintError` (422, constraint named) when the edit
    introduces or worsens a seat-capacity or stop-cap violation — rolling the
    whole mutation back. Edits toward compliance always pass.

    A degraded auto or plan-ordered recalculation (fallback or offline
    estimate with students assigned) persists ``last_recalc_degraded = true``
    and logs a WARNING — silent degradation is banned (R10). Returns
    ``False`` exactly in that case, so callers can thread
    ``stops_recalculated`` into mutation responses; ``True`` otherwise
    (geometry success, custom/authoritative, empty).

    In-progress runs are untouched by construction: runs operate on their own
    ``run_stops`` snapshot (R12).
    """
    # Phase 1 — provider calls before the lock (reads only).
    pre = _compute_route_geometry(conn, route_id)

    # Phase 2 — lock, re-read, validate, write.
    route = conn.execute(
        _ROUTE_GEOMETRY_INPUTS_SQL + " for update of r",
        (route_id,),
    ).fetchone()
    if not route or route["custom_stops"]:
        return True

    students = _assigned_students(conn, route_id)

    is_afternoon = route["type"] == "afternoon"
    has_gate = route["school_id"] is not None
    is_manual = bool(route["manual_stop_order"])
    is_plan = bool(route["plan_ordered"]) and not is_manual
    is_auto = not is_manual and not is_plan

    # Group students by location, preserving morning pickup order.
    location_keys, by_key, points = _group_students(students)

    # Pre-delete snapshot keyed by location-group identity (the keys above),
    # feeding the preservation fallback and the plan-ordered fixed sequence.
    prev_rows = _prev_stop_rows(conn, route_id)

    # Hard-constraint guard (U8): under the route-row lock, before any write —
    # an over-capacity / over-cap addition rolls the whole mutation back.
    _check_live_edit_constraints(route, students, location_keys, prev_rows)

    prev_groups, prev_gate_time = _prev_group_snapshot(prev_rows)

    conn.execute("delete from live_route_stops where route_id = %s", (route_id,))

    n_locations = len(location_keys)
    gate_name = route["school_name"] or "School"

    # --- Plan-ordered fixed-sequence path (U8) --------------------------------
    degraded_reason = None
    if is_plan and n_locations > 0:
        seq_keys, _ = _surviving_plus_new_keys(location_keys, by_key, prev_groups, is_afternoon)
        base_fp = _geometry_fingerprint(route, location_keys, points)
        fp_now = (base_fp, tuple(seq_keys))
        if pre["fingerprint"] == fp_now and pre["computed"] is not None:
            computed = pre["computed"]
            for key in computed["seq_keys"]:
                for st in by_key[key]:
                    _insert_stop(conn, route_id, _stop_label(st), computed["orders"][key],
                                 computed["group_times"][key], st["home_lat"], st["home_lng"],
                                 False, st["id"])
            _insert_stop(conn, route_id, gate_name, computed["gate_order"],
                         computed["gate_time"],
                         route["school_lat"], route["school_lng"], True, None)
            degraded = bool(computed.get("degraded"))
            # Order preserved either way; a recompute on the offline duration
            # estimate is still flagged through the durable channel (the
            # non-convergent-solve precedent) — never silent.
            conn.execute(
                "update live_routes set last_recalc_degraded = %s, "
                "stops_computed = true, total_duration_s = %s where id = %s",
                (degraded, computed.get("total_duration_s"), route_id),
            )
            if degraded:
                logger.warning(
                    "route %s plan-ordered recompute used the offline duration "
                    "estimate; order preserved, times recomputed degraded", route_id
                )
                return False
            return True
        if pre["fingerprint"] in (fp_now, base_fp) and pre["degraded_reason"]:
            # Precondition failure (uncoordinated group / unlocated gate):
            # phase 1 named it before deriving the fixed sequence.
            degraded_reason = pre["degraded_reason"]
        else:
            # The route or its students drifted between the unlocked compute
            # and the locked re-read: the computed times are stale — discard
            # and fall back to the preservation path observably.
            degraded_reason = "route/students drifted during recalculation"

    # --- Geometry path (auto routes with something to compute) ---------------
    if is_auto and n_locations > 0:
        if pre["fingerprint"] is not None and pre["fingerprint"] == _geometry_fingerprint(
            route, location_keys, points
        ):
            if pre["computed"] is not None:
                computed = pre["computed"]
                for key in computed["seq_keys"]:
                    for st in by_key[key]:
                        _insert_stop(conn, route_id, _stop_label(st), computed["orders"][key],
                                     computed["group_times"][key], st["home_lat"], st["home_lng"],
                                     False, st["id"])
                _insert_stop(conn, route_id, gate_name, computed["gate_order"],
                             computed["gate_time"],
                             route["school_lat"], route["school_lng"], True, None)
                # The best-iterate geometry is written either way; a non-convergent
                # morning solve (U4) is still flagged degraded through the durable
                # channel — never silent.
                converged = computed.get("converged", True)
                conn.execute(
                    "update live_routes set last_recalc_degraded = %s, "
                    "stops_computed = true, total_duration_s = %s where id = %s",
                    (not converged, computed.get("total_duration_s"), route_id),
                )
                if not converged:
                    logger.warning(
                        "route %s bell-anchor backward solve did not converge; "
                        "wrote the best-error iterate", route_id
                    )
                    return False
                return True
            degraded_reason = pre["degraded_reason"]
        else:
            # The route or its students drifted between the unlocked compute
            # and the locked re-read: the computed geometry is stale —
            # discard it and fall back observably. The mutation that caused
            # the drift regenerates again with fresh inputs.
            degraded_reason = "route/students drifted during recalculation"

    # --- Fallback / preservation ---------------------------------------------
    # "Previously computed" is read off the prior gate row's scheduled_time:
    # only the geometry path writes one, and this fallback re-writes it as-is,
    # so the marker survives degraded rebuilds in between. Manual routes (U7)
    # preserve unconditionally — the admin's order is authoritative.
    # U3: read the explicit marker, not the gate-row-time inference. Once admins
    # can type a gate time (U4), "the gate row carries a scheduled_time" no longer
    # means "geometry was computed"; the column is backfilled at 009 to match the
    # old inference exactly at the cutover. prev_gate_time is still used below to
    # re-write the gate row's preserved time.
    previously_computed = bool(route["stops_computed"])
    preserve = previously_computed or is_manual or is_plan
    resolved: dict[str, dict] = {}
    if preserve:
        # Surviving groups keep their previous relative order (claim-once
        # location-key/student-alias resolution) and new groups append — the
        # shared helper, so this fallback and the plan-ordered fixed sequence
        # can never disagree (U8).
        final_keys, resolved = _surviving_plus_new_keys(
            location_keys, by_key, prev_groups, is_afternoon
        )
    else:
        # Never computed: the original pickup-time-then-name build, wholesale
        # (reversed for afternoon routes — first pickup is dropped last),
        # writing current pickup times so time edits keep applying.
        final_keys = list(reversed(location_keys)) if is_afternoon else list(location_keys)

    student_base = 2 if (is_afternoon and has_gate) else 1
    gate_order = 1 if (is_afternoon and has_gate) else (len(final_keys) + 1 if has_gate else None)
    for idx, key in enumerate(final_keys):
        rec = resolved.get(key) if preserve else None
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

    if (is_auto or is_plan) and n_locations > 0:
        # Observable degradation (R10; plan-ordered routes share the auto
        # path's semantics, U8): the durable flag drives the route card's
        # warning badge and survives reloads; the return value threads
        # stops_recalculated: false into the mutation response.
        conn.execute(
            "update live_routes set last_recalc_degraded = true where id = %s", (route_id,)
        )
        logger.warning(
            "route %s stop recalculation degraded (%s); fell back to %s",
            route_id,
            degraded_reason,
            "preserved previous order and times"
            if (previously_computed or is_plan) else "pickup-time order",
        )
        return False
    if is_auto or is_plan:
        # Empty route: nothing to compute is not a degradation.
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
    (custom_stops > manual_stop_order > plan_ordered > auto). A manual reorder
    on a plan-ordered route deliberately leaves ``plan_ordered`` set: manual
    now outranks it, and only the explicit recalculate clears both flags (U8).
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
    """Explicit return to automatic ordering (U7, R11; fleet-plan U8): clear
    ``manual_stop_order`` AND ``plan_ordered`` under the route-row lock, then
    regenerate immediately through the U6 path. This is the ONE release
    action for both freezes — an admin's manual order and an applied plan's
    order — so Google re-ordering only ever happens on explicit request; the
    UI fences the plan-ordered case behind copy warning that the applied
    plan's order is discarded. Returns its ``stops_recalculated`` signal
    (False = the rebuild fell back instead of computing geometry).

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
        "update live_routes set manual_stop_order = false, plan_ordered = false "
        "where id = %s", (route_id,)
    )
    return regenerate_route_stops(conn, route_id)


def _check_route_bus_conflict(
    conn, bus_id: str | None, route_type: str, trip_index: int = 1,
    exclude_route_id: str | None = None,
) -> None:
    """One route per (bus, type, trip_index) (U6/R19: multi-trip). This friendly
    pre-check names the conflicting route and bus; the partial unique index
    live_routes_bus_type_key (now 3-column, migration 009) is the race-proof
    backstop. A bus may hold several trips per period as long as each carries a
    distinct trip_index."""
    from app.core.errors import ConflictError

    if not bus_id:
        return
    exclude_sql = " and r.id <> %s" if exclude_route_id else ""
    params: list = [bus_id, route_type, trip_index]
    if exclude_route_id:
        params.append(exclude_route_id)
    existing = conn.execute(
        "select r.name as route_name, b.name as bus_name "
        "from live_routes r join live_buses b on b.id = r.bus_id "
        f"where r.bus_id = %s and r.type = %s and r.trip_index = %s{exclude_sql} limit 1",
        params,
    ).fetchone()
    if existing:
        raise ConflictError(
            f"Bus {existing['bus_name']} already has a {route_type} trip {trip_index} "
            f"({existing['route_name']}) — give this trip a different trip number"
        )


_TURNAROUND_BUFFER_MIN_DEFAULT = 15


def _hhmm_to_min(hhmm: str) -> int:
    h, m = (int(x) for x in hhmm.split(":")[:2])
    return h * 60 + m


def _check_turnaround_feasibility(conn, bus_id: str | None, route_type: str) -> None:
    """Flag an infeasible multi-trip chain for one (bus, period) (U6/R20). Each
    later trip's solved departure must be >= the prior trip's BUS-FREE time plus
    the turnaround buffer. Bus-free is period-asymmetric: morning = the gate
    ARRIVAL (the anchor); afternoon = last-dropoff ETA + the return-to-gate
    deadhead — the buffer stands in for that unmodeled return leg. Infeasible
    trips persist last_recalc_degraded=true + WARN through the durable channel;
    never a hard block (an admin may knowingly stage a tight chain). Runs after
    regeneration, which clears the flag on a converged solve, so this is the
    authority on chain feasibility."""
    if not bus_id:
        return
    from app.core.config import get_settings

    buffer_min = getattr(get_settings(), "turnaround_buffer_min", _TURNAROUND_BUFFER_MIN_DEFAULT)
    is_afternoon = route_type == "afternoon"
    trips = conn.execute(
        "select r.id, r.gate_anchor, r.type, r.total_duration_s, "
        "s.morning_bell, s.afternoon_bell "
        "from live_routes r left join live_schools s on s.id = r.school_id "
        "where r.bus_id = %s and r.type = %s",
        (bus_id, route_type),
    ).fetchall()
    if len(trips) < 2:
        return  # a single trip is always feasible
    trips = sorted(trips, key=lambda t: _hhmm_to_min(resolve_gate_anchor(t)))
    for prev, cur in zip(trips, trips[1:]):
        prev_anchor, cur_anchor = _hhmm_to_min(resolve_gate_anchor(prev)), _hhmm_to_min(resolve_gate_anchor(cur))
        prev_drive = round((prev["total_duration_s"] or 0) / 60)
        cur_drive = round((cur["total_duration_s"] or 0) / 60)
        if is_afternoon:
            # bus is free after driving the whole route out (return deadhead in
            # the buffer); the next afternoon trip departs at its own anchor.
            bus_free, cur_departure = prev_anchor + prev_drive, cur_anchor
        else:
            # bus is free at the gate arrival (the anchor); the next morning trip
            # departs its solved drive before its anchor.
            bus_free, cur_departure = prev_anchor, cur_anchor - cur_drive
        if cur_departure < bus_free + buffer_min:
            conn.execute(
                "update live_routes set last_recalc_degraded = true where id = %s", (cur["id"],)
            )
            logger.warning(
                "turnaround infeasible: bus %s %s trip %s departs before the prior "
                "trip is free + %d min buffer", bus_id, route_type, cur["id"], buffer_min
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
    # U3: a planner-saved route is "computed" — mark it so a later
    # student-assignment handover (which flips custom_stops off and regenerates)
    # takes the preservation path rather than a wholesale rebuild. Mirrors the
    # marker's gate-time semantics (true iff the gate row carries a time).
    conn.execute(
        "update live_routes set stops_computed = exists ("
        "select 1 from live_route_stops s where s.route_id = %s "
        "and s.is_school_gate and s.scheduled_time is not null"
        ") where id = %s",
        (route_id, route_id),
    )


def _check_driver_at_school(conn, driver_id: str, school_id: str) -> None:
    """A bus's driver must hold an ACTIVE driver membership at the bus's own
    school (R28). The membership row is locked so a concurrent driver removal
    serializes with the assignment; the caller locks (or inserts) the bus row
    FIRST — bus before membership is the one cross-table order every path
    takes (see also delete_driver), the apply_restore sorted-lock discipline.
    The refusal wording is neutral by design: it must never name, or even
    imply the existence of, another school (R34's sibling rule).
    """
    row = conn.execute(
        "select id from school_memberships "
        "where user_id = %s and school_id = %s and role = 'driver' "
        "and state = 'active' and removed_at is null "
        "order by id limit 1 for update",
        (driver_id, school_id),
    ).fetchone()
    if not row:
        raise ConflictError("That driver is not available for this school")


class FleetDao:
    # --- buses -------------------------------------------------------------

    def list_buses(self, scope: SchoolScope) -> list[dict[str, Any]]:
        with get_connection(scope) as conn:
            # derived_status (U9) replaces the hand-maintained status column as
            # the value every admin surface reads. The raw column still travels
            # in the payload (select *) but nothing should render it.
            rows = conn.execute(
                f"select b.*, {bus_status_case('b')} as derived_status "
                "from live_buses b where b.school_id = %s order by b.name asc",
                (scope.school_id,),
            ).fetchall()
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

    def create_bus(self, scope: SchoolScope, data: dict, actor: dict) -> dict[str, Any]:
        with get_connection(scope) as conn:
            # school_id comes from the scope, never the client (U6). The
            # insert claims the new row before the membership check locks the
            # driver's row (bus -> membership, the one cross-table order).
            row = conn.execute(
                """
                insert into live_buses (name, plate_number, driver_id, driver_name, driver_phone,
                    capacity, availability, depot_lat, depot_lng, depot_address, depot_provenance,
                    school_id)
                values (%(name)s, %(plate_number)s, %(driver_id)s, %(driver_name)s, %(driver_phone)s,
                        coalesce(%(capacity)s, 45), coalesce(%(availability)s, 'in-service'),
                        %(depot_lat)s, %(depot_lng)s, %(depot_address)s, %(depot_provenance)s,
                        %(school_id)s)
                returning *
                """,
                {**data, "school_id": scope.school_id},
            ).fetchone()
            if data.get("driver_id"):
                _check_driver_at_school(conn, data["driver_id"], scope.school_id)
            record_audit(
                conn, action="bus-created", actor=actor, scope=scope,
                resource_type="bus", resource_id=row["id"], detail={},
            )
        return dict(row)

    def update_bus(self, scope: SchoolScope, bus_id: str, data: dict, actor: dict) -> dict[str, Any]:
        """Update a bus. Note two deliberate omissions (U9).

        `status` is no longer written by any path — it is derived at read time
        from the bus's current run. This mirrors the fix applied when the admin
        student edit was found silently resetting a live child's status: an
        operational field must not have a CRUD surface writing it.

        `availability` coalesces to its own stored value rather than to a
        default, so a caller that omits the key leaves it alone. FleetMapPage
        saves a depot by re-sending the bus's whole field set, and a coalesce to
        'in-service' would quietly return an out-of-service bus to service on
        every depot move — the same silent-reset defect in a new place.

        U6: scoped by school — another school's bus does not exist here (404);
        the bus row is locked before the driver-membership check (the one
        cross-table order), so an assignment cannot race a driver removal.
        """
        with get_connection(scope) as conn:
            before = conn.execute(
                "select depot_lat, depot_lng from live_buses "
                "where id = %s and school_id = %s for update",
                (bus_id, scope.school_id),
            ).fetchone()
            if not before:
                raise NotFoundError("Bus not found")
            if data.get("driver_id"):
                _check_driver_at_school(conn, data["driver_id"], scope.school_id)
            row = conn.execute(
                """
                update live_buses set
                    name = %(name)s, plate_number = %(plate_number)s, driver_id = %(driver_id)s,
                    driver_name = %(driver_name)s, driver_phone = %(driver_phone)s,
                    capacity = coalesce(%(capacity)s, 45),
                    availability = coalesce(%(availability)s, availability),
                    depot_lat = %(depot_lat)s, depot_lng = %(depot_lng)s,
                    depot_address = %(depot_address)s, depot_provenance = %(depot_provenance)s
                where id = %(id)s and school_id = %(school_id)s returning *
                """,
                {**data, "id": bus_id, "school_id": scope.school_id},
            ).fetchone()
            record_audit(
                conn, action="bus-updated", actor=actor, scope=scope,
                resource_type="bus", resource_id=bus_id, detail={},
            )
            # A depot move changes the boundary-trip geometry (U7): regenerate
            # this bus's routes so the first-morning origin / last-afternoon
            # destination leg is recomputed. order by id: the global lock order.
            depot_changed = before is not None and (
                before["depot_lat"] != row["depot_lat"] or before["depot_lng"] != row["depot_lng"]
            )
            route_ids = []
            if depot_changed:
                route_ids = [
                    r["id"] for r in conn.execute(
                        "select id from live_routes where bus_id = %s order by id", (bus_id,)
                    ).fetchall()
                ]
        # One transaction per route (mirrors update_school): a depot edit fans
        # out to each affected route's regeneration + provider calls.
        for route_id in route_ids:
            with get_connection(scope) as conn:
                regenerate_route_stops(conn, route_id)
                _check_turnaround_feasibility(conn, bus_id, "morning")
                _check_turnaround_feasibility(conn, bus_id, "afternoon")
        return dict(row)

    def delete_bus(self, scope: SchoolScope, bus_id: str, actor: dict) -> None:
        with get_connection(scope) as conn:
            row = conn.execute(
                "delete from live_buses where id = %s and school_id = %s returning id",
                (bus_id, scope.school_id),
            ).fetchone()
            if not row:
                raise NotFoundError("Bus not found")
            record_audit(
                conn, action="bus-deleted", actor=actor, scope=scope,
                resource_type="bus", resource_id=bus_id, detail={},
            )

    # --- schools -----------------------------------------------------------

    def list_schools(self) -> list[dict[str, Any]]:
        # Catalog read (live_schools carries no row policy). The router no
        # longer exposes it wholesale; route_options still resolves a school's
        # bells/coordinates through it until the route family converts (U7).
        with get_connection() as conn:
            rows = conn.execute("select * from live_schools order by name asc").fetchall()
        return [dict(r) for r in rows]

    def get_school(self, scope: SchoolScope) -> dict[str, Any]:
        """The active school's settings row (R4): the scope IS the id."""
        with get_connection(scope) as conn:
            row = conn.execute(
                "select * from live_schools where id = %s", (scope.school_id,)
            ).fetchone()
        if not row:
            raise NotFoundError("School not found")
        return dict(row)

    def update_school(self, scope: SchoolScope, data: dict, actor: dict) -> dict[str, Any]:
        """Update the ACTIVE school's settings (U6): the router already pinned
        the path id to scope.school_id, and the predicate repeats it."""
        with get_connection(scope) as conn:
            row = conn.execute(
                "update live_schools set name=%(name)s, address=%(address)s, phone=%(phone)s, "
                "lat=%(lat)s, lng=%(lng)s, morning_bell=%(morning_bell)s, "
                "afternoon_bell=%(afternoon_bell)s where id=%(id)s returning *",
                {**data, "id": scope.school_id},
            ).fetchone()
            if not row:
                raise NotFoundError("School not found")
            record_audit(
                conn, action="school-updated", actor=actor, scope=scope,
                resource_type="school", resource_id=scope.school_id, detail={},
            )
            # order by id: the global route-lock order (student_live_dao's
            # _sync_routes) — concurrent multi-route writers cannot deadlock.
            route_ids = [
                r["id"]
                for r in conn.execute(
                    "select id from live_routes where school_id = %s order by id",
                    (scope.school_id,),
                ).fetchall()
            ]
        # One transaction per route: a school edit fans out to every route's
        # regeneration — provider calls included — and must not hold N route
        # locks (nor park the committed school row behind them) for the whole
        # sweep. Each route rebuilds and commits independently; regeneration
        # tolerates a route deleted in between (early-returns True).
        for route_id in route_ids:
            with get_connection(scope) as conn:
                regenerate_route_stops(conn, route_id)
        return dict(row)

    # --- routes ------------------------------------------------------------

    def list_routes(self) -> list[dict[str, Any]]:
        with get_connection() as conn:
            routes = conn.execute("select * from live_routes order by name asc").fetchall()
            result = []
            for route in routes:
                stops = conn.execute(
                    """
                    select rs.*, st.home_lat as _home_lat, st.home_lng as _home_lng,
                           st.home_address as _home_address,
                           st.pickup_time as student_pickup_time
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
                    # student_pickup_time (the student's own attribute; NULL on
                    # gate and planner stops) stays on each stop row so the
                    # admin time editor prefills the INPUT value rather than
                    # the computed ETA in scheduled_time.
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
            _check_route_bus_conflict(
                conn, data.get("bus_id"), data.get("type") or "morning", data.get("trip_index") or 1
            )
            # A planner save carries its own stops: flag the route custom,
            # keep the polyline/totals, write the stops verbatim and skip
            # student-based regeneration (R17/R18).
            custom = bool(data.get("stops"))
            row = conn.execute(
                "insert into live_routes (name, type, bus_id, school_id, gate_anchor, trip_index, "
                "custom_stops, polyline, total_distance_m, total_duration_s) "
                "values (%(name)s, coalesce(%(type)s,'morning'), %(bus_id)s, %(school_id)s, "
                "%(gate_anchor)s, coalesce(%(trip_index)s, 1), %(custom_stops)s, %(polyline)s, "
                "%(total_distance_m)s, %(total_duration_s)s) returning *",
                {
                    **data,
                    "custom_stops": custom,
                    "polyline": data.get("polyline") if custom else None,
                    "total_distance_m": data.get("total_distance_m") if custom else None,
                    "total_duration_s": data.get("total_duration_s") if custom else None,
                },
            ).fetchone()
            stops_recalculated = True
            if custom:
                _write_custom_stops(conn, row["id"], data["stops"])
            else:
                stops_recalculated = regenerate_route_stops(conn, row["id"])
            # Turnaround feasibility across this bus's period chain (U6/R20) —
            # after regeneration, which clears the flag on a converged solve.
            _check_turnaround_feasibility(conn, row["bus_id"], row["type"])
        # Observable degradation (U6/R10): false when the rebuild fell back —
        # same signal shape as update_route.
        return {**dict(row), "stops_recalculated": stops_recalculated}

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
                data.get("trip_index") or 1, exclude_route_id=route_id,
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
                    "bus_id=%(bus_id)s, school_id=%(school_id)s, gate_anchor=%(gate_anchor)s, "
                    "trip_index=coalesce(%(trip_index)s, 1), "
                    "custom_stops=true, manual_stop_order=false, last_recalc_degraded=false, "
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
                    "bus_id=%(bus_id)s, school_id=%(school_id)s, gate_anchor=%(gate_anchor)s, "
                    "trip_index=coalesce(%(trip_index)s, 1) where id=%(id)s returning *",
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
                # Re-evaluate turnaround feasibility for the route's (new) chain,
                # and for the chain it may have left (bus/type change).
                _check_turnaround_feasibility(conn, row["bus_id"], row["type"])
                if current["bus_id"] != row["bus_id"] or current["type"] != row["type"]:
                    _check_turnaround_feasibility(conn, current["bus_id"], current["type"])
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

        - auto: regenerate. Under bell-anchoring (U4) a computed morning route
          is solved backwards from its gate anchor, so a pickup_time edit no
          longer moves its schedule — it only affects the canonical stop sort
          (``_assigned_students``) and the degraded/never-computed fallback
          order. It never re-anchors a computed morning departure any more.
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
