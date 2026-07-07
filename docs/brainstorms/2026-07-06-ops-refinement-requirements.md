---
date: 2026-07-06
topic: ops-refinement
---

# SafeRide Ops Refinement — Requirements

## Summary

Seven customer-requested operational refinements: real-time day-scoped student status on the admin Students page (blank until a route is assigned), a disjoint Recent/History split of the parent feed, recalculated stop order and pickup times when route rosters change, admin manual stop reordering, a parent Cancel-a-Ride flow for same-day ride cancellations, and an admin time-sensitive broadcast to all parents of a route.

The verbatim customer spec is docs/specs/2026-07-06-ops-refinement.md.

---

## Problem Frame

The school's office staff cannot see where a child actually is from the Students page — it shows the raw stored status, which goes stale overnight (a child sits "dropped-off" until the next run writes over it) and shows "at-school" for children who were never assigned a bus. Parents see yesterday's events mixed into today's feed. Route stop order is driven by hand-typed per-student pickup times, so adding a student to a route puts them last regardless of geography, and the office cannot reorder stops when roads close. When a parent needs to cancel a pickup, they phone the office; nothing reaches the driver's list. And the office has no way to tell all parents on a route that the bus is delayed.

---

## Key Decisions

- **Real-time status is derived at read time; "reset every day" is day-scoping, not a stored reset.** No scheduler exists on the Lambda deployment, and the prior branch's rule stands: display status is computed at read time, never stored (`backend/app/dao/parent_live_dao.py:22-83`). The admin Students list reuses the same Nairobi-dated derivation the parent portal already trusts — absent-today overrides, stale on-bus and stale dropped-off resolve to "at home". One derivation, two surfaces.
- **"No default status" is display gating, not a schema change.** `live_students.status` keeps its NOT NULL, CHECK, and `'at-school'` default; all operational writers (driver taps, sweeps, absence flows) stay untouched. A student with zero `route_ids` displays "Unassigned" instead of any status — the stored value is operational plumbing the office never sees until the child is routed.
- **Recent and History become disjoint windows over an undeleted audit trail.** Both tabs exist today but overlap (History's 168h window contains Recent's 24h). "Moved to History after 24H" means Recent shows strictly the last 24h and History shows 24h–7d. No rows are ever deleted — the feed remains the audit trail (existing invariant, `backend/app/dao/push_dao.py:221-226`).
- **A per-route ordering mode resolves auto-recalculation vs manual order.** `auto` (default): every roster change recalculates order and times. `manual`: the admin's dragged order persists, later assignments insert without re-sorting, and an explicit "Recalculate" action returns the route to auto and recomputes immediately. Mirrors the proven `custom_stops` flag pattern — a route-level flag that exempts a route from regeneration (`backend/app/dao/fleet_dao.py:15-112`).
- **Recalculated order and times come from route geometry, with the current behavior as fallback — and the fallback is observable.** Today "recalculation" is a sort by hand-typed `pickup_time` then name; the planner already computes real drive-time ETAs but discards them (`backend/app/api/fleet.py:261`). Auto-mode recalculation orders stops by drive sequence and writes computed times to each stop when coordinates and the Maps key are available (key wired on live since `b68c877`). When they aren't, order falls back to the pickup-time sort and the admin sees a "times not recalculated" signal — geo_service degrading silently is how the last geo feature shipped dead.
- **Cancel-a-Ride is a scoped absence, not a new entity.** Every roster consumer already keys off `live_student_absences`: run-start filtering, afternoon auto-board, driver roster flags, `run_absences` snapshots, boarded recounts. Cancellation extends absences with a scope — `morning`, `afternoon`, or `day` — and existing rows backfill to `day`. The driver's list updates through the machinery it already polls.
- **Ride granularity is morning / afternoon / rest-of-day.** The spec says "Cancel-a-Ride", singular. The common real case — "I'm picking her up after school" — cancels only the afternoon ride after a completed morning ride, which a whole-day flag cannot express without lying about the morning.
- **The admin is notified through a student-stamped incident, exactly like the driver absent flow.** Student-stamped incidents land on the admin Alerts page with the unread badge and acknowledge flow, and are excluded from every parent feed (`backend/app/dao/parent_live_dao.py:187`) — no other parent learns a named child's cancellation. No new admin surface.
- **Cancellation guards mirror the driver absent rules.** Same-day only; rejected when the target ride's run is completed or the child is already on the bus; withdrawable by the parent until the affected run starts (absence clearing during active runs stays blocked — existing rule).
- **The broadcast is a new notification type on existing rails, with recipients resolved from route assignments.** Recipients come from `live_student_routes` joined to linked parent accounts — never from derived `bus_id`, which drifts (prior branch lesson: rosters are assignment-scoped, never bus-scoped). Delivery reuses the in-feed + FCM/web-push path; one notification per parent regardless of how many of their children ride the route. No new infrastructure or env vars.

