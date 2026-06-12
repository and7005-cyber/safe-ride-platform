# Gap analysis: Ridesafe-main original vs new safe-ride app

# SafeRide Gap Analysis — Original (Lovable/Supabase) vs New (React + FastAPI)

**Path conventions** (all paths absolute under `/Users/andreanatali/Documents/GitHub/Safe Ride 1/`):
- `ORIG/` = `/Users/andreanatali/Documents/GitHub/Safe Ride 1/Ridesafe-main/`
- `NEW/` = `/Users/andreanatali/Documents/GitHub/Safe Ride 1/safe-ride/`

**Framing fact that changes the analysis**: the original is a deliberately *unstyled* behavioral MVP (zero CSS, 8 routes, Supabase RPC/trigger backend). The new app has already re-implemented essentially **all original behavior** plus a Lovable-look admin shell on FastAPI/Postgres. The real gaps are therefore (a) **security regressions** vs the original's server-enforced model, (b) **decorative admin UI** that looks done but isn't wired, and (c) **shared PRD-level gaps** neither app closed. Visual parity to the original is undefined — only copy/ARIA/behavior parity matters, and that is largely preserved.

---

## 1. Admin role

| # | Concern | Original has | New app has | Severity | Original files | New files |
|---|---|---|---|---|---|---|
| A1 | Admin identity & tenant isolation | No login UI, but **real** Supabase Auth + `admin_profiles` + RLS (`current_school_id()`) enforcing school scoping server-side on all 16 tables | Cosmetic login (`admin@test.com`/`test1234` hardcoded in bundle, localStorage flag); **every `/api/admin/*` endpoint unauthenticated**, trusts client-supplied `school_id` | **Missing (server-side enforcement) — top security regression** | `ORIG/supabase/migrations/0001_initial_schema.sql`, `0002_functions_and_triggers.sql` | `NEW/frontend/src/lib/adminAuth.ts`, `NEW/frontend/src/features/auth/AuthPage.tsx`, `NEW/frontend/src/features/auth/RequireAdminAuth.tsx`, `NEW/backend/app/api/admin.py` |
| A2 | Live Fleet dashboard | `/` polls active trips every 15s (manual School UUID entry); plain trip cards; Alerts hardcoded "No current issues." | `/` polls active trips every 15s (hardcoded `DEMO_SCHOOL_ID`); metric cards + Fleet Status panel — but bus status/capacity **faked from label maps** (`fleetDisplay.ts`), "Incidents Today" hardcoded 0, "Students on Bus" actually counts trips | **Partial** (active-trips list = parity; surrounding metrics are fake) | `ORIG/src/features/admin/AdminDashboard.tsx` | `NEW/frontend/src/features/admin/AdminDashboard.tsx`, `NEW/frontend/src/features/admin/fleetDisplay.ts`, `NEW/frontend/src/lib/demoSchool.ts` |
| A3 | School Setup (buses/drivers/students/trips/parents) | Tabbed CRUD-creation forms, raw UUID inputs, client-generated 48-hex parent-link tokens, auto-increment stop/minutes | Same flows at `/admin/setup` & `/schools`, **plus** combined transactional student setup, student edit (PATCH), driver default-bus select, grade level | **Parity (new exceeds)** — but page kept pre-Lovable styling (styling-only debt) and is not in the sidebar under its own name | `ORIG/src/features/admin/SchoolSetup.tsx`, `ORIG/src/services/adminApi.ts` | `NEW/frontend/src/features/admin/SchoolSetup.tsx`, `NEW/backend/app/api/admin.py`, `NEW/backend/app/services/admin_service.py` |
| A4 | Daily Attendance | UUID-entry form; upsert on (student, date); trigger propagates to same-day trip_passengers; `marked_by` = real auth user id | Identical flow + propagation ported to `AdminDao.mark_daily_attendance`; `marked_by` = literal `'local-admin'` | **Parity** (behavior) / **partial** (actor identity lost; page orphaned from nav, unstyled) | `ORIG/src/features/admin/DailyAttendance.tsx`, `ORIG/supabase/migrations/0002_functions_and_triggers.sql` | `NEW/frontend/src/features/admin/DailyAttendance.tsx`, `NEW/backend/app/dao/admin_dao.py` |
| A5 | Run History + audited corrections | `/admin/history`: load completed trips, correct passenger status via RPC writing `audit_log` (server-derived originals) | Same flow works at `/admin/history` (POST `/api/admin/trip-passenger-corrections`, row-locked, audit row). **But** sidebar "Run History" points to `/runs`, a stub that fetches completed trips yet **never renders the rows** — the working page is orphaned | **Partial** (functional parity exists but is unreachable from nav; `/runs` is a dead shell) | `ORIG/src/features/admin/RunHistory.tsx` | `NEW/frontend/src/features/admin/RunHistory.tsx`, `NEW/frontend/src/features/admin/AdminLists.tsx` (RunsPage), `NEW/frontend/src/app/AppShell.tsx` |
| A6 | Alerts | Hardcoded static "No current issues." | Real feed: `GET /api/admin/alerts` over `trip_events` metadata + 7 seeded alerts; but Ack/Delete buttons do nothing and sidebar badge "7" is hardcoded | **New exceeds original; partial within new** | `ORIG/src/features/admin/AdminDashboard.tsx` (Alerts section) | `NEW/frontend/src/features/admin/AdminLists.tsx` (AlertsPage), `NEW/backend/app/dao/admin_dao.py` (`list_driver_alerts`), `NEW/backend/db/seeds/001_demo_seed.sql` |
| A7 | Entity pickers (searchable selects) | Raw UUID text inputs; README lists searchable selects as known follow-up | Same raw UUID inputs (Setup/Attendance/RunHistory); a few real selects only inside SchoolSetup driver/trip forms | **Gap in both** (inherited follow-up) | `ORIG/README.md` | `NEW/frontend/src/features/admin/SchoolSetup.tsx`, `DailyAttendance.tsx`, `RunHistory.tsx` |

