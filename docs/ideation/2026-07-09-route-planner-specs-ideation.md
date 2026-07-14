---
date: 2026-07-09
topic: route-planner-specs
focus: 7-item customer spec — bell-time anchoring, stop picking, depot, CSV repair, home-field cleanup, multi-trip buses, one-per-type allocation
mode: repo-grounded, go-deep (6 frames, ceiling tier; 46 raw candidates; dual-critic verification)
---

# Route Planner Specs — Ideation

## Grounding Context

- Codebase: planner lives at `backend/app/api/fleet.py` (route-options) + `frontend/src/features/admin/FleetMapPage.tsx`; regeneration/anchor semantics in `backend/app/dao/fleet_dao.py`; standing invariants from docs/plans/2026-07-06-001-feat-ops-refinement-plan.md (ordering authority custom>manual>auto, scheduled_time ownership, degraded-signal persistence, run-snapshot immutability) and docs/solutions/2026-07-07-pickup-time-is-morning-clock.md (clock-class discipline).
- External: school-native routers (Transfinder/Versatrans/BusPlanner) treat bell time as the hard anchor with backward-generated pickup windows; "tiered routing" (multi-wave buses) hinges on turnaround buffers and joint solving; Bertsimas et al. (PNAS, Boston Public Schools) saved ~$5M/yr treating bell times as variables; courier importers (Circuit/Routific) use import-time geocode triage with pin-repair queues; Google Routes API has **no arrive-by parameter** — arrive-by must be iterated.
- Evidence dossiers: /tmp/compound-engineering/ce-ideate/ideate-223132/evidence-*.md (5 axes).
- Verification: fresh-context basis verifier (41 sound / 5 weak / 0 refuted) + ceiling novelty-feasibility critic (top-10 + 10 interaction notes), both cross-checked against the repo.

## Topic Axes

time-anchoring-and-scheduling (12 ideas) · stop-and-address-management (12) · student-allocation-and-churn (9) · multi-trip-bus-model (7) · depot-and-route-endpoints (6)

## Ranked Ideas

### 1. Bell-owned scheduling: per-route gate anchor, backward solve, one anchor hierarchy
- **Axis:** time-anchoring-and-scheduling · **Confidence:** High · **Complexity:** M
- **Merges:** C1, C9, C10, C17, C25, C32, C40 (+C2 as a later stage)
- **Summary:** Store a target school-gate time on the route (`gate_anchor`), defaulted from school bell data (delete the hardcoded 07:00/15:30 constants; hierarchy = school bell default, route-level override — one authority, never two). The solver runs *backward from the bell*: departure and every pickup time become computed outputs, not inputs. Because Google Routes has no arrive-by parameter (verified), arrive-by is solved by fixed-point iteration (forward solve → measure gate ETA → shift departure; airline block-time pattern). Student churn shifts departure earlier — never the gate later. Later stage (only after this model change): a promised-vs-computed drift ledger with one-click adopt (C2) — safe only once `pickup_time` is demoted from anchor input to student attribute.
- **Basis:** direct — `fleet_dao.py:149-156` forward-solve anchor derivation; `fleet.py:342` duplicated defaults; external — Transfinder/Versatrans bell-anchor pattern.
- **Why it matters:** This is spec 1 verbatim, and it is the keystone every other time behavior hangs off (multi-trip wave anchors, depot leg timing, drift handling).

### 2. Promote the "previously computed" marker to an explicit column (prerequisite)
- **Axis:** time-anchoring-and-scheduling · **Confidence:** High · **Complexity:** S
- **Merges:** C26
- **Summary:** U6's preservation logic infers "this route was geometry-computed" from the gate row carrying a `scheduled_time` (`fleet_dao.py:366-370`). The moment spec 1 lets admins set gate times, that inference breaks — never-computed routes would silently take the preservation path with mispaired times. Promote the marker to an explicit column before any gate-anchor work writes times to gate rows.
- **Basis:** direct — `fleet_dao.py:366-370` marker mechanics (verified exact).
- **Why it matters:** Hard prerequisite for idea 1; trivial to do now, expensive to untangle later.

