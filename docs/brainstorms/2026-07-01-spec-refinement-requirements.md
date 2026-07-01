---
date: 2026-07-01
topic: spec-refinement-admin-driver-parent
---

# SafeRide Spec Refinement — Requirements

## Summary

Fourteen customer-requested improvements across the Admin console, Driver app, and Parent portal: route/bus conflict prevention, a cleaner student + parents creation flow, auditable run reports, a persistable route planner with CSV import and map previews, a driver absent-marking flow with parent+school notifications, safer boarding confirmation with afternoon drop-off semantics, and parent notification filtering, lifecycle windows, and trustworthy child status.

All open questions in the original spec were resolved autonomously; the decisions below favor the day-to-day experience of the three stakeholders (admin, driver, parent) and the existing architecture of the platform.

---

## Key Decisions

- **"Active route" conflict means one route per (bus, type).** Routes carry no schedule; activity lives on runs, and a run-level guard already exists (one non-completed run per bus per date, DB partial unique index + app checks). The only meaningful new rule at the route level is: a bus may hold at most one morning and one afternoon route. Enforced app-side with a friendly 409 and DB-side with a partial unique index (mirrors the proven runs-guard pattern).
- **Student status becomes operational-only.** The status select disappears from the Add/Edit Student dialog entirely (it is one shared component), and the backend stops writing `status` on student update. This closes a live bug: today any admin edit resets an on-bus child to `at-school` via `coalesce`. Status stays visible (read-only) in the students list, and end-of-run sweeps remain the self-healing mechanism.
- **Two parent contact slots on the student, accounts auto-linked by email.** The existing flat-columns model is extended (`parent2_name/phone/email`) rather than normalized into a new table — every existing consumer (pending-parent inference, bulk upload, parents directory) keys off these columns. Parent emails on the student form are the source of truth for account linking: student create/update and parent signup both synchronize `live_parent_students` links by case-insensitive email match, capped at 2 per student.
- **Absent students are snapshotted per run.** Post-hoc joins against mutable route membership drift; the run report needs auditable truth. `start_run` (and the driver absent action) writes a `run_absences` snapshot; the report reads it, falling back to a date-join for legacy runs.
- **"Notify the school" = an admin incident.** There is no school entity with users; admins are the school. Driver absent-marking creates a `live_incidents` row (type `student`, run-stamped) that lands on the existing admin Alerts page with its unread badge and acknowledge flow — no new admin surface needed. The targeted parent gets a push/feed notification of new type `student-absent`; other bus parents are not notified.
- **"Past route" = already completed today.** No schedule data exists to support wall-clock cutoffs, and blocking a late-running morning route by clock would hurt drivers (buses run late). A route whose run completed today is not startable again (client: disabled with a label; server: rejected in `start_run`).
- **Afternoon runs get real drop-off semantics, not a relabel.** At afternoon `start_run` all non-absent roster students are auto-marked `on-bus` (they board at the school gate; parents already receive "on the way home"). The Board page shows a per-student "Drop-off" action gated on the home stop being reached; confirming sets `dropped-off` immediately and fires the per-student `dropped-off` notification at tap time (the end-run sweep's duplicate is suppressed by the existing dedup index).
- **"Call History" is a notification history view labeled "History."** No calling feature exists anywhere (Call Driver is a `tel:` link); the spec section is titled "Notification lifecycle." The view shows the last 7 days. The 24h/7d windows are display filters — no rows are deleted (no scheduler exists on the Lambda deployment, and the feed is the audit trail of what parents were told).
- **Notifications carry a persisted `run_type`.** Morning/afternoon filtering must survive run deletion (`run_id` is `ON DELETE SET NULL`), so the period is stamped at insert time and backfilled by join where possible. Driver incidents also get stamped with the active run.
- **Planner-saved routes hold custom stops until students arrive.** "Save to Routes" persists the selected option as a real route (named, typed, school required, bus optional) with ordered custom stops (`student_id NULL`), polyline, distance, and duration. A `custom_stops` flag exempts such routes from `regenerate_route_stops`. If students are later assigned to the route, regeneration takes over: student stops replace custom stops and the stored polyline is cleared (previews fall back to straight lines). One coherent model, no silent data loss.
- **Route previews render from existing data, zero per-card API calls.** Cards draw stop markers + stored polyline (planner routes) or straight-line segments (student routes) from the already-embedded `route_stops`. No Google Routes calls on page view; a `hasMapsKey` fallback finally gets used.

---

## Requirements

### Admin — routes, scheduling, dashboard

- R1. A bus can be assigned at most one route per type (`morning`/`afternoon`). Route create/update violating this returns a 409 naming the conflicting route; a DB partial unique index on `live_routes (bus_id, type)` backs the check.
- R2. The migration adding R1's index first resolves existing violations deterministically: for each (bus, type) group the earliest-created route keeps the bus, the rest get `bus_id = NULL`. The migration then re-derives `live_students.bus_id` for all affected students (morning-route join), and route updates that change a bus re-derive it too — the column feeds driver rosters, parent tracking, and notification fan-out.
- R3. Admin run edits (`PUT /api/runs/{id}`) re-check the active-run conflict app-side so violations surface with the friendly 409 message instead of the generic "Record already exists".
- R4. The Dashboard "Active Runs" card shows only today's (Africa/Nairobi) non-completed runs and refreshes automatically (~15s poll). Prior-day stale runs no longer appear; they remain visible in Run History.
- R5. The dashboard consumes a server-side active-runs filter (e.g. `GET /api/runs?active=true`) rather than downloading full run history on every poll.

### Admin — students

- R6. The Add/Edit Student dialog no longer contains a status field; new students default to `at-school`. The students list keeps its read-only Status column and filter.
- R7. `PUT /api/students/{id}` no longer writes `status` (payload value ignored), eliminating the silent reset of live statuses on admin edits.
- R8. Picking or dragging the map pin reverse-geocodes into the Home address text field (editable afterwards); selecting an address suggestion updates the pin. Address text input becomes the autocomplete component. Coordinates remain optional; the existing server-side geocode fallback stays.

### Admin — parents

- R9. The Add/Edit Student dialog captures up to 2 parents, each with name, phone, email. Parent 1 name is required.
- R10. Saving a student requires at least one phone AND at least one email across the two parents, enforced server-side and mirrored client-side. Bulk upload rows obey the same invariant (template gains parent 2 columns) with per-row errors.
- R11. Parent accounts are linked automatically: student create/update synchronizes `live_parent_students` to the set of accounts whose email matches either parent email (add matching, remove no-longer-matching); parent signup auto-links to all students carrying that email. At most 2 linked accounts per student, enforced in every link path (bulk upload included). The same email in both slots counts once. Before sync activates, a migration backfills empty parent-email slots from currently-linked account emails so existing manual links survive; the manual link endpoints are removed with the page.
- R12. The standalone Parent Assignments page, its nav entry, and its route are removed; `/parent-assignments` redirects to `/students`. The Parents directory page stays; its "pending parents" derivation considers both parent email columns.
- R13. Existing students that violate R10 are grandfathered: reads and unrelated flows work untouched; the invariant is enforced when the student form is saved.

### Admin — run history

- R14. Each Run History row is clickable and opens a Run Report dialog showing at minimum: number of students, number of stops, absent students (names), run start time, run end time — plus route, bus, driver, type, date, status, stops completed, and boarded count as context. Row action buttons keep working via click isolation.
- R15. A `GET /api/runs/{run_id}/report` endpoint serves the report. Absent students come from a per-run `run_absences` snapshot captured at run start (and by the driver absent action); legacy runs without a snapshot fall back to joining `live_student_absences` on the run date against the route roster.
- R16. `students_boarded` is maintained by recount (never increment) inside the tap transaction: on morning runs it counts boarded students, on afternoon runs confirmed drop-offs; `end_run` persists the final pre-sweep count and the report labels it accordingly. Admin-created runs with no route/snapshot render the report with dashes and an empty absent list.

### Admin — fleet map planner

- R17. After a route is calculated, a "Save to Routes" action persists the selected option as a route: name (required, prefilled), type (from planner), school (required), bus (optional, subject to R1). Stops are stored as ordered custom stops with coordinates and labels; polyline, total distance, and duration are stored on the route.
- R18. Routes with custom stops are skipped by `regenerate_route_stops`. When a student is assigned to such a route, regeneration resumes: student-derived stops replace custom stops, the flag clears, and the stored polyline is removed.
- R19. Save is disabled while a calculation is in flight or when unresolved addresses exist (the unresolved list is shown). A successful save resets the planner and confirms with a link to the Routes page.
- R20. A Reset button clears all planner state (rows, results, unresolved, selection, direction back to morning, school back to none). Responses from requests in flight at reset time are discarded.
- R21. A "Upload CSV" action on the planner imports address rows as an alternative to one-by-one entry: header row with `address` (required), `pickup_time` (optional HH:MM), `lat`/`lng` (optional); a downloadable template; rows append to existing non-empty rows; invalid rows are listed per-row without blocking valid ones; total planner stops are capped at 24 (Google waypoint-optimization limit). Geocoding of address-only rows stays deferred to the calculate call, which already reports unresolved addresses.

### Admin — routes page

- R22. Each route card shows a small map preview: stop markers (siblings sharing a stop order collapse to one marker, null-coordinate stops filtered) plus the stored polyline when present, straight-line segments otherwise, auto-fit to bounds. No per-card routing API calls.
- R23. When no Maps key is configured, or a route has fewer than one located stop, the card shows a placeholder instead of a broken map pane.

### Driver

- R24. On the Boarding page, when a student's stop is reached and the student is not on the bus, an "Absent" action is shown alongside Board/Drop-off. On afternoon runs Absent is available while the student is `on-bus` (auto-boarded) and their home stop has not been confirmed, covering no-shows at the school gate.
- R25. Confirming Absent records a `live_student_absences` row for today (marked by the driver), appends to the run's `run_absences` snapshot, and sets the student's status to `absent` — one transaction, notifications queued post-commit. Marking an absence (driver or admin, mid-run included) sets status `absent` and appends to any active run's snapshot; clearing one resets an `absent` status to `at-school`. Clearing an absence is rejected while the student's bus has an active run (the driver UI says "contact the office to undo"; the office ends or waits out the run first).
- R25b. A student marked absent mid-run stays visible in the driver's stop list, tagged Absent with actions disabled — no stop renumbering, so arrive-counts and progress stay consistent.
- R26. Marking absent notifies the student's parents (new notification type `student-absent`, run-scoped dedup) and the school via a `live_incidents` row (type `student`, stamped with the run and the student) surfacing on the admin Alerts page. Incidents carrying a `student_id` are excluded from the parent incidents feed, so no other parent learns of the absence.
- R27. "Start Run" is disabled until a route is explicitly selected: the Run page dropdown starts on a placeholder with no auto-selection, and the Home page Start Run tile navigates to the Run page instead of starting a run directly.
- R28. Routes whose run has already completed today (any creator) are not startable: shown disabled with a "Completed today" hint in the dropdown, and rejected server-side by `start_run` with a friendly 409. Driver context exposes today's completed route ids. The admin recovery path is deleting the mistaken run from Run History.
- R28b. Routes holding planner-saved custom stops (no students assigned) are not startable; `start_run` rejects them with "No students are assigned to this route yet".
- R29. Board (morning) and Drop-off (afternoon) taps require an explicit confirmation dialog naming the student before anything is recorded. After confirmation the action is final: the reverse ("X off") button is gone and the row shows a static state badge. End Run also gets a confirmation dialog; on afternoon runs it lists students not yet confirmed dropped off.
- R30. The driver boarding endpoint rejects un-boarding (`on_bus=false`) so stale clients cannot revert states.
- R31. On afternoon runs the Board page presents "Drop-off" instead of "Board", with counters/labels reworded accordingly ("Dropped off" / "Remaining").
- R32. Afternoon `start_run` auto-marks all non-absent roster students `on-bus` without per-student boarded notifications (parents already receive "on the way home"). A confirmed Drop-off sets the student to `dropped-off` immediately and fires that student's `dropped-off` parent notification at tap time (carrying run and student ids so dedup holds). The end-run sweep still normalizes unconfirmed students' status to `dropped-off` but sends them no notification — a child the driver never confirmed must not generate a false "dropped off" assertion. A new driver drop-off endpoint carries this; the boarding endpoint is morning-only and rejects un-boarding with copy telling stale clients to refresh.

### Parent

- R33. The Alerts feed gains filters: a period toggle (All / Morning / Afternoon) and a type filter over the merged, parent-labeled taxonomy (notification types plus incidents; the never-produced `custom` type is excluded). Filtering is client-side over the fetched window.
- R34. Notifications persist their run period: a `run_type` column stamped at insert (all producers, including the deprecated GPS path while it exists), backfilled by run join where the run survives. Driver incidents are stamped with the driver's active run id and its `run_type` at creation (the period must survive run deletion there too). Rows without a period appear only under "All".
- R35. The main Alerts feed shows a rolling 24-hour window across both sources (typed notifications and incidents). A "History" tab shows the last 7 days. Windows are server-side query params (`window_hours`, with the list cap raised to 200 for history); no rows are deleted.
- R36. The Parent Home children cards show a trustworthy highlighted status. A derived `display_status` is served per child: `absent` when a today-absence exists; `at-home` when a terminal `dropped-off` state has gone stale (no afternoon run today containing the student) or when an `on-bus` state is stale (no in-progress run today on the student's bus — e.g. a run abandoned yesterday); otherwise the live status. The Profile page's My Children card gains the same badge; the admin students list keeps showing the raw operational status. 5-second polling remains the real-time mechanism.

---

## Acceptance Examples

- AE1. **Covers R1.** Given bus Express 1 already has morning route A, when an admin creates morning route B on Express 1, the save fails with "Express 1 already has a morning route (A)". Creating an afternoon route on Express 1 succeeds.
- AE2. **Covers R4.** Given a driver taps End Run, within one poll interval the run disappears from the Dashboard Active Runs card without a manual refresh. A run left in-progress yesterday does not appear today.
- AE3. **Covers R7.** Given a student is `on-bus` mid-run, when an admin fixes the student's grade and saves, the student remains `on-bus`.
- AE4. **Covers R10.** Given parent 1 has only a phone and parent 2 is empty, saving the student is blocked with a message that at least one email is required. Adding an email to either parent slot unblocks it.
- AE5. **Covers R11.** Given a student is saved with parent email `mom@x.com` and no such account exists, when a parent later signs up with `mom@x.com`, the account is linked automatically and the parent sees the child immediately.
- AE6. **Covers R15/R25.** Given a driver marks Jane absent mid-run, the run's report lists Jane under absent students; Jane's parents receive a "marked absent" notification; admins see a student incident for the run.
- AE7. **Covers R18.** Given a planner-saved route with 8 custom stops, when an admin assigns student Brian to that route, the route's stops regenerate from students (gate + Brian) and the stored polyline is cleared; the Routes page preview falls back to straight lines.
- AE8. **Covers R28.** Given route "Morning A" completed today at 07:40, the driver's dropdown shows it disabled with "Completed today", and a direct API start attempt returns 409.
- AE9. **Covers R29/R32.** Given an afternoon run where Faith's home stop was just reached, the driver sees "Drop-off" for Faith; tapping it asks "Drop off Faith Achieng?"; confirming records `dropped-off`, notifies Faith's parent, and leaves no undo control.
- AE10. **Covers R35.** Given a notification created 25 hours ago, it is absent from the parent's main Alerts feed but present in History; after 8 days it appears in neither, yet its row still exists in the database.
- AE11. **Covers R36.** Given Brian was dropped off yesterday afternoon and no run has started today, his card shows "At home"; once he is marked absent for today it shows "Absent".
- AE12. **Covers R32.** Given an afternoon run ends while Grace was never confirmed dropped off, her status normalizes to `dropped-off` but her parent receives no "dropped off" notification; the End Run confirmation had listed her as unconfirmed.
- AE13. **Covers R26.** Given a driver marks Jane absent, Jane's parent sees the absence notification, admins see the student incident, and other parents on the bus see nothing new in their alerts feed.

---

## Scope Boundaries

- No wall-clock "past route" cutoffs, per-school schedules, or run auto-expiry sweeps — completed-today is the only past-ness rule; stale prior-day runs are merely excluded from Active Runs.
- No offline tap queue for drivers (failed taps keep the existing error toast); no undo window after confirmation.
- No physical deletion or archival jobs for notifications; windows are display filters.
- No normalization of parents into a separate table; no admin "create parent account" flow (self-signup + auto-link remains the account path).
- The parent home ETA stays the documented mock; replacing it is out of scope.
- No websocket/SSE; polling cadences stay as-is (5s live, 15s admin).
- Legacy migrations 001–003 and the supabase-legacy folder stay untouched.

---

## Dependencies / Assumptions

- "Call History" is assumed to mean the notification history view (no calling feature exists; the spec section is titled "Notification lifecycle"). The tab is labeled "History"; renaming is trivial if the customer insists on the literal name.
- "Remove the status field from the Add New Student form" is applied to the shared Add/Edit dialog, since it is one component and manual status writes are the source of a live bug (R7). The read-only list column stays.
- The map picker largely exists; R8 covers the remaining gap (two-way sync between pin and address text). A reverse-geocode endpoint is added server-side using the existing Google key with graceful degradation.
- Mis-tap corrections after R29/R30: the end-of-run sweep self-corrects statuses; wrong-boarding pushes cannot be unsent (accepted; the confirmation dialog is the guard).
- Google Maps API key present in dev/prod (`backend/.env`, `VITE_GOOGLE_MAPS_API_KEY`); key-less environments degrade to placeholders/straight lines per R23.
- All date logic stays Africa/Nairobi, matching every existing query.
- E2E/integration suites pin current UI flows (one-tap Start Run, bare Board button, parent-assignment page, students-without-parents fixtures) and must be updated alongside the features; `scripts/certify.sh` is the gate.

---

## Outstanding Questions

- Deferred to planning: exact copy for confirmations, notification titles/bodies, and disabled-state hints; dialog vs. drawer layout details for the Run Report; whether the planner CSV dialog reuses `BulkUploadDialog` internals or stands alone.
