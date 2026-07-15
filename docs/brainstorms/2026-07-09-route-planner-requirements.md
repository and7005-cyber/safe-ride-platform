---
date: 2026-07-09
topic: route-planner
origin_ideation: docs/ideation/2026-07-09-route-planner-specs-ideation.md
---

# SafeRide Route Planner — Requirements

## Summary

Seven operator-facing improvements to the Admin Fleet Map Route Planner and the Student form: bell-time-anchored route optimization, a unified place-picker (autocomplete + map pin) for every location field, an overnight bus depot, CSV import with import-time address triage and in-place map repair, removal of the redundant "Home location" field, multiple trips per bus per period, and enforcement of one morning + one afternoon route per student. The verbatim customer spec is preserved in the origin ideation doc; every open question is resolved below as a Key Decision.

The organizing lens is the **system operator** — the person who runs routes, stops, and pickup/drop-off times every day while students are added, removed, and moved between routes. Each requirement is judged by whether it reduces the operator's daily work and prevents silent wrong states.

---

## Problem Frame

The planner today optimizes forward from student pickup times: the operator cannot say "the bus must reach the gate by 07:45" and have the schedule fall out of it. Every location — a stop, a home, the depot — is entered differently (some autocomplete, some raw coordinates), so addresses and pins drift apart. CSV imports fail addresses into a transient toast the operator must chase. A bus can run only one route per period, so a small fleet can't serve staggered waves. And nothing stops a student being double-booked onto two morning routes, which silently corrupts rosters. The operator's real workflow — bell times, term-start bulk onboarding, daily moves — isn't the workflow the system is built around.

---

## Key Decisions

Each decision resolves an ambiguity in the spec from the operator's viewpoint; the *why* is the operator consequence.

- **The bell time is the anchor; pickup/drop-off times are outputs.** The operator sets one time per route — a morning gate-arrival target or an afternoon gate-departure target — and the optimizer schedules backward from it. Student pickup times become computed results, not inputs. *Why:* the operator thinks in bell times ("be at school by 07:45"), and making pickups an output means adding or moving a student never silently violates the bell — it shifts the departure earlier instead.

- **Backward scheduling is solved by forward iteration, because the routing API has no arrive-by parameter.** The system computes the drive sequence, measures the gate ETA, and shifts the departure so the ETA lands on the target (a fixed-point / block-time pattern). *Why:* this is a verified constraint of the mapping provider; the operator sees a schedule that hits the bell without needing to know how it was solved.

- **A route carries its own gate-anchor time, defaulting from the school's bell and overridable per route.** Bell times become school data (replacing the hardcoded 07:00 / 15:30 defaults); a route inherits its school's bell but the operator can override one route without touching others. *Why:* one authority for time (school default → route override), so the operator never reconciles two competing anchors, and staggered-wave bells are expressible.

- **Churn shifts the departure earlier, never the gate later.** When a student is added, removed, or moved, regeneration re-solves against the unchanged gate anchor. *Why:* the promise the operator makes to the school (arrival time) must be the fixed point; absorbing churn by moving the gate would break that promise invisibly.

- **The "previously computed" state becomes an explicit flag before any admin gate time is stored.** Today the system infers "this route was geometry-computed" from the gate row already carrying a time; once an operator can type a gate time, that inference is ambiguous. *Why (operator-invisible but load-bearing):* without this, a route an operator just gave a gate time to could be mistaken for a computed route and silently mispair preserved times — a wrong schedule the operator would trust. This is a prerequisite, not a feature.

- **One place-picker primitive replaces every ad-hoc location field.** A single control resolves any location to `{address, coordinates, provenance}` via autocomplete OR map pin OR reverse-geocode, and is reused for planner Add Stop, the student home, the depot, and CSV repair. *Why:* the operator gets identical behavior everywhere, and address-vs-pin drift (the current bug class) ends because the two always move together.

- **"Home location" the field is removed; the home coordinates are kept.** The standalone coordinate-display field beside the map is redundant with the address field — but `home_lat`/`home_lng` are load-bearing (they are the stop-grouping identity that clusters siblings and drives optimization). *Why:* the operator loses a confusing duplicate input, not any capability — map-picking stays, folded into the one address control, and the data the optimizer needs is untouched.

