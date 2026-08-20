"""Shared fleet-plan vocabulary and helpers (split from ``fleet_plan_dao``).

Constraint/diff/drift constants, the two typed 422 errors, wall-clock
arithmetic (U5), the shared plan-vs-live diff (R24 preview = R15 fan-out),
the as-evolved live capture SQL (U6/R21) and the document mutation +
recompute helpers (U5). ``app.dao.fleet_plan_dao`` remains the import path
and re-exports every name defined here — consumers (the router, push
fan-out's lazy imports) never import this module directly.
"""
import logging
import math
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Mapping

from app.core.errors import BadRequestError, NotFoundError, SafeRideError
from app.dao.fleet_dao import resolve_gate_anchor
from app.services import geo_service, plan_solver

logger = logging.getLogger("saferide.fleet_plans")

# Constraint name for students excluded from the solver input because their
# home has no coordinates (unresolved triage rows, R7/U4).
UNRESOLVED_ADDRESS_CONSTRAINT = "unresolved address"
# Constraint name for a leg a pattern edit widened onto with no mirror-leg
# placement to inherit: nothing binds — the child simply awaits a manual
# assign (U5's pre-slot-in placement path).
UNASSIGNED_CONSTRAINT = "unassigned"

# Review-edit hard-constraint names (R9): the 422 detail leads with one of
# these two tokens verbatim. 'seats' stays the SOLVER's unplaceable
# attribution vocabulary; the edit gates use the product's words.
CONSTRAINT_CAPACITY = "capacity"
CONSTRAINT_STOP_CAP = "stop cap"

# R15: a communicated stop that moves by at least this many minutes notifies.
NOTIFY_MOVE_THRESHOLD_MIN = 5

# Shared diff categories (R24/R15). U6's fan-out consumes exactly this list:
# the first four map to 'route-updated' feed rows, the last two to
# 'route-unassigned'. See diff_plan_vs_live for the semantics of each.
DIFF_BUS_CHANGE = "bus-change"
DIFF_TIME_MOVE = "time-move"
DIFF_PLACE_CHANGE = "place-change"
DIFF_FIRST_COMMUNICATION = "first-communication"
DIFF_NEWLY_UNPLACEABLE = "newly-unplaceable"
DIFF_LEG_REMOVED = "leg-removed"

_META_COLUMNS = "id, status, created_at, applied_at"

# R22 basis-drift kinds (U6): each drift item since generation is confirmed as
# (kind, student_id) in the apply payload, or the apply 409s listing it.
DRIFT_ENROLLED = "enrolled"
DRIFT_ADDRESS_CHANGED = "address-changed"
DRIFT_DEPARTED = "departed"

# Coordinates closer than this (degrees, ~1 cm) are the same place: guards
# the place-change diff against float round-trip noise, nothing more.
_COORD_EPS = 1e-7


class PlanConstraintError(SafeRideError):
    """A review edit violates a hard constraint (R9): 422 with the constraint
    NAMED at the head of the detail (CONSTRAINT_CAPACITY or
    CONSTRAINT_STOP_CAP) and the draft document left unchanged — every edit
    mutates a working copy inside one transaction, so raising rolls the whole
    edit back. Mirrors the closure-gate refusal shape: a typed error the
    router maps straight to its status."""

    status_code = 422


class UnacknowledgedUnplaceableError(SafeRideError):
    """Apply refused (R23): unplaceable children were not acknowledged by
    name — 422 with every missing (name, leg) listed, matching the review
    surface's constraint shape. Distinct from PlanConstraintError so the two
    422 families keep their own vocabulary."""

    status_code = 422


def _leg_label(leg: str) -> str:
    """Parent-facing leg wording for the apply fan-out bodies."""
    return "Morning pickup" if leg == plan_solver.LEG_MORNING else "Afternoon drop-off"


def _pattern_legs(pattern: str | None) -> tuple[str, ...]:
    """The legs a ridership pattern rides — for naming a coordinate-less
    student unplaceable per leg, matching the solver's per-leg output shape."""
    if pattern == plan_solver.PATTERN_MORNING:
        return (plan_solver.LEG_MORNING,)
    if pattern == plan_solver.PATTERN_AFTERNOON:
        return (plan_solver.LEG_AFTERNOON,)
    return (plan_solver.LEG_MORNING, plan_solver.LEG_AFTERNOON)