---

## Requirements

**Admin — real-time student status**

- R1. The Students page shows each student's real-time status next to their name: at school, on bus, dropped off, at home, absent today — or Unassigned.
- R2. Status derivation reuses the parent-portal rules, day-scoped to Africa/Nairobi: a today-absence overrides everything; stale on-bus and stale dropped-off (no qualifying run today) display as at home; otherwise the operational status shows.
- R3. A student with no route assignments displays Unassigned and no status, including immediately after creation; the status appears once the student is assigned to a route.
- R4. The status column, its filter, and status counts operate on the derived values; the existing separate absence badge remains.

**Parent — notification lifecycle**

- R5. Recent shows only notifications from the last 24 hours; History shows only notifications older than 24 hours and up to 7 days. The two sets are disjoint.
- R6. No notification rows are deleted; the 7-day bound is a display window over the retained audit trail.
- R7. The split applies to both feeds the page renders (notifications and alerts).

**Routes — stop order and pickup times**

- R8. Each route has an ordering mode: auto (default) or manual.
- R9. In auto mode, any roster change (student assigned or removed) recalculates the route's stop order and per-stop scheduled times.
- R10. Recalculation orders stops as an efficient drive sequence and derives each stop's scheduled time from route geometry when student coordinates and the Maps key allow; otherwise it falls back to the existing pickup-time-then-name order, leaves times as entered, and shows the admin a visible "order/times not recalculated" signal.
- R11. On the Routes page the admin can reorder a route's stops; doing so switches the route to manual mode and persists the order. Later assignments add the new student's stop without re-sorting the rest. An explicit Recalculate action returns the route to auto mode and recomputes immediately.
- R12. Reordering and recalculation never alter a run already in progress; changes take effect from the next run start.
- R13. The existing per-student pickup-time edit keeps working: in auto mode it triggers recalculation (current behavior); in manual mode it updates that stop's time without re-sorting.

**Parent — Cancel-a-Ride**

- R14. A parent can submit a same-day ride cancellation for a linked child, scoped to the morning ride, the afternoon ride, or the rest of the day.
- R15. A cancellation is a scoped absence: run-start roster filtering, afternoon auto-board, driver roster flags, run-absence snapshots, and boarded recounts all respect the scope. Existing absences behave as day-scoped.
- R16. Guards: cancellations apply to today only; a cancellation is rejected when the target ride's run is already completed, or is in progress with the child on the bus. A not-yet-boarded child on an in-progress run can still be cancelled (matching the driver absent flow).
- R17. On submission, the admin receives a student-stamped alert on the Alerts page (unread badge, acknowledge flow); it is not visible in any parent's feed. The child's linked parents receive a confirmation notification.
- R18. The submitting household can withdraw a cancellation until the affected run starts; after that, only the existing school/driver flows can change it.
- R19. The driver's list reflects cancellations: before a run starts the child is excluded from the run's stops; during a run the child's absent flag updates on the boarding list.

**Admin — route broadcast**

- R20. From a route, the admin can send a free-text time-sensitive message to the parents of every student assigned to that route.
- R21. Delivery is one in-feed notification per parent (deduplicated across siblings on the route), pushed via FCM/web-push where configured.
- R22. The message renders in the parent feed with its own label and styling, carries no run period, remains visible under any period filter, and obeys the Recent/History windows.
- R23. The message body is length-capped; sending is admin-only; each send creates a new notification (no run-scoped dedup).

---

## Acceptance Examples

- AE1. **Covers R1–R3.**
  - **Given** a child swept to dropped-off yesterday afternoon, **when** the admin opens Students this morning before any run, **then** the child shows "at home" — not "dropped-off".
  - **Given** a newly created student with no routes, **then** they show "Unassigned"; **when** assigned to a morning route, **then** live status appears.
  - **Given** a child marked absent today whose stored status is on-bus, **then** the list shows "absent today".
