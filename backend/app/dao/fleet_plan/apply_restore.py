"""Apply (U6), restore (U7) and their shared materialization machinery.

One method group of ``FleetPlanDao`` — ``app.dao.fleet_plan_dao`` composes
it with the draft, review and slot-in groups and stays the import path.
Methods are verbatim moves; shared vocabulary comes from ``_shared``.
"""
import time
from typing import Any, Mapping

from psycopg.types.json import Jsonb

from app.dao.audit_dao import record_audit

from app.core.db import UNSET, get_connection
from app.core.errors import ConflictError, NotFoundError
from app.dao.fleet_dao import _depot_leg, _stop_label
from app.dao.fleet_plan._shared import (
    DIFF_NEWLY_UNPLACEABLE,
    UNASSIGNED_CONSTRAINT,
    UnacknowledgedUnplaceableError,
    _CAPTURE_LIVE_SQL,
    _META_COLUMNS,
    _effective_pattern,
    _empty_leg_doc,
    _fleet_drift_problems,
    _hhmm_to_minutes,
    _leg_label,
    _multi_trip_bus_ids,
    _roster_drift,
    _validate_leg,
    computed_stop_times,
    diff_plan_vs_live,
    logger,
    plan_gate_anchors,
)
from app.services import geo_service, plan_solver