- **Provenance is stamped so a re-geocode never clobbers a deliberate pin.** Each resolved place records how its coordinates were set (typed-and-geocoded, map-picked, or imported). A background re-geocode may refine a geocoded/imported coordinate but must not overwrite a map-picked one. *Why:* the operator who carefully dropped a pin on the actual bus stop must be able to trust it survives the next address edit.

- **Overnight parking is a bus attribute that enters only as a geometry leg — never a stop.** The depot is a location on the bus; it is prepended as the origin of that bus's first morning trip and appended as the destination of its last afternoon trip, as a drive leg with no boarding. *Why:* modeling the depot as a stop row was evaluated and rejected — it would break gate-order numbering, the ETA-to-stop pairing, order preservation, and run-completion detection. As a leg it gives correct depot-to-first-stop and last-stop-to-depot drive time with zero risk to those mechanics.

- **CSV addresses are triaged at import into resolved / ambiguous / failed, surfaced as a persistent repair table.** Every row is geocoded on upload; unresolved rows land in a repair list (not a disappearing toast), each fixable in place with the map-picker, each showing its provider confidence. Import completes only when the operator has cleared or accepted the list. *Why:* the operator's real failure mode is a bad address discovered days later on the road; catching it at import with a one-click map fix is the whole value.

- **Student CSV import finishes onboarding by honoring the route column.** The student bulk upload already accepts a route name column that is currently ignored; it will assign students to routes through the same allocation path as manual assignment, with route regeneration batched per route across the import. *Why:* term-start onboarding is the operator's heaviest day; finishing allocation in the same upload — without firing one route recomputation per student row — turns a multi-day chore into one pass.

- **A bus may run multiple trips per period, modeled as an ordered chain with a turnaround feasibility check.** The uniqueness that today caps a bus to one route per period is relaxed to allow ordered trips; each trip carries its own gate anchor, and the next trip's departure must be feasible after the previous trip's gate arrival plus a turnaround buffer. *Why:* a small fleet serving staggered bells needs one bus to run consecutive waves — but only if the operator is warned when a chain is physically impossible, so infeasible chains surface (through the existing durable warning channel), never silently.

- **The one-route-per-type rule ships before multi-trip, and assignment is a move, not an error.** Enforcing at most one morning and one afternoon route per student lands first; the assignment UX displaces (radio-button semantics — assigning to a new morning route moves the student off the old one) rather than rejecting. *Why:* the daily operator action is *moving* a child between routes, so an error dialog is friction where a move is intended; and shipping the cap first bounds the blast radius before multi-trip makes same-bus double-pickups possible.

- **The allocation constraint is enforced without breaking the common move.** The existing assignment sync inserts the new route link before deleting the old one, so a naive uniqueness constraint would reject the very move it should allow; the enforcement uses delete-before-insert ordering or a deferrable constraint, plus a one-time cleanup of any pre-existing double-allocations. *Why:* the operator must be able to move a student between same-period routes on the busiest workflow without hitting a spurious conflict.

---

## Requirements

### Prerequisite — scheduling foundation

- R1. The "previously computed" property of a route is represented explicitly (its own persisted state), not inferred from whether the gate row already carries a time. All existing regeneration/preservation behavior keys off the explicit state.
- R2. Route auto-recomputation persists the route's total drive duration (today it is stored only for planner-saved custom routes), so downstream feasibility checks read it rather than recompute it.

### Bell-time-anchored optimization (spec 1)

- R3. A route has a gate-anchor time: a morning gate-arrival target or an afternoon gate-departure target. It defaults from the route's school bell time and is overridable per route in the planner.
- R4. School bell times are stored data (morning arrival, afternoon departure) and replace the hardcoded time defaults; a route with no override uses its school's bell.
- R5. The planner optimizes the route so the bus reaches (morning) or leaves (afternoon) the gate at the anchor time, computing departure and every stop's scheduled time backward from it via forward iteration against the routing provider.
- R6. Adding, removing, or moving a student re-solves against the unchanged gate anchor; the gate time never drifts to absorb churn — the departure and stop times move instead.
- R7. Each stop's computed time honors the existing clock-class discipline (morning pickups, afternoon drop-offs) and ordering-authority rules (custom > manual > auto); manual/degraded routes keep their preserved order and times.

