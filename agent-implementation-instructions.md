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

### High priority
1. **Repoint local development at `safe-ride-platform`.** Clone it fresh (`git clone git@github.com:and7005-cyber/safe-ride-platform.git`), copy untracked env files (`backend/.env`, `frontend/.env.local` if present in the old tree), confirm `scripts/start-local.sh --reset` boots and all test suites pass from the clone. Do new work there, not in the monorepo.
2. **Prune the legacy unauthenticated admin router.** `backend/app/api/` still mounts the original migration-era `/api/admin/*` endpoints that accept `school_id` from the client with **no auth**. The new live-model routers enforce roles; the legacy ones must be deleted or put behind admin auth before any non-local deployment. (Legacy frontend pages using them were already removed.)
3. **Rate-limit the credential endpoints.** `auth/login`, `auth/pin-login` (4-digit PIN space is tiny), and the legacy driver PIN login have no brute-force protection. Add per-IP + per-account throttling; make it reverse-proxy aware (X-Forwarded-For) for production.
4. **Rotate the seeded credentials / gate the seed** so demo logins never reach production.

### Medium priority
5. **Real web-push delivery.** The parent profile has subscribe/unsubscribe UI and the backend stores subscriptions (`push` router), but nothing sends actual notifications — no VAPID keys, no service-worker push handler, no send-on-incident hook. Wire `pywebpush` (or similar) + a service worker, triggered on incident creation and arrival events.
6. **Parent ETA is a mock.** `ParentHomePage` shows a deterministic fake "~N min" (matching the live app's own mock, see `mockEta`). Replace with a real computation from bus GPS (`live_buses.current_lat/lng`) vs. the child's stop once that matters.
7. **Driver GPS → Fleet Map live updates.** Driver browser geolocation posts to the backend during a run; verify the admin Fleet Map polls/refreshes frequently enough and renders movement smoothly with multiple concurrent buses.
8. **CI.** The new repo has no CI. Add a GitHub Actions workflow: backend pytest (with a Postgres service), frontend vitest + tsc + build, optionally Playwright against the composed stack.

### Nice to have
9. Production deployment story (Dockerfile for the frontend, reverse proxy, env-based config, real domain).
10. SMS notifications via Africa's Talking (env vars exist; delivery is simulated locally).
11. Admin attendance / absence marking parity check — the old monorepo had a daily-attendance flow; confirm the live platform's equivalent (if any) is covered or intentionally dropped.
12. Accessibility & mobile QA pass on real devices (the driver/parent flows are mobile web).

## Verification protocol (follow this for any UI change)

1. Start the stack: `scripts/start-local.sh` (API health: `curl localhost:9001/api/health`).
2. Log in as the affected role(s) with the seed credentials above and verify in a real browser at mobile width for driver/parent, desktop for admin.
3. Compare against the live platform (https://www.saferidekenya.com) when fidelity matters — the user can log in there with the same credentials; an agent must never type credentials into the live site itself.
4. Run: backend `pytest`; frontend `npx tsc --noEmit`, `npm test`, `npm run build`, and the Playwright e2e suite.
5. After DB-mutating verification, re-apply the seed to restore canonical demo state (see `backend/db/seeds/002_live_demo_seed.sql`; the runner scripts apply every file in `db/seeds/`).