### 3. One-per-type allocation as displacement, with a swap-safe constraint — sequenced BEFORE multi-trip
- **Axis:** student-allocation-and-churn · **Confidence:** High · **Complexity:** M
- **Merges:** C5, C14, C23, C31, C37, C44
- **Summary:** Enforce at most one morning + one afternoon route per student with the repo's proven dual-layer pattern (friendly 409 + DB backstop) — but the UX is *displacement, not error*: assigning a student to a new morning route moves them (radio-button semantics), because the operator's daily reality is moves. Critical trap (verified): `_sync_routes` inserts new links before deleting removed ones, so a naive unique constraint 409s the most common operator action; use delete-first ordering or a deferrable constraint on a denormalized route_type (PG partial unique indexes cannot be deferrable). Includes a deterministic dedup migration for existing violations, then deletes the now-dead silent tie-breaks (morning-preferring `_derive_student_bus` etc.). **Must land before multi-trip:** today's (bus,type) uniqueness caps double-allocation blast radius; relaxing it first would enable same-bus double pickups.
- **Basis:** direct — `student_live_dao.py:168-179` insert-before-delete (verified); migration 006/007 dual-layer precedents.
- **Why it matters:** Spec 7 verbatim, and the sequencing insight (before spec 6) prevents a child-safety-adjacent regression class.

### 4. Multi-trip as trip chains: trip_index + turnaround feasibility + per-wave anchors + per-bus rotation strip
- **Axis:** multi-trip-bus-model · **Confidence:** High · **Complexity:** L
- **Merges:** C3, C15, C18, C21, C29, C35, C36, C42
- **Summary:** The run layer already tolerates multiple runs per bus per day (verified — the constraint lives in the route unique index + completed-today gating). Relax `unique(bus_id, type)` to `unique(bus_id, type, trip_index)` and model a bus's day as an ordered *chain* of trips. The real engineering is the **turnaround feasibility gate**: trip N+1's backward-solved departure must be ≥ trip N's gate arrival + a turnaround buffer — and since `total_duration_s` is NOT persisted for auto routes (verified), idea 1's geometry writes must start persisting durations to make this check cheap. Each wave carries its own gate anchor (staggered bells — the coupling variable between specs 1 and 6). Infeasible chains warn through the existing durable-badge channel, never silently. Operator surface: a per-bus **rotation strip** (airline tail Gantt) showing the day's chained trips with layovers.
- **Basis:** direct — `run_dao.py:317-336` gating, migrations 006/007 (verified); external — tiered routing (BusBoss/RouteBot), GTFS blocks.
- **Why it matters:** Spec 6 verbatim; reverses a prior deliberate constraint, so it must be modeled (chains + feasibility), not just un-constrained.

### 5. Depot on the bus, entering geometry only — never a stop row
- **Axis:** depot-and-route-endpoints · **Confidence:** High · **Complexity:** M
- **Merges:** C4, C11, C22, C30, C34 (C43 explicitly rejected)
- **Summary:** Overnight parking is a *bus attribute* (depot lat/lng/address on `live_buses`, set via the same place-picker). It enters the system exclusively as geometry legs: prepended origin on the bus's FIRST morning trip, appended destination on its LAST afternoon trip (GTFS deadhead framing). It is never a `route_stops` row — a depot stop row would break gate-order arithmetic, the ETA-sequence zip, claim-once preservation, the computed marker, and `is_last` run completion (all verified). "First/last trip" is a chain property → build after ideas 1 and 4.
- **Basis:** direct — `run_dao.py:462-471`, `fleet_dao.py:194-201` order arithmetic (verified); external — GTFS deadheading.
- **Why it matters:** Spec 3 verbatim, delivered without touching five invariants that a naive "depot stop" would break.