def _multi_trip_bus_ids(conn, bus_ids: list) -> set[str]:
    """Buses holding any trip_index >= 2 route: multi-trip chains are out of
    drafting scope (Scope Boundaries) — excluded with a named notice."""
    if not bus_ids:
        return set()
    rows = conn.execute(
        "select distinct bus_id from live_routes "
        "where bus_id = any(%s::uuid[]) and trip_index >= 2",
        ([str(b) for b in bus_ids],),
    ).fetchall()
    return {str(r["bus_id"]) for r in rows}


# --- wall-clock arithmetic (U5) ----------------------------------------------
# The stored document carries DURATIONS only (seconds); wall-clock times are
# pure read-time arithmetic against the school's gate anchors — no provider
# calls on read. resolve_gate_anchor stays the single arrive-by authority.


def plan_gate_anchors(school: Mapping[str, Any]) -> dict[str, str]:
    """The school's gate anchors per leg (HH:MM), through the ONE authority
    (`resolve_gate_anchor`): school bell for the direction, else the system
    default. A plan has no route-level override — routes don't exist yet."""
    return {
        leg: resolve_gate_anchor({
            "type": leg if leg == "afternoon" else "morning",
            "gate_anchor": None,
            "morning_bell": school.get("morning_bell"),
            "afternoon_bell": school.get("afternoon_bell"),
        })
        for leg in plan_solver.LEGS
    }


def _hhmm_to_minutes(hhmm: Any) -> int | None:
    """Defensive HH:MM -> minutes-from-midnight (None on malformed/absent)."""
    if not hhmm or not isinstance(hhmm, str):
        return None
    parts = hhmm.split(":")
    try:
        h, m = int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return h * 60 + m


def _shift_hhmm(hhmm: str, delta_seconds: float) -> str:
    """``hhmm`` shifted by ``delta_seconds`` (negative = earlier), floored to
    the minute, wrapped on the 24 h clock. Date-independent wall-clock, like
    every other communicated time in the product."""
    base_min = _hhmm_to_minutes(hhmm) or 0
    total_min = math.floor((base_min * 60 + delta_seconds) / 60) % (24 * 60)
    return f"{total_min // 60:02d}:{total_min % 60:02d}"


def _stop_key(stop: Mapping[str, Any]) -> str:
    """Server-issued identity of a document stop, echoed back by the reorder
    payload (the shipped stop-order contract's shape). Solver stops collapse
    within 30 m, so six decimals (~0.1 m) can never collide."""
    return f"{float(stop['lat']):.6f},{float(stop['lng']):.6f}"


def _leg_rides(leg_doc: Mapping[str, Any]) -> dict[str, float]:
    return {
        str(r["student_id"]): r["ride_seconds"]
        for r in (leg_doc.get("ride_seconds") or [])
    }


def computed_stop_times(document: Mapping[str, Any], school: Mapping[str, Any]) -> dict:
    """Per-(student, leg) computed schedule from the stored document —
    ``{(student_id, leg): {bus_id, bus_name, stop_name, lat, lng,
    ride_seconds, scheduled_time}}``.

    Pure arithmetic from document durations: the AM leg walks BACKWARD from
    the gate anchor (a stop's ride_seconds IS the cumulative drive from that
    stop to the gate, so its pickup time is anchor − ride), the PM leg walks
    FORWARD (anchor + ride). HH:MM Nairobi wall clock; no provider calls."""
    anchors = plan_gate_anchors(school)
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for bus in document.get("buses") or []:
        for leg in plan_solver.LEGS:
            leg_doc = (bus.get("legs") or {}).get(leg) or {}
            rides = _leg_rides(leg_doc)
            sign = -1 if leg == plan_solver.LEG_MORNING else 1
            for stop in leg_doc.get("stops") or []:
                for s in stop.get("students") or []:
                    ride = float(rides.get(str(s["id"]), 0.0))
                    out[(str(s["id"]), leg)] = {
                        "bus_id": str(bus["bus_id"]),
                        "bus_name": bus.get("bus_name"),
                        "stop_name": stop.get("name"),
                        "lat": stop.get("lat"),
                        "lng": stop.get("lng"),
                        "ride_seconds": ride,
                        "scheduled_time": _shift_hhmm(anchors[leg], sign * ride),
                    }
    return out