### Unified place-picking (specs 2 and 5)

- R8. Every location field — planner Add Stop, student home, depot, CSV repair — resolves a place through one primitive supporting address autocomplete, map-pin placement, and reverse-geocode, always yielding an address, coordinates, and a provenance stamp.
- R9. Planner Add Stop lets the operator find the exact stop by typing an address (autocomplete) or dropping/dragging a pin on the map, interchangeably.
- R10. The standalone "Home location" coordinate field is removed from the Add/Edit Student form; the home is a single address control that still supports map-picking. Home coordinates remain in the data model and continue to drive stop grouping and optimization.
- R11. Coordinate provenance is recorded (typed-and-geocoded / map-picked / imported). A later re-geocode may refine geocoded or imported coordinates but must not overwrite a map-picked coordinate.

### Overnight bus depot (spec 3)

- R12. A bus has an optional overnight parking location, set via the place-picker.
- R13. The depot is prepended as the origin drive leg of the bus's first morning trip and appended as the destination drive leg of its last afternoon trip; it is never a boardable stop and never appears on any roster or parent view.
- R14. Depot legs contribute their drive time to the schedule (depot→first stop in the morning, last stop→depot in the afternoon) without altering stop numbering, the stop-to-ETA pairing, order preservation, or run-completion detection.

### CSV import & address repair (spec 4)

- R15. On CSV upload (planner stops and student bulk upload), every row is geocoded at import time and triaged into resolved, ambiguous, or failed.
- R16. Unresolved rows are surfaced as a persistent repair table (not a transient notification), each row editable in place with the place-picker and showing its geocode confidence/provider.
- R17. The import is not committed until the operator has repaired or explicitly accepted each unresolved row; accepted-as-is rows are flagged so they can be found later.
- R18. Student CSV import assigns students to routes via the existing route-name column, through the same allocation path (and constraint, R21) as manual assignment, with route regeneration batched once per affected route rather than once per row.

### Multiple trips per bus per period (spec 6)

- R19. A bus may run more than one route in the morning and more than one in the afternoon, modeled as an ordered chain of trips per bus per period; each trip carries its own gate anchor.
- R20. A trip chain is feasibility-checked: a later trip's backward-solved departure must be no earlier than the previous trip's gate time plus a turnaround buffer. Infeasible chains are surfaced to the operator through the existing durable warning mechanism, never silently accepted.

### Student allocation (spec 7)

- R21. A student is allocated to at most one morning route and at most one afternoon route, enforced server-side and mirrored in the UI. This ships before R19–R20.
- R22. Assigning a student to a route of a period they already occupy moves them (displacement/radio semantics), not rejects — the prior same-period route is vacated in the same action.
- R23. Enforcement does not break the legitimate same-period move (the assignment sync must not reject a move it should allow), and a one-time cleanup resolves any pre-existing double-allocations deterministically before the constraint is enforced.

---

## Acceptance Examples

- AE1. **Covers R3, R5, R6.** An operator sets a morning route's gate arrival to 07:45. The planner produces stop times ending in a 07:45 gate arrival. Adding a new student to that route re-solves so the departure is earlier; the gate arrival stays 07:45.
- AE2. **Covers R9.** In Add Stop, the operator types a partial address and picks a suggestion; the stop lands with a pin. On another stop they drop a pin on the map; the address reverse-geocodes into the same field. Both produce a stop with address + coordinates.
- AE3. **Covers R10, R11.** The Add Student form shows one "Home address" control with map-picking and no separate coordinate field. The operator drops a pin on the exact house; editing the address text later does not move that pin.
- AE4. **Covers R13, R14.** A bus with a depot runs two morning trips. Its first morning trip begins with a depot→first-stop leg; its last afternoon trip ends with a last-stop→depot leg. Stop numbering, roster, and parent views are unchanged; the depot appears on neither roster.
- AE5. **Covers R15, R16, R17.** A CSV with three unresolvable addresses uploads; the three appear in a repair table, not a toast. The operator fixes two with the map-picker and accepts one as-is; the import commits with the accepted row flagged.
- AE6. **Covers R19, R20.** A bus is assigned two morning trips with 07:30 and 08:15 gate anchors; the chain is accepted. When the second is changed to 07:40, the turnaround is infeasible and the operator sees a warning.
- AE7. **Covers R21, R22.** A student on Morning Route A is assigned to Morning Route B; they are moved to B and removed from A in one action, with no error. Attempting to keep them on two morning routes is not representable.

