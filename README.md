# SafeRide Kenya Beta

SafeRide is a Phase 1 school transport MVP for Nairobi private schools. It includes an admin web app, a driver mobile web flow, parent progress links, a local FastAPI/Postgres backend, parent notifications, and offline-tolerant driver taps.

## What Is Included

- Admin dashboard for live active trips.
- Multi-tenant schools: per-school staff roles (director / transport
  coordinator), a school switcher for multi-school staff, parent links across
  schools with explicit acceptance, and a Kuumbai provider console (two-step
  sign-in, school provisioning, audited step-in) — with isolation layered from
  scoped queries down to database row-level security.
- School setup workflows for buses, drivers, students, parent contacts, parent links, trips, and ordered student stops.
- Daily attendance marking for absent students and alternative transport.
- Driver PIN login with short-lived session tokens.
- Driver trip selection, trip start/end, passenger boarding/drop-off/not-present taps, issue reporting, and offline tap queueing.
- Parent link page that shows only the parent child by name and anonymizes other stops.
- FastAPI routes for admin, driver, parent, and notification workflows.
- Local Postgres migrations, seed data, Docker Compose scripts, and DAO/service layers.
- Unit and e2e test files for core privacy, ETA, phone, notification, parent link, and beta-flow behavior.

## Project Structure

```text
frontend/
  src/                 React/Vite application
  tests/               Vitest and Playwright tests
  package.json         Frontend scripts and dependencies
backend/
  app/                 FastAPI routes, services, DAOs, schemas, and core helpers
  db/                  Local Postgres migrations and seed data
  supabase-legacy/     Original Supabase reference assets
docs/superpowers/      Implementation plan and status notes
scripts/               Local stack helpers
```

## Prerequisites

- Node.js 20 or newer.
- npm, pnpm, or yarn.
- Docker Compose.
- Python 3.11+ when running backend tests outside Docker.
- Firebase Cloud Messaging credentials only when real push delivery is enabled
  (see [docs/push-notifications.md](docs/push-notifications.md)).

## Environment Variables

Create `.env.local` from the local template:

```bash
cp .env.local.example .env.local
```

The root template is used by Docker Compose and backend local defaults:

```bash
DATABASE_URL=postgresql://saferide:saferide@localhost:5432/saferide
```

The frontend defaults to `http://localhost:9001`. If you need to override it, create a frontend env file:

```bash
cp frontend/.env.local.example frontend/.env.local
```

When no push credentials are configured, notification delivery is simulated
locally and the in-app parent alerts feed still works.

## Install And Run

Run frontend commands from the frontend folder:

```bash
cd frontend
npm install
npm run dev
```

Open the local Vite URL shown in the terminal. Start the local FastAPI/Postgres stack first when testing real data.

Main routes:

- `/` - admin console for the active school (fleet map, buses, routes,
  students, runs, parents, drivers, alerts, staff, settings)
- `/auth` - email/password and driver-PIN sign in (parent-only sign-up)
- `/driver` - driver home (run, boarding, incident tabs)
- `/parent` - parent home (track, alerts, profile tabs)
- `/provider` - Kuumbai provider console (school list, accounts, audit)

## Local FastAPI/Postgres Stack

Prerequisites:

- Docker Compose

Backend settings live in `backend/.env`. Docker Compose reads that file for the API container; Vite reads `frontend/.env.local`.

Start the local database and run the API. On the first initialized run, this applies migrations and seeds demo data; after the database has already been initialized, it skips migration and seed replay:

```bash
scripts/start-local.sh
```

Start the whole local stack from a clean seeded database:

```bash
scripts/start-local.sh --reset
```

This launches Postgres and the backend API through Docker, then starts the Vite frontend using `frontend/.env.local`. Frontend logs and PID files are kept under `.local/`.

Reset the local database from scratch, then reapply migrations and seed demo data:

```bash
scripts/reset-local-db.sh
```

The API runs at `http://localhost:9001`. The frontend should use:

```bash
VITE_API_BASE_URL=http://localhost:9001
```

Demo identities (local seed only): the seed provisions two demo schools with a
director, a coordinator, a driver (with PIN), a parent, and an unenrolled
provider account. The emails, passwords and PIN are deliberately not listed
here — see the tenancy-fixtures tail of `backend/db/seeds/003_local_snapshot.sql`
(applied by `scripts/reset-local-db.sh`) for the seeded credentials.

### Demo seed safety

The demo seeds under `backend/db/seeds/` contain well-known test credentials and are
**local-development only**. Two guards keep them out of real environments:

- The seed scripts (`scripts/start-local.sh`, `scripts/reset-local-db.sh`) refuse to
  run unless `APP_ENV=local` in `backend/.env`.
- Each seed file opens with a SQL guard that aborts unless the session has set
  `saferide.allow_demo_seed = 'yes'` (the scripts set this for you).

Before any public or production deployment, provision real accounts through the
signup/admin flows and never apply these seed files; rotate any credential that may
have been exposed.

## Verified Local Flow

The local FastAPI/Postgres stack is ready when:

