"""Fleet-plan store and draft generation (U4).

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
from typing import Any, Mapping

from psycopg.types.json import Jsonb

from app.core.db import get_connection
from app.core.errors import BadRequestError, ConflictError, NotFoundError, SafeRideError
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
