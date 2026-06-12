# SafeRide Kenya Beta

SafeRide is a Phase 1 school transport MVP for Nairobi private schools. It includes an admin web app, a driver mobile web flow, parent progress links, a local FastAPI/Postgres backend, parent notifications, and offline-tolerant driver taps.

## What Is Included

- Admin dashboard for live active trips.
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
- Africa's Talking SMS credentials only when real SMS delivery is enabled.

## Environment Variables

Create `.env.local` from the local template:

```bash
cp .env.local.example .env.local
```

The root template is used by Docker Compose and backend local defaults:

```bash
DATABASE_URL=postgresql://saferide:saferide@localhost:5432/saferide
AFRICAS_TALKING_API_KEY=
AFRICAS_TALKING_USERNAME=
```

The frontend defaults to `http://localhost:9001`. If you need to override it, create a frontend env file:

```bash
cp frontend/.env.local.example frontend/.env.local
```

When Africa's Talking credentials are blank, local notification processing simulates SMS delivery.

## Install And Run

Run frontend commands from the frontend folder:

```bash
cd frontend
npm install
npm run dev
```

Open the local Vite URL shown in the terminal. Start the local FastAPI/Postgres stack first when testing real data.

Main routes:

- `/` - admin live fleet
- `/admin/setup` - school setup
- `/admin/attendance` - daily attendance
- `/admin/history` - completed trip history and corrections
- `/driver` - driver PIN login
- `/driver/trips` - assigned driver trips
- `/p/:token` - parent trip progress link

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

Demo values:

- School ID: `11111111-1111-1111-1111-111111111111`
- Driver PIN: `1234`
- Parent token: `demo-parent-token-00000000000000000001`

## Verified Local Migration Flow

The local FastAPI/Postgres stack is ready when:

- `curl http://localhost:9001/api/health` returns `{"status":"ok"}`.
- The seeded driver PIN `1234` opens the driver trip list.
- The seeded parent token `/p/demo-parent-token-00000000000000000001` shows only the parent's child by name.
- Frontend `npm run build`, frontend `npm test`, backend `pytest`, and frontend `npm run e2e` pass.

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

```bash
cd backend
pytest -v
cd ../frontend
npm run build
npm test
npm run e2e
```

Current workspace note: Docker is required for the full local stack and e2e flow. Frontend and backend unit checks can run from their respective folders.

## Beta Setup Flow

1. Start the local FastAPI/Postgres stack with `scripts/start-local.sh`.
2. Use the seeded school ID or `/admin/setup` to add buses, drivers, students, parent contacts, parent links, trips, and student stops.
3. Use `/admin/attendance` each day to mark absent students or alternative transport.
4. Drivers log in at `/driver` with their PIN and operate assigned trips.
5. Parents open their generated `/p/:token` link to see trip progress for their child.
6. Process pending notification rows with `POST /api/notifications/process`.

## Security Notes

- Driver PINs are hashed by the Python backend before they are stored.
- Driver sessions store only SHA-256 token hashes and expire after 16 hours.
- Parent links are revokable and token-scoped.
- Parent progress hides other students names and addresses.
- Admin endpoints are intentionally login-free for this local migration stage and accept `school_id` from the frontend. Add admin authentication after the migrated stack is verified.

## Known Follow-Ups

- Add a proper seeded school/admin bootstrap script.
- Replace manual UUID entry in admin screens with searchable selects.
- Add richer run history details for per-passenger timestamps and issues.
- Add SMS scheduling infrastructure for recurring outbox processing.
- Add browser/device QA once the local app can be installed and run.
