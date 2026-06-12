# Layered Backend Migration Design

## Goal

Migrate the current SafeRide application away from the Lovable/Supabase runtime into a local, layered application while preserving current behavior first. The first working target is a browser-testable local stack with:

- React/Vite frontend.
- Python FastAPI backend.
- Postgres database.
- psycopg-based DAOs.
- Local scripts for starting, migrating, seeding, and resetting the stack.

Admin authentication is intentionally out of scope for the first migration. Driver PIN sessions and parent token links stay in scope because they are part of the product workflow and privacy model.

## Current Project Context

The current app is a Vite/React TypeScript application for a SafeRide Kenya beta. It has admin, driver, and parent routes:

- `/` for live fleet.
- `/admin/setup` for school setup.
- `/admin/attendance` for daily attendance.
- `/admin/history` for completed trip corrections.
- `/driver` and `/driver/trips` for driver PIN and trip flow.
- `/p/:token` for parent-safe trip progress.

Frontend data access is currently concentrated in:

- `src/services/adminApi.ts`
- `src/services/driverApi.ts`
- `src/services/parentApi.ts`
- `src/lib/supabase.ts`

The backend behavior currently lives in Supabase assets:

- `supabase/migrations/0001_initial_schema.sql` for tables, enums, constraints, indexes, and RLS.
- `supabase/migrations/0002_functions_and_triggers.sql` for RPCs, triggers, event handling, attendance application, notifications, and corrections.
- `supabase/functions/send-notifications/index.ts` for notification outbox processing.
- `supabase/functions/register-push/index.ts` for push subscription registration.

Important existing behaviors to preserve:

- Student-as-stop routing with ordered trip passengers.
- Trip as the operational unit.
- Driver PIN login with short-lived session tokens.
- Daily attendance applied to assigned trip passengers.
- Parent token links that reveal only the parent's own child by name.
- Driver event recording for trip start/end, boarding, drop-off, not-present, and issue reporting.
- Notification outbox rows generated from relevant trip events.
- Admin corrections with audit records.

## Recommended Migration Approach

Use a behavior-preserving layered backend migration.

This approach keeps the current schema and business behavior recognizable while moving runtime ownership out of Supabase and into a Python backend. It avoids redesigning the schema, auth model, and frontend at the same time.

Rejected alternatives:

- A full redesign of schema, auth, API contracts, and frontend in one pass. This may produce a cleaner future architecture but raises migration risk sharply.
- A thin FastAPI wrapper around existing Supabase RPCs. This is faster initially but keeps too much Supabase-specific behavior in place and delays the real migration.

## Target Backend Structure

Create a `backend/` application with clear layers:

```text
backend/
  app/
    main.py
    api/
      admin.py
      driver.py
      parent.py
      notifications.py
    core/
      config.py
      db.py
      security.py
    dao/
      admin_dao.py
      driver_dao.py
      parent_dao.py
      notification_dao.py
    schemas/
      admin.py
      driver.py
      parent.py
      notifications.py
    services/
      admin_service.py
      driver_service.py
      parent_service.py
      notification_service.py
  db/
    migrations/
    seeds/
  scripts/
```

Responsibilities:

- `app/main.py` creates the FastAPI app, configures CORS, adds health checks, and registers routers.
- `app/api/` owns HTTP routes and maps request/response models.
- `app/services/` owns business workflows that replace Supabase RPCs, triggers, and Edge Functions.
- `app/dao/` owns SQL and database reads/writes through psycopg.
- `app/schemas/` owns Pydantic API contracts.
- `app/core/` owns settings, database pooling, and security helpers for PIN/session hashing.
- `db/migrations/` owns ordinary Postgres SQL migrations adapted from the current Supabase migrations.
- `db/seeds/` owns demo data for local browser testing.
- `scripts/` owns local start, migrate, seed, reset, and test helpers.

## Database Migration Design

The first migration keeps the current domain model:

