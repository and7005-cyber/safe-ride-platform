"""Slot-in proposals: list/accept/dismiss + materializer (U12).

One method group of ``FleetPlanDao`` — ``app.dao.fleet_plan_dao`` composes
it with the draft, review and apply/restore groups and stays the import
path. Methods are verbatim moves; shared vocabulary comes from ``_shared``.
"""
from typing import Any

from psycopg.types.json import Jsonb

from app.core.db import get_connection
from app.core.errors import ConflictError, NotFoundError
from app.dao.audit_dao import record_audit
from app.dao.fleet_dao import (
    _ROUTE_GEOMETRY_INPUTS_SQL,
    _depot_leg,
    _stop_label,
    resolve_gate_anchor,
)
from app.dao.fleet_plan._shared import (
    CONSTRAINT_CAPACITY,
    CONSTRAINT_STOP_CAP,
    PlanConstraintError,
    _hhmm_to_minutes,
    _shift_hhmm,
    logger,
)
from app.dao.student_live_dao import _derive_student_bus
from app.services import geo_service, plan_solver, slot_in_service


class SlotInOps:
    """Slot-in proposal reads and decisions against the applied plan row."""

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

    def list_slot_ins(self, scope) -> dict[str, Any]:
        """The ACTIVE school's pending slot-in records, aged-out marked (never
        auto-removed — the student stays visibly unassigned, R13/aging).

        Read-only display hygiene: a record whose student departed, or whose
        every leg is now linked (a manual placement raced the record), is
        filtered from the response without a write — accept re-validates
        against live truth anyway.
        """
        school_id = scope.school_id
        with get_connection(scope) as conn:
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

    def accept_slot_in(self, scope, proposal_id: str, actor: dict) -> dict[str, Any]:
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
        communication; co-riders on the >= 5-minute rule). U7 adds the
        slot-in-accepted audit row in the same transaction.
        """
        school_id = scope.school_id
        with get_connection(scope) as conn:
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
            record_audit(
                conn, action="slot-in-accepted", actor=actor, scope=scope,
                resource_type="plan", resource_id=str(plan["id"]),
                detail={"proposal_id": str(proposal_id), "student_id": sid,
                        "legs": leg_names},
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
        rides, short = geo_service.cumulative_ride_seconds(
            durations, len(seq_groups),
            is_afternoon=is_afternoon, has_depot=depot is not None,
        )
        sign = 1 if is_afternoon else -1
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
        degraded = bool(geom["degraded"]) or short
        conn.execute(
            "update live_routes set polyline = %s, total_distance_m = %s, "
            "total_duration_s = %s, last_recalc_degraded = %s where id = %s",
            (geom["polyline"], geom["total_distance_m"],
             geom["total_duration_s"], degraded, rid),
        )
        return {"position": pos, "join": False,
                "scheduled_time": new_time, "degraded": degraded}

    def dismiss_slot_in(self, scope, proposal_id: str, actor: dict) -> dict[str, Any]:
        """Dismiss one record (proposal or unplaceable notice): removed from
        the store, no live-table side effects — the student stays visibly
        unassigned (their display_status already reads 'unassigned' with no
        route links)."""
        with get_connection(scope) as conn:
            plan = self._applied_plan_slot_ins(conn, scope.school_id, lock=True)
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
            record_audit(
                conn, action="slot-in-dismissed", actor=actor, scope=scope,
                resource_type="plan", resource_id=str(plan["id"]),
                detail={"proposal_id": str(proposal_id),
                        "student_id": str(record.get("student_id")),
                        "kind": record.get("kind")},
            )
        return {
            "ok": True,
            "removed": {
                "id": str(record.get("id")),
                "student_id": str(record.get("student_id")),
                "kind": record.get("kind"),
            },
        }