### 6. One PlacePicker primitive; remove Home Location by making address⇄coordinates one field with provenance
- **Axis:** stop-and-address-management · **Confidence:** High · **Complexity:** M
- **Merges:** C8, C13, C27, C39, C45 (C19 deferred — see rejections)
- **Summary:** Build a single resolved-place primitive — autocomplete + map pin + reverse geocode wrapped as one field whose value is always `{address, lat, lng, provenance}` — and use it for planner Add Stop (spec 2), the student form (spec 5), CSV repair (spec 4), and the depot (spec 3). Spec 5's "remove Home Location" then becomes safe: the visible coordinate field disappears, the *invariant* (address and coordinates move together, provenance stamped: typed/picked/geocoded/imported) replaces it. Coordinates are stop identity (sibling grouping keys off rounded coords — verified), so a coherence guard prevents a silent re-geocode from clobbering a deliberately picked pin.
- **Basis:** direct — `MapPicker.tsx:26-33`, `StudentsPage.tsx:502-523` dual-writer split, `fleet_dao.py:29-40` `_group_key` (all verified).
- **Why it matters:** Specs 2+5 verbatim, spec 4's fix surface, and one primitive ends the address/pin drift bug class everywhere at once.

### 7. Import-time geocode triage with a persistent repair queue; wire the dead route_name column with batched regeneration
- **Axis:** stop-and-address-management / student-allocation-and-churn · **Confidence:** High · **Complexity:** M
- **Merges:** C6, C7, C12, C16, C20, C24, C38, C46 (C28 corrected)
- **Summary:** Geocode every CSV row at import (planner stops CSV and student bulk upload), triage into three tiers — resolved / ambiguous / failed — and surface failures as a *persistent repair table* (not a toast) whose fix affordance is the PlacePicker (idea 6). Surface the already-computed-but-discarded provider tag (Google vs fallback — verified discarded today) as row-level confidence. Resurrect the student CSV's dead `route_name` column (accepted, never applied — verified) so term-start onboarding lands allocations through the same `_sync_routes` choke point idea 3 guards — with per-route regeneration **deduped/batched across rows** (per-row regeneration on a 200-row import = O(rows) Google calls in one Lambda invocation — timeout risk, verified concern).
- **Basis:** direct — `fleet.py:360-362` unresolved-toast path, `students_live.py:86` dead field (verified); external — Circuit/Routific import-review pattern.
- **Why it matters:** Spec 4 verbatim plus the operator's actual bulk workflow (term start) finishing onboarding in one pass.

## Rejection Summary

46 raw → 7 survivors (merging 38 candidates into clusters); notable rejections:

- **C43 (depot as a second virtual gate stop row)** — rejected by design: verified to break gate-order arithmetic, the ETA zip, claim-once preservation, and `is_last` run completion. Recorded explicitly so the "obvious" shape is never re-attempted.
- **C41 (nightly self-rebuild)** — rejected: requires a scheduler (collides with the no-new-infra KTD) + nightly Google cost; the underlying stale-times concern is handled inside idea 1's drift ledger without assuming a scheduler.
- **C19 (stops as first-class shared entities)** — deferred, scope overrun: re-keys stop identity that preservation/aliasing/snapshots assume; the entity-vs-guard decision is made once, here: guards now (idea 6), entity only if corner-stop policies ever become real.
- **C2 (drift ledger with one-click adopt)** — deferred into idea 1 stage 2: verified unsafe under the current model (writes ETAs into the morning anchor — the banned round-trip) until `gate_anchor` demotes `pickup_time`.
- **C5/C23 citation drift** (parent_live_dao line numbers) and **C18/C42 overclaim** (`total_duration_s` "already persisted" — false for auto routes) noted and corrected in the survivor text.

Axis coverage: all five axes have survivors. Interaction notes (build order) carried into the brainstorm: idea 2 → idea 1 → idea 3 → idea 4 → idea 5; idea 6 underpins 1/4/5/7.