- AE2. **Covers R5.** A notification created 23 hours ago appears in Recent and not History; at 25 hours it appears in History and not Recent; at 8 days it appears in neither (but the row still exists).
- AE3. **Covers R9, R11.** Assigning a student to an auto route reorders stops and refreshes times; assigning to a manual route appends their stop and leaves the admin's order untouched; pressing Recalculate re-sorts the whole route and flips it back to auto.
- AE4. **Covers R14–R16.** Morning run completed; at 13:00 a parent cancels the afternoon ride — the afternoon auto-board excludes the child, the driver's list flags them, admin gets the alert. At 07:30, with the child on the bus, cancelling the morning ride is rejected.
- AE5. **Covers R21.** A parent with two children on the route receives exactly one copy of a broadcast.
- AE6. **Covers R10.** Assigning a student with no home coordinates to an auto route falls back to pickup-time ordering and the admin sees the "not recalculated" signal.

---

## Scope Boundaries

- No admin approval workflow for cancellations — the spec says notify, and the ride must update regardless; approval would strand same-day requests in a queue nobody watches at 06:30.
- No future-dated parent cancellations — same-day only per the spec; future absences remain an office task.
- Parent-portal children view keeps its current statuses — the Unassigned gating is admin-side only.
- No notification deletion or archival mechanics — windows are display-only.
- No per-stop live ETAs for parents or drivers — recalculated times are scheduled times on the route, not live tracking.
- No broadcast scheduling, drafts, or multi-route targeting — one route, one message, sent now.
- No mid-run mutation of a run's stop snapshot — the driver-list update path is the absent-flag mechanism, matching how driver-marked absences behave today.

---

## Dependencies / Assumptions

- The Google Maps key is wired on live (SSM `/saferide/google-maps-api-key`, since `b68c877`); geometry recalculation adds no new env vars or infrastructure. Degradation must stay observable per R10.
- No scheduler exists on the Lambda deployment; anything "daily" must be read-derived, never batch-reset.
- FCM/VAPID push config remains optional; the in-feed notification is the source of truth for broadcasts and confirmations.
- Absences are unique per (student, date) today; adding scope changes that uniqueness. How overlapping scopes merge (e.g. morning cancellation followed by afternoon cancellation the same day) is a planning decision.

---

## Outstanding Questions

**Deferred to Planning**

- Time anchoring for computed stop times (anchor on the route's earliest existing time vs earliest student pickup time), and where a manual-mode insertion places the new stop (append vs nearest-neighbor).
- Whether the disjoint History window is enforced server-side (new max-age parameter) or client-side over the existing 168h query.
- The scope-merge rule for a second same-day cancellation (morning + afternoon → day?).
- The broadcast length cap value and any per-admin send throttle.

---

## Sources & Research

- Status derivation and its five output values: `backend/app/dao/parent_live_dao.py:22-83`; status writers: `backend/app/dao/student_live_dao.py:216,233-246`, `backend/app/dao/run_dao.py:376-413,539-547,605,653`, `backend/app/dao/absence_dao.py:65-100,140`.
- Students list payload already carries `route_ids`: `backend/app/dao/student_live_dao.py` (`list_students`); admin badge renders raw status: `frontend/src/features/admin/StudentsPage.tsx:302`.
- Feed windows and audit-trail invariant: `frontend/src/features/parent/parentHooks.ts:22-29`, `backend/app/dao/push_dao.py:218-246`, `backend/app/dao/parent_live_dao.py:154-193`.
- Stop regeneration, ordering rule, custom_stops exemption, call sites: `backend/app/dao/fleet_dao.py:15-112,385-400`; planner ETAs computed and discarded: `backend/app/api/fleet.py:261-271`.
- Run snapshot immutability and driver roster flags: `backend/app/dao/run_dao.py:291-452,663-734`; driver context: `backend/app/api/runs_live.py:93-95`.
- Absence machinery: `backend/app/dao/absence_dao.py`; student-stamped incident channel and parent-feed exclusion: `backend/app/dao/parent_live_dao.py:180-187`, `backend/app/dao/incident_dao.py:43-79`.
- Push fan-out, dedup index, delivery: `backend/app/services/push_service.py`, `backend/app/dao/push_dao.py:106-134,211`, `backend/db/migrations/005_push_notifications.sql:44-46`.
- Prior requirements and plan (patterns this doc extends): docs/brainstorms/2026-07-01-spec-refinement-requirements.md, docs/plans/2026-07-01-001-feat-spec-refinement-three-views-plan.md.