- `schools`
- `buses`
- `drivers`
- `driver_sessions`
- `students`
- `parent_contacts`
- `staff_passengers`
- `trips`
- `trip_passengers`
- `daily_attendance`
- `parent_links`
- `trip_events`
- `audit_log`
- `notification_outbox`
- `push_subscriptions`

Keep useful Postgres pieces:

- Enums for trip sessions, trip status, passenger status, attendance status, event types, and notification status.
- Foreign keys and multi-tenant school integrity constraints.
- Indexes for common list and lookup paths.
- `pgcrypto` for UUID generation and any database-side cryptographic helpers that remain useful.

Remove or replace Supabase-only assumptions:

- Remove dependencies on `auth.users` for stage 1 local development.
- Remove RLS policies from the first local backend path.
- Replace `auth.uid()` and Supabase grants with backend-enforced access rules.
- Replace RPCs with Python service methods.
- Replace Edge Functions with FastAPI routes or backend service tasks.

Stage 1 admin access is intentionally simple. Admin endpoints accept `school_id` from the frontend, matching the existing local testing workflow. A later auth-hardening stage can move `school_id` behind a real admin identity.

## API Design

The FastAPI surface should mirror the current frontend service boundaries so the React migration stays small.

### Admin API

Initial endpoints:

- `GET /api/admin/trips/active?school_id=...`
- `GET /api/admin/students?school_id=...`
- `GET /api/admin/buses?school_id=...`
- `GET /api/admin/drivers?school_id=...`
- `GET /api/admin/trips?school_id=...`
- `GET /api/admin/trips/completed?school_id=...`
- `POST /api/admin/buses`
- `POST /api/admin/students`
- `POST /api/admin/drivers`
- `POST /api/admin/parent-contacts`
- `POST /api/admin/parent-links`
- `POST /api/admin/trips`
- `POST /api/admin/trip-passengers`
- `POST /api/admin/daily-attendance`
- `POST /api/admin/trip-passenger-corrections`

### Driver API

Initial endpoints:

- `POST /api/driver/login`
- `GET /api/driver/trips/today?session_token=...&service_date=...`
- `GET /api/driver/trips/{trip_id}/passengers?session_token=...`
- `POST /api/driver/events`

Driver APIs validate session tokens and enforce that the session belongs to the assigned driver and school.

### Parent API

Initial endpoints:

- `GET /api/parent/trips/{token}`
- `POST /api/parent/push-subscriptions`

Parent APIs validate that the token exists and has not been revoked. Parent trip progress must return only the parent's own child by name and use safe labels for other passengers.

### Notification API

Initial endpoint:

- `POST /api/notifications/process`

Local notification processing should:

- Claim pending outbox rows.
- Mark unsupported local channels as skipped.
- Simulate SMS delivery by default.
- Use real Africa's Talking credentials only if configured.
- Record success, failure, retry, and final failure states.

## Frontend Migration Design

Add a small HTTP client configured by `VITE_API_BASE_URL`.

Migrate the existing frontend service files rather than rewriting the UI:

- `src/services/adminApi.ts` will call the admin FastAPI endpoints.
- `src/services/driverApi.ts` will call the driver FastAPI endpoints.
- `src/services/parentApi.ts` will call the parent FastAPI endpoints.
- `src/lib/supabase.ts` and the Supabase browser dependency can be removed once no frontend code imports it.

Keep the exported TypeScript function names and data shapes close to the current ones. This keeps React component changes small and preserves existing tests where possible.

Admin screens remain login-free in stage 1. They can use a visible `school_id` field or a seeded default school value for quick browser testing.

Driver and parent flows remain product-secured:

- Drivers still log in with a PIN and receive a session token.
- Parent pages still load by token and reveal only parent-safe progress.

## Local Runtime Design

Provide a simple local startup path:

- A backend Dockerfile for FastAPI.
- A Postgres container using the official Postgres image.
- A shell script that starts Postgres, applies migrations, loads demo seed data, and starts the FastAPI server.
- A reset script that drops/recreates local data, reapplies migrations, and reseeds.
- Environment templates for local configuration.