## 2. Driver role

| # | Concern | Original | New | Severity | Files (orig → new) |
|---|---|---|---|---|---|
| D1 | PIN login (4–6 digit, global match, exactly-one rule) | `verify_driver_pin` RPC, bcrypt hashes, 16h SHA-256-hashed session | `POST /api/driver/login`, PBKDF2-SHA256/200k, same 16h hashed session, same error semantics | **Parity** (hash algorithm internal change only) | `ORIG/src/features/driver/DriverLogin.tsx`, `ORIG/supabase/migrations/0002_...sql` → `NEW/frontend/src/features/driver/DriverLogin.tsx`, `NEW/backend/app/core/security.py`, `NEW/backend/app/services/driver_service.py` |
| D2 | Trip select (today, Nairobi date) | RPC `get_driver_trips_for_today`; session cleared on fetch error | `GET /api/driver/trips/today`; identical behavior incl. session purge | **Parity** | `ORIG/src/features/driver/DriverTripSelect.tsx` → `NEW/frontend/src/features/driver/DriverTripSelect.tsx` |
| D3 | Trip ops + server state machine + offline queue | `record_driver_event` RPC + triggers; IndexedDB `saferide-driver`/`driver-events` queue, replay on mount | `POST /api/driver/events` with atomic insert+state+outbox in one connection; identical IndexedDB queue/replay | **Parity** (trigger logic faithfully ported, with explicit 409s) | `ORIG/src/features/driver/DriverTrip.tsx`, `ORIG/src/lib/offlineQueue.ts` → `NEW/frontend/src/features/driver/DriverTrip.tsx`, `NEW/frontend/src/lib/offlineQueue.ts`, `NEW/backend/app/dao/driver_dao.py` |
| D4 | Session token transport | Token sent in RPC POST body | Token sent as **`session_token` query parameter** on GETs — leaks into URLs/server logs | **Partial (security regression)** | `ORIG/src/services/driverApi.ts` → `NEW/frontend/src/services/driverApi.ts`, `NEW/backend/app/api/driver.py` |
| D5 | Logout / token refresh | None | None | **Gap in both** | — |

## 3. Parent role

| # | Concern | Original | New | Severity | Files |
|---|---|---|---|---|---|
| P1 | Token capability page + privacy projection | `get_parent_trip_progress` RPC; own child named, others "Stop N"; exact error/empty copy | `GET /api/parent/trips/:token`; same anonymization, same Nairobi-today trip selection, same copy | **Parity** | `ORIG/src/features/parent/ParentTrip.tsx`, `ORIG/src/services/parentApi.ts` → `NEW/frontend/src/features/parent/ParentTrip.tsx`, `NEW/backend/app/services/parent_service.py` |
| P2 | Live updates | Single fetch on mount, no polling | Identical single fetch | **Parity-of-gap** (PRD wants live progress; neither delivers) | same as P1 |
| P3 | Push registration | `register-push` Edge Function (backend only; frontend never calls it) | `POST /api/parent/push-subscriptions` (frontend never calls it either); delivery unimplemented in both | **Parity** (backend-only in both) | `ORIG/supabase/functions/register-push/index.ts` → `NEW/backend/app/api/parent.py` |
| P4 | ETA / "van approaching" | `eta.ts` unit-tested, unused | Same file, same status | **Parity-of-gap** | `ORIG/src/lib/eta.ts` → `NEW/frontend/src/lib/eta.ts` |

