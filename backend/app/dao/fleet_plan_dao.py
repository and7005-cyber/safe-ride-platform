"""Fleet-plan store, draft generation (U4), review/edit (U5), apply (U6) and
restore (U7).

Plans are JSONB documents in ``live_fleet_plans`` (migration 011), one row per
plan: ``document`` holds the solver output (per-bus AM/PM stop sequences,
student ids AND names, ride times, unplaceable list), ``basis`` snapshots the
inputs at generation (students + coords + patterns, fleet config) — both
denormalize names alongside ids so later gates can name departed children (the
007/010 name-rot precedent). Drafts are working documents, not queryable
facts; apply (U6) materializes them into the live route tables. Generation is
strictly read-only against ``live_routes`` / ``live_route_stops`` (R8).

One open draft per school (partial unique in 011): a second draft 409s unless
superseded. Superseding and discarding scrub ``document``/``basis`` to NULL,
keeping metadata — plan documents are an aggregate PII target (every child's
name and coordinates), so payloads never outlive the draft's working life.

The travel-time matrix is fetched in memory per generation request and never
persisted (Google ToS: no caching allowance for durations); the keyless local
stack takes the deterministic haversine-degraded path, flagged on the row.
"""
import logging
import math
import time
from typing import Any, Mapping

from psycopg.types.json import Jsonb