Expected local variables:

- `DATABASE_URL`
- `APP_ENV`
- `CORS_ORIGINS`
- `DEMO_SCHOOL_ID`
- `AFRICAS_TALKING_API_KEY` optional
- `AFRICAS_TALKING_USERNAME` optional

The intended local flow:

1. Run one startup script.
2. Start or open the React frontend.
3. Point the frontend at the FastAPI base URL.
4. Test admin, driver, and parent flows using seeded data.

## Demo Seed Data

Include seed data so the app works immediately after startup:

- One school.
- One bus.
- One active driver with a known demo PIN.
- Several students.
- Parent contacts for at least one student.
- A parent link with a known token.
- One current trip assigned to the demo driver.
- Ordered trip passengers.
- One pending SMS notification outbox row and one pending unsupported-channel row for local notification testing.

The demo seed should be deterministic so tests and browser checks can rely on stable IDs or clearly printed values.

## Business Logic Placement

Move Supabase RPC and trigger behavior into Python services.

Driver service:

- Verify driver PINs.
- Create and validate driver sessions.
- List assigned trips for a service date.
- List trip passengers.
- Record trip events.
- Apply trip event effects to trips and trip passengers.
- Enqueue parent notifications where appropriate.

Admin service:

- Create buses, drivers, students, parent contacts, parent links, trips, and trip passengers.
- Mark daily attendance.
- Apply attendance to trip passengers for the matching date.
- Correct trip passenger statuses.
- Write audit records for corrections.

Parent service:

- Resolve parent token links.
- Select the relevant trip and passenger progress.
- Project parent-safe labels and hide other student identities.
- Store push subscriptions.

Notification service:

- Claim pending messages.
- Recover stale claims.
- Dispatch or simulate supported channels.
- Mark unsupported channels skipped.
- Record attempts and errors.

## Error Handling

Use consistent API responses:

- `400` for invalid input.
- `401` for invalid driver session credentials.
- `403` for invalid or revoked parent links, or forbidden driver access.
- `404` for missing records.
- `409` for business conflicts such as invalid event transitions.
- `500` for unexpected backend failures.

Return concise error messages that the current frontend can display without complex parsing. Add structured error codes later if the UI needs richer handling.

## Testing Strategy

Backend tests:

- Unit tests for driver PIN/session handling.
- Unit tests for driver event state transitions.
- Unit tests for attendance application.
- Unit tests for parent-safe progress projection.
- Unit tests for notification outbox processing decisions.
- Integration tests for DAO/API flows against local Postgres.

Frontend tests:

- Update service-level tests around the new HTTP client behavior.
- Keep existing unit tests for date, ETA, phone, privacy, and notification copy.
- Adjust React tests only where component behavior changes.

End-to-end tests:

- Use seeded local data.
- Verify admin can see seeded active trips.
- Verify driver can log in with demo PIN.
- Verify driver can record trip events.
- Verify parent token page shows only the parent's child by name.

## Staged Plan

1. Backend skeleton and local Postgres startup.
2. Schema migration and deterministic demo seed data.
3. DAOs and services for admin, driver, parent, and notifications.
4. FastAPI routes and backend tests.
5. Frontend API client migration.
6. Local full-flow verification.
7. Later auth hardening and Supabase cleanup.

## Out Of Scope For This Migration

- Real admin authentication.
- Production deployment.
- Route optimization or maps.
- WhatsApp.
- Billing.
- Major UI redesign.
- Multi-school group admin.
- Parent messaging beyond current notification behavior.

## Open Implementation Notes

- The current workspace does not appear to contain a `.git` repository, so the design document may not be committable from this directory until git is initialized or the project is placed inside its repository root.
- The visual brainstorming companion stores temporary files under `.superpowers/brainstorm/`; these files should not be committed if this project later becomes a git repository.