- `curl http://localhost:9001/api/health` returns `{"status":"ok"}`.
- The seeded driver PIN (see the seed file) opens the driver home at `/driver`.
- The seeded parent login lands on `/parent` and shows only their children.
- The seeded director login lands on the admin console scoped to school #1.
- `scripts/certify.sh` passes (or the individual suites under Testing below).

## Legacy Supabase Reference

The migrated local path uses FastAPI and Postgres. The `backend/supabase-legacy/` folder is kept as a reference for the original schema, RPC, trigger, and Edge Function behavior during migration.

Apply the migrations:

```bash
cd backend/supabase-legacy
supabase db push
```

Deploy Edge Functions:

```bash
cd backend/supabase-legacy
supabase functions deploy send-notifications
supabase functions deploy register-push
```

The migrations create:

- Tenant tables for schools and admin profiles.
- Transport tables for buses, drivers, students, parent contacts, staff passengers, trips, and trip passengers.
- Parent link, attendance, event, audit, notification, and push subscription tables.
- RLS policies scoped through `admin_profiles`.
- RPCs for parent progress, hashed driver creation, driver PIN verification, driver trip reads, driver event writes, and admin corrections.
- Triggers for attendance application, trip state updates, and parent notification enqueueing.

## Testing

One command certifies the whole app (requires the running local stack):

```bash
scripts/certify.sh
```

It runs, in order: backend unit tests (pytest), backend API integration tests
against the live stack, the frontend typecheck, frontend unit tests (vitest),
the production build, and the full Playwright end-to-end browser suite
(auth, role access, admin CRUD, driver run lifecycle, parent portal,
notifications pipeline, PWA installability) — then restores the canonical
demo seed.

Individual suites:

```bash
cd backend && pytest -q                                  # unit (no stack needed)
cd backend && RUN_INTEGRATION=1 pytest tests/integration # API integration (stack up)
cd frontend && npm test                                  # vitest unit tests
cd frontend && npm run e2e                               # Playwright e2e (stack up)
```

The e2e suites mutate the database (runs, notifications, CRUD fixtures with an
"E2E" prefix). They clean up after themselves; run `scripts/reset-local-db.sh`
to restore pristine demo state at any time.

## Push Notifications & PWA

The app installs as a PWA (manifest + service worker + icons) and notifies
parents about bus events: run started, child boarded, bus approaching,
arrived at school, on the way home, dropped off, and driver incidents. The
notification feed always works in-app; real device push activates when
Firebase Cloud Messaging (or plain VAPID web push) credentials are configured.
See [docs/push-notifications.md](docs/push-notifications.md) for the full
setup guide and architecture.

## Security Notes

### Multi-tenant isolation

The platform is multi-tenant: every school is a walled space on the one
deployment, enforced in layers so that no single forgotten filter can leak a
child's data.

- **One school per request.** Every school-scoped request names its school in
  the `X-School-Id` header. The server resolves the caller's access key for
  exactly that school — a staff or driver membership, a parent's accepted
  child links, or a provider's active step-in — and never trusts the header
  without checking the key. Driver and parent surfaces derive their school
  set server-side instead of accepting a header.
- **Server-side role matrix.** Per-school roles (director, transport
  coordinator, driver) replace the old global role. Guards are enforced on
  the server per route: deletions of completed records and staff management
  are director-only; hiding a control in the UI is never the only barrier.
- **Layered data isolation.** Every DAO statement carries a typed school
  scope in its predicates; the scope is also written into a
  transaction-local GUC (`saferide.school_ids`); and the database itself is
  the backstop — the API connects as a dedicated runtime role
  (`saferide_app`, `LOGIN NOBYPASSRLS`, not the owner) against row-level
  security policies on every school-owned table. Composite
  `(id, school_id)` foreign keys make a cross-school reference (say, school
  B's bus on school A's route) unrepresentable at the schema level.
- **Not-found, never forbidden.** A record belonging to another school is
  answered exactly like a record that does not exist (404) — requests can
  never be used to probe what another school holds. (The wrong role at a
  school you *do* belong to is a plain 403.)
- **Attributed actions.** Every staff create/change/removal is written to a
  per-school audit trail under the acting person; provider (Kuumbai)
  step-ins and actions land in the same trail flagged provider-originated,
  masked as "SafeRide" on school-facing screens and readable unmasked only
  provider-side.
- **Provider second factor.** Provider accounts — the only accounts that can
  reach more than one school — sign in with a TOTP second step, and
  sensitive provider actions require a code fresh within 15 minutes.

### General

- Passwords are PBKDF2-hashed; driver PINs are HMAC-peppered and unique.
  Staff temporary passwords are server-generated, shown once, expire unused
  after 72 hours, and must be replaced at first sign-in.
- Sessions store only SHA-256 token hashes and slide-expire after 16 hours;
  removing a role or resetting a password revokes the affected sessions at
  once.
- All API endpoints require a bearer token; parents can only read their own
  children.
- Credential endpoints are rate-limited per IP and per account (reverse-proxy
  aware via `TRUST_PROXY_HEADERS`).
- Demo seeds are double-gated and refuse to run outside local dev.