from app.core.db import get_connection
from app.core.errors import BadRequestError, ConflictError, NotFoundError, SafeRideError
from app.dao.fleet_dao import (
    _ROUTE_GEOMETRY_INPUTS_SQL,
    _depot_leg,
    _stop_label,
    resolve_gate_anchor,
)
from app.dao.push_dao import PushDao
from app.dao.student_live_dao import _derive_student_bus
from app.services import geo_service, plan_solver, slot_in_service
from app.services.push_service import PushService

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
    * ``place-change`` — the stop's coordinates or name differ from the
      baseline stop.
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
            if (
                _floats_differ(base["stop_lat"], cur["lat"])
                or _floats_differ(base["stop_lng"], cur["lng"])
                or (base["stop_name"] or "") != (cur["stop_name"] or "")
            ):
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
    toward driving; ride seconds are cumulative to/from the gate, matching
    the solver's semantics exactly. Returns True when any recompute took the
    degraded (offline) path. Refreshes the document objective afterwards."""
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
        geom = geo_service.fixed_sequence_geometry(seq)
        degraded_any = degraded_any or bool(geom["degraded"])
        durations = [float(leg_row.get("duration_s") or 0) for leg_row in geom["legs"]]
        n = len(stops)
        rides = [0.0] * n
        if leg == plan_solver.LEG_MORNING:
            offset = 1 if depot else 0
            acc = 0.0
            for i in range(n - 1, -1, -1):
                acc += durations[offset + i]
                rides[i] = acc
        else:
            acc = 0.0
            for i in range(n):
                acc += durations[i]
                rides[i] = acc
        ride_rows = [
            {"student_id": s["id"], "name": s["name"], "ride_seconds": rides[i]}
            for i, stop in enumerate(stops)
            for s in stop["students"]
        ]
        leg_doc["ride_seconds"] = ride_rows
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


class FleetPlanDao:
    def __init__(self) -> None:
        # Apply (U6) writes feed rows through the conn-threaded PushDao insert
        # and delivers push post-commit through the service; both are cheap,
        # stateless constructions.
        self._push_dao = PushDao()
        self._push_service = PushService(self._push_dao)

    # --- fleet confirmation (F1 step 1) ------------------------------------

    def confirm_fleet(self, school_id: str, bus_ids: list[str]) -> dict[str, Any]:
        """Assign the listed buses to the school (``live_buses.school_id``).

        A bus already claimed by a DIFFERENT school blocks the whole confirm
        with a 409 naming the bus and the claiming school. Buses previously
        claimed by THIS school but deselected are released — unless they carry
        the school's applied plan routes (``plan_ordered``), in which case
        they stay claimed with a notice: unclaiming a bus that is actively
        serving the applied plan would orphan its routes.

        The response reports per-bus notices: a multi-trip bus (any
        trip_index >= 2 route) is claimed but EXCLUDED from drafting; a
        depot-less bus proceeds with a notice.
        """
        requested = list(dict.fromkeys(str(b) for b in bus_ids))
        with get_connection() as conn:
            school = conn.execute(
                "select id, name from live_schools where id = %s", (school_id,)
            ).fetchone()
            if not school:
                raise NotFoundError("School not found")
            # Lock every bus this confirmation may touch — the requested set
            # plus the school's currently claimed buses — in sorted-id order
            # (the global lock-order convention). for update of b only: the
            # left-joined school row is read-only here.
            rows = conn.execute(
                "select b.id, b.name, b.school_id, b.availability, "
                "b.depot_lat, b.depot_lng, s.name as claiming_school_name "
                "from live_buses b left join live_schools s on s.id = b.school_id "
                "where b.id = any(%s::uuid[]) or b.school_id = %s "
                "order by b.id for update of b",
                (requested, school_id),
            ).fetchall()
            by_id = {str(r["id"]): r for r in rows}
            missing = [bid for bid in requested if bid not in by_id]
            if missing:
                raise NotFoundError(f"Bus {missing[0]} was not found")
            for bid in requested:
                row = by_id[bid]
                if row["school_id"] is not None and str(row["school_id"]) != str(school_id):
                    claiming = row["claiming_school_name"] or "another school"
                    raise ConflictError(
                        f"Bus {row['name']} is already claimed by {claiming} — "
                        "release it there before adding it to this school's fleet"
                    )

            # Deselected buses: previously claimed by THIS school, not in the
            # list — released unless they carry the school's applied plan routes.
            released: list[dict] = []
            retained: list[dict] = []
            for r in rows:
                bid = str(r["id"])
                if bid in requested or str(r["school_id"] or "") != str(school_id):
                    continue
                keeps_plan_routes = conn.execute(
                    "select 1 from live_routes "
                    "where bus_id = %s and school_id = %s and plan_ordered limit 1",
                    (r["id"], school_id),
                ).fetchone()
                if keeps_plan_routes:
                    retained.append(r)
                else:
                    conn.execute(
                        "update live_buses set school_id = null where id = %s", (r["id"],)
                    )
                    released.append(r)

            if requested:
                conn.execute(
                    "update live_buses set school_id = %s where id = any(%s::uuid[])",
                    (school_id, requested),
                )

            multi_trip = _multi_trip_bus_ids(conn, requested)
            notices: list[dict] = []
            buses_out: list[dict] = []
            for bid in requested:
                r = by_id[bid]
                excluded = bid in multi_trip
                has_depot = r["depot_lat"] is not None and r["depot_lng"] is not None
                if excluded:
                    notices.append({
                        "bus_id": bid, "bus_name": r["name"], "kind": "multi-trip-excluded",
                        "message": (
                            f"Bus {r['name']} runs a multi-trip chain and is excluded "
                            "from drafting — multi-trip chains are out of drafting scope"
                        ),
                    })
                elif not has_depot:
                    notices.append({
                        "bus_id": bid, "bus_name": r["name"], "kind": "no-depot",
                        "message": (
                            f"Bus {r['name']} has no depot set — drafting proceeds "
                            "without its depot leg"
                        ),
                    })
                buses_out.append({
                    "id": bid, "name": r["name"],
                    "excluded_from_drafting": excluded, "has_depot": has_depot,
                })
            for r in retained:
                notices.append({
                    "bus_id": str(r["id"]), "bus_name": r["name"],
                    "kind": "kept-applied-routes",
                    "message": (
                        f"Bus {r['name']} was deselected but keeps its claim — it "
                        "carries this school's applied plan routes"
                    ),
                })
        return {
            "ok": True,
            "school_id": str(school["id"]),
            "school_name": school["name"],
            "buses": buses_out,
            "released": [str(r["id"]) for r in released],
            "notices": notices,
        }

    # --- draft generation (F1 step 2 / F4) ---------------------------------

    def create_draft(
        self, school_id: str, *, seed: int | None, supersede: bool, created_by: str | None
    ) -> dict[str, Any]:
        """Snapshot the basis, fetch the matrix, run the solver, persist the
        draft. One open draft per school: an existing draft 409s unless
        ``supersede``, which flips it to superseded AND scrubs its
        document/basis payloads (metadata kept — the retention rule).

        Read-only against live routes by construction: nothing here writes
        ``live_routes`` / ``live_route_stops`` / ``live_student_routes`` (R8).

        Everything runs on one connection/transaction. The matrix fetch does
        happen inside it — unlike fleet regeneration this holds no route
        locks, only the school's own draft row, and a concurrent draft for
        the same school resolves via the 011 partial unique (409), so the
        short provider hold is an accepted simplification at pilot scale.
        """
        seed_val = int(seed) if seed is not None else 0
        with get_connection() as conn:
            school = conn.execute(
                "select id, name, lat, lng from live_schools where id = %s", (school_id,)
            ).fetchone()
            if not school:
                raise NotFoundError("School not found")
            if school["lat"] is None or school["lng"] is None:
                raise ConflictError(
                    f"School {school['name']} has no gate coordinates — set its "
                    "location before drafting"
                )

            existing = conn.execute(
                "select id, created_at from live_fleet_plans "
                "where school_id = %s and status = 'draft' for update",
                (school_id,),
            ).fetchone()
            if existing and not supersede:
                raise ConflictError(
                    f"An open draft already exists for {school['name']} "
                    f"(draft {existing['id']}, created "
                    f"{existing['created_at']:%Y-%m-%d %H:%M}) — supersede it or "
                    "discard it first"
                )
            if existing:
                # Scrub on supersede: payloads emptied, metadata kept. The
                # partial unique cannot defer, so demote before the new insert.
                conn.execute(
                    "update live_fleet_plans set status = 'superseded', "
                    "document = null, basis = null where id = %s",
                    (existing["id"],),
                )

            students = conn.execute(
                "select id, name, home_lat, home_lng, ridership_pattern "
                "from live_students where school_id = %s order by name asc, id asc",
                (school_id,),
            ).fetchall()
            buses = conn.execute(
                "select id, name, capacity, depot_lat, depot_lng from live_buses "
                "where school_id = %s and availability = 'in-service' order by id asc",
                (school_id,),
            ).fetchall()
            multi_trip = _multi_trip_bus_ids(conn, [b["id"] for b in buses])

            # Plannable = home coordinates present; the rest are listed
            # unplaceable with the constraint named and are NOT solver input.
            plannable = [s for s in students if s["home_lat"] is not None and s["home_lng"] is not None]
            unresolved = [s for s in students if s["home_lat"] is None or s["home_lng"] is None]

            solver_students = [
                {
                    "id": str(s["id"]), "name": s["name"],
                    "lat": s["home_lat"], "lng": s["home_lng"],
                    "pattern": s["ridership_pattern"] or plan_solver.PATTERN_BOTH,
                }
                for s in plannable
            ]
            included_buses = [b for b in buses if str(b["id"]) not in multi_trip]
            solver_buses = [
                {
                    "id": str(b["id"]), "name": b["name"], "capacity": b["capacity"],
                    "depot": (
                        {"lat": b["depot_lat"], "lng": b["depot_lng"]}
                        if b["depot_lat"] is not None and b["depot_lng"] is not None
                        else None
                    ),
                }
                for b in included_buses
            ]

            # All student homes + the school + the depots go through one matrix
            # call; the solver collapses nearby homes itself, so every point it
            # can ever query is in this list. The matrix result is an N×N grid
            # indexed by this points order; the solver wants a callable over
            # (lat, lng) tuples — bridge with an index dict and a closure.
            points: list[dict] = [
                {"lat": float(s["lat"]), "lng": float(s["lng"])} for s in solver_students
            ]
            points.append({"lat": float(school["lat"]), "lng": float(school["lng"])})
            for b in solver_buses:
                if b["depot"] is not None:
                    points.append(
                        {"lat": float(b["depot"]["lat"]), "lng": float(b["depot"]["lng"])}
                    )
            matrix_result = geo_service.compute_duration_matrix(points)
            grid = matrix_result["matrix"]
            index = {(p["lat"], p["lng"]): i for i, p in enumerate(points)}

            def matrix_fn(origin: tuple[float, float], dest: tuple[float, float]) -> float:
                return grid[index[origin]][index[dest]]

            document = plan_solver.solve(
                solver_students,
                solver_buses,
                matrix_fn,
                {"lat": school["lat"], "lng": school["lng"]},
                seed=seed_val,
                stop_cap=plan_solver.DEFAULT_STOP_CAP,
                degraded=bool(matrix_result["degraded"]),
            )
            # Unresolved-triage students join the unplaceable list per leg
            # their pattern rides, with the binding constraint named (R7).
            for s in unresolved:
                for leg in _pattern_legs(s["ridership_pattern"]):
                    document["unplaceable"].append({
                        "student_id": str(s["id"]), "name": s["name"],
                        "leg": leg, "constraint": UNRESOLVED_ADDRESS_CONSTRAINT,
                    })

            basis = {
                "version": 1,
                "school": {
                    "id": str(school["id"]), "name": school["name"],
                    "lat": school["lat"], "lng": school["lng"],
                },
                "students": [
                    {
                        "id": str(s["id"]), "name": s["name"],
                        "lat": s["home_lat"], "lng": s["home_lng"],
                        "pattern": s["ridership_pattern"] or plan_solver.PATTERN_BOTH,
                        "plannable": s["home_lat"] is not None and s["home_lng"] is not None,
                    }
                    for s in students
                ],
                # No pins exist at draft time — review edits (U5) add them.
                "pins": [],
                "fleet": [
                    {
                        "id": str(b["id"]), "name": b["name"], "capacity": b["capacity"],
                        "depot_lat": b["depot_lat"], "depot_lng": b["depot_lng"],
                    }
                    for b in included_buses
                ],
                "excluded_buses": [
                    {
                        "id": str(b["id"]), "name": b["name"],
                        "reason": "multi-trip chain — out of drafting scope",
                    }
                    for b in buses
                    if str(b["id"]) in multi_trip
                ],
                "matrix_provider": matrix_result["provider"],
            }

            degraded = bool(matrix_result["degraded"] or document.get("degraded"))
            row = conn.execute(
                "insert into live_fleet_plans "
                "(school_id, status, document, basis, solver_seed, degraded, created_by) "
                "values (%s, 'draft', %s, %s, %s, %s, %s) returning *",
                (school_id, Jsonb(document), Jsonb(basis), seed_val, degraded, created_by),
            ).fetchone()
        logger.info(
            "fleet plan draft %s for school %s: seed=%s degraded=%s students=%d "
            "plannable=%d buses=%d excluded=%d",
            row["id"], school_id, seed_val, degraded, len(students),
            len(plannable), len(included_buses), len(buses) - len(included_buses),
        )
        return dict(row)

    # --- reads --------------------------------------------------------------

    def current_plans(self, school_id: str) -> dict[str, Any]:
        """The school's open draft (full row) plus applied/previous metadata
        WITHOUT their document/basis payloads. Full review computation is
        U5's job — the draft's stored document is returned as-is."""
        with get_connection() as conn:
            school = conn.execute(
                "select id from live_schools where id = %s", (school_id,)
            ).fetchone()
            if not school:
                raise NotFoundError("School not found")
            draft = conn.execute(
                "select * from live_fleet_plans where school_id = %s and status = 'draft'",
                (school_id,),
            ).fetchone()
            # 'applied' uniqueness is DAO-enforced, not indexed — read the
            # newest defensively.
            applied = conn.execute(
                f"select {_META_COLUMNS} from live_fleet_plans "
                "where school_id = %s and status = 'applied' "
                "order by applied_at desc nulls last, created_at desc limit 1",
                (school_id,),
            ).fetchone()
            previous = conn.execute(
                f"select {_META_COLUMNS} from live_fleet_plans "
                "where school_id = %s and status = 'previous'",
                (school_id,),
            ).fetchone()
        return {
            "draft": dict(draft) if draft else None,
            "applied": dict(applied) if applied else None,
            "previous": dict(previous) if previous else None,
        }

    # --- review surface (U5) --------------------------------------------------

    def review(self, school_id: str) -> dict[str, Any]:
        """The open draft's computed review surface (R10/R24): the stored
        document plus per-child ride times with wall-clock stop times, per-bus
        capacity use, total driving, the per-leg unplaceable lists, and the
        diff vs live from the shared diff function. Read-only and provider-
        free: every number is arithmetic over the stored durations."""
        with get_connection() as conn:
            school = conn.execute(
                "select id, name, lat, lng, morning_bell, afternoon_bell "
                "from live_schools where id = %s",
                (school_id,),
            ).fetchone()
            if not school:
                raise NotFoundError("School not found")
            plan = conn.execute(
                "select * from live_fleet_plans where school_id = %s and status = 'draft'",
                (school_id,),
            ).fetchone()
            if not plan or not plan["document"]:
                raise NotFoundError("No open draft for this school — generate one first")
            document = plan["document"]

            anchors = plan_gate_anchors(school)
            buses_out: list[dict] = []
            ride_times: list[dict] = []
            total_driving = 0.0
            for bus in document.get("buses") or []:
                legs_out: dict[str, dict] = {}
                capacity_use: dict[str, dict] = {}
                for leg in plan_solver.LEGS:
                    leg_doc = (bus.get("legs") or {}).get(leg) or _empty_leg_doc()
                    rides = _leg_rides(leg_doc)
                    sign = -1 if leg == plan_solver.LEG_MORNING else 1
                    stops_out = []
                    children = 0
                    for stop in leg_doc.get("stops") or []:
                        students = stop.get("students") or []
                        children += len(students)
                        ride = float(rides.get(str(students[0]["id"]), 0.0)) if students else 0.0
                        scheduled = _shift_hhmm(anchors[leg], sign * ride)
                        stops_out.append({
                            "key": _stop_key(stop),
                            "name": stop.get("name"),
                            "lat": stop.get("lat"),
                            "lng": stop.get("lng"),
                            "students": students,
                            "ride_seconds": ride,
                            "scheduled_time": scheduled,
                        })
                        for s in students:
                            ride_times.append({
                                "student_id": str(s["id"]),
                                "name": s.get("name"),
                                "leg": leg,
                                "bus_id": str(bus["bus_id"]),
                                "bus_name": bus.get("bus_name"),
                                "ride_seconds": float(rides.get(str(s["id"]), 0.0)),
                                "scheduled_time": scheduled,
                            })
                    driving = float(leg_doc.get("driving_seconds") or 0.0)
                    total_driving += driving
                    legs_out[leg] = {
                        "stops": stops_out,
                        "driving_seconds": driving,
                        "children": children,
                    }
                    capacity_use[leg] = {
                        "children": children,
                        "capacity": bus.get("capacity"),
                    }
                buses_out.append({
                    "bus_id": str(bus["bus_id"]),
                    "bus_name": bus.get("bus_name"),
                    "capacity": bus.get("capacity"),
                    "capacity_use": capacity_use,
                    "legs": legs_out,
                })

            unplaceable_by_leg: dict[str, list] = {leg: [] for leg in plan_solver.LEGS}
            for u in document.get("unplaceable") or []:
                unplaceable_by_leg.setdefault(u["leg"], []).append(u)

            diff = diff_plan_vs_live(conn, document, school)
        return {
            "plan": {
                "id": str(plan["id"]),
                "status": plan["status"],
                "degraded": plan["degraded"],
                "solver_seed": plan["solver_seed"],
                "created_at": plan["created_at"],
            },
            "school_id": str(school["id"]),
            "anchors": anchors,
            "document": document,
            "buses": buses_out,
            "ride_times": ride_times,
            "total_driving_seconds": total_driving,
            "objective": document.get("objective"),
            "unplaceable": unplaceable_by_leg,
            "patterns": document.get("patterns") or {},
            "pins": document.get("pins") or {},
            "diff": diff,
        }

    # --- review edits (U5) -----------------------------------------------------
    # Every edit mutates a working copy of the draft document inside one
    # transaction under the plan-row lock, re-checks the hard constraints
    # (a violation raises the 422 and rolls everything back — the document is
    # provably unchanged), recomputes the affected route legs through the
    # fixed-sequence geo helper, and persists. Provider calls run inside the
    # transaction holding only the plan row lock — the U4 accepted
    # simplification, unchanged.

    @staticmethod
    def _draft_for_update(conn, plan_id: str) -> dict:
        row = conn.execute(
            "select * from live_fleet_plans where id = %s for update", (plan_id,)
        ).fetchone()
        if not row:
            raise NotFoundError("Plan not found")
        if row["status"] != "draft":
            raise ConflictError(
                f"Only a draft can be edited — this plan is {row['status']}"
            )
        if not row["document"] or not row["basis"]:
            raise ConflictError("This draft has no document payload")
        return row

    @staticmethod
    def _persist(conn, plan_id: str, document: dict, degraded_recompute: bool) -> dict:
        """Write the edited document back. The degraded flag is monotone: a
        recompute that fell back to the offline estimate marks the draft
        degraded exactly like a degraded generation (observable, R10)."""
        degraded = bool(document.get("degraded") or degraded_recompute)
        document["degraded"] = degraded
        row = conn.execute(
            "update live_fleet_plans set document = %s, degraded = %s "
            "where id = %s returning *",
            (Jsonb(document), degraded, plan_id),
        ).fetchone()
        return dict(row)

    def move_student(
        self, plan_id: str, student_id: str, to_bus_id: str,
        legs: list[str] | None = None, position: int | None = None,
    ) -> dict[str, Any]:
        """Move a placed child to another bus — both legs by default (R6/R9).
        An explicit ONE-leg move of a both-legs rider leaves the legs on
        different buses and flips the document pattern to split; a move that
        reunites a split child's legs onto one bus flips it back to both_ways
        — the stored pattern stays authoritative (R20). The child joins an
        existing same-place stop or lands as a new stop (at ``position``,
        else appended). Capacity/stop-cap violations 422 with the constraint
        named; the draft is left unchanged."""
        sid = str(student_id)
        with get_connection() as conn:
            plan = self._draft_for_update(conn, plan_id)
            document, basis = plan["document"], plan["basis"]
            target = _bus_doc(document, to_bus_id)

            placements = {
                leg: _find_placement(document, sid, leg) for leg in plan_solver.LEGS
            }
            ridden = [leg for leg in plan_solver.LEGS if placements[leg] is not None]
            if not ridden:
                raise ConflictError(
                    "Student is not placed on any leg of this plan — use assign "
                    "for an unplaceable child"
                )
            if legs is None:
                move_legs = ridden
            else:
                move_legs = [_validate_leg(leg) for leg in legs]
                for leg in move_legs:
                    if leg not in ridden:
                        raise ConflictError(
                            f"Student is not placed on the {leg} leg of this plan"
                        )

            targets: set[tuple[str, str]] = set()
            for leg in move_legs:
                src_bus, _ = placements[leg]
                if str(src_bus["bus_id"]) == str(to_bus_id):
                    continue
                entry, coords = _pop_student(src_bus, leg, sid)
                _add_student_to_leg(target, leg, entry, coords[0], coords[1], position)
                targets.add((str(src_bus["bus_id"]), leg))
                targets.add((str(to_bus_id), leg))
            for leg in move_legs:
                _check_bus_leg(target, leg)

            # Pattern authority (R20): buses-per-leg decides split/both_ways.
            after = {
                leg: _find_placement(document, sid, leg) for leg in plan_solver.LEGS
            }
            am, pm = after[plan_solver.LEG_MORNING], after[plan_solver.LEG_AFTERNOON]
            if am is not None and pm is not None:
                current = _effective_pattern(document, basis, sid)
                am_bus, pm_bus = str(am[0]["bus_id"]), str(pm[0]["bus_id"])
                patterns = document.setdefault("patterns", {})
                if am_bus != pm_bus and current != plan_solver.PATTERN_SPLIT:
                    patterns[sid] = plan_solver.PATTERN_SPLIT
                elif am_bus == pm_bus and current == plan_solver.PATTERN_SPLIT:
                    patterns[sid] = plan_solver.PATTERN_BOTH

            degraded = _recompute_legs(document, basis, targets)
            return self._persist(conn, plan_id, document, degraded)

    def reorder_stops(
        self, plan_id: str, bus_id: str, leg: str, order: list[str]
    ) -> dict[str, Any]:
        """Reorder one route's stops to an explicit FULL-order echo of stop
        keys (the shipped stop-order contract's shape, against the document).
        The echoed order IS the requested sequence, so a 25-stop order 422s
        on the stop cap before anything else; duplicates and set mismatches
        are 400s. Times recompute along the new fixed order."""
        _validate_leg(leg)
        with get_connection() as conn:
            plan = self._draft_for_update(conn, plan_id)
            document, basis = plan["document"], plan["basis"]
            bus_doc = _bus_doc(document, bus_id)
            leg_doc = bus_doc["legs"][leg]
            if len(order) > plan_solver.DEFAULT_STOP_CAP:
                raise PlanConstraintError(
                    f"{CONSTRAINT_STOP_CAP}: the requested order lists {len(order)} "
                    f"stops — the cap is {plan_solver.DEFAULT_STOP_CAP} stops per "
                    "route; the draft was left unchanged"
                )
            if len(order) != len(set(order)):
                raise BadRequestError("Duplicate stop in the requested order")
            by_key = {_stop_key(stop): stop for stop in leg_doc.get("stops") or []}
            if set(order) != set(by_key):
                raise BadRequestError(
                    "Stop order does not match the route's current stops — "
                    "refresh and try again"
                )
            leg_doc["stops"] = [by_key[key] for key in order]
            degraded = _recompute_legs(document, basis, {(str(bus_id), leg)})
            return self._persist(conn, plan_id, document, degraded)

    def set_pin(
        self, plan_id: str, student_id: str,
        bus: Any = None, order: Any = None, unpin: str | None = None,
    ) -> dict[str, Any]:
        """Pin or unpin a child (R9/R13): a bus pin (per leg or both) and/or an
        order pin (0-based position per leg or both), stored in the document
        so re-solve honours them as solver inputs. ``unpin`` removes one
        dimension ('bus'/'order') or 'all'. Pins never move anyone by
        themselves — no recompute."""
        sid = str(student_id)
        with get_connection() as conn:
            plan = self._draft_for_update(conn, plan_id)
            document, basis = plan["document"], plan["basis"]
            _basis_student(basis, sid)
            pins = document.setdefault("pins", {})
            if unpin is not None:
                if unpin not in ("bus", "order", "all"):
                    raise BadRequestError("unpin must be 'bus', 'order' or 'all'")
                entry = pins.get(sid) or {}
                if unpin == "all":
                    pins.pop(sid, None)
                else:
                    entry.pop(unpin, None)
                    if entry:
                        pins[sid] = entry
                    else:
                        pins.pop(sid, None)
                return self._persist(conn, plan_id, document, False)

            bus_map = _normalize_per_leg(bus, "bus pin")
            order_map = _normalize_per_leg(order, "order pin")
            if bus_map is None and order_map is None:
                raise BadRequestError("Nothing to pin — provide bus and/or order")
            entry = pins.setdefault(sid, {})
            if bus_map is not None:
                for value in bus_map.values():
                    _bus_doc(document, str(value))  # must be a plan bus
                merged = {**(entry.get("bus") or {}),
                          **{leg: str(v) for leg, v in bus_map.items()}}
                if (len(set(merged.values())) > 1
                        and _effective_pattern(document, basis, sid)
                        != plan_solver.PATTERN_SPLIT):
                    raise BadRequestError(
                        "Conflicting per-leg bus pins — only a split rider can "
                        "be pinned to different buses per leg"
                    )
                entry["bus"] = merged
            if order_map is not None:
                for value in order_map.values():
                    if not isinstance(value, int) or value < 0:
                        raise BadRequestError("Order pin positions must be integers >= 0")
                entry["order"] = {**(entry.get("order") or {}), **order_map}
            return self._persist(conn, plan_id, document, False)

    def set_pattern(self, plan_id: str, student_id: str, pattern: str) -> dict[str, Any]:
        """Change a child's ridership pattern in the DRAFT only (R20 —
        live_students is untouched until apply). Removing a leg drops the
        child's stop from that leg and recomputes its times; a widened leg
        mirrors onto the other leg's bus when one exists (constraint-checked,
        422 on violation), else the child lands on the unplaceable list —
        'unresolved address' without coordinates, 'unassigned' otherwise
        (assign is the placement path)."""
        if pattern not in plan_solver.PATTERNS:
            raise BadRequestError(
                f"Unknown pattern '{pattern}' — expected one of {list(plan_solver.PATTERNS)}"
            )
        sid = str(student_id)
        with get_connection() as conn:
            plan = self._draft_for_update(conn, plan_id)
            document, basis = plan["document"], plan["basis"]
            bs = _basis_student(basis, sid)
            new_legs = set(_pattern_legs(pattern))
            targets: set[tuple[str, str]] = set()

            for leg in plan_solver.LEGS:
                if leg in new_legs:
                    continue
                placement = _find_placement(document, sid, leg)
                if placement is not None:
                    bus_doc, _ = placement
                    _pop_student(bus_doc, leg, sid)
                    targets.add((str(bus_doc["bus_id"]), leg))
                document["unplaceable"] = [
                    u for u in document.get("unplaceable") or []
                    if not (str(u["student_id"]) == sid and u["leg"] == leg)
                ]

            unplaceable_legs = {
                u["leg"] for u in document.get("unplaceable") or []
                if str(u["student_id"]) == sid
            }
            for leg in plan_solver.LEGS:
                if leg not in new_legs:
                    continue
                if _find_placement(document, sid, leg) or leg in unplaceable_legs:
                    continue
                other = (plan_solver.LEG_AFTERNOON if leg == plan_solver.LEG_MORNING
                         else plan_solver.LEG_MORNING)
                mirror = _find_placement(document, sid, other)
                if mirror is not None:
                    mirror_bus, stop_idx = mirror
                    stop = mirror_bus["legs"][other]["stops"][stop_idx]
                    _add_student_to_leg(
                        mirror_bus, leg, {"id": sid, "name": bs["name"]},
                        stop["lat"], stop["lng"],
                    )
                    _check_bus_leg(mirror_bus, leg)
                    targets.add((str(mirror_bus["bus_id"]), leg))
                else:
                    constraint = (
                        UNRESOLVED_ADDRESS_CONSTRAINT
                        if bs.get("lat") is None or bs.get("lng") is None
                        else UNASSIGNED_CONSTRAINT
                    )
                    document.setdefault("unplaceable", []).append({
                        "student_id": sid, "name": bs["name"],
                        "leg": leg, "constraint": constraint,
                    })

            document.setdefault("patterns", {})[sid] = pattern
            degraded = _recompute_legs(document, basis, targets)
            return self._persist(conn, plan_id, document, degraded)

    def assign_student(
        self, plan_id: str, student_id: str, bus_id: str,
        position: Any = None, legs: list[str] | None = None,
    ) -> dict[str, Any]:
        """Place a currently-UNPLACEABLE child onto a bus at an explicit
        position, per pattern legs — the manual mid-year placement path until
        the slot-in engine ships. Defaults to every leg the child is listed
        unplaceable for; the placed legs leave the unplaceable list. Hard
        constraints re-checked (422, draft unchanged); a child without home
        coordinates cannot be assigned (409 — resolve the address first)."""
        sid = str(student_id)
        with get_connection() as conn:
            plan = self._draft_for_update(conn, plan_id)
            document, basis = plan["document"], plan["basis"]
            target = _bus_doc(document, bus_id)
            entries = [
                u for u in document.get("unplaceable") or []
                if str(u["student_id"]) == sid
            ]
            if not entries:
                raise ConflictError(
                    "Student is not on the unplaceable list — use move for a "
                    "placed child"
                )
            unpl_legs = [leg for leg in plan_solver.LEGS
                         if any(u["leg"] == leg for u in entries)]
            if legs is None:
                assign_legs = unpl_legs
            else:
                assign_legs = [_validate_leg(leg) for leg in legs]
                for leg in assign_legs:
                    if leg not in unpl_legs:
                        raise ConflictError(
                            f"Student is not unplaceable on the {leg} leg"
                        )
            bs = _basis_student(basis, sid)
            if bs.get("lat") is None or bs.get("lng") is None:
                raise ConflictError(
                    f"Student {bs['name']} has no home coordinates — resolve "
                    "the address first, then assign"
                )
            positions = _normalize_per_leg(position, "position") or {}
            targets: set[tuple[str, str]] = set()
            for leg in assign_legs:
                pos = positions.get(leg)
                if pos is not None and (not isinstance(pos, int) or pos < 0):
                    raise BadRequestError("Positions must be integers >= 0")
                _add_student_to_leg(
                    target, leg, {"id": sid, "name": bs["name"]},
                    bs["lat"], bs["lng"], pos,
                )
                _check_bus_leg(target, leg)
                targets.add((str(bus_id), leg))
            document["unplaceable"] = [
                u for u in document.get("unplaceable") or []
                if not (str(u["student_id"]) == sid and u["leg"] in assign_legs)
            ]
            degraded = _recompute_legs(document, basis, targets)
            return self._persist(conn, plan_id, document, degraded)

    # --- in-draft re-solve (U5, R13) --------------------------------------------

    def resolve_draft(self, plan_id: str) -> dict[str, Any]:
        """Re-run the solver on the draft's CURRENT basis with the document's
        pins and pattern edits as inputs (R13 — full re-optimisation only on
        explicit request). A fresh in-memory matrix is fetched (never
        persisted); the stored solver seed is reused so a re-solve of
        unchanged inputs reproduces bit-identically. Unpinned manual
        arrangements — moves, reorders, assigns — are DISCARDED, and the
        response says so explicitly; pins and pattern edits survive."""
        with get_connection() as conn:
            plan = self._draft_for_update(conn, plan_id)
            document, basis = plan["document"], plan["basis"]
            patterns = document.get("patterns") or {}
            pins = document.get("pins") or {}

            solver_students = []
            unresolved = []
            for s in basis.get("students") or []:
                sid = str(s["id"])
                pattern = patterns.get(sid) or s.get("pattern") or plan_solver.PATTERN_BOTH
                if not s.get("plannable"):
                    unresolved.append((s, pattern))
                    continue
                pin = pins.get(sid) or {}
                solver_students.append({
                    "id": sid, "name": s["name"],
                    "lat": s["lat"], "lng": s["lng"],
                    "pattern": pattern,
                    "bus_pin": pin.get("bus"),
                    "order_pin": pin.get("order"),
                })
            solver_buses = [
                {
                    "id": str(b["id"]), "name": b["name"], "capacity": b["capacity"],
                    "depot": (
                        {"lat": b["depot_lat"], "lng": b["depot_lng"]}
                        if b.get("depot_lat") is not None and b.get("depot_lng") is not None
                        else None
                    ),
                }
                for b in basis.get("fleet") or []
            ]

            points: list[dict] = [
                {"lat": float(s["lat"]), "lng": float(s["lng"])} for s in solver_students
            ]
            school = basis["school"]
            points.append({"lat": float(school["lat"]), "lng": float(school["lng"])})
            for b in solver_buses:
                if b["depot"] is not None:
                    points.append(
                        {"lat": float(b["depot"]["lat"]), "lng": float(b["depot"]["lng"])}
                    )
            matrix_result = geo_service.compute_duration_matrix(points)
            grid = matrix_result["matrix"]
            index = {(p["lat"], p["lng"]): i for i, p in enumerate(points)}

            def matrix_fn(origin: tuple[float, float], dest: tuple[float, float]) -> float:
                return grid[index[origin]][index[dest]]

            seed_val = int(plan["solver_seed"] or 0)
            try:
                new_doc = plan_solver.solve(
                    solver_students,
                    solver_buses,
                    matrix_fn,
                    {"lat": school["lat"], "lng": school["lng"]},
                    seed=seed_val,
                    stop_cap=plan_solver.DEFAULT_STOP_CAP,
                    degraded=bool(matrix_result["degraded"]),
                )
            except ValueError as error:
                # e.g. pins made contradictory by a later pattern edit — a
                # draft-state conflict, not a server fault.
                raise ConflictError(str(error)) from error
            for s, pattern in unresolved:
                for leg in _pattern_legs(pattern):
                    new_doc["unplaceable"].append({
                        "student_id": str(s["id"]), "name": s["name"],
                        "leg": leg, "constraint": UNRESOLVED_ADDRESS_CONSTRAINT,
                    })
            # Pins and pattern edits survive the re-solve; everything manual
            # and unpinned was just re-derived away.
            new_doc["pins"] = pins
            new_doc["patterns"] = patterns

            degraded = bool(matrix_result["degraded"] or new_doc.get("degraded"))
            new_doc["degraded"] = degraded
            row = conn.execute(
                "update live_fleet_plans set document = %s, degraded = %s "
                "where id = %s returning *",
                (Jsonb(new_doc), degraded, plan_id),
            ).fetchone()
        logger.info(
            "fleet plan re-solve %s: seed=%s degraded=%s pins=%d patterns=%d",
            plan_id, seed_val, degraded, len(pins), len(patterns),
        )
        return {
            **dict(row),
            "resolve": {
                "discarded_unpinned_arrangements": True,
                "message": (
                    "Re-solve finished: pins and pattern edits were honoured "
                    "as inputs; unpinned manual arrangements (moves, reorders, "
                    "assignments) were discarded."
                ),
            },
        }

    # --- apply (U6) -----------------------------------------------------------

    def apply_plan(
        self, plan_id: str, *, confirmations: list[dict],
        acknowledgments: list[dict], actor: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Apply the draft: ONE provider-free transaction, then compensable
        post-commit phases (R8/R21/R22/R23; the plan's U6 step order).

        Inside the transaction, in this order: (1) lock the plan row FOR
        UPDATE first, then the school's live routes in sorted-id order;
        (2) idempotency by status — re-applying an applied plan (a retry
        racing a gateway timeout past a late commit) answers the applied
        state with zero side effects; (3) gates against the locked snapshot —
        basis drift each confirmed or 409, fleet drift 409 naming the bus,
        unplaceable acknowledgments or 422 naming them; then the notification
        diff (the U5 shared function, pre-mutation — see inline comment);
        (4) capture the displaced live state as ONE JSONB statement and
        demote previous/applied (delete-then-flip: the partial uniques cannot
        defer); (5) reconcile live_routes in place to the document's
        (bus, type) pairs — multi-trip chains untouched, surplus deleted;
        (6) rewrite live_student_routes delete-before-insert scoped by
        STUDENT-AND-LEG; (7) materialize stops from the document with the
        document's computed times, gate row per the house convention, and
        ``last_recalc_degraded = TRUE`` as the durable refresh-pending
        marker; (8) re-derive live_students bus/pickup/pattern (morning-clock
        rule); (9) the ``plan-applied`` audit row; (10) feed rows on THIS
        connection + baseline upserts, then stale-baseline deletes;
        (11) flip the draft to applied and run the final unknown-
        acknowledgment gate. Commit.

        Post-commit, outside the transaction: geometry-only refresh one route
        per transaction along the plan's fixed order (never times — document
        times are frozen), then awaited push delivery for the already-written
        feed rows. Phase timings (gates/txn/refresh/push) are logged here
        against the 30 s ceiling.

        No date-scoped SQL exists on this path, so there is nothing to
        convert to Africa/Nairobi (the rule that would otherwise apply).
        """
        t_start = time.monotonic()
        confirmed = {
            (str(c.get("kind")), str(c.get("student_id"))) for c in confirmations
        }
        acked = {
            (str(a.get("student_id")), _validate_leg(str(a.get("leg"))))
            for a in acknowledgments
        }

        with get_connection() as conn:
            # (1) Plan row FIRST: this serializes a double-submit even for a
            # school with zero live routes (route locks alone could not).
            plan = conn.execute(
                "select * from live_fleet_plans where id = %s for update", (plan_id,)
            ).fetchone()
            if not plan:
                raise NotFoundError("Plan not found")
            # (2) Idempotency by status (System-Wide Impact): the commit was
            # the act; a repeat answers the applied state, side-effect free.
            if plan["status"] == "applied":
                logger.info("fleet plan apply %s: already applied — idempotent return", plan_id)
                return {
                    "ok": True,
                    "already_applied": True,
                    "plan": {
                        "id": str(plan["id"]), "status": plan["status"],
                        "created_at": plan["created_at"], "applied_at": plan["applied_at"],
                    },
                    "school_id": str(plan["school_id"]),
                }
            if plan["status"] != "draft":
                raise ConflictError(
                    f"Only a draft can be applied — this plan is {plan['status']}"
                )
            if not plan["document"] or not plan["basis"]:
                raise ConflictError("This draft has no document payload")
            document, basis = plan["document"], plan["basis"]
            school_id = str(plan["school_id"])
            school = conn.execute(
                "select id, name, lat, lng, morning_bell, afternoon_bell "
                "from live_schools where id = %s",
                (school_id,),
            ).fetchone()
            if not school:
                raise NotFoundError("School not found")
            # ...then every live route of the school in sorted-id order (the
            # global route-lock convention shared with _sync_routes /
            # update_school), so apply and concurrent route writers serialize
            # instead of deadlocking.
            routes = conn.execute(
                "select * from live_routes where school_id = %s order by id for update",
                (school_id,),
            ).fetchall()

            # (3) Gates, against the LOCKED snapshot. -------------------------
            students_now = conn.execute(
                "select id, name, home_address, home_lat, home_lng, ridership_pattern "
                "from live_students where school_id = %s",
                (school_id,),
            ).fetchall()
            current_by_id = {str(r["id"]): r for r in students_now}
            basis_by_id = {str(s["id"]): s for s in basis.get("students") or []}

            # (3a) Basis drift (R22): enrolments, address changes (coords
            # differ from the basis snapshot) and departures since generation
            # — each explicitly confirmed by (kind, student) or 409 listing it.
            drift: list[tuple[str, str, str]] = []
            for sid, row in current_by_id.items():
                if sid not in basis_by_id:
                    drift.append((DRIFT_ENROLLED, sid, row["name"]))
                else:
                    b = basis_by_id[sid]
                    if _floats_differ(b.get("lat"), row["home_lat"]) or _floats_differ(
                        b.get("lng"), row["home_lng"]
                    ):
                        drift.append((DRIFT_ADDRESS_CHANGED, sid, row["name"]))
            for sid, b in basis_by_id.items():
                if sid not in current_by_id:
                    # The basis denormalizes names, so a departed child can
                    # still be named (the 007/010 name-rot precedent).
                    drift.append((DRIFT_DEPARTED, sid, b.get("name") or sid))
            unconfirmed = [d for d in drift if (d[0], d[1]) not in confirmed]
            if unconfirmed:
                listing = "; ".join(
                    f"{kind}: {name} ({sid})" for kind, sid, name in sorted(unconfirmed)
                )
                raise ConflictError(
                    "The school has changed since this draft was generated — "
                    f"confirm each item or re-draft. Unconfirmed: {listing}"
                )

            # (3b) Fleet drift: every drafted bus re-checked against live —
            # capacity lowered below its drafted load, taken out of service,
            # or re-claimed by another school all block naming the bus (the
            # shared checker; restore feeds it the preserved capture's loads).
            doc_buses = document.get("buses") or []
            loads = []
            for bus in doc_buses:
                load = max(
                    (
                        sum(
                            len(s.get("students") or [])
                            for s in ((bus.get("legs") or {}).get(leg) or {}).get("stops") or []
                        )
                        for leg in plan_solver.LEGS
                    ),
                    default=0,
                )
                loads.append(
                    (str(bus["bus_id"]), bus.get("bus_name") or str(bus["bus_id"]), load)
                )
            problems = _fleet_drift_problems(conn, school_id, loads)
            if problems:
                raise ConflictError(
                    "The fleet has drifted since this draft was generated — "
                    + "; ".join(problems)
                    + ". Fix the fleet or re-draft"
                )

            # (3c) R23: every unplaceable (student, leg) acknowledged by name.
            unplaceable = document.get("unplaceable") or []
            missing = [
                u for u in unplaceable
                if (str(u["student_id"]), u["leg"]) not in acked
            ]
            if missing:
                listing = "; ".join(
                    f"{u.get('name') or u['student_id']} ({u['leg']})" for u in missing
                )
                raise UnacknowledgedUnplaceableError(
                    "Unplaceable children must be acknowledged by name before this "
                    f"plan can be applied: {listing}. They will have no seat from "
                    "the next run — which can be this same afternoon"
                )
            t_gates_ms = int((time.monotonic() - t_start) * 1000)

            # Notification diff — the U5 shared function on THIS transaction's
            # connection, and deliberately BEFORE any mutation: bus-change and
            # newly-unplaceable read live membership, and the R24 preview the
            # admin just confirmed was computed against this same pre-apply
            # truth — diffing after the link rewrite would compare the
            # document with itself and mute every send. (Trivially also
            # before the baseline deletions below.)
            diff = diff_plan_vs_live(conn, document, school)
            current_ids = set(current_by_id)
            # Confirmed departures are excluded from every write from here on:
            # their student rows (and cascaded links/baselines/parent links)
            # are gone, so a stop, link or baseline write for them would break
            # FKs — and their diff rows can carry no families anyway.
            diff_rows = [r for r in diff["rows"] if r["student_id"] in current_ids]
            notified = sorted({pid for r in diff_rows for pid in r["family_ids"]})

            placements = computed_stop_times(document, school)
            anchors = plan_gate_anchors(school)

            # (4) Preserve the displaced live state BEFORE any mutation (R21),
            # then demote-before-promote — ordered because the 011 partial
            # uniques enforce immediately ('applied' uniqueness itself is
            # DAO-enforced, so the swap needs no intermediate status).
            applied_prev = conn.execute(
                "select id from live_fleet_plans "
                "where school_id = %s and status = 'applied' "
                "order by applied_at desc nulls last, created_at desc limit 1 for update",
                (school_id,),
            ).fetchone()
            if applied_prev:
                captured = conn.execute(_CAPTURE_LIVE_SQL, (school_id,)).fetchone()["document"]
                # One-level history is DELIBERATE DESTRUCTION: the displaced
                # 'previous' row is a document holding every child's name and
                # coordinates (an aggregate PII target), so it is DELETED
                # outright, never archived — apply→restore→restore stays a
                # clean toggle and a plan two applies back is gone (the
                # restore KTD, doubling as retention hygiene).
                conn.execute(
                    "delete from live_fleet_plans where school_id = %s and status = 'previous'",
                    (school_id,),
                )
                conn.execute(
                    "update live_fleet_plans set status = 'previous', document = %s "
                    "where id = %s",
                    (Jsonb(captured), applied_prev["id"]),
                )

            # (5) Reconcile live_routes IN PLACE to the document's (bus, type)
            # pairs. pairs preserves document order (bus order, then legs) —
            # the fixed order the post-commit refresh follows.
            multi_trip = _multi_trip_bus_ids(
                conn, sorted({str(r["bus_id"]) for r in routes if r["bus_id"] is not None})
            )
            pairs: dict[tuple[str, str], dict] = {}
            for bus in doc_buses:
                bid = str(bus["bus_id"])
                for leg in plan_solver.LEGS:
                    leg_doc = (bus.get("legs") or {}).get(leg) or {}
                    kept = []
                    for stop in leg_doc.get("stops") or []:
                        students = [
                            s for s in stop.get("students") or []
                            if str(s["id"]) in current_ids
                        ]
                        if students:
                            kept.append({**stop, "students": students})
                    if kept:
                        pairs[(bid, leg)] = {"bus_name": bus.get("bus_name"), "stops": kept}

            route_ids: dict[tuple[str, str], str] = {}
            surplus: list[dict] = []
            for r in routes:
                bid = str(r["bus_id"]) if r["bus_id"] is not None else None
                if bid is not None and bid in multi_trip:
                    # Multi-trip chains survive UNTOUCHED (Scope Boundaries):
                    # U4 never drafted these buses, the document carries no
                    # pairs for them, and retiring an operating chain would
                    # destroy service the plan declared out of scope.
                    continue
                key = (bid, r["type"])
                if bid is not None and key in pairs and key not in route_ids:
                    route_ids[key] = str(r["id"])
                else:
                    surplus.append(r)

            for key, info in pairs.items():
                bid, leg = key
                rid = route_ids.get(key)
                if rid is not None:
                    # Reuse in place: route identity (and everything hanging
                    # off the id) survives the apply. Flags per the ordering
                    # authority (custom > manual > plan > auto): plan order
                    # wins and both freezes clear — the 008 CHECK stays
                    # satisfied. gate_anchor clears because the document's
                    # times were solved against the school bell (routes did
                    # not exist at draft time), and a stale route-level
                    # override would re-anchor the first post-apply recompute
                    # away from what families were just told. polyline/totals
                    # clear because they describe the OLD stop set.
                    # last_recalc_degraded = TRUE is the durable
                    # refresh-pending marker: the post-commit geometry refresh
                    # clears it per route on Google-quality success, so a
                    # crash between commit and refresh leaves the route
                    # visibly flagged rather than silently stale.
                    conn.execute(
                        "update live_routes set custom_stops = false, "
                        "manual_stop_order = false, plan_ordered = true, "
                        "stops_computed = true, last_recalc_degraded = true, "
                        "gate_anchor = null, polyline = null, "
                        "total_distance_m = null, total_duration_s = null "
                        "where id = %s",
                        (rid,),
                    )
                else:
                    label = "Morning" if leg == plan_solver.LEG_MORNING else "Afternoon"
                    row = conn.execute(
                        "insert into live_routes (name, type, bus_id, school_id, "
                        "trip_index, custom_stops, manual_stop_order, plan_ordered, "
                        "stops_computed, last_recalc_degraded) "
                        "values (%s, %s, %s, %s, 1, false, false, true, true, true) "
                        "returning id",
                        (f"{info['bus_name']} {label}", leg, bid, school_id),
                    ).fetchone()
                    route_ids[key] = str(row["id"])

            for r in surplus:
                # Retired surplus routes are DELETED, not emptied-and-kept:
                # run history is safe (live_runs.route_id is ON DELETE SET
                # NULL and run rows denormalize bus/school/type, so nothing a
                # closed or in-flight run needs lives on the route row), links
                # and stop rows cascade with it, and nothing reads a retired
                # route afterwards — while a kept empty row would linger on
                # RoutesPage as a ghost of the plan that just replaced it.
                conn.execute("delete from live_routes where id = %s", (r["id"],))

            # (6) Membership rewrite, delete-before-insert scoped BY
            # STUDENT-AND-LEG — deliberately NOT by school or route: the
            # deferrable unique (student_id, route_type) is GLOBAL, so a stale
            # cross-school link left behind would abort this commit at the
            # deferred check; deleting by student-and-leg clears it wherever
            # it lives. The scope is every document student (placed AND
            # unplaceable) for every leg the document writes: a child the
            # plan narrowed or could not place must not keep riding a
            # materialized route through a stale link.
            doc_student_ids = {sid for (sid, _leg) in placements} | {
                str(u["student_id"]) for u in unplaceable
            }
            link_students = sorted(doc_student_ids & current_ids)
            legs_written = sorted({leg for (_bid, leg) in pairs})
            for leg in legs_written:
                if link_students:
                    conn.execute(
                        "delete from live_student_routes "
                        "where route_type = %s and student_id = any(%s::uuid[])",
                        (leg, link_students),
                    )
            for key, info in pairs.items():
                rid = route_ids[key]
                for stop in info["stops"]:
                    for s in stop["students"]:
                        conn.execute(
                            "insert into live_student_routes (student_id, route_id) "
                            "values (%s, %s) on conflict (student_id, route_id) do nothing",
                            (str(s["id"]), rid),
                        )

            # (7) Materialize stops from the document: student-linked rows in
            # document order carrying the document's computed times (frozen
            # from here — the post-commit refresh never rewrites them), plus
            # the gate row per the house convention (first on afternoon, last
            # on morning) carrying the anchor: the document's AM leg arrives
            # at the gate exactly at the anchor and the PM leg departs there.
            for key, info in pairs.items():
                _bid, leg = key
                rid = route_ids[key]
                conn.execute("delete from live_route_stops where route_id = %s", (rid,))
                is_afternoon = leg == plan_solver.LEG_AFTERNOON
                base = 2 if is_afternoon else 1
                for i, stop in enumerate(info["stops"]):
                    for s in stop["students"]:
                        sid = str(s["id"])
                        conn.execute(
                            "insert into live_route_stops (route_id, name, stop_order, "
                            "scheduled_time, lat, lng, is_school_gate, student_id) "
                            "values (%s, %s, %s, %s, %s, %s, false, %s)",
                            (
                                rid,
                                _stop_label(current_by_id[sid]),
                                base + i,
                                placements[(sid, leg)]["scheduled_time"],
                                stop.get("lat"),
                                stop.get("lng"),
                                sid,
                            ),
                        )
                gate_order = 1 if is_afternoon else len(info["stops"]) + 1
                conn.execute(
                    "insert into live_route_stops (route_id, name, stop_order, "
                    "scheduled_time, lat, lng, is_school_gate, student_id) "
                    "values (%s, %s, %s, %s, %s, %s, true, null)",
                    (rid, school["name"] or "School", gate_order, anchors[leg],
                     school["lat"], school["lng"]),
                )

            # (8) Re-derive the denormalized student attributes from the
            # document. pickup_time is a MORNING-CLOCK value (the shipped
            # rule): the AM computed time when the child rides mornings, NULL
            # otherwise — a PM-only child never carries a pickup_time. The
            # document pattern (review edits included) becomes live truth
            # here (R20: draft-scoped until apply).
            for sid in link_students:
                am = placements.get((sid, plan_solver.LEG_MORNING))
                pm = placements.get((sid, plan_solver.LEG_AFTERNOON))
                conn.execute(
                    "update live_students set bus_id = %s, pickup_time = %s, "
                    "ridership_pattern = %s where id = %s",
                    (
                        (am or pm or {}).get("bus_id"),
                        am["scheduled_time"] if am else None,
                        _effective_pattern(document, basis, sid),
                        sid,
                    ),
                )

            # (9) The plan-applied audit row: the self-contained apply record.
            # elapsed_ms covers gates + writes up to this row — a transaction
            # cannot know its own post-commit refresh/push phases; those land
            # in the phase-timing log line instead.
            detail = {
                "plan_id": str(plan["id"]),
                "routes_written": len(pairs),
                "routes_retired": len(surplus),
                "families_notified": len(notified),
                "degraded": bool(plan["degraded"]),
                "elapsed_ms": int((time.monotonic() - t_start) * 1000),
            }
            audit = conn.execute(
                "insert into live_admin_audit "
                "(actor_id, actor_name, actor_email, action, school_id, detail) "
                "values (%s, %s, %s, 'plan-applied', %s, %s) returning id",
                (
                    actor.get("id"),
                    actor.get("full_name") or actor.get("email") or "unknown",
                    actor.get("email") or "unknown",
                    school_id,
                    Jsonb(detail),
                ),
            ).fetchone()
            audit_id = str(audit["id"])

            # (10) Feed rows — inserted ON THIS CONNECTION so a rollback takes
            # them too (the shared composer; restore reuses it verbatim).
            feed_rows = self._write_plan_feed_rows(conn, diff_rows, audit_id)

            # Baselines upsert for every notified PLACED (student, leg) — R15
            # updates the baseline only on send (upserts, then stale deletes
            # — the shared writer; restore reuses it verbatim).
            self._write_baselines(conn, diff_rows)

            # (11) Promote the draft — the demote already happened in step 4.
            applied_row = conn.execute(
                "update live_fleet_plans set status = 'applied', applied_at = now() "
                f"where id = %s returning {_META_COLUMNS}",
                (plan_id,),
            ).fetchone()

            # FINAL gate, deliberately the transaction's LAST act: every
            # acknowledgment must name a (student, leg) this document actually
            # lists unplaceable. A stale client acknowledging children a
            # re-solve already placed is not looking at this plan and must not
            # apply it. The position is load-bearing twice over: it fires
            # after every write above, so it doubles as the integration
            # suite's atomicity probe — a failure here proves routes, links,
            # stops, audit, feed rows, baselines and status flips all roll
            # back as one atom (test_fleet_plan_apply).
            known = {(str(u["student_id"]), u["leg"]) for u in unplaceable}
            unknown = acked - known
            if unknown:
                listing = "; ".join(f"{sid} ({leg})" for sid, leg in sorted(unknown))
                raise ConflictError(
                    "Acknowledgment names children this plan does not list as "
                    f"unplaceable — the plan may have changed under you: {listing}"
                )
        t_txn_ms = int((time.monotonic() - t_start) * 1000) - t_gates_ms

        # --- Post-commit, OUTSIDE the transaction (compensable phases) --------
        # (a) Geometry-only refresh, ONE ROUTE PER TRANSACTION along the
        # plan's fixed order — the fleet_dao.update_school precedent:
        # provider calls never run while holding every route lock, which is
        # what keeps the atomic core provider-free.
        t0 = time.monotonic()
        refreshed, degraded_routes = self._refresh_routes(
            "apply", plan_id, [route_ids[key] for key in pairs]
        )
        t_refresh_ms = int((time.monotonic() - t0) * 1000)

        # (b) Awaited push delivery for the feed rows the transaction already
        # wrote — delivery only, never re-insertion; per-recipient isolation
        # and the one-line fan-out summary live in the push service.
        t0 = time.monotonic()
        push_summary = self._push_service.deliver_plan_feed_rows(feed_rows)
        t_push_ms = int((time.monotonic() - t0) * 1000)

        logger.info(
            "fleet plan apply %s school %s: gates=%dms txn=%dms refresh=%dms "
            "push=%dms routes=%d retired=%d families=%d feed_rows=%d degraded_routes=%d",
            plan_id, school_id, t_gates_ms, t_txn_ms, t_refresh_ms, t_push_ms,
            len(pairs), len(surplus), len(notified), len(feed_rows),
            len(degraded_routes),
        )
        return {
            "ok": True,
            "already_applied": False,
            "plan": {**dict(applied_row), "id": str(applied_row["id"])},
            "school_id": school_id,
            "routes_written": len(pairs),
            "routes_retired": len(surplus),
            "notified_family_count": len(notified),
            "notified_families": notified,
            "feed_rows_written": len(feed_rows),
            "audit_id": audit_id,
            "degraded": bool(plan["degraded"]),
            "refresh": {"refreshed": refreshed, "degraded": degraded_routes},
            "push": push_summary,
        }

    # --- shared apply/restore machinery (U6/U7) -------------------------------
    # Restore is an apply whose source is the preserved snapshot (the KTD), so
    # the fan-out, baseline and refresh phases are ONE implementation each,
    # called by both acts.

    def _write_plan_feed_rows(self, conn, diff_rows: list[dict], audit_id: str) -> list[dict]:
        """Feed rows for one apply/restore act, inserted ON THE CALLER'S
        transaction connection so a rollback takes them too
        (PushDao.insert_notification opens its own connection per row and
        cannot serve here). Composed per student, one row per (family,
        student, type) per act: a both-legs change reads as one message, and
        the 011 plan dedup arbiter backstops repeats."""
        feed_rows: list[dict] = []
        by_student: dict[str, dict] = {}
        for row in diff_rows:
            g = by_student.setdefault(
                row["student_id"],
                {"name": row["student_name"], "placed": [], "unplaced": [],
                 "families": row["family_ids"]},
            )
            (g["placed"] if row["current"] is not None else g["unplaced"]).append(row)
        for sid in sorted(by_student):
            g = by_student[sid]
            if g["placed"]:
                parts = [
                    f"{_leg_label(r['leg'])}: {r['current']['stop_name']} at "
                    f"{r['current']['scheduled_time']} on "
                    f"{r['current'].get('bus_name') or 'the school bus'}"
                    for r in g["placed"]
                ]
                legs = {r["leg"] for r in g["placed"]}
                for pid in g["families"]:
                    inserted = self._push_dao.insert_plan_notification(
                        conn, pid,
                        type="route-updated", title="Route updated",
                        body=f"{g['name']} — " + "; ".join(parts) + ".",
                        student_id=sid,
                        bus_id=g["placed"][0]["current"]["bus_id"],
                        run_type=next(iter(legs)) if len(legs) == 1 else None,
                        plan_audit_id=audit_id,
                    )
                    if inserted:
                        feed_rows.append(inserted)
            if g["unplaced"]:
                parts = []
                for r in g["unplaced"]:
                    leg_word = _leg_label(r["leg"]).lower()
                    if DIFF_NEWLY_UNPLACEABLE in r["categories"]:
                        parts.append(
                            f"no {leg_word} could be planned — the school will follow up"
                        )
                    else:
                        parts.append(f"the {leg_word} was removed from the plan")
                legs = {r["leg"] for r in g["unplaced"]}
                for pid in g["families"]:
                    inserted = self._push_dao.insert_plan_notification(
                        conn, pid,
                        type="route-unassigned", title="Route change",
                        body=f"{g['name']} — " + "; ".join(parts) + ".",
                        student_id=sid, bus_id=None,
                        run_type=next(iter(legs)) if len(legs) == 1 else None,
                        plan_audit_id=audit_id,
                    )
                    if inserted:
                        feed_rows.append(inserted)
        return feed_rows

    @staticmethod
    def _write_baselines(conn, diff_rows: list[dict]) -> None:
        """R15 baseline maintenance for one apply/restore act: upsert for
        every notified PLACED (student, leg) — written for every diff row,
        linked family or not, so the first apply seeds baselines for the
        whole school by design — THEN delete the stale baselines the diff
        just consumed: every notified (student, leg) the plan no longer
        serves (leg removed or now unplaceable). The removal itself notified;
        deleting the baseline makes a later re-widening a first communication
        again (it must notify even when the new time happens to match the
        stale one) and keeps a still-unplaceable child silent on the next act
        (neither membership nor baseline — the R15 still-unplaceable rule)."""
        for row in diff_rows:
            if row["current"] is None:
                continue
            cur = row["current"]
            conn.execute(
                "insert into live_communicated_stops (student_id, route_type, "
                "stop_name, stop_lat, stop_lng, scheduled_time, bus_id, communicated_at) "
                "values (%s, %s, %s, %s, %s, %s, %s, now()) "
                "on conflict (student_id, route_type) do update set "
                "stop_name = excluded.stop_name, stop_lat = excluded.stop_lat, "
                "stop_lng = excluded.stop_lng, scheduled_time = excluded.scheduled_time, "
                "bus_id = excluded.bus_id, communicated_at = excluded.communicated_at",
                (row["student_id"], row["leg"], cur["stop_name"], cur["lat"],
                 cur["lng"], cur["scheduled_time"], cur["bus_id"]),
            )
        for row in diff_rows:
            if row["current"] is None:
                conn.execute(
                    "delete from live_communicated_stops "
                    "where student_id = %s and route_type = %s",
                    (row["student_id"], row["leg"]),
                )

    def _refresh_routes(self, act: str, plan_id: str, route_ids: list[str]) -> tuple[list[str], list[str]]:
        """Post-commit geometry-only refresh, one route per transaction along
        the given fixed order (the fleet_dao.update_school precedent — shared
        by apply and restore). Returns (refreshed, degraded_routes)."""
        refreshed: list[str] = []
        degraded_routes: list[str] = []
        for rid in route_ids:
            try:
                ok = self._refresh_route_geometry(rid)
            except Exception:
                # A failed refresh leaves the route flagged (the marker was
                # set in-transaction) — degraded per route, never plan-wide.
                logger.exception(
                    "fleet plan %s %s: geometry refresh failed for route %s",
                    act, plan_id, rid,
                )
                ok = False
            (refreshed if ok else degraded_routes).append(rid)
        return refreshed, degraded_routes

    # --- restore (U7) ---------------------------------------------------------

    def restore_plan(
        self, plan_id: str, *, confirmations: list[dict],
        acknowledgments: list[dict], actor: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Restore the preserved 'previous' plan (R21/R22/R23): an APPLY whose
        source is the as-evolved capture the displacing act preserved (the
        KTD), through the same gate pipeline re-validated against CURRENT
        enrolment and fleet, with the same diff → feed rows → baselines →
        audit → geometry-refresh → push machinery in the same order.

        Inside ONE provider-free transaction, mirroring apply's step order:
        (1) lock the plan row FOR UPDATE first; (2) idempotency by status —
        restoring the just-restored (now applied) plan answers the applied
        state with zero side effects, so a retry racing a gateway timeout
        cannot toggle the school BACK (the reason restore targets the plan
        id, never 'the school's current previous'); (3) gates against the
        locked snapshot — roster drift (a DEPARTED child in the preserved
        document, named via its denormalized name, or a child ENROLLED after
        the capture, who will be left routeless) each confirmed or 409
        listing them; fleet drift against the preserved loads through the
        shared checker, 409 naming the bus — no partial restore; then the
        notification diff (the shared function, pre-mutation) over a
        solver-shaped view of the capture whose ride durations are derived
        from the preserved wall-clock times, so the restored times are the
        preserved times exactly; (4) capture the displaced live state
        as-evolved (same one-statement capture) and swap statuses — PROMOTE
        the restoring row first (vacating the one-'previous' partial unique;
        'applied' uniqueness is DAO-enforced, so two applied rows may
        coexist within the swap — the 011 design), then demote the old
        applied row to 'previous' carrying the fresh capture: net effect
        apply<->restore toggle, restore-of-restore returns you back;
        (5) reconcile route IDENTITY, not (bus, type): preserved route ids
        that still exist are rewritten in place, vanished ones get fresh
        rows, live routes the preserved plan does not cover are retired —
        with the same multi-trip-chain exclusion as apply, and surplus
        deleted BEFORE rewrites because live_routes_bus_type_key cannot
        defer; (6) links rewritten delete-before-insert scoped by
        student-and-leg; (7) stops materialized VERBATIM from the captured
        rows (names, order, times, gate rows — the as-evolved truth);
        (8) student bus/pickup/pattern restored verbatim from the capture
        (enrolled-after children go routeless); (9) the 'plan-restored'
        audit row; (10) feed rows + baselines through the shared writers;
        (11) the final unknown-acknowledgment gate, deliberately the
        transaction's LAST act — the same atomicity probe position as apply.
        Commit; then geometry-only refresh and awaited push."""
        t_start = time.monotonic()
        confirmed = {
            (str(c.get("kind")), str(c.get("student_id"))) for c in confirmations
        }
        acked = {
            (str(a.get("student_id")), _validate_leg(str(a.get("leg"))))
            for a in acknowledgments
        }

        with get_connection() as conn:
            # (1) Plan row FIRST (serializes a double-submit), then the
            # school's routes in sorted-id order — apply's lock order.
            plan = conn.execute(
                "select * from live_fleet_plans where id = %s for update", (plan_id,)
            ).fetchone()
            if not plan:
                raise NotFoundError(
                    "Plan not found — the school may have no preserved "
                    "previous plan to restore"
                )
            # (2) Idempotency by status, apply's no-op shape: the plan being
            # already live IS the requested end state.
            if plan["status"] == "applied":
                logger.info(
                    "fleet plan restore %s: already applied — idempotent return", plan_id
                )
                return {
                    "ok": True,
                    "already_applied": True,
                    "plan": {
                        "id": str(plan["id"]), "status": plan["status"],
                        "created_at": plan["created_at"], "applied_at": plan["applied_at"],
                    },
                    "school_id": str(plan["school_id"]),
                }
            if plan["status"] != "previous":
                raise ConflictError(
                    "Only the preserved previous plan can be restored — this "
                    f"plan is {plan['status']}"
                )
            capture = plan["document"]
            if not capture or "routes" not in capture:
                raise ConflictError(
                    "This preserved plan has no as-evolved capture to restore"
                )
            school_id = str(plan["school_id"])
            school = conn.execute(
                "select id, name, lat, lng, morning_bell, afternoon_bell "
                "from live_schools where id = %s",
                (school_id,),
            ).fetchone()
            if not school:
                raise NotFoundError("School not found")
            routes = conn.execute(
                "select * from live_routes where school_id = %s order by id for update",
                (school_id,),
            ).fetchall()

            # (3) Gates, against the LOCKED snapshot. -------------------------
            students_now = conn.execute(
                "select id, name, home_address, home_lat, home_lng, ridership_pattern "
                "from live_students where school_id = %s",
                (school_id,),
            ).fetchall()
            current_by_id = {str(r["id"]): r for r in students_now}
            current_ids = set(current_by_id)
            capture_students = {
                str(s["id"]): s for s in capture.get("students") or []
            }

            # (3a) Roster drift vs the capture (R22 re-validated): a child in
            # the preserved document who no longer exists is DEPARTED — named
            # via the capture's denormalized name (the 007/010 precedent) and
            # dropped from every write once confirmed; a child who exists now
            # but has no place in the preserved plan is ENROLLED-after-capture
            # and will be left routeless (per-leg 'unassigned' semantics).
            drift: list[tuple[str, str, str]] = []
            for sid, s in capture_students.items():
                if sid not in current_ids:
                    drift.append((DRIFT_DEPARTED, sid, s.get("name") or sid))
            for sid, row in current_by_id.items():
                if sid not in capture_students:
                    drift.append((DRIFT_ENROLLED, sid, row["name"]))
            unconfirmed = [d for d in drift if (d[0], d[1]) not in confirmed]
            if unconfirmed:
                listing = "; ".join(
                    f"{kind}: {name} ({sid})" for kind, sid, name in sorted(unconfirmed)
                )
                raise ConflictError(
                    "The school has changed since this plan was preserved — "
                    "confirm each item to restore anyway (a departed child is "
                    "dropped by name; a child enrolled after the capture is "
                    f"left without a route). Unconfirmed: {listing}"
                )

            # Preserved routes eligible for materialization: bus-carrying,
            # single-trip, and not on a bus that NOW runs a multi-trip chain —
            # the same exclusion as apply (chains survive untouched, out of
            # plan scope). Captured stop rows for departed children are
            # dropped; a route left with nothing but its gate row is not
            # restored (its live id, if alive, is retired like any other
            # uncovered route — apply's empty-pair behavior).
            multi_trip = _multi_trip_bus_ids(
                conn, sorted({str(r["bus_id"]) for r in routes if r["bus_id"] is not None})
            )
            restored: list[dict] = []   # [{route, kept_rows}] in capture order
            for route in capture.get("routes") or []:
                bid = str(route["bus_id"]) if route.get("bus_id") is not None else None
                if bid is None or bid in multi_trip:
                    continue
                if int(route.get("trip_index") or 1) >= 2:
                    continue
                kept = [
                    r for r in route.get("stops") or []
                    if r.get("student_id") is None or str(r["student_id"]) in current_ids
                ]
                if not any(not r.get("is_school_gate") for r in kept):
                    continue
                restored.append({"route": route, "kept": kept})

            # (3b) Fleet drift vs the PRESERVED loads (post-departure), via
            # the shared checker: no partial restore.
            loads_by_bus: dict[str, tuple[str, int]] = {}
            for item in restored:
                route = item["route"]
                bid = str(route["bus_id"])
                children = sum(
                    1 for r in item["kept"]
                    if r.get("student_id") is not None and not r.get("is_school_gate")
                )
                name = route.get("bus_name") or bid
                prev_load = loads_by_bus.get(bid, (name, 0))[1]
                loads_by_bus[bid] = (name, max(children, prev_load))
            problems = _fleet_drift_problems(
                conn, school_id,
                [(bid, name, load) for bid, (name, load) in loads_by_bus.items()],
            )
            if problems:
                raise ConflictError(
                    "The fleet has changed since this plan was preserved — "
                    + "; ".join(problems)
                    + ". Fix the fleet first: a preserved plan is restored "
                    "whole or not at all"
                )

            # Solver-shaped view of the capture for the SHARED diff function:
            # per-(bus, leg) stops with student lists in the solver's naming
            # convention (joined child names — what the baselines carry), and
            # ride durations derived from each preserved wall-clock time
            # against the current anchor, so computed_stop_times reproduces
            # the preserved times EXACTLY, bell drift or not.
            anchors = plan_gate_anchors(school)
            document = self._capture_as_plan_document(restored, anchors)

            # Chain riders' legs stay out of the routeless list and the link
            # rewrite: their membership belongs to a chain the plan never
            # governed (the multi-trip exclusion, rider side).
            chain_pairs: set[tuple[str, str]] = set()
            if multi_trip:
                for r in conn.execute(
                    "select sr.student_id, sr.route_type "
                    "from live_student_routes sr "
                    "join live_routes r on r.id = sr.route_id "
                    "where r.school_id = %s and r.bus_id = any(%s::uuid[])",
                    (school_id, sorted(multi_trip)),
                ).fetchall():
                    chain_pairs.add((str(r["student_id"]), r["route_type"]))

            # Every current (student, leg) the preserved plan does not place
            # is routeless after the restore — the per-leg 'unassigned'
            # semantics. The shared diff notifies only those the live world
            # currently serves (membership or baseline), so an
            # always-routeless child stays silent (R15).
            placements = computed_stop_times(document, school)
            for sid, row in sorted(current_by_id.items()):
                for leg in plan_solver.LEGS:
                    if (sid, leg) in placements or (sid, leg) in chain_pairs:
                        continue
                    document["unplaceable"].append({
                        "student_id": sid, "name": row["name"],
                        "leg": leg, "constraint": UNASSIGNED_CONSTRAINT,
                    })
            t_gates_ms = int((time.monotonic() - t_start) * 1000)

            # Notification diff — the shared function on THIS transaction's
            # connection, deliberately BEFORE any mutation (apply's rule:
            # bus-change and newly-unplaceable read live membership).
            diff = diff_plan_vs_live(conn, document, school)
            diff_rows = [r for r in diff["rows"] if r["student_id"] in current_ids]
            notified = sorted({pid for r in diff_rows for pid in r["family_ids"]})

            # (4) Preserve the displaced live state BEFORE any mutation, then
            # swap statuses. The order INVERTS apply's demote-before-promote:
            # the restoring row itself holds the one-'previous' slot, so it
            # must vacate (promote) before the old applied row can take it —
            # legal because 'applied' uniqueness is DAO-enforced, not indexed
            # (the 011 design: "the restore swap needs no intermediate
            # status").
            captured = conn.execute(_CAPTURE_LIVE_SQL, (school_id,)).fetchone()["document"]
            restored_row = conn.execute(
                "update live_fleet_plans set status = 'applied', applied_at = now() "
                f"where id = %s returning {_META_COLUMNS}",
                (plan_id,),
            ).fetchone()
            displaced = conn.execute(
                "select id from live_fleet_plans "
                "where school_id = %s and status = 'applied' and id <> %s "
                "order by applied_at desc nulls last, created_at desc limit 1 for update",
                (school_id, plan_id),
            ).fetchone()
            if displaced:
                # The old applied row becomes the new previous, carrying the
                # as-evolved capture — the clean one-level toggle.
                conn.execute(
                    "update live_fleet_plans set status = 'previous', document = %s "
                    "where id = %s",
                    (Jsonb(captured), displaced["id"]),
                )
                displaced_id = str(displaced["id"])
            else:
                # Defensive: no applied row to demote (never the case after a
                # normal apply). The displaced live state must still be
                # preserved or the toggle would destroy it.
                row = conn.execute(
                    "insert into live_fleet_plans (school_id, status, document, created_by) "
                    "values (%s, 'previous', %s, %s) returning id",
                    (school_id, Jsonb(captured), actor.get("id")),
                ).fetchone()
                displaced_id = str(row["id"])

            # (5) Reconcile route IDENTITY. Surplus first: the partial unique
            # live_routes_bus_type_key (bus_id, type, trip_index) cannot
            # defer, so live rows about to release a (bus, type) slot must go
            # before any rewrite claims it; rewritten rows then park bus_id
            # NULL (outside the partial index) before taking their captured
            # values, so an in-swap collision is impossible.
            covered = {
                str(item["route"]["id"]) for item in restored
            } & {str(r["id"]) for r in routes}
            surplus: list[dict] = []
            for r in routes:
                bid = str(r["bus_id"]) if r["bus_id"] is not None else None
                if bid is not None and bid in multi_trip:
                    continue  # chains survive untouched (Scope Boundaries)
                if str(r["id"]) not in covered:
                    surplus.append(r)
            for r in surplus:
                # Deleted, not emptied — apply's retirement semantics: run
                # history survives (live_runs.route_id ON DELETE SET NULL),
                # links and stops cascade.
                conn.execute("delete from live_routes where id = %s", (r["id"],))
            if covered:
                conn.execute(
                    "update live_routes set bus_id = null where id = any(%s::uuid[])",
                    (sorted(covered),),
                )
            route_id_map: dict[str, str] = {}
            restored_route_ids: list[str] = []
            for item in restored:
                route = item["route"]
                rid = str(route["id"])
                fields = (
                    route.get("name"), route["type"], str(route["bus_id"]),
                    int(route.get("trip_index") or 1), route.get("gate_anchor"),
                    bool(route.get("custom_stops")),
                    bool(route.get("manual_stop_order")),
                    bool(route.get("plan_ordered")),
                    bool(route.get("stops_computed", True)),
                )
                if rid in covered:
                    # Rewritten IN PLACE: route identity (and everything
                    # hanging off the id) survives the restore. Flags are the
                    # CAPTURED flags — as-evolved truth, freezes included —
                    # not apply's plan-ordered stamp: restore puts back what
                    # was, it does not re-plan. polyline/totals clear (they
                    # describe the displaced stop set); last_recalc_degraded
                    # = TRUE is the durable refresh-pending marker, exactly
                    # as in apply.
                    conn.execute(
                        "update live_routes set name = %s, type = %s, bus_id = %s, "
                        "trip_index = %s, gate_anchor = %s, custom_stops = %s, "
                        "manual_stop_order = %s, plan_ordered = %s, "
                        "stops_computed = %s, last_recalc_degraded = true, "
                        "polyline = null, total_distance_m = null, "
                        "total_duration_s = null where id = %s",
                        (*fields, rid),
                    )
                    route_id_map[rid] = rid
                else:
                    # Vanished (routes are hard-deletable): a fresh row — its
                    # old run history is already detached by ON DELETE SET
                    # NULL.
                    row = conn.execute(
                        "insert into live_routes (name, type, bus_id, trip_index, "
                        "gate_anchor, custom_stops, manual_stop_order, plan_ordered, "
                        "stops_computed, school_id, last_recalc_degraded) "
                        "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, true) "
                        "returning id",
                        (*fields, school_id),
                    ).fetchone()
                    route_id_map[rid] = str(row["id"])
                restored_route_ids.append(route_id_map[rid])

            # (6) Membership rewrite, delete-before-insert scoped by
            # STUDENT-AND-LEG (the deferrable unique is global): every
            # (student, leg) the restore places, lists routeless, or writes a
            # membership for — chain legs excluded via the routeless filter
            # above, so a chain rider's link survives untouched.
            severed: dict[str, set[str]] = {}
            for (sid, leg) in placements:
                severed.setdefault(leg, set()).add(sid)
            for u in document["unplaceable"]:
                severed.setdefault(u["leg"], set()).add(str(u["student_id"]))
            for item in restored:
                leg = item["route"]["type"]
                for s in item["route"].get("students") or []:
                    if str(s["id"]) in current_ids:
                        severed.setdefault(leg, set()).add(str(s["id"]))
            for leg in sorted(severed):
                conn.execute(
                    "delete from live_student_routes "
                    "where route_type = %s and student_id = any(%s::uuid[])",
                    (leg, sorted(severed[leg])),
                )
            for item in restored:
                rid = route_id_map[str(item["route"]["id"])]
                for s in item["route"].get("students") or []:
                    if str(s["id"]) in current_ids:
                        conn.execute(
                            "insert into live_student_routes (student_id, route_id) "
                            "values (%s, %s) on conflict (student_id, route_id) do nothing",
                            (str(s["id"]), rid),
                        )

            # (7) Materialize stops VERBATIM from the captured rows — names,
            # order, times, gate rows: the as-evolved truth, frozen exactly
            # as families were told it (departed children's rows dropped).
            for item in restored:
                rid = route_id_map[str(item["route"]["id"])]
                conn.execute("delete from live_route_stops where route_id = %s", (rid,))
                for r in item["kept"]:
                    conn.execute(
                        "insert into live_route_stops (route_id, name, stop_order, "
                        "scheduled_time, lat, lng, is_school_gate, student_id) "
                        "values (%s, %s, %s, %s, %s, %s, %s, %s)",
                        (
                            rid, r.get("name"), r.get("stop_order"),
                            r.get("scheduled_time"), r.get("lat"), r.get("lng"),
                            bool(r.get("is_school_gate")), r.get("student_id"),
                        ),
                    )

            # (8) Student attributes restored VERBATIM from the capture (it
            # snapshotted live_students truth: bus, morning-clock pickup,
            # pattern); a bus deleted since capture nulls out defensively.
            # Enrolled-after children go routeless: bus and pickup cleared,
            # their live pattern kept.
            captured_bus_ids = sorted({
                str(s["bus_id"]) for s in capture_students.values()
                if s.get("bus_id") is not None
            })
            live_bus_ids: set[str] = set()
            if captured_bus_ids:
                live_bus_ids = {
                    str(r["id"]) for r in conn.execute(
                        "select id from live_buses where id = any(%s::uuid[])",
                        (captured_bus_ids,),
                    ).fetchall()
                }
            for sid, s in sorted(capture_students.items()):
                if sid not in current_ids:
                    continue  # departed: confirmed and dropped from every write
                bus = str(s["bus_id"]) if s.get("bus_id") is not None else None
                conn.execute(
                    "update live_students set bus_id = %s, pickup_time = %s, "
                    "ridership_pattern = %s where id = %s",
                    (
                        bus if bus in live_bus_ids else None,
                        s.get("pickup_time"),
                        s.get("pattern") or current_by_id[sid]["ridership_pattern"],
                        sid,
                    ),
                )
            for sid in sorted(current_ids - set(capture_students)):
                conn.execute(
                    "update live_students set bus_id = null, pickup_time = null "
                    "where id = %s",
                    (sid,),
                )

            # (9) The plan-restored audit row — restores are audit-logged
            # like applies (the KTD), same self-contained detail shape.
            detail = {
                "plan_id": str(plan["id"]),
                "displaced_plan_id": displaced_id,
                "routes_written": len(restored),
                "routes_retired": len(surplus),
                "families_notified": len(notified),
                "degraded": bool(plan["degraded"]),
                "elapsed_ms": int((time.monotonic() - t_start) * 1000),
            }
            audit = conn.execute(
                "insert into live_admin_audit "
                "(actor_id, actor_name, actor_email, action, school_id, detail) "
                "values (%s, %s, %s, 'plan-restored', %s, %s) returning id",
                (
                    actor.get("id"),
                    actor.get("full_name") or actor.get("email") or "unknown",
                    actor.get("email") or "unknown",
                    school_id,
                    Jsonb(detail),
                ),
            ).fetchone()
            audit_id = str(audit["id"])

            # (10) Feed rows + baselines through the shared writers, on THIS
            # connection — one atom with everything above.
            feed_rows = self._write_plan_feed_rows(conn, diff_rows, audit_id)
            self._write_baselines(conn, diff_rows)

            # (11) FINAL gate, deliberately the transaction's LAST act (the
            # apply parity position — doubling as the atomicity probe): every
            # acknowledgment must name a (student, leg) this restore actually
            # leaves routeless; a stale client is not looking at this plan.
            known = {
                (str(u["student_id"]), u["leg"]) for u in document["unplaceable"]
            }
            unknown = acked - known
            if unknown:
                listing = "; ".join(f"{sid} ({leg})" for sid, leg in sorted(unknown))
                raise ConflictError(
                    "Acknowledgment names children this restore does not leave "
                    f"routeless — the preserved plan may have changed under you: {listing}"
                )
        t_txn_ms = int((time.monotonic() - t_start) * 1000) - t_gates_ms

        # --- Post-commit, OUTSIDE the transaction: the same compensable
        # phases as apply, along the capture's fixed route order.
        t0 = time.monotonic()
        refreshed, degraded_routes = self._refresh_routes(
            "restore", plan_id, restored_route_ids
        )
        t_refresh_ms = int((time.monotonic() - t0) * 1000)

        t0 = time.monotonic()
        push_summary = self._push_service.deliver_plan_feed_rows(feed_rows)
        t_push_ms = int((time.monotonic() - t0) * 1000)

        logger.info(
            "fleet plan restore %s school %s: gates=%dms txn=%dms refresh=%dms "
            "push=%dms routes=%d retired=%d families=%d feed_rows=%d degraded_routes=%d",
            plan_id, school_id, t_gates_ms, t_txn_ms, t_refresh_ms, t_push_ms,
            len(restored), len(surplus), len(notified), len(feed_rows),
            len(degraded_routes),
        )
        return {
            "ok": True,
            "already_applied": False,
            "plan": {**dict(restored_row), "id": str(restored_row["id"])},
            "school_id": school_id,
            "displaced_plan_id": displaced_id,
            "routes_written": len(restored),
            "routes_retired": len(surplus),
            "notified_family_count": len(notified),
            "notified_families": notified,
            "feed_rows_written": len(feed_rows),
            "audit_id": audit_id,
            "degraded": bool(plan["degraded"]),
            "refresh": {"refreshed": refreshed, "degraded": degraded_routes},
            "push": push_summary,
        }

    @staticmethod
    def _capture_as_plan_document(restored: list[dict], anchors: Mapping[str, str]) -> dict:
        """A solver-shaped document over the preserved capture's materializable
        routes, for the SHARED diff function — the restore materializer's
        read-side twin.

        Stop rows group by stop_order (siblings share an order — apply's
        materialization convention); stop names re-join the children's names
        in the solver's convention (the captured ROW names are `_stop_label`
        address labels, while the baselines apply seeded carry solver stop
        names — diffing labels against names would cry place-change for every
        unchanged family). Ride seconds are derived per child from the
        preserved wall-clock time against the current anchor (AM: anchor −
        time, PM: time − anchor), so ``computed_stop_times`` reproduces the
        preserved times exactly even if the school bell moved since capture.
        Departed children's rows were already dropped by the caller."""
        buses_by_id: dict[str, dict] = {}
        for item in restored:
            route = item["route"]
            leg = route["type"]
            if leg not in plan_solver.LEGS:
                continue
            bid = str(route["bus_id"])
            bus_doc = buses_by_id.setdefault(bid, {
                "bus_id": bid,
                "bus_name": route.get("bus_name"),
                "legs": {leg_key: _empty_leg_doc() for leg_key in plan_solver.LEGS},
            })
            stops_by_order: dict[int, list[dict]] = {}
            for r in item["kept"]:
                if r.get("is_school_gate") or r.get("student_id") is None:
                    continue
                stops_by_order.setdefault(int(r.get("stop_order") or 0), []).append(r)
            leg_doc = bus_doc["legs"][leg]
            anchor_min = _hhmm_to_minutes(anchors[leg]) or 0
            sign = -1 if leg == plan_solver.LEG_MORNING else 1
            for order in sorted(stops_by_order):
                rows = sorted(
                    stops_by_order[order],
                    key=lambda r: (r.get("student_name") or "", str(r["student_id"])),
                )
                students = [
                    {"id": str(r["student_id"]),
                     "name": r.get("student_name") or str(r["student_id"])}
                    for r in rows
                ]
                leg_doc["stops"].append({
                    "lat": rows[0].get("lat"),
                    "lng": rows[0].get("lng"),
                    "name": " / ".join(s["name"] for s in students),
                    "students": students,
                })
                for r, s in zip(rows, students):
                    t = _hhmm_to_minutes(r.get("scheduled_time"))
                    ride = 0.0 if t is None else float(sign * (t - anchor_min) * 60)
                    leg_doc["ride_seconds"].append({
                        "student_id": s["id"], "name": s["name"],
                        "ride_seconds": ride,
                    })
        return {
            "version": 1,
            "buses": [buses_by_id[b] for b in sorted(buses_by_id)],
            "unplaceable": [],
        }

    def _refresh_route_geometry(self, route_id: str) -> bool:
        """Post-commit geometry refresh for ONE materialized route in its own
        transaction (U6). GEOMETRY ONLY: polyline / total_distance_m /
        total_duration_s along the materialized stop order via
        ``geo_service.fixed_sequence_geometry`` — NEVER ``scheduled_time``:
        the document's reviewed, communicated times are frozen at apply (the
        KTD), and on the keyless stack the offline duration estimate must not
        overwrite them either. Clears ``last_recalc_degraded`` (the
        refresh-pending marker the apply transaction set) only on
        Google-quality geometry; a failed or offline refresh writes its
        best-effort totals but leaves the route visibly flagged. Returns True
        exactly on Google-quality success."""
        with get_connection() as conn:
            route = conn.execute(
                "select r.id, r.type, r.bus_id, r.trip_index, "
                "b.depot_lat, b.depot_lng "
                "from live_routes r left join live_buses b on b.id = r.bus_id "
                "where r.id = %s for update of r",
                (route_id,),
            ).fetchone()
            if not route:
                return False
            rows = conn.execute(
                "select lat, lng, stop_order, is_school_gate "
                "from live_route_stops where route_id = %s "
                "order by stop_order asc, name asc",
                (route_id,),
            ).fetchall()
            gate = next(
                ({"lat": r["lat"], "lng": r["lng"]} for r in rows if r["is_school_gate"]),
                None,
            )
            stop_points: list[dict] = []
            seen_orders: set[int] = set()
            for r in rows:
                # One point per stop_order: siblings share a stop.
                if r["is_school_gate"] or r["stop_order"] in seen_orders:
                    continue
                seen_orders.add(r["stop_order"])
                stop_points.append({"lat": r["lat"], "lng": r["lng"]})
            # Depot as the shipped boundary leg (origin on the first morning
            # trip, destination on the last afternoon trip) — drafted buses
            # are single-trip, so this is their own trip either way.
            depot = _depot_leg(conn, route)
            if route["type"] == plan_solver.LEG_AFTERNOON:
                seq = ([gate] if gate else []) + stop_points + ([depot] if depot else [])
            else:
                seq = ([depot] if depot else []) + stop_points + ([gate] if gate else [])
            geom = geo_service.fixed_sequence_geometry(seq)
            degraded = bool(geom["degraded"])
            conn.execute(
                "update live_routes set polyline = %s, total_distance_m = %s, "
                "total_duration_s = %s, last_recalc_degraded = %s where id = %s",
                (geom["polyline"], geom["total_distance_m"], geom["total_duration_s"],
                 degraded, route_id),
            )
        return not degraded

    # --- discard ------------------------------------------------------------

    def discard_draft(self, plan_id: str) -> dict[str, Any]:
        """Discard an open draft: status -> discarded, document/basis scrubbed
        to NULL (metadata kept) — the retention rule, mechanized. Draft only:
        any other status 409s by name."""
        with get_connection() as conn:
            row = conn.execute(
                "select id, status from live_fleet_plans where id = %s for update",
                (plan_id,),
            ).fetchone()
            if not row:
                raise NotFoundError("Plan not found")
            if row["status"] != "draft":
                raise ConflictError(
                    f"Only a draft can be discarded — this plan is {row['status']}"
                )
            updated = conn.execute(
                "update live_fleet_plans set status = 'discarded', "
                f"document = null, basis = null where id = %s returning {_META_COLUMNS}",
                (plan_id,),
            ).fetchone()
        return {"ok": True, **dict(updated)}

    # --- slot-in proposals (U12) ----------------------------------------------
    # Storage: a JSONB list under slot_in_service.PROPOSALS_KEY inside the
    # APPLIED plan row's document (the decision is documented in
    # slot_in_service's module docstring — generation writes it there, this
    # DAO owns list/accept/dismiss against the same key). Accept applies JUST
    # that insertion into the live tables and the router then notifies through
    # the U13 manual-edit pipeline (notify_route_changes) — the shipped
    # diff/baseline machinery, which needs no audit row.

    @staticmethod
    def _applied_plan_slot_ins(conn, school_id: str, *, lock: bool) -> dict:
        suffix = " for update" if lock else ""
        plan = conn.execute(
            "select id, document from live_fleet_plans "
            "where school_id = %s and status = 'applied' "
            f"order by applied_at desc nulls last, created_at desc limit 1{suffix}",
            (school_id,),
        ).fetchone()
        if not plan:
            raise NotFoundError(
                "No applied plan for this school — slot-in proposals live on "
                "the applied plan"
            )
        return plan

    def list_slot_ins(self, school_id: str) -> dict[str, Any]:
        """The school's pending slot-in records, aged-out marked (never
        auto-removed — the student stays visibly unassigned, R13/aging).

        Read-only display hygiene: a record whose student departed, or whose
        every leg is now linked (a manual placement raced the record), is
        filtered from the response without a write — accept re-validates
        against live truth anyway.
        """
        with get_connection() as conn:
            if conn.execute(
                "select 1 from live_schools where id = %s", (school_id,)
            ).fetchone() is None:
                raise NotFoundError("School not found")
            plan = self._applied_plan_slot_ins(conn, school_id, lock=False)
            records = (plan["document"] or {}).get(slot_in_service.PROPOSALS_KEY) or []
            sids = sorted({str(r.get("student_id")) for r in records})
            existing: set[str] = set()
            linked: set[tuple[str, str]] = set()
            if sids:
                existing = {
                    str(r["id"]) for r in conn.execute(
                        "select id from live_students where id = any(%s::uuid[])",
                        (sids,),
                    ).fetchall()
                }
                for r in conn.execute(
                    "select student_id, route_type from live_student_routes "
                    "where student_id = any(%s::uuid[])",
                    (sids,),
                ).fetchall():
                    linked.add((str(r["student_id"]), r["route_type"]))
        proposals: list[dict] = []
        unplaceable: list[dict] = []
        for record in records:
            sid = str(record.get("student_id"))
            legs = record.get("legs") or {}
            if sid not in existing:
                continue
            if legs and all((sid, leg) in linked for leg in legs):
                continue
            out = {**record, "expired": slot_in_service.is_expired(record)}
            (proposals if record.get("kind") == "proposal" else unplaceable).append(out)
        return {
            "school_id": str(school_id),
            "plan_id": str(plan["id"]),
            "aging_days": slot_in_service.SLOT_IN_AGING_DAYS,
            "proposals": proposals,
            "unplaceable": unplaceable,
        }

    def accept_slot_in(self, school_id: str, proposal_id: str) -> dict[str, Any]:
        """Accept ONE proposal: apply just that insertion (AE2/AE7).

        One transaction, apply's lock order (plan row FIRST, then the
        affected route rows in sorted-id order): re-validate the proposal
        against live truth — not expired (409: aged proposals are marked and
        not acceptable), student present with coordinates and not already
        linked on a proposed leg, each route still carrying the applied (or
        frozen) order on an in-service bus, capacity and stop cap re-checked
        (422 naming the constraint, the U5 vocabulary). Then per leg:
        rewrite the link delete-before-insert scoped by STUDENT-AND-LEG (the
        global deferrable unique — apply's rule), materialize the stop at the
        explicit position along the fixed order (siblings join their stop;
        surviving stops NEVER reorder — a frozen route keeps its manual order
        untouched, AE7), and recompute times + geometry along the fixed
        sequence via ``geo_service.fixed_sequence_geometry`` anchored on the
        gate row's communicated time. Finally re-derive the child's bus and
        morning-clock pickup_time and drop the record from the store.

        The provider call runs inside the transaction holding one route row —
        the U5 review-edit precedent (narrow locks, 8 s best-effort timeout),
        accepted at pilot scale. Notification is the ROUTER's post-commit
        dispatch of the U13 ``notify_route_changes`` pipeline over the
        returned route ids — only affected families hear (new child = first
        communication; co-riders on the >= 5-minute rule), and no audit row
        is written (the manual-edit path's contract).
        """
        with get_connection() as conn:
            plan = self._applied_plan_slot_ins(conn, school_id, lock=True)
            document = dict(plan["document"] or {})
            records = list(document.get(slot_in_service.PROPOSALS_KEY) or [])
            record = next(
                (r for r in records if str(r.get("id")) == str(proposal_id)), None
            )
            if record is None:
                raise NotFoundError("Proposal not found — it may have been dismissed")
            if record.get("kind") != "proposal":
                raise ConflictError(
                    "This entry is an unplaceable notice, not a proposal — "
                    "place the child manually through the review surface or "
                    "the Routes page"
                )
            if slot_in_service.is_expired(record):
                raise ConflictError(
                    "This proposal has aged out and can no longer be accepted "
                    f"(window: {slot_in_service.SLOT_IN_AGING_DAYS} days) — "
                    "the student stays unassigned; place them manually or "
                    "re-trigger a fresh proposal by editing the student"
                )
            sid = str(record["student_id"])
            student = conn.execute(
                "select id, name, home_address, home_lat, home_lng "
                "from live_students where id = %s",
                (sid,),
            ).fetchone()
            if not student:
                raise ConflictError(
                    "The student no longer exists — dismiss the proposal"
                )
            if student["home_lat"] is None or student["home_lng"] is None:
                raise ConflictError(
                    f"Student {student['name']} has no home coordinates — "
                    "resolve the address first"
                )
            legs = record.get("legs") or {}
            leg_names = [leg for leg in plan_solver.LEGS if leg in legs]
            if not leg_names:
                raise ConflictError("This proposal names no legs — dismiss it")
            already = conn.execute(
                "select route_type from live_student_routes "
                "where student_id = %s and route_type = any(%s)",
                (sid, leg_names),
            ).fetchall()
            if already:
                taken = ", ".join(sorted(r["route_type"] for r in already))
                raise ConflictError(
                    f"Student {student['name']} is already placed on the "
                    f"{taken} leg — the proposal is stale; dismiss it"
                )

            # Lock the affected route rows in sorted-id order (the global
            # route-lock convention), validating each still carries the
            # applied (or admin-frozen) order on an in-service bus.
            route_ids = sorted(str(legs[leg]["route_id"]) for leg in leg_names)
            routes: dict[str, dict] = {}
            for rid in route_ids:
                route = conn.execute(
                    _ROUTE_GEOMETRY_INPUTS_SQL + " for update of r", (rid,)
                ).fetchone()
                if not route or str(route["school_id"] or "") != str(school_id):
                    raise ConflictError(
                        "A proposed route no longer exists — the proposal is "
                        "stale; dismiss it and place the child manually"
                    )
                if not (route["plan_ordered"] or route["manual_stop_order"]):
                    raise ConflictError(
                        "A proposed route was recalculated since this proposal "
                        "was generated — the stated position no longer applies; "
                        "dismiss it and place the child manually"
                    )
                routes[rid] = dict(route)

            placed: dict[str, dict] = {}
            degraded_any = False
            for leg in leg_names:
                info = legs[leg]
                route = routes[str(info["route_id"])]
                if route["type"] != leg:
                    raise ConflictError(
                        "A proposed route changed direction since this proposal "
                        "was generated — dismiss it and place the child manually"
                    )
                # Link rewrite: delete-before-insert scoped by student-and-leg.
                conn.execute(
                    "delete from live_student_routes "
                    "where route_type = %s and student_id = %s",
                    (leg, sid),
                )
                conn.execute(
                    "insert into live_student_routes (student_id, route_id) "
                    "values (%s, %s) on conflict (student_id, route_id) do nothing",
                    (sid, str(route["id"])),
                )
                result = self._materialize_slot_in(conn, route, info, dict(student))
                degraded_any = degraded_any or result["degraded"]
                placed[leg] = {
                    "route_id": str(route["id"]),
                    "position": result["position"],
                    "join": result["join"],
                    "scheduled_time": result["scheduled_time"],
                }

            # Denormalized student attributes: bus per the shipped morning-wins
            # derivation; pickup_time is MORNING-CLOCK — written only when the
            # morning leg was placed, never a PM time.
            _derive_student_bus(conn, sid)
            am = placed.get(plan_solver.LEG_MORNING)
            if am is not None:
                conn.execute(
                    "update live_students set pickup_time = %s where id = %s",
                    (am["scheduled_time"], sid),
                )

            document[slot_in_service.PROPOSALS_KEY] = [
                r for r in records if str(r.get("id")) != str(proposal_id)
            ]
            conn.execute(
                "update live_fleet_plans set document = %s where id = %s",
                (Jsonb(document), plan["id"]),
            )
        logger.info(
            "slot-in accept %s school %s: student=%s legs=%s degraded=%s",
            proposal_id, school_id, sid, leg_names, degraded_any,
        )
        return {
            "ok": True,
            "proposal_id": str(proposal_id),
            "student_id": sid,
            "student_name": student["name"],
            "text": record.get("text"),
            "legs": placed,
            "route_ids": sorted({p["route_id"] for p in placed.values()}),
            "degraded": degraded_any,
        }

    @staticmethod
    def _materialize_slot_in(conn, route: dict, info: dict, student: dict) -> dict:
        """Insert one student's stop into a materialized route at the
        proposal's explicit position and recompute times along the FIXED
        order (never a re-ordering call — AE7's no-reflow rule holds for
        frozen and plan-ordered routes alike).

        Position semantics: 0-based among the leg's stop GROUPS (siblings
        share a ``stop_order``), clamped to the current group count (the
        route may have gained or lost stops since generation). A home within
        the solver's 30 m collapse radius of an existing group JOINS that
        stop — same order, same time, no shift, no recompute (zero detour).
        Otherwise every row at or past the insertion point (the gate
        included, on morning routes) shifts one order down, the new row lands
        at ``base + position``, and times re-derive from the gate row's
        communicated anchor along the sequence via
        ``fixed_sequence_geometry`` — backward from the gate for morning,
        forward for afternoon, the depot as the shipped boundary leg. The
        gate row's time never moves. A degraded (offline) recompute still
        writes times but flags the route (``last_recalc_degraded``), the
        plan-ordered recompute convention.

        Re-checks capacity and the stop cap against live rows first, raising
        the U5-shaped 422 with the constraint named; the transaction rolls
        the whole accept back.
        """
        rid = str(route["id"])
        rows = conn.execute(
            "select id, stop_order, scheduled_time, lat, lng, is_school_gate, "
            "student_id from live_route_stops where route_id = %s "
            "order by stop_order asc, name asc",
            (rid,),
        ).fetchall()
        gate = next((r for r in rows if r["is_school_gate"]), None)
        groups: list[dict] = []
        by_order: dict[int, dict] = {}
        children = 0
        for r in rows:
            if r["is_school_gate"] or r["student_id"] is None:
                continue
            children += 1
            g = by_order.get(r["stop_order"])
            if g is None:
                g = {"order": r["stop_order"], "lat": r["lat"], "lng": r["lng"],
                     "time": r["scheduled_time"]}
                by_order[r["stop_order"]] = g
                groups.append(g)

        leg = route["type"]
        capacity = route.get("bus_capacity")
        bus_name = route.get("bus_name") or "the assigned bus"
        if capacity is not None and children + 1 > capacity:
            raise PlanConstraintError(
                f"{CONSTRAINT_CAPACITY}: bus {bus_name} would carry "
                f"{children + 1} children on the {leg} leg — its capacity is "
                f"{capacity}; the proposal was not applied"
            )

        home = (float(student["home_lat"]), float(student["home_lng"]))
        join_group = next(
            (g for g in groups
             if g["lat"] is not None and g["lng"] is not None
             and geo_service.haversine_m(home, (float(g["lat"]), float(g["lng"])))
             <= plan_solver.STOP_COLLAPSE_RADIUS_M),
            None,
        )
        if join_group is None and len(groups) + 1 > plan_solver.DEFAULT_STOP_CAP:
            raise PlanConstraintError(
                f"{CONSTRAINT_STOP_CAP}: bus {bus_name} would have "
                f"{len(groups) + 1} stops on the {leg} leg — the cap is "
                f"{plan_solver.DEFAULT_STOP_CAP}; the proposal was not applied"
            )

        if join_group is not None:
            # Sibling join: same order, same communicated time, zero detour —
            # nothing else moves, nothing recomputes.
            conn.execute(
                "insert into live_route_stops (route_id, name, stop_order, "
                "scheduled_time, lat, lng, is_school_gate, student_id) "
                "values (%s, %s, %s, %s, %s, %s, false, %s)",
                (rid, _stop_label(student), join_group["order"],
                 join_group["time"], join_group["lat"], join_group["lng"],
                 str(student["id"])),
            )
            return {
                "position": groups.index(join_group), "join": True,
                "scheduled_time": join_group["time"], "degraded": False,
            }

        is_afternoon = leg == plan_solver.LEG_AFTERNOON
        base = groups[0]["order"] if groups else (2 if (is_afternoon and gate) else 1)
        pos = max(0, min(int(info.get("position") or 0), len(groups)))
        new_order = base + pos
        conn.execute(
            "update live_route_stops set stop_order = stop_order + 1 "
            "where route_id = %s and stop_order >= %s",
            (rid, new_order),
        )
        conn.execute(
            "insert into live_route_stops (route_id, name, stop_order, "
            "scheduled_time, lat, lng, is_school_gate, student_id) "
            "values (%s, %s, %s, null, %s, %s, false, %s)",
            (rid, _stop_label(student), new_order, home[0], home[1],
             str(student["id"])),
        )
        seq_groups = (groups[:pos]
                      + [{"order": new_order, "lat": home[0], "lng": home[1]}]
                      + [{**g, "order": g["order"] + 1} for g in groups[pos:]])

        if any(g["lat"] is None or g["lng"] is None for g in seq_groups):
            # Defensive: a coordinate-less surviving stop (should not exist on
            # a materialized route) blocks the geometry recompute — the new
            # stop inherits its nearest neighbour's time, nothing else moves.
            neighbour = (seq_groups[pos - 1] if pos > 0
                         else (seq_groups[pos + 1] if len(seq_groups) > pos + 1 else None))
            time = (neighbour or {}).get("time")
            conn.execute(
                "update live_route_stops set scheduled_time = %s "
                "where route_id = %s and stop_order = %s",
                (time, rid, new_order),
            )
            return {"position": pos, "join": False,
                    "scheduled_time": time, "degraded": True}

        # Fixed-sequence recompute anchored on the gate row's COMMUNICATED
        # time (falling back to the one anchor authority when absent) — the
        # same arithmetic as the plan document's computed times: backward
        # from the gate for morning, forward for afternoon, depot legs count
        # only toward geometry/driving.
        anchor = None
        if gate is not None and _hhmm_to_minutes(gate["scheduled_time"]) is not None:
            anchor = gate["scheduled_time"]
        else:
            anchor = resolve_gate_anchor(route)
        gate_pt = {"lat": route["school_lat"], "lng": route["school_lng"]}
        depot = _depot_leg(conn, route)
        pts = [{"lat": float(g["lat"]), "lng": float(g["lng"])} for g in seq_groups]
        if is_afternoon:
            seq = [gate_pt] + pts + ([depot] if depot else [])
        else:
            seq = ([depot] if depot else []) + pts + [gate_pt]
        geom = geo_service.fixed_sequence_geometry(seq)
        durations = [float(row.get("duration_s") or 0) for row in geom["legs"]]
        n = len(seq_groups)
        rides = [0.0] * n
        if is_afternoon:
            acc = 0.0
            for i in range(n):
                acc += durations[i] if i < len(durations) else 0.0
                rides[i] = acc
            sign = 1
        else:
            offset = 1 if depot else 0
            acc = 0.0
            for i in range(n - 1, -1, -1):
                idx = offset + i
                acc += durations[idx] if idx < len(durations) else 0.0
                rides[i] = acc
            sign = -1
        new_time = None
        for i, g in enumerate(seq_groups):
            time = _shift_hhmm(anchor, sign * rides[i])
            conn.execute(
                "update live_route_stops set scheduled_time = %s "
                "where route_id = %s and stop_order = %s and not is_school_gate",
                (time, rid, g["order"]),
            )
            if g["order"] == new_order:
                new_time = time
        degraded = bool(geom["degraded"])
        conn.execute(
            "update live_routes set polyline = %s, total_distance_m = %s, "
            "total_duration_s = %s, last_recalc_degraded = %s where id = %s",
            (geom["polyline"], geom["total_distance_m"],
             geom["total_duration_s"], degraded, rid),
        )
        return {"position": pos, "join": False,
                "scheduled_time": new_time, "degraded": degraded}

    def dismiss_slot_in(self, school_id: str, proposal_id: str) -> dict[str, Any]:
        """Dismiss one record (proposal or unplaceable notice): removed from
        the store, no live-table side effects — the student stays visibly
        unassigned (their display_status already reads 'unassigned' with no
        route links)."""
        with get_connection() as conn:
            plan = self._applied_plan_slot_ins(conn, school_id, lock=True)
            document = dict(plan["document"] or {})
            records = list(document.get(slot_in_service.PROPOSALS_KEY) or [])
            record = next(
                (r for r in records if str(r.get("id")) == str(proposal_id)), None
            )
            if record is None:
                raise NotFoundError("Proposal not found — it may already be gone")
            document[slot_in_service.PROPOSALS_KEY] = [
                r for r in records if str(r.get("id")) != str(proposal_id)
            ]
            conn.execute(
                "update live_fleet_plans set document = %s where id = %s",
                (Jsonb(document), plan["id"]),
            )
        return {
            "ok": True,
            "removed": {
                "id": str(record.get("id")),
                "student_id": str(record.get("student_id")),
                "kind": record.get("kind"),
            },
        }