---

## Scope Boundaries

- **Depot as a stop row — rejected by design.** Modeling overnight parking as a boardable stop breaks gate-order arithmetic, the ETA-to-stop pairing, order preservation, and run-completion detection. Recorded here so the "obvious" shape is not re-attempted; the depot is a geometry leg (R13–R14).
- **No joint bell-time optimization.** The system schedules routes around given bell times; it does not optimize the bell times themselves (a known higher-order win in the literature) — out of scope for this operator-facing pass.
- **No nightly automatic rebuild.** Re-solving is triggered by operator/roster changes, not a scheduler; a nightly self-rebuild would require new infrastructure and recurring cost.
- **Stops remain student-home-derived, not first-class shared entities.** Corner-stops / shared named stops with walk-to policies are not introduced; coordinate coherence is enforced by provenance guards (R11), not a new stop entity.
- **Turnaround buffer is a single configurable value**, not a per-route or traffic-aware model, for this pass.

---

## Dependencies / Assumptions

- The mapping provider (Google Routes) has no arrive-by parameter; backward scheduling is solved by forward iteration (verified). Geometry uses the already-wired maps key; no new infrastructure or env is required.
- Home coordinates are consumed by stop grouping (`_group_key`, rounded to 6 decimals) and route optimization; removing the coordinate field is a UI change only, not a data-model removal (verified against the student form and fleet DAO).
- The student bulk-upload route-name column exists in the payload but is currently ignored (verified); wiring it is additive.
- Standing invariants are honored: ordering authority custom > manual > auto, scheduled_time ownership by mode, degraded-signal persistence (silent degradation is banned — warnings are persisted, not just toasted), run-snapshot immutability, and morning-clock `pickup_time` discipline.

---

## Outstanding Questions

**Deferred to Planning**

- Turnaround buffer default value and whether it is global vs per-bus.
- How school bell times are captured (school entity field vs planner input) and their timezone handling.
- Fixed-point iteration convergence bounds (max iterations, acceptable gate-time tolerance) and the fallback when it does not converge within budget.
- Exact provenance enumeration and migration of existing coordinates to a default provenance.
- Whether the per-bus rotation-strip visualization (all trips + layovers on one timeline) ships in this pass or is a follow-up surface; the underlying trip-chain data must support it regardless.
- Ambiguous-tier CSV rows: auto-accept the top geocode candidate vs force operator review.

---

## Sources & Research

- Origin ideation (7 dual-critic-verified survivors, build order, rejected designs): docs/ideation/2026-07-09-route-planner-specs-ideation.md.
- Spec-5 precondition verified in code: `frontend/src/features/admin/StudentsPage.tsx:502-521` ("Home address" autocomplete + standalone "Home location" MapPicker), home-coordinate consumers `backend/app/dao/fleet_dao.py:29-69` (`_group_key`, optimization), `backend/app/dao/student_live_dao.py`.
- Scheduling / anchor / preservation machinery and clock-class discipline: `backend/app/dao/fleet_dao.py` (`regenerate_route_stops`, anchor derivation, previously-computed marker ~366-370), `backend/app/services/geo_service.py` (next_departure, route_geometry, optimize), `backend/app/api/fleet.py` (route-options), docs/solutions/2026-07-07-pickup-time-is-morning-clock.md.
- Multi-trip / allocation constraints and the insert-before-delete move trap: `backend/db/migrations/006_absence_and_run_uniqueness.sql`, `007_spec_refinement.sql`, `backend/app/dao/student_live_dao.py` (`_sync_routes`), `backend/app/dao/run_dao.py` (start-run gating, `is_last`).
- External patterns (bell-anchored routing, tiered/multi-wave buses, courier import-repair): docs/ideation research section; Transfinder/Versatrans/BusPlanner, GTFS blocks/deadheading, Circuit/Routific import review.
- Prior requirements/plan patterns this doc mirrors: docs/brainstorms/2026-07-06-ops-refinement-requirements.md, docs/plans/2026-07-06-001-feat-ops-refinement-plan.md.
