# Agent Implementation Instructions — SafeRide Kenya

Handoff notes for the next coding agent working on this project. Read this top to bottom before touching anything.

## Project context

SafeRide is a school bus management platform (Nairobi private schools) with three roles: **admin** (web console), **driver** (mobile web), **parent** (mobile web). It was originally built on Lovable (live at https://www.saferidekenya.com, Supabase-backed) and has been **fully migrated** to a self-hosted stack:

- **Frontend**: React 18 + Vite + TypeScript, Tailwind v3 + shadcn/ui, React Query, React Router (21 routes)
- **Backend**: FastAPI + Postgres, DAO/service layers, Pydantic schemas
- **Auth**: email+password and 4-digit driver PIN, sliding 16h bearer sessions, role-guarded endpoints (no Firebase, no Supabase)
- **Local stack**: Docker Compose (Postgres + API on :9001) + Vite dev server (:5173). Start with `scripts/start-local.sh` (`--reset` for clean seeded DB).

### Repos and folders (important — there was churn)

| Location | What it is |
|---|---|
| `github.com/and7005-cyber/safe-ride-platform` | **Canonical repo for the new app.** Single initial commit `0fcafa5` on `main`, app at repo root. All future work should target this. |
| `github.com/and7005-cyber/Safe-Ride-1` | Old monorepo. Full development history on branch `feat/lovable-live-parity` (pushed, PR never created). Treat as archive. |
| `~/Documents/GitHub/Safe Ride 1/` | Local working copy of the old monorepo. The app lives in `safe-ride/`. The Lovable original source is in `Ridesafe-main/` (gitignored, reference only). Folder was renamed back and forth during the session — beware stale absolute paths. |
| `~/Documents/GitHub/safe-ride-old/` | Non-git backup of an older app version. Ignore. |

**HTTPS git pushes fail in sandboxed shells** (no keychain/TTY). Use the SSH remote (`git@github.com:...`) — the user's `~/.ssh/id_ed25519` authenticates as `and7005-cyber`. `gh` CLI is NOT installed.

### Test credentials (local seed, mirror the live platform)

- Admin: `admin@test.com` / `test1234.` (note the **trailing dot**)
- Parent: `and7005@gmail.com` / `Test1234`
- Driver: `and7005@yahoo.it` / `Test1234`, driver PIN `1234`
- Seed file: `backend/db/seeds/002_live_demo_seed.sql` — **rotate before any public deployment**

## What has been done

1. **Reverse-engineered the live Lovable app** from its JS bundle into specs under `safe-ride/docs/live-app-reference/` (pages, routes, data model, colors, copy). Brainstorm/plan docs are under the monorepo's root `docs/`.
2. **Rebuilt the entire frontend** to match the live platform: design tokens (forest green, DM Sans / Space Grotesk), admin sidebar console (dashboard, fleet map, buses, routes, students, run history, schools, parent assignments, parents, drivers, alerts), driver flow (home/run/boarding/incident + GPS capture), parent flow (home/track/alerts/profile + web-push UI), auth page with Email/PIN tabs, 404.
3. **Built the backend live-model**: migration `004` creates `live_*` tables; seed `002`; real auth (PBKDF2 passwords, HMAC-peppered DB-unique PINs, sliding sessions); per-domain REST routers (`fleet`, `students_live`, `runs_live`, `incidents`, `accounts`, `parent_portal`, `push`, `auth`) with server-side ownership scoping.
4. **Verified against the live platform in a real browser** (Chrome extension, logged in as each role): compared every admin/parent/driver page side-by-side and fixed all gaps found (sidebar branding "SafeRide / KENYA" + MANAGEMENT label + "Run History" nav; page taglines + "N of M" counts; search/filter toolbars; students/buses table columns + avatars; dashboard date + Fleet Status panel; "Driver Alerts" naming; parent per-page titles, Track-in-card, Alerts/Call Driver tiles, Notifications heading; driver 4-tab nav, greeting + Start Run card + stat tiles, Student Boarding counters + search, Report Incident banner).
5. **Fixed a login UX bug**: 401s from credential endpoints (login/signup/pin-login/reset) no longer trigger the global "session expired" sign-out path — the server's real message (e.g. "Invalid email or password") surfaces in the toast. Regression unit test added.
6. **Test status (all green at handoff)**: backend `pytest` 60 passed; frontend `vitest` 9 passed; `npm run build` clean; Playwright e2e 4 passed (run with `PLAYWRIGHT_BASE_URL=http://localhost:5173 npx playwright test` while the local stack is up).
7. **Published**: app pushed as initial commit to `safe-ride-platform` (`main`); full history pushed to `Safe-Ride-1` branch `feat/lovable-live-parity`.

## What still needs doing

> **Update (June 2026):** the former high-priority list is done and pushed to
> `safe-ride-platform` `main`: local dev repointed at the clean clone; the
> legacy unauthenticated routers were deleted outright; all credential
> endpoints are rate-limited per IP + per account (reverse-proxy aware via
> `TRUST_PROXY_HEADERS`); demo seeds are double-gated (APP_ENV=local check in
> the scripts + a SQL session-flag guard in each seed file). Push delivery is
> implemented end-to-end: typed parent notifications (run-started,
> student-boarded, bus-approaching, reached-school, on-way-home, dropped-off,
> incident) stored in `live_notifications` and delivered via Firebase Cloud
> Messaging and/or VAPID web push when configured (see
> `docs/push-notifications.md`); the app is installable as a PWA. Test
> certification: `scripts/certify.sh` runs backend unit (51), live-stack API
> integration (19), tsc, vitest (14), build, and Playwright e2e (41) and
> reseeds. A 54-agent adversarial review of the change set was run and all 24
> confirmed findings were fixed.

### Remaining
1. **Real FCM credentials.** Local delivery is simulated; create the Firebase
   project and set `FIREBASE_*` env vars per `docs/push-notifications.md`,
   then verify on a real phone (install PWA → Profile → Enable Push).
2. **Parent ETA is a mock.** `ParentHomePage` shows a deterministic fake
   "~N min" (`mockEta`, live-app parity). Replace with a real computation from
   bus GPS vs. the child's stop once that matters.
3. **CI.** Add a GitHub Actions workflow: backend pytest (Postgres service),
   frontend vitest + tsc + build, optionally Playwright against the composed
   stack (mirror `scripts/certify.sh`).
4. Production deployment story (frontend Dockerfile, reverse proxy with
   `TRUST_PROXY_HEADERS=true`, env-based config, real domain).
5. ~~Admin attendance / absence marking parity check~~ — **done (July 2026,
   feat/ops-refinement):** absences are first-class and scoped
   (day/morning/afternoon with parent/driver/admin provenance), written by
   admin, driver, and the parent Cancel-a-Ride flow, and consumed by every
   roster surface. See docs/plans/2026-07-06-001-feat-ops-refinement-plan.md.
6. Accessibility & mobile QA pass on real devices (driver/parent flows are
   mobile web; push on iOS requires the installed PWA, iOS 16.4+).

## Verification protocol (follow this for any UI change)

1. Start the stack: `scripts/start-local.sh` (API health: `curl localhost:9001/api/health`).
2. Log in as the affected role(s) with the seed credentials above and verify in a real browser at mobile width for driver/parent, desktop for admin.
3. Compare against the live platform (https://www.saferidekenya.com) when fidelity matters — the user can log in there with the same credentials; an agent must never type credentials into the live site itself.
4. Run: backend `pytest`; frontend `npx tsc --noEmit`, `npm test`, `npm run build`, and the Playwright e2e suite.
5. After DB-mutating verification, re-apply the seed to restore canonical demo state (see `backend/db/seeds/002_live_demo_seed.sql`; the runner scripts apply every file in `db/seeds/`).