## 4. Cross-cutting

### Auth
- **Admin**: missing server-side (see A1). The new `POST /api/notifications/process` is also unauthenticated (original edge function was similarly open via CORS *, but sat behind Supabase infra + service-role DB access; the new DB has zero protective layer).
- **Driver**: parity except token-in-query-string (D4).
- **Parent**: parity (capability URL in both; new server-generates `token_urlsafe(24)` in combined setup, client-generates 48-hex in the parents tab — both ≥32 chars).
- Schema-level: `admin_profiles` table and **all RLS policies dropped** in `NEW/backend/db/migrations/001_initial_schema.sql` (deliberate per `NEW/docs/superpowers/specs/2026-05-13-layered-backend-migration-design.md`, but it is the single biggest delta from the original's security model).

### Design system
- Original: **zero CSS** — parity is copy/ARIA/data-attributes only, and the new app preserves those (`role=status`/`role=alert`, `aria-pressed`, `data-status`/`data-own-child`).
- New: 1091-line hand-rolled `NEW/frontend/src/app/styles.css` mimicking the Lovable look (green `#206f4a`/gold `#f4a825`, Inter). **New strictly exceeds the original.** Internal debt: no CSS tokens/variables, no dark mode, Inter not actually loaded, and three working pages (SchoolSetup, DailyAttendance, RunHistory) kept pre-Lovable styling — **styling-only**.

### Realtime
- **Parity**: both apps' only "realtime" is TanStack Query 15s polling on the admin active-trips query. No websockets/SSE/Supabase Realtime on either side. Parent page is one-shot fetch in both.

### Notifications
| Aspect | Original | New | Severity |
|---|---|---|---|
| Enqueue (boarded/dropped/not-present → SMS outbox, both contacts, dedupe) | DB trigger `enqueue_parent_notifications` | `DriverDao._enqueue_parent_notifications` (same NOT-EXISTS dedupe — including the original's **lifetime per-(passenger,template,phone) dedupe quirk**, faithfully ported) | **Parity** |
| Outbox worker (claim CAS, 3 attempts, stale-claim recovery, push/email skipped) | `send-notifications` Edge Function, real Africa's Talking send | `POST /api/notifications/process`; **SMS simulated** when AT keys blank (they are blank in `NEW/backend/.env`) | **Partial** (production sending unproven) |
| Scheduler | None (cron'd externally, documented follow-up) | None | **Gap in both** |
| Template coverage | 10 templates defined, only 3 ever produced | Identical (notificationCopy.ts carried over, unrendered) | **Parity-of-gap vs PRD** (trip_started/approaching/arrived/delayed/issue templates never fire) |

Files: `ORIG/supabase/functions/send-notifications/index.ts`, `ORIG/src/services/notificationCopy.ts` → `NEW/backend/app/services/notification_service.py`, `NEW/backend/app/api/notifications.py`, `NEW/frontend/src/services/notificationCopy.ts`.

### Data model
- New = original 16 tables **minus `admin_profiles`** (missing), **minus all RLS** (missing/by design), **plus** `drivers.default_bus_id` (002), `students.grade_level` (003), `saferide_local_migrations` marker table.
- All 7 enums identical. Composite `(id, school_id)` tenant-safety FKs preserved.
- `audit_log.admin_user_id` (FK→auth.users) became `admin_actor text default 'local-admin'` — audit attribution degraded until real admin auth exists.
- Triggers/RPCs → Python services with explicit transactions: faithful (attendance propagation, event→state projection, outbox enqueue). `missed_tap`/`admin_correction` event types remain reserved-unused in both; `staff_passengers` has no admin UI/endpoint in either.
- Files: `ORIG/supabase/migrations/0001/0002` → `NEW/backend/db/migrations/001-003`, `NEW/backend/app/dao/*.py`.

### Seed data
- Original: **none** (manual Supabase dashboard bootstrap; README follow-up). New: full idempotent demo seed (`NEW/backend/db/seeds/001_demo_seed.sql` — school, 6 buses, 6 drivers with known PINs, 5 students, contacts, parent link `demo-parent-token-...0001`, 4 today-dated trips, 10 passengers, 7 alert events, 2 outbox rows) + `NEW/scripts/start-local.sh` / `reset-local-db.sh` / `NEW/docker-compose.local.yml`. **New exceeds — closes the original's documented follow-up.**

---

## 5. Things the NEW app has that the original lacks
1. Full admin app shell (sidebar/topbar, 11 nav items, sign-out) — `NEW/frontend/src/app/AppShell.tsx`
2. Complete visual design system (Lovable-look CSS) — `NEW/frontend/src/app/styles.css`
3. Admin login page + route guard (cosmetic, but original had no login UI at all)
4. Students directory (aggregated endpoint `GET /api/admin/student-directory` + searchable table)
5. Buses / Drivers / Parents list pages; Alerts feed full-stack; Runs / Routes / Parent-Assignments / Fleet-Map page shells
6. Combined transactional student setup, student edit (PATCH), driver default bus, student grade level
7. Idempotent demo seed + one-command local stack (Docker Postgres + API + Vite)
8. `GET /api/health`, `GET /api/admin/trips` (all trips), typed `httpClient` with unit tests, much richer e2e spec
9. Layered, unit-tested Python backend (fake-DAO service tests) replacing opaque SQL triggers

**Caveat — decorative debt unique to the new app** (looks done, isn't): Routes page (pure placeholder), `/runs` never renders rows, `/fleet-map` re-renders the dashboard (no map), Parent Assignments assign flow unwired, Alerts Ack/Delete no-ops, Add/Bulk Upload/filter/row-kebab buttons across all list pages, AuthPage "Driver PIN" tab / Forgot password / Sign up, hardcoded sidebar badge "7", "Greenfield Academy" footer card, "TA" avatar, `fleetDisplay.ts` fake statuses/capacities.

## 6. Gaps vs documented PRD intent present in BOTH apps (for completeness)
Per `ORIG/2026-05-12-safe-ride-app.md`: fleet cards lack "last tap" + "position 1 of 20"; no delay/missed-tap detection; van-approaching/ETA notifications never fire; parent page has no live updates; no logout; no 404 route; searchable selects; staff-passenger admin UI; SMS scheduler. These are not migration regressions — they were never built.

---

## 7. Recommended priority order for closing gaps
1. **Server-side admin auth + tenant scoping** (A1): restore an `admin_profiles`-equivalent, authenticate `/api/admin/*` and `/api/notifications/process`, stop trusting client `school_id`. This is the only place the new app is *worse* than the original in a way that matters.
2. **Driver session token out of query strings** (D4): move to Authorization header or POST body; small change in `driverApi.ts` + `app/api/driver.py`.
3. **Fix Run History reachability** (A5): either render rows in `/runs` or point the sidebar at `/admin/history`; cheap, restores a fully working original feature currently hidden.
4. **De-fake the dashboard/alerts chrome** (A2, A6): derive bus status from active trips, incidents from `trip_events`, alert badge from the alerts endpoint; delete `fleetDisplay.ts` hardcoding.
5. **Wire or remove decorative actions**: Alerts Ack/Delete (needs an acknowledged flag or metadata update endpoint), Parent Assignments assign, Add/Bulk/filter buttons — each is currently a silent lie to the user.
6. **Restyle + nav-link the orphaned working pages** (SchoolSetup, DailyAttendance, RunHistory) and replace UUID text inputs with searchable selects (the original's own top follow-up).
7. **Production notifications**: Africa's Talking credentials path + a scheduler (cron/`POST /api/notifications/process` loop); then the 7 unfired templates (trip_started, approaching, arrived, delayed, issue) — closes PRD scope unfinished in both apps.
8. **Parent live updates + ETA**: add polling (mirroring the admin 15s pattern) and wire `eta.ts` — biggest end-user value gap inherited from the original.
9. **Actor identity in audit/attendance** (`marked_by`/`admin_actor`) once #1 lands.
10. **Hygiene**: gitignore `Ridesafe-main/`, push the unpushed `23498de` commit, prune unused modules (`privacy.ts` duplication, unused lucide in original)."