class ApplyRestoreOps:
    """Apply pipeline, restore pipeline, and their shared writers."""

    # --- apply (U6) -----------------------------------------------------------

    def apply_plan(
        self, scope, plan_id: str, *, confirmations: list[dict],
        acknowledgments: list[dict], actor: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Apply the draft: ONE provider-free transaction, then compensable
        post-commit phases (R8/R21/R22/R23; the plan's U6 step order).

        Inside the transaction, in this order: (1) lock the plan row FOR
        UPDATE first, then the school's currently-applied plan row (the
        global lock order is PLAN ROWS BEFORE ROUTE ROWS — accept_slot_in
        locks the applied plan row before its route rows, so apply taking
        route rows first would deadlock ABBA against it), then the school's
        live routes in sorted-id order; (2) idempotency by status —
        re-applying an applied plan (a retry racing a gateway timeout past a
        late commit) answers the applied state with zero side effects;
        (3) gates against the locked snapshot — basis drift (the shared
        ``_roster_drift`` — review's ``basis_drift`` reads the same rows)
        each confirmed or 409, fleet drift 409 naming the bus (a drafted bus
        that ENTERED the multi-trip set since drafting included — its
        (bus, type) slots are no longer writable), unplaceable
        acknowledgments or 422 naming them; then the notification diff (the
        U5 shared function, pre-mutation — see inline comment); (4) capture
        the displaced live state as ONE JSONB statement and demote
        previous/applied (delete-then-flip: the partial uniques cannot
        defer); (5) reconcile live_routes in place to the document's
        (bus, type) pairs — multi-trip chains untouched, surplus deleted;
        (6) rewrite live_student_routes delete-before-insert scoped by
        STUDENT-AND-LEG — severing EVERY current student's links on the
        written legs (document students to relink, absent-from-document
        students so a post-draft manual placement cannot linger as a
        link-without-stop; chain riders' legs excluded, restore's rule);
        (7) materialize stops from the document with the
        document's computed times, gate row per the house convention, and
        ``last_recalc_degraded = TRUE`` as the durable refresh-pending
        marker; (8) re-derive live_students bus/pickup/pattern (morning-clock
        rule) — and null bus/pickup for current students the document does
        not know (pattern kept), so they surface as cleanly unassigned;
        (9) the ``plan-applied`` audit row; (10) feed rows on THIS
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

        with get_connection(scope) as conn:
            # (1) Plan row FIRST: this serializes a double-submit even for a
            # school with zero live routes (route locks alone could not).
            # U7: pinned to the ACTIVE school — a foreign plan id answers the
            # not-found contract.
            plan = conn.execute(
                "select * from live_fleet_plans where id = %s and school_id = %s "
                "for update",
                (plan_id, scope.school_id),
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
            # ...then the school's currently-applied plan row, BEFORE any
            # route-row lock — the global order 'plan rows before route
            # rows': accept_slot_in locks the applied plan row first and its
            # route rows second, so taking routes before this row would
            # deadlock ABBA against a concurrent slot-in accept. The locked
            # row is reused verbatim by the demote in step (4).
            applied_prev = conn.execute(
                "select id from live_fleet_plans "
                "where school_id = %s and status = 'applied' "
                "order by applied_at desc nulls last, created_at desc limit 1 for update",
                (school_id,),
            ).fetchone()
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
            # — each explicitly confirmed by (kind, student) or 409 listing
            # it. THE shared gate-diff: review's `basis_drift` field serves
            # these exact rows on the read path, so a caller can assemble the
            # confirmations without a blind POST.
            drift = _roster_drift(current_by_id, basis_by_id, address_changes=True)
            unconfirmed = [
                d for d in drift if (d["kind"], d["student_id"]) not in confirmed
            ]
            if unconfirmed:
                listing = "; ".join(
                    f"{d['kind']}: {d['name']} ({d['student_id']})"
                    for d in unconfirmed
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
            # A drafted bus that ENTERED the multi-trip set since drafting is
            # fleet drift too: drafting excluded chain buses, so the document
            # believes this bus's (bus, type, trip_index=1) slots are its to
            # write — falling through to the reconcile would either orphan an
            # operating chain or die on the live_routes_bus_type_key unique.
            # Blocked here, by name, in the fleet-drift vocabulary.
            now_chained = _multi_trip_bus_ids(conn, [bid for bid, _n, _l in loads])
            for bid, doc_name, _load in loads:
                if bid in now_chained:
                    problems.append(
                        f"bus {doc_name} now runs a multi-trip chain — re-draft"
                    )
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
            # ``applied_prev`` was locked in step (1), before the route rows
            # (the plan-rows-before-route-rows order).
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
            if applied_prev:
                conn.execute(
                    "update live_fleet_plans set status = 'previous', document = %s "
                    "where id = %s",
                    (Jsonb(captured), applied_prev["id"]),
                )
            else:
                # First apply: there is no applied row to demote, but the
                # pre-apply live state (possibly hand-built routes, possibly
                # empty) is still "the plan it replaced" (R21) — preserve it
                # as a fresh 'previous' row or the first apply would be the
                # one un-restorable act in the product.
                conn.execute(
                    "insert into live_fleet_plans (school_id, status, document, created_by) "
                    "values (%s, 'previous', %s, %s)",
                    (school_id, Jsonb(captured), actor.get("id")),
                )

            # (5) Reconcile live_routes IN PLACE to the document's (bus, type)
            # pairs. pairs preserves document order (bus order, then legs) —
            # the fixed order the post-commit refresh follows.
            multi_trip = _multi_trip_bus_ids(
                conn, sorted({str(r["bus_id"]) for r in routes if r["bus_id"] is not None})
            )
            # Chain riders' legs stay out of the link-severing scope below —
            # their membership belongs to a chain the plan never governed
            # (restore's chain_pairs exclusion, reused verbatim).
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
            # it lives. The scope is EVERY current student, for every leg the
            # document writes (restore's semantics): document students
            # (placed AND unplaceable) are severed to relink — a child the
            # plan narrowed or could not place must not keep riding a
            # materialized route through a stale link — and current students
            # ABSENT from the document (e.g. enrolled after drafting, then
            # manually placed on a live route this apply reuses, confirmed at
            # the gate) are severed too, or the stop-row rewrite below would
            # leave them a link-without-stop phantom membership. Chain
            # riders' legs are excluded: their membership belongs to a chain
            # the plan never governed.
            doc_student_ids = {sid for (sid, _leg) in placements} | {
                str(u["student_id"]) for u in unplaceable
            }
            link_students = sorted(doc_student_ids & current_ids)
            legs_written = sorted({leg for (_bid, leg) in pairs})
            for leg in legs_written:
                sever_ids = sorted(
                    sid for sid in current_ids
                    if sid in doc_student_ids or (sid, leg) not in chain_pairs
                )
                if sever_ids:
                    conn.execute(
                        "delete from live_student_routes "
                        "where route_type = %s and student_id = any(%s::uuid[])",
                        (leg, sever_ids),
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
                            "scheduled_time, lat, lng, is_school_gate, student_id, school_id) "
                            "values (%s, %s, %s, %s, %s, %s, false, %s, (select school_id from live_routes where id = %s))",
                            (
                                rid,
                                _stop_label(current_by_id[sid]),
                                base + i,
                                placements[(sid, leg)]["scheduled_time"],
                                stop.get("lat"),
                                stop.get("lng"),
                                sid,
                                rid,
                            ),
                        )
                gate_order = 1 if is_afternoon else len(info["stops"]) + 1
                conn.execute(
                    "insert into live_route_stops (route_id, name, stop_order, "
                    "scheduled_time, lat, lng, is_school_gate, student_id, school_id) "
                    "values (%s, %s, %s, %s, %s, %s, true, null, (select school_id from live_routes where id = %s))",
                    (rid, school["name"] or "School", gate_order, anchors[leg],
                     school["lat"], school["lng"], rid),
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
            # Current students the document does not know (enrolled after
            # drafting, confirmed at the gate): their links were severed
            # above, so their denormalized bus/pickup must clear too or they
            # would read as phantom riders of a bus they no longer link to.
            # ridership_pattern is left alone (the document has no opinion on
            # them); chain riders keep everything — their membership survived.
            for sid in sorted(current_ids - doc_student_ids):
                if any((sid, leg) in chain_pairs for leg in plan_solver.LEGS):
                    continue
                conn.execute(
                    "update live_students set bus_id = null, pickup_time = null "
                    "where id = %s",
                    (sid,),
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
            audit_id = record_audit(
                conn,
                action="plan-applied",
                actor=actor,
                scope=scope,
                resource_type="plan",
                resource_id=str(plan["id"]),
                detail=detail,
            )

            # (10) Feed rows — inserted ON THIS CONNECTION so a rollback takes
            # them too (the shared composer; restore reuses it verbatim).
            # Bodies and baseline names carry each child's OWN address label
            # (the _stop_label convention), never the document's sibling-
            # joined stop name — a collapsed stop's name would leak the other
            # families' children's names.
            stop_labels = {
                sid: _stop_label(current_by_id[sid])
                for sid in {r["student_id"] for r in diff_rows}
                if sid in current_by_id
            }
            feed_rows = self._write_plan_feed_rows(
                conn, diff_rows, audit_id, stop_labels, school_id=school_id
            )

            # Baselines upsert for every notified PLACED (student, leg) — R15
            # updates the baseline only on send (upserts, then stale deletes
            # — the shared writer; restore reuses it verbatim).
            self._write_baselines(conn, diff_rows, stop_labels)

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
            "apply", plan_id, [route_ids[key] for key in pairs], scope=scope
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

    def _write_plan_feed_rows(
        self, conn, diff_rows: list[dict], audit_id: str,
        stop_labels: Mapping[str, str] | None = None,
        school_id: str | None = None,
    ) -> list[dict]:
        """Feed rows for one apply/restore act, inserted ON THE CALLER'S
        transaction connection so a rollback takes them too
        (PushDao.insert_notification opens its own connection per row and
        cannot serve here). Composed per student, one row per (family,
        student, type) per act: a both-legs change reads as one message, and
        the 011 plan dedup arbiter backstops repeats.

        ``stop_labels`` maps student_id → that child's OWN stop label (the
        ``_stop_label`` address convention the manual-edit writer and the
        materialized stop rows already use). Parent-facing bodies name ONLY
        the recipient's child's stop — never the document's sibling-joined
        stop name, which would leak the other families' children's names to
        every household sharing a collapsed stop."""
        stop_labels = stop_labels or {}
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
                label = stop_labels.get(sid) or "their stop"
                parts = [
                    f"{_leg_label(r['leg'])}: {label} at "
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
                        school_id=school_id,
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
                        school_id=school_id,
                    )
                    if inserted:
                        feed_rows.append(inserted)
        return feed_rows

    @staticmethod
    def _write_baselines(
        conn, diff_rows: list[dict],
        stop_labels: Mapping[str, str] | None = None,
    ) -> None:
        """R15 baseline maintenance for one apply/restore act: upsert for
        every notified PLACED (student, leg) — written for every diff row,
        linked family or not, so the first apply seeds baselines for the
        whole school by design — THEN delete the stale baselines the diff
        just consumed: every notified (student, leg) the plan no longer
        serves (leg removed or now unplaceable). The removal itself notified;
        deleting the baseline makes a later re-widening a first communication
        again (it must notify even when the new time happens to match the
        stale one) and keeps a still-unplaceable child silent on the next act
        (neither membership nor baseline — the R15 still-unplaceable rule).

        ``stop_labels`` (apply/restore) overrides the stored ``stop_name``
        per child with THAT child's own address label — a baseline row is
        per-child truth and must never carry a collapsed stop's sibling-
        joined name (another family's child's name). The manual-edit caller
        passes nothing: its ``current.stop_name`` is already the live stop
        row's per-child label."""
        stop_labels = stop_labels or {}
        for row in diff_rows:
            if row["current"] is None:
                continue
            cur = row["current"]
            name = stop_labels.get(row["student_id"]) or cur["stop_name"]
            conn.execute(
                "insert into live_communicated_stops (student_id, route_type, "
                "stop_name, stop_lat, stop_lng, scheduled_time, bus_id, communicated_at, "
                "school_id) "
                "values (%s, %s, %s, %s, %s, %s, %s, now(), "
                "(select school_id from live_students where id = %s)) "
                "on conflict (student_id, route_type) do update set "
                "stop_name = excluded.stop_name, stop_lat = excluded.stop_lat, "
                "stop_lng = excluded.stop_lng, scheduled_time = excluded.scheduled_time, "
                "bus_id = excluded.bus_id, communicated_at = excluded.communicated_at, "
                "school_id = excluded.school_id",
                (row["student_id"], row["leg"], name, cur["lat"],
                 cur["lng"], cur["scheduled_time"], cur["bus_id"],
                 row["student_id"]),
            )
        for row in diff_rows:
            if row["current"] is None:
                conn.execute(
                    "delete from live_communicated_stops "
                    "where student_id = %s and route_type = %s",
                    (row["student_id"], row["leg"]),
                )

    def _refresh_routes(
        self, act: str, plan_id: str, route_ids: list[str], scope: object = UNSET
    ) -> tuple[list[str], list[str]]:
        """Post-commit geometry-only refresh, one route per transaction along
        the given fixed order (the fleet_dao.update_school precedent — shared
        by apply and restore). Returns (refreshed, degraded_routes)."""
        refreshed: list[str] = []
        degraded_routes: list[str] = []
        for rid in route_ids:
            try:
                ok = self._refresh_route_geometry(rid, scope=scope)
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
        self, scope, plan_id: str, *, confirmations: list[dict],
        acknowledgments: list[dict], actor: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Restore the preserved 'previous' plan (R21/R22/R23): an APPLY whose
        source is the as-evolved capture the displacing act preserved (the
        KTD), through the same gate pipeline re-validated against CURRENT
        enrolment and fleet, with the same diff → feed rows → baselines →
        audit → geometry-refresh → push machinery in the same order.

        Inside ONE provider-free transaction, mirroring apply's step order:
        (1) lock the plan row FOR UPDATE first, then the school's
        currently-applied plan row (plan rows before route rows — the global
        lock order shared with apply and accept_slot_in, the ABBA guard);
        (2) idempotency by status —
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

        with get_connection(scope) as conn:
            # (1) Plan row FIRST (serializes a double-submit), then the
            # school's routes in sorted-id order — apply's lock order.
            # U7: pinned to the ACTIVE school (foreign plan → 404).
            plan = conn.execute(
                "select * from live_fleet_plans where id = %s and school_id = %s "
                "for update",
                (plan_id, scope.school_id),
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
            # The school's currently-applied plan row, locked BEFORE any
            # route-row lock — the global order 'plan rows before route
            # rows' (accept_slot_in locks the applied plan row first, then
            # route rows; taking routes first here would deadlock ABBA
            # against it). Reused verbatim by the demote in step (4).
            displaced = conn.execute(
                "select id from live_fleet_plans "
                "where school_id = %s and status = 'applied' and id <> %s "
                "order by applied_at desc nulls last, created_at desc limit 1 for update",
                (school_id, plan_id),
            ).fetchone()
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
            # THE shared gate-diff (`_roster_drift`, no address gating —
            # restore puts communicated stops back verbatim): /current's
            # `previous.drift` serves these exact rows on the read path, so
            # a caller can assemble the confirmations without a blind POST.
            drift = _roster_drift(
                current_by_id, capture_students, address_changes=False
            )
            unconfirmed = [
                d for d in drift if (d["kind"], d["student_id"]) not in confirmed
            ]
            if unconfirmed:
                listing = "; ".join(
                    f"{d['kind']}: {d['name']} ({d['student_id']})"
                    for d in unconfirmed
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
            # ``displaced`` was locked in step (1), before the route rows
            # (the plan-rows-before-route-rows order).
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
                        "scheduled_time, lat, lng, is_school_gate, student_id, school_id) "
                        "values (%s, %s, %s, %s, %s, %s, %s, %s, (select school_id from live_routes where id = %s))",
                        (
                            rid, r.get("name"), r.get("stop_order"),
                            r.get("scheduled_time"), r.get("lat"), r.get("lng"),
                            bool(r.get("is_school_gate")), r.get("student_id"), rid,
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
            audit_id = record_audit(
                conn,
                action="plan-restored",
                actor=actor,
                scope=scope,
                resource_type="plan",
                resource_id=str(plan["id"]),
                detail=detail,
            )

            # (10) Feed rows + baselines through the shared writers, on THIS
            # connection — one atom with everything above. Per-child address
            # labels, apply's rule: bodies and baseline names never carry a
            # collapsed stop's sibling-joined name.
            stop_labels = {
                sid: _stop_label(current_by_id[sid])
                for sid in {r["student_id"] for r in diff_rows}
                if sid in current_by_id
            }
            feed_rows = self._write_plan_feed_rows(
                conn, diff_rows, audit_id, stop_labels, school_id=school_id
            )
            self._write_baselines(conn, diff_rows, stop_labels)

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
            "restore", plan_id, restored_route_ids, scope=scope
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
        in the solver's convention purely for display parity with a draft
        document (the diff never compares names for located stops, and the
        feed/baseline writers substitute each child's own address label).
        Ride seconds are derived per child from the
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

    def _refresh_route_geometry(self, route_id: str, scope: object = UNSET) -> bool:
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
        exactly on Google-quality success.

        ``scope`` (U7): the act's request scope, threaded explicitly — an
        unthreaded call inside a SchoolScope request raises under the strict
        seam instead of silently borrowing the context."""
        with get_connection(scope) as conn:
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

