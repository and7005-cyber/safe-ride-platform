"""Review surface, review edits and in-draft re-solve (U5, R13).

One method group of ``FleetPlanDao`` — ``app.dao.fleet_plan_dao`` composes
it with the draft, apply/restore and slot-in groups and stays the import
path. Methods are verbatim moves; shared vocabulary comes from ``_shared``.
"""
from typing import Any

from psycopg.types.json import Jsonb

from app.core.db import get_connection
from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.dao.fleet_plan._shared import (
    CONSTRAINT_STOP_CAP,
    UNASSIGNED_CONSTRAINT,
    UNRESOLVED_ADDRESS_CONSTRAINT,
    PlanConstraintError,
    _add_student_to_leg,
    _basis_student,
    _bus_doc,
    _check_bus_leg,
    _effective_pattern,
    _empty_leg_doc,
    _find_placement,
    _leg_rides,
    _normalize_per_leg,
    _pattern_legs,
    _pop_student,
    _recompute_legs,
    _roster_drift,
    _shift_hhmm,
    _stop_key,
    _validate_leg,
    diff_plan_vs_live,
    logger,
    plan_gate_anchors,
)
from app.services import geo_service, plan_solver


class ReviewOps:
    """Review surface (R10/R24), review edits (R9) and in-draft re-solve."""

    # --- review surface (U5) --------------------------------------------------

    def review(self, school_id: str) -> dict[str, Any]:
        """The open draft's computed review surface (R10/R24): the stored
        document plus per-child ride times with wall-clock stop times, per-bus
        capacity use, total driving, the per-leg unplaceable lists, the diff
        vs live from the shared diff function, and ``basis_drift`` — the
        exact enrolled/address-changed/departed rows apply's R22 gate will
        demand confirmations for (the shared ``_roster_drift``), so a caller
        can assemble the apply payload without a blind POST. Read-only and
        provider-free: every number is arithmetic over the stored
        durations."""
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

            # Basis drift vs CURRENT enrolment — the same rows apply's gate
            # computes, through the same helper (single source of truth).
            students_now = conn.execute(
                "select id, name, home_lat, home_lng from live_students "
                "where school_id = %s",
                (school_id,),
            ).fetchall()
            basis_drift = _roster_drift(
                {str(r["id"]): r for r in students_now},
                {str(s["id"]): s for s in (plan["basis"] or {}).get("students") or []},
                address_changes=True,
            )

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
            "basis_drift": basis_drift,
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