# --- shared diff vs live (R24 preview = R15 fan-out) --------------------------


def _floats_differ(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return (a is None) != (b is None)
    return abs(float(a) - float(b)) > _COORD_EPS


def _place_differs(a_lat: Any, a_lng: Any, b_lat: Any, b_lng: Any) -> bool:
    """Physical place comparison for the place-change diff. A located and an
    unlocated side differ; two located points are the SAME place within
    ``plan_solver.STOP_COLLAPSE_RADIUS_M`` (haversine metres — the solver's
    own collapse radius, so a stop re-labelled or float-jittered inside one
    collapse cluster never reads as a move); two unlocated points do not
    differ HERE — the caller falls back to names for those, mirroring the
    manual-edit writer's guard in push_service."""
    a_located = a_lat is not None and a_lng is not None
    b_located = b_lat is not None and b_lng is not None
    if a_located != b_located:
        return True
    if not a_located:
        return False
    return (
        geo_service.haversine_m(
            (float(a_lat), float(a_lng)), (float(b_lat), float(b_lng))
        )
        > plan_solver.STOP_COLLAPSE_RADIUS_M
    )


def diff_plan_vs_live(
    conn, document: Mapping[str, Any], school: Mapping[str, Any]
) -> dict[str, Any]:
    """Diff a plan document against live truth — THE shared diff function.

    One implementation feeds both the R24 review preview (U5) and apply's
    notification fan-out (U6), so the previewed count and the actual sends
    cannot drift. ``conn`` is the caller's connection: U6 threads its apply
    transaction's connection straight through.

    Categories — U6's fan-out consumes exactly this list (the first four map
    to ``route-updated`` feed rows, the last two to ``route-unassigned``):

    * ``bus-change`` — the child's document bus differs from their LIVE
      membership bus for that leg (live_student_routes → live_routes.bus_id).
      Notifies even when the stop time is unchanged (R15).
    * ``time-move`` — the computed wall-clock stop time moved by at least
      ``NOTIFY_MOVE_THRESHOLD_MIN`` minutes against the communicated baseline
      (live_communicated_stops.scheduled_time) for that (student, leg). A
      baseline row whose time is NULL/malformed counts as moved — the family
      is being told a time they were never told.
    * ``place-change`` — the stop physically moved vs the baseline: located
      coordinates beyond the solver's collapse radius apart (or coordinate
      presence flipped); names are compared ONLY when both sides are wholly
      coordinate-less — the manual-edit writer's guard, shared, so a
      re-labelled stop at the same place never notifies.
    * ``first-communication`` — the child is placed but has NO baseline row
      for that leg. Null baseline notifies (R15): the whole school on first
      apply is intended.
    * ``newly-unplaceable`` — the document lists the child unplaceable for a
      leg where they have live membership OR a baseline. A child unplaceable
      with NEITHER is not re-notified (R15's still-unplaceable rule).
    * ``leg-removed`` — a baseline row exists for a (student, leg) the
      document no longer serves at all (neither placed nor listed
      unplaceable — e.g. a pattern narrowed in review).

    Returns ``{rows, notified_family_count, notified_families}``. Each row is
    one notified (student, leg)::

        {student_id, student_name, leg, categories: [..], current, baseline,
         live_bus_id, family_ids}

    ``current`` carries the document's new stop (bus id/name, stop name,
    coords, computed HH:MM time) for placed legs and is None otherwise;
    ``baseline`` mirrors the live_communicated_stops row (None when absent);
    ``family_ids`` are the child's linked parent accounts. The
    ``notified_family_count`` is the DISTINCT parent count across all rows
    via live_parent_students — a family with two affected children counts
    once, and a child without a linked account contributes no family (the
    feed-row truth U6 writes)."""
    school_id = str(school["id"])
    placements = computed_stop_times(document, school)
    unplaceable: dict[tuple[str, str], dict] = {}
    for u in document.get("unplaceable") or []:
        unplaceable[(str(u["student_id"]), u["leg"])] = u

    membership: dict[tuple[str, str], str | None] = {}
    for r in conn.execute(
        "select sr.student_id, sr.route_type, r.bus_id "
        "from live_student_routes sr "
        "join live_routes r on r.id = sr.route_id "
        "join live_students st on st.id = sr.student_id "
        "where st.school_id = %s",
        (school_id,),
    ).fetchall():
        membership[(str(r["student_id"]), r["route_type"])] = (
            str(r["bus_id"]) if r["bus_id"] is not None else None
        )

    baselines: dict[tuple[str, str], dict] = {}
    for b in conn.execute(
        "select c.student_id, c.route_type, c.stop_name, c.stop_lat, c.stop_lng, "
        "c.scheduled_time, c.bus_id, st.name as student_name "
        "from live_communicated_stops c "
        "join live_students st on st.id = c.student_id "
        "where st.school_id = %s",
        (school_id,),
    ).fetchall():
        baselines[(str(b["student_id"]), b["route_type"])] = dict(b)

    def baseline_out(base: dict | None) -> dict | None:
        if base is None:
            return None
        return {
            "stop_name": base["stop_name"],
            "lat": base["stop_lat"],
            "lng": base["stop_lng"],
            "scheduled_time": base["scheduled_time"],
            "bus_id": str(base["bus_id"]) if base["bus_id"] is not None else None,
        }

    rows: list[dict] = []

    def add_row(sid: str, name: str, leg: str, categories: list[str],
                current: dict | None, base: dict | None) -> None:
        rows.append({
            "student_id": sid,
            "student_name": name,
            "leg": leg,
            "categories": categories,
            "current": current,
            "baseline": baseline_out(base),
            "live_bus_id": membership.get((sid, leg)),
        })

    # Placed legs: bus change vs membership; time/place vs baseline.
    for (sid, leg), cur in placements.items():
        categories: list[str] = []
        live_bus = membership.get((sid, leg))
        if (sid, leg) in membership and live_bus is not None and live_bus != cur["bus_id"]:
            categories.append(DIFF_BUS_CHANGE)
        base = baselines.get((sid, leg))
        if base is None:
            categories.append(DIFF_FIRST_COMMUNICATION)
        else:
            old_min = _hhmm_to_minutes(base["scheduled_time"])
            new_min = _hhmm_to_minutes(cur["scheduled_time"])
            if old_min is None or (
                new_min is not None
                and abs(new_min - old_min) >= NOTIFY_MOVE_THRESHOLD_MIN
            ):
                categories.append(DIFF_TIME_MOVE)
            if _place_differs(base["stop_lat"], base["stop_lng"], cur["lat"], cur["lng"]):
                categories.append(DIFF_PLACE_CHANGE)
            elif (
                base["stop_lat"] is None
                and base["stop_lng"] is None
                and cur["lat"] is None
                and cur["lng"] is None
                and (base["stop_name"] or "") != (cur["stop_name"] or "")
            ):
                # Names decide only for wholly coordinate-less stops — the
                # manual-edit writer's rule: baselines may carry a different
                # naming convention (labels vs joined names) for the same
                # physical place.
                categories.append(DIFF_PLACE_CHANGE)
        if categories:
            # The placed student's name travels in the document stop rows.
            name = next(
                (s["name"] for bus in document.get("buses") or []
                 for stop in ((bus.get("legs") or {}).get(leg) or {}).get("stops") or []
                 for s in stop.get("students") or [] if str(s["id"]) == sid),
                sid,
            )
            add_row(sid, name, leg, categories, cur, base)

    # Unplaceable legs: only a child the live world knows about re-notifies.
    for (sid, leg), u in unplaceable.items():
        if (sid, leg) in membership or (sid, leg) in baselines:
            add_row(sid, u.get("name") or sid, leg, [DIFF_NEWLY_UNPLACEABLE],
                    None, baselines.get((sid, leg)))

    # Baselines for legs the document no longer serves at all.
    for (sid, leg), base in baselines.items():
        if (sid, leg) in placements or (sid, leg) in unplaceable:
            continue
        add_row(sid, base.get("student_name") or sid, leg, [DIFF_LEG_REMOVED],
                None, base)

    rows.sort(key=lambda r: (r["student_name"], r["student_id"],
                             plan_solver.LEGS.index(r["leg"])))

    student_ids = sorted({r["student_id"] for r in rows})
    families: dict[str, list[str]] = {}
    if student_ids:
        for pr in conn.execute(
            "select parent_id, student_id from live_parent_students "
            "where student_id = any(%s::uuid[])",
            (student_ids,),
        ).fetchall():
            families.setdefault(str(pr["student_id"]), []).append(str(pr["parent_id"]))
    for r in rows:
        r["family_ids"] = sorted(families.get(r["student_id"], []))
    notified = sorted({pid for r in rows for pid in r["family_ids"]})
    return {
        "rows": rows,
        "notified_family_count": len(notified),
        "notified_families": notified,
    }


# --- as-evolved live capture (U6, R21) ------------------------------------------
# ONE SQL statement builds the displaced live state as a JSONB document, so the
# capture is a single consistent snapshot under READ COMMITTED — routes with
# their stop rows and memberships, plus the per-student pattern/bus/pickup
# truth, names denormalized throughout (the 007/010 name-rot precedent so a
# later restore can name departed children). This is AS-EVOLVED live truth —
# manual edits made while the displaced plan was live are captured — never the
# displaced plan's own stale document.

_CAPTURE_LIVE_SQL = """
select jsonb_build_object(
  'version', 1,
  'captured', 'as-evolved-live',
  'school', jsonb_build_object(
      'id', s.id, 'name', s.name, 'lat', s.lat, 'lng', s.lng,
      'morning_bell', s.morning_bell, 'afternoon_bell', s.afternoon_bell),
  'routes', coalesce((
      select jsonb_agg(jsonb_build_object(
          'id', r.id, 'name', r.name, 'type', r.type, 'trip_index', r.trip_index,
          'bus_id', r.bus_id, 'bus_name', b.name, 'gate_anchor', r.gate_anchor,
          'custom_stops', r.custom_stops, 'manual_stop_order', r.manual_stop_order,
          'plan_ordered', r.plan_ordered, 'stops_computed', r.stops_computed,
          'stops', coalesce((
              select jsonb_agg(jsonb_build_object(
                  'name', rs.name, 'stop_order', rs.stop_order,
                  'scheduled_time', rs.scheduled_time, 'lat', rs.lat, 'lng', rs.lng,
                  'is_school_gate', rs.is_school_gate, 'student_id', rs.student_id,
                  'student_name', st.name)
                order by rs.stop_order, rs.name)
              from live_route_stops rs
              left join live_students st on st.id = rs.student_id
              where rs.route_id = r.id), '[]'::jsonb),
          'students', coalesce((
              select jsonb_agg(jsonb_build_object('id', sr.student_id, 'name', st2.name)
                order by st2.name, sr.student_id)
              from live_student_routes sr
              join live_students st2 on st2.id = sr.student_id
              where sr.route_id = r.id), '[]'::jsonb))
        order by r.id)
      from live_routes r
      left join live_buses b on b.id = r.bus_id
      where r.school_id = s.id), '[]'::jsonb),
  'students', coalesce((
      select jsonb_agg(jsonb_build_object(
          'id', st.id, 'name', st.name, 'lat', st.home_lat, 'lng', st.home_lng,
          'pattern', st.ridership_pattern, 'bus_id', st.bus_id,
          'pickup_time', st.pickup_time)
        order by st.name, st.id)
      from live_students st where st.school_id = s.id), '[]'::jsonb)
) as document
from live_schools s
where s.id = %s
"""


# --- document mutation + recompute helpers (U5) --------------------------------


def _empty_leg_doc() -> dict[str, Any]:
    return {"stops": [], "ride_seconds": [], "driving_seconds": 0.0}


def _bus_doc(document: Mapping[str, Any], bus_id: str) -> dict:
    for bus in document.get("buses") or []:
        if str(bus["bus_id"]) == str(bus_id):
            return bus
    raise NotFoundError("Bus is not part of this plan")


def _find_placement(document: Mapping[str, Any], student_id: str, leg: str):
    """(bus_doc, stop_index) of the student's stop on ``leg``, or None."""
    for bus in document.get("buses") or []:
        leg_doc = (bus.get("legs") or {}).get(leg) or {}
        for i, stop in enumerate(leg_doc.get("stops") or []):
            if any(str(s["id"]) == str(student_id) for s in stop.get("students") or []):
                return bus, i
    return None


def _pop_student(bus_doc: dict, leg: str, student_id: str):
    """Remove the student from their stop on ``leg``; an emptied stop is
    dropped, a shared one keeps the siblings and re-derives its name.
    Returns ``(student_entry, (lat, lng))`` or ``(None, None)``."""
    leg_doc = bus_doc["legs"][leg]
    for i, stop in enumerate(leg_doc["stops"]):
        for j, s in enumerate(stop["students"]):
            if str(s["id"]) == str(student_id):
                entry = stop["students"].pop(j)
                coords = (stop["lat"], stop["lng"])
                if not stop["students"]:
                    leg_doc["stops"].pop(i)
                else:
                    stop["name"] = " / ".join(m["name"] for m in stop["students"])
                return entry, coords
    return None, None


def _add_student_to_leg(bus_doc: dict, leg: str, entry: dict,
                        lat: float, lng: float, position: int | None = None) -> None:
    """Join an existing same-place stop (siblings share a stop — the solver's
    collapse rule carried into edits; the given position is then moot) or
    insert a new stop at ``position`` (clamped; None appends)."""
    leg_doc = bus_doc["legs"][leg]
    key = f"{float(lat):.6f},{float(lng):.6f}"
    for stop in leg_doc["stops"]:
        if _stop_key(stop) == key:
            stop["students"].append(entry)
            stop["name"] = " / ".join(m["name"] for m in stop["students"])
            return
    stop = {"lat": float(lat), "lng": float(lng),
            "name": entry["name"], "students": [entry]}
    pos = len(leg_doc["stops"]) if position is None else max(
        0, min(int(position), len(leg_doc["stops"])))
    leg_doc["stops"].insert(pos, stop)


def _check_bus_leg(bus_doc: Mapping[str, Any], leg: str) -> None:
    """Hard constraints on one (bus, leg) after a mutation (R9): seat capacity
    counts children, the stop cap counts stops. Raises the 422 with the
    constraint named at the head of the message."""
    leg_doc = bus_doc["legs"][leg]
    children = sum(len(s["students"]) for s in leg_doc["stops"])
    capacity = bus_doc.get("capacity")
    if capacity is not None and children > capacity:
        raise PlanConstraintError(
            f"{CONSTRAINT_CAPACITY}: bus {bus_doc['bus_name']} would carry "
            f"{children} children on the {leg} leg — its capacity is {capacity}; "
            "the draft was left unchanged"
        )
    if len(leg_doc["stops"]) > plan_solver.DEFAULT_STOP_CAP:
        raise PlanConstraintError(
            f"{CONSTRAINT_STOP_CAP}: bus {bus_doc['bus_name']} would have "
            f"{len(leg_doc['stops'])} stops on the {leg} leg — the cap is "
            f"{plan_solver.DEFAULT_STOP_CAP}; the draft was left unchanged"
        )


def _validate_leg(leg: str) -> str:
    if leg not in plan_solver.LEGS:
        raise BadRequestError(f"Unknown leg '{leg}' — expected one of {list(plan_solver.LEGS)}")
    return leg


def _basis_student(basis: Mapping[str, Any], student_id: str) -> dict:
    for s in basis.get("students") or []:
        if str(s["id"]) == str(student_id):
            return s
    raise NotFoundError("Student is not part of this plan's basis")


def _effective_pattern(document: Mapping[str, Any], basis: Mapping[str, Any],
                       student_id: str) -> str:
    """The student's authoritative draft pattern: the document's review-edited
    override first (R20 — draft-scoped until apply), else the basis snapshot."""
    override = (document.get("patterns") or {}).get(str(student_id))
    if override:
        return override
    for s in basis.get("students") or []:
        if str(s["id"]) == str(student_id):
            return s.get("pattern") or plan_solver.PATTERN_BOTH
    return plan_solver.PATTERN_BOTH


def _recompute_legs(document: dict, basis: Mapping[str, Any],
                    targets: set[tuple[str, str]]) -> bool:
    """Refresh ride/driving durations for each edited (bus_id, leg) through
    the NEW fixed-sequence recompute (geo_service.fixed_sequence_geometry):
    durations along the GIVEN order — depot → stops → gate for AM, gate →
    stops → depot for PM — never a re-ordering call. Depot legs count only
    toward driving; ride seconds walk cumulatively to/from the gate through
    the shared ``geo_service.cumulative_ride_seconds`` (matching the solver's
    semantics exactly; a short provider leg list degrades observably instead
    of crashing). The per-target provider calls are pure request/response, so
    they run CONCURRENTLY under a bounded ThreadPoolExecutor (at most the
    2-bus × 2-leg edit fan-out, ≤ 4 workers) and results are assigned back in
    the original deterministic order — wall-clock under the plan-row lock is
    the slowest single call (~8 s best-effort timeout), not the sum. Returns
    True when any recompute took the degraded (offline) path. Refreshes the
    document objective afterwards."""
    depots = {
        str(f["id"]): (
            {"lat": f["depot_lat"], "lng": f["depot_lng"]}
            if f.get("depot_lat") is not None and f.get("depot_lng") is not None
            else None
        )
        for f in basis.get("fleet") or []
    }
    gate = {"lat": basis["school"]["lat"], "lng": basis["school"]["lng"]}
    degraded_any = False
    jobs: list[dict] = []  # deterministic (sorted-target) order
    for bus_id, leg in sorted(targets):
        bus_doc = _bus_doc(document, bus_id)
        leg_doc = bus_doc["legs"][leg]
        stops = leg_doc.get("stops") or []
        if not stops:
            bus_doc["legs"][leg] = _empty_leg_doc()
            continue
        pts = [{"lat": s["lat"], "lng": s["lng"]} for s in stops]
        depot = depots.get(str(bus_id))
        if leg == plan_solver.LEG_MORNING:
            seq = ([depot] if depot else []) + pts + [gate]
        else:
            seq = [gate] + pts + ([depot] if depot else [])
        jobs.append({"leg": leg, "leg_doc": leg_doc, "stops": stops,
                     "depot": depot, "seq": seq})
    if jobs:
        with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
            geoms = list(pool.map(
                geo_service.fixed_sequence_geometry, [j["seq"] for j in jobs]
            ))
        for job, geom in zip(jobs, geoms):
            leg, leg_doc, stops = job["leg"], job["leg_doc"], job["stops"]
            degraded_any = degraded_any or bool(geom["degraded"])
            durations = [float(leg_row.get("duration_s") or 0) for leg_row in geom["legs"]]
            rides, short = geo_service.cumulative_ride_seconds(
                durations, len(stops),
                is_afternoon=leg != plan_solver.LEG_MORNING,
                has_depot=job["depot"] is not None,
            )
            degraded_any = degraded_any or short
            leg_doc["ride_seconds"] = [
                {"student_id": s["id"], "name": s["name"], "ride_seconds": rides[i]}
                for i, stop in enumerate(stops)
                for s in stop["students"]
            ]
            leg_doc["driving_seconds"] = float(sum(durations))
    _refresh_objective(document)
    return degraded_any


def _refresh_objective(document: dict) -> None:
    """Re-derive the lexicographic tuple (worst child ride, total child ride,
    total driving) from the document's per-leg rows after an edit."""
    worst = total = driving = 0.0
    for bus in document.get("buses") or []:
        for leg in plan_solver.LEGS:
            leg_doc = (bus.get("legs") or {}).get(leg) or {}
            for r in leg_doc.get("ride_seconds") or []:
                ride = float(r["ride_seconds"])
                total += ride
                if ride > worst:
                    worst = ride
            driving += float(leg_doc.get("driving_seconds") or 0.0)
    document["objective"] = [worst, total, driving]


def _roster_drift(
    current_by_id: Mapping[str, Mapping[str, Any]],
    snapshot_by_id: Mapping[str, Mapping[str, Any]],
    *,
    address_changes: bool,
) -> list[dict[str, str]]:
    """Roster drift between the live student rows and a plan snapshot — THE
    shared gate-diff (R22): apply's gate feeds it the draft basis
    (``address_changes=True``: enrolments, coordinate changes vs the
    snapshot, departures), restore's gate the preserved capture's students
    (``address_changes=False`` — restore puts back communicated stops
    verbatim, so an address change never gates it). The same computation also
    feeds the READ paths (review's ``basis_drift``, /current's previous
    ``drift``), so what a caller can fetch and what the act's 409 lists can
    never drift apart.

    ``current_by_id`` rows carry ``name``/``home_lat``/``home_lng``;
    ``snapshot_by_id`` rows carry ``name``/``lat``/``lng`` (both the basis and
    the capture denormalize names, so a departed child is still named — the
    007/010 name-rot precedent). Returns ``[{kind, student_id, name}]``
    sorted by (kind, name, student_id)."""
    drift: list[dict[str, str]] = []
    for sid, row in current_by_id.items():
        if sid not in snapshot_by_id:
            drift.append(
                {"kind": DRIFT_ENROLLED, "student_id": sid, "name": row["name"]}
            )
        elif address_changes:
            b = snapshot_by_id[sid]
            if _floats_differ(b.get("lat"), row["home_lat"]) or _floats_differ(
                b.get("lng"), row["home_lng"]
            ):
                drift.append({
                    "kind": DRIFT_ADDRESS_CHANGED, "student_id": sid,
                    "name": row["name"],
                })
    for sid, b in snapshot_by_id.items():
        if sid not in current_by_id:
            drift.append({
                "kind": DRIFT_DEPARTED, "student_id": sid,
                "name": b.get("name") or sid,
            })
    drift.sort(key=lambda d: (d["kind"], d["name"], d["student_id"]))
    return drift


def _fleet_drift_problems(conn, school_id: str, loads: list[tuple[str, str, int]]) -> list[str]:
    """Re-check each planned bus against the LIVE fleet — THE shared drift
    checker: apply (U6) feeds it the solver document's loads, restore (U7)
    the preserved capture's, so both acts refuse on the same vocabulary.

    ``loads`` is ``[(bus_id, name_as_documented, planned_children)]``; the
    returned problem strings name the bus for: no longer exists, capacity
    now below the planned load, out of service, re-claimed by another
    school. Empty list = no drift."""
    bus_rows: dict[str, dict] = {}
    if loads:
        for r in conn.execute(
            "select b.*, s.name as claiming_school_name from live_buses b "
            "left join live_schools s on s.id = b.school_id "
            "where b.id = any(%s::uuid[])",
            ([bid for bid, _name, _load in loads],),
        ).fetchall():
            bus_rows[str(r["id"])] = r
    problems: list[str] = []
    for bid, doc_name, load in loads:
        row = bus_rows.get(bid)
        if row is None:
            problems.append(f"bus {doc_name} no longer exists")
            continue
        if row["capacity"] is not None and load > row["capacity"]:
            problems.append(
                f"bus {row['name']} now seats {row['capacity']} but the plan "
                f"loads it with {load} children"
            )
        if row["availability"] != "in-service":
            problems.append(f"bus {row['name']} is {row['availability']}")
        if row["school_id"] is not None and str(row["school_id"]) != str(school_id):
            claiming = row["claiming_school_name"] or "another school"
            problems.append(f"bus {row['name']} was re-claimed by {claiming}")
    return problems


def _normalize_per_leg(value: Any, what: str) -> dict[str, Any] | None:
    """Scalar-or-mapping pin payload -> per-leg dict (the solver's pin shape),
    with the leg vocabulary validated up front so a bad payload 400s instead
    of exploding inside the solver at re-solve time."""
    if value is None:
        return None
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for leg, v in value.items():
            _validate_leg(leg)
            if v is not None:
                out[leg] = v
        return out or None
    return {leg: value for leg in plan_solver.LEGS}

