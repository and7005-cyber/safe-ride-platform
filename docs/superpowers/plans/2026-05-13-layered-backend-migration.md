# Layered Backend Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local layered SafeRide stack with FastAPI, psycopg DAOs, Postgres, deterministic seed data, and a React frontend that talks to the new API instead of Supabase.

**Architecture:** Preserve the current SafeRide behavior first. Move Supabase table/RPC/Edge Function behavior into a Python backend with routes, services, DAOs, Pydantic schemas, SQL migrations, and local scripts. Keep admin screens login-free for browser testing while preserving driver PIN sessions and parent token privacy.

**Tech Stack:** Python 3.11+, FastAPI, psycopg 3 with connection pooling, Pydantic settings, Postgres, Docker, shell scripts, React/Vite/TypeScript, Vitest, Playwright.

---

## Source Spec

Use this approved design as the source of truth:

- `docs/superpowers/specs/2026-05-13-layered-backend-migration-design.md`

## Workspace Notes

- This workspace currently has no `.git` directory. Commit steps are still included because the plan is intended to be used inside a repository. In the current folder, run the verification commands and record changed files instead of committing until git is initialized or the files are moved into the repository root.
- The `.superpowers/brainstorm/` directory was created by the visual brainstorming companion and should stay ignored once a `.gitignore` exists.
- Use `apply_patch` for manual edits.

## File Structure

Create or modify these files:

```text
.env.local.example                         Local frontend/backend environment template
.gitignore                                 Ignore local generated files and secrets
docker-compose.local.yml                   Local Postgres and FastAPI orchestration
backend/Dockerfile                         FastAPI container image
backend/requirements.txt                   Python dependencies
backend/pytest.ini                         Backend test configuration
backend/app/__init__.py                    Backend package marker
backend/app/main.py                        FastAPI app factory and router registration
backend/app/api/__init__.py                API package marker
backend/app/api/admin.py                   Admin routes
backend/app/api/driver.py                  Driver routes
backend/app/api/parent.py                  Parent routes
backend/app/api/notifications.py           Notification routes
backend/app/core/__init__.py               Core package marker
backend/app/core/config.py                 Environment settings
backend/app/core/db.py                     psycopg connection pool
backend/app/core/errors.py                 Domain errors and HTTP mapping
backend/app/core/security.py               PIN hashing, session token creation, session hashing
backend/app/dao/__init__.py                DAO package marker
backend/app/dao/admin_dao.py               Admin SQL
backend/app/dao/driver_dao.py              Driver SQL
backend/app/dao/parent_dao.py              Parent SQL
backend/app/dao/notification_dao.py        Notification SQL
backend/app/schemas/__init__.py            Schema package marker
backend/app/schemas/admin.py               Admin request/response models
backend/app/schemas/driver.py              Driver request/response models
backend/app/schemas/parent.py              Parent request/response models
backend/app/schemas/notifications.py       Notification request/response models
backend/app/services/__init__.py           Service package marker
backend/app/services/admin_service.py      Admin workflows
backend/app/services/driver_service.py     Driver workflows and event transitions
backend/app/services/parent_service.py     Parent-safe trip projection
backend/app/services/notification_service.py Notification processing
backend/db/migrations/001_initial_schema.sql Local Postgres schema
backend/db/seeds/001_demo_seed.sql         Deterministic demo data
backend/tests/api/test_health.py           FastAPI health smoke test
backend/tests/core/test_security.py        Security helper tests
backend/tests/services/test_admin_service.py Admin service tests
backend/tests/services/test_driver_service.py Driver service tests
backend/tests/services/test_parent_service.py Parent service tests
backend/tests/services/test_notification_service.py Notification service tests
scripts/start-local.sh                     Start local DB, migrate, seed, and run API
scripts/reset-local-db.sh                  Reset local DB and reseed
src/services/httpClient.ts                 Frontend HTTP helper
src/services/adminApi.ts                   Replace Supabase admin calls with FastAPI calls
src/services/driverApi.ts                  Replace Supabase driver RPC calls with FastAPI calls
src/services/parentApi.ts                  Replace Supabase parent RPC calls with FastAPI calls
src/lib/supabase.ts                        Remove after imports are gone
package.json                               Remove Supabase dependency after frontend migration
tests/unit/httpClient.test.ts              Frontend HTTP helper tests
tests/e2e/admin-driver-parent.spec.ts      Seeded local full-flow browser test
README.md                                  Local stack instructions
```

## Task 1: Backend Skeleton, Settings, Errors, And Security

**Files:**
- Create: `backend/requirements.txt`
- Create: `backend/pytest.ini`
- Create: `backend/app/__init__.py`
- Create: `backend/app/core/__init__.py`
- Create: `backend/app/core/config.py`
- Create: `backend/app/core/db.py`
- Create: `backend/app/core/errors.py`
- Create: `backend/app/core/security.py`
- Create: `backend/app/api/__init__.py`
- Create: `backend/app/api/health.py`
- Create: `backend/app/main.py`
- Create: `backend/tests/api/test_health.py`
- Create: `backend/tests/core/test_security.py`

- [ ] **Step 1: Add backend dependencies**

Create `backend/requirements.txt`:

```text
fastapi
uvicorn[standard]
psycopg[binary,pool]
pydantic-settings
pytest
httpx
```

- [ ] **Step 2: Add backend pytest config**

Create `backend/pytest.ini`:

```ini
[pytest]
testpaths = tests
pythonpath = .
```

- [ ] **Step 3: Create package marker files**

Create these empty files:

```text
backend/app/__init__.py
backend/app/api/__init__.py
backend/app/core/__init__.py
```

- [ ] **Step 4: Write settings**

Create `backend/app/core/config.py`:

```python
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = Field(default="local", alias="APP_ENV")
    database_url: str = Field(
        default="postgresql://saferide:saferide@localhost:5432/saferide",
        alias="DATABASE_URL",
    )
    cors_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173",
        alias="CORS_ORIGINS",
    )
    demo_school_id: str = Field(
        default="11111111-1111-1111-1111-111111111111",
        alias="DEMO_SCHOOL_ID",
    )
    africas_talking_api_key: str = Field(default="", alias="AFRICAS_TALKING_API_KEY")
    africas_talking_username: str = Field(default="", alias="AFRICAS_TALKING_USERNAME")

    model_config = SettingsConfigDict(env_file=".env.local", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 5: Write database pool helper**

Create `backend/app/core/db.py`:

```python
from collections.abc import Iterator
from contextlib import contextmanager

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.core.config import get_settings

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=get_settings().database_url,
            kwargs={"row_factory": dict_row},
            open=False,
        )
        _pool.open()
    return _pool


@contextmanager
def get_connection() -> Iterator[Connection]:
    with get_pool().connection() as connection:
        yield connection


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
```

- [ ] **Step 6: Write domain errors**

Create `backend/app/core/errors.py`:

```python
from fastapi import HTTPException


class SafeRideError(Exception):
    status_code = 500


class BadRequestError(SafeRideError):
    status_code = 400


class UnauthorizedError(SafeRideError):
    status_code = 401


class ForbiddenError(SafeRideError):
    status_code = 403


class NotFoundError(SafeRideError):
    status_code = 404


class ConflictError(SafeRideError):
    status_code = 409


def to_http_exception(error: SafeRideError) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail=str(error))
```

- [ ] **Step 7: Write security helpers**

Create `backend/app/core/security.py`:

```python
import base64
import hashlib
import hmac
import secrets

PIN_HASH_SCHEME = "pbkdf2_sha256"
PIN_HASH_ITERATIONS = 200_000


def hash_pin(pin: str, salt: str | None = None) -> str:
    if not pin.isdigit() or not 4 <= len(pin) <= 6:
        raise ValueError("Driver PIN must be 4 to 6 digits")

    salt_value = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        pin.encode("utf-8"),
        salt_value.encode("utf-8"),
        PIN_HASH_ITERATIONS,
    )
    encoded = base64.b64encode(digest).decode("ascii")
    return f"{PIN_HASH_SCHEME}${PIN_HASH_ITERATIONS}${salt_value}${encoded}"


def verify_pin(pin: str, stored_hash: str) -> bool:
    try:
        scheme, iterations, salt, encoded_digest = stored_hash.split("$", 3)
    except ValueError:
        return False

    if scheme != PIN_HASH_SCHEME:
        return False

    digest = hashlib.pbkdf2_hmac(
        "sha256",
        pin.encode("utf-8"),
        salt.encode("utf-8"),
        int(iterations),
    )
    expected = base64.b64encode(digest).decode("ascii")
    return hmac.compare_digest(expected, encoded_digest)


def create_session_token() -> str:
    return secrets.token_hex(32)


def hash_session_token(session_token: str) -> str:
    return hashlib.sha256(session_token.encode("utf-8")).hexdigest()
```

- [ ] **Step 8: Write failing security tests**

Create `backend/tests/core/test_security.py`:

```python
import pytest

from app.core.security import create_session_token, hash_pin, hash_session_token, verify_pin


def test_hash_pin_round_trips_with_valid_pin() -> None:
    stored_hash = hash_pin("1234", salt="demo-driver-salt")

    assert stored_hash == (
        "pbkdf2_sha256$200000$demo-driver-salt$"
        "ooEh79F7IwGlxeLQ4G000PzDJkAtL1EHMqH7/qj6jb0="
    )
    assert verify_pin("1234", stored_hash)
    assert not verify_pin("9999", stored_hash)


def test_hash_pin_rejects_invalid_pin() -> None:
    with pytest.raises(ValueError, match="Driver PIN must be 4 to 6 digits"):
        hash_pin("12ab")


def test_session_tokens_are_hashable_and_not_empty() -> None:
    token = create_session_token()

    assert len(token) == 64
    assert hash_session_token(token) == hash_session_token(token)
    assert hash_session_token(token) != token
```

- [ ] **Step 9: Write health route and app factory**

Create `backend/app/api/health.py`:

```python
from fastapi import APIRouter

router = APIRouter(prefix="/api")


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
```

Create `backend/app/main.py`:

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health
from app.core.config import get_settings
from app.core.db import close_pool


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="SafeRide API")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)

    @app.on_event("shutdown")
    def shutdown() -> None:
        close_pool()

    return app


app = create_app()
```

- [ ] **Step 10: Write health smoke test**

Create `backend/tests/api/test_health.py`:

```python
from fastapi.testclient import TestClient

from app.main import create_app


def test_health_endpoint_returns_ok() -> None:
    client = TestClient(create_app())

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 11: Run backend unit tests**

Run:

```bash
cd backend
python3 -m pip install -r requirements.txt
pytest tests/api/test_health.py tests/core/test_security.py -v
```

Expected: both test files pass.

- [ ] **Step 12: Commit or record checkpoint**

If inside a git repository, run:

```bash
git add backend/requirements.txt backend/pytest.ini backend/app backend/tests/api/test_health.py backend/tests/core/test_security.py
git commit -m "feat: add FastAPI backend skeleton"
```

If the workspace still has no `.git` directory, record these changed files in the task checkpoint instead of committing.

## Task 2: Local Docker Runtime And Shell Scripts

**Files:**
- Create: `.gitignore`
- Create: `.env.local.example`
- Create: `backend/Dockerfile`
- Create: `docker-compose.local.yml`
- Create: `scripts/start-local.sh`
- Create: `scripts/reset-local-db.sh`
- Modify: `README.md`

- [ ] **Step 1: Add local ignore rules**

Create or update `.gitignore`:

```gitignore
.DS_Store
.env
.env.local
.superpowers/
node_modules/
dist/
coverage/
__pycache__/
.pytest_cache/
.venv/
backend/.venv/
backend/**/*.pyc
```

- [ ] **Step 2: Add environment template**

Create `.env.local.example`:

```bash
APP_ENV=local
DATABASE_URL=postgresql://saferide:saferide@localhost:5432/saferide
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
DEMO_SCHOOL_ID=11111111-1111-1111-1111-111111111111
VITE_API_BASE_URL=http://localhost:8000
AFRICAS_TALKING_API_KEY=
AFRICAS_TALKING_USERNAME=
```

- [ ] **Step 3: Add backend Dockerfile**

Create `backend/Dockerfile`:

```dockerfile
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 4: Add local compose file**

Create `docker-compose.local.yml`:

```yaml
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_DB: saferide
      POSTGRES_USER: saferide
      POSTGRES_PASSWORD: saferide
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U saferide -d saferide"]
      interval: 2s
      timeout: 5s
      retries: 30
    volumes:
      - saferide-postgres-data:/var/lib/postgresql/data

  api:
    build:
      context: ./backend
    environment:
      APP_ENV: local
      DATABASE_URL: postgresql://saferide:saferide@db:5432/saferide
      CORS_ORIGINS: http://localhost:5173,http://127.0.0.1:5173
      DEMO_SCHOOL_ID: 11111111-1111-1111-1111-111111111111
      AFRICAS_TALKING_API_KEY: ${AFRICAS_TALKING_API_KEY:-}
      AFRICAS_TALKING_USERNAME: ${AFRICAS_TALKING_USERNAME:-}
    ports:
      - "8000:8000"
    depends_on:
      db:
        condition: service_healthy

volumes:
  saferide-postgres-data:
```

- [ ] **Step 5: Add startup script**

Create `scripts/start-local.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$ROOT_DIR/docker-compose.local.yml"

cd "$ROOT_DIR"

docker compose -f "$COMPOSE_FILE" up -d db

until docker compose -f "$COMPOSE_FILE" exec -T db pg_isready -U saferide -d saferide >/dev/null 2>&1; do
  sleep 1
done

if [ -f "$ROOT_DIR/backend/db/migrations/001_initial_schema.sql" ]; then
  docker compose -f "$COMPOSE_FILE" exec -T db psql -U saferide -d saferide < "$ROOT_DIR/backend/db/migrations/001_initial_schema.sql"
fi

if [ -f "$ROOT_DIR/backend/db/seeds/001_demo_seed.sql" ]; then
  docker compose -f "$COMPOSE_FILE" exec -T db psql -U saferide -d saferide < "$ROOT_DIR/backend/db/seeds/001_demo_seed.sql"
fi

docker compose -f "$COMPOSE_FILE" up --build api
```

- [ ] **Step 6: Add reset script**

Create `scripts/reset-local-db.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$ROOT_DIR/docker-compose.local.yml"

cd "$ROOT_DIR"

docker compose -f "$COMPOSE_FILE" up -d db

until docker compose -f "$COMPOSE_FILE" exec -T db pg_isready -U saferide -d saferide >/dev/null 2>&1; do
  sleep 1
done

docker compose -f "$COMPOSE_FILE" exec -T db psql -U saferide -d saferide <<'SQL'
drop schema public cascade;
create schema public;
SQL

docker compose -f "$COMPOSE_FILE" exec -T db psql -U saferide -d saferide < "$ROOT_DIR/backend/db/migrations/001_initial_schema.sql"
docker compose -f "$COMPOSE_FILE" exec -T db psql -U saferide -d saferide < "$ROOT_DIR/backend/db/seeds/001_demo_seed.sql"

echo "Local SafeRide database reset and seeded."
```

- [ ] **Step 7: Make scripts executable**

Run:

```bash
chmod +x scripts/start-local.sh scripts/reset-local-db.sh
```

Expected: no output.

- [ ] **Step 8: Document local runtime in README**

Add this section to `README.md` after "Install And Run":

````markdown
## Local FastAPI/Postgres Stack

Copy `.env.local.example` to `.env.local` for local defaults.

Start the local database, apply migrations, seed demo data, and run the API:

```bash
scripts/start-local.sh
```

Reset the local database:

```bash
scripts/reset-local-db.sh
```

The API runs at `http://localhost:8000`. The frontend should use:

```bash
VITE_API_BASE_URL=http://localhost:8000
```

Demo values:

- School ID: `11111111-1111-1111-1111-111111111111`
- Driver PIN: `1234`
- Parent token: `demo-parent-token-00000000000000000001`
````

- [ ] **Step 9: Verify scripts parse**

Run:

```bash
bash -n scripts/start-local.sh
bash -n scripts/reset-local-db.sh
docker compose -f docker-compose.local.yml config >/tmp/saferide-compose-check.yml
```

Expected: all commands exit successfully.

- [ ] **Step 10: Commit or record checkpoint**

If inside a git repository, run:

```bash
git add .gitignore .env.local.example backend/Dockerfile docker-compose.local.yml scripts/start-local.sh scripts/reset-local-db.sh README.md
git commit -m "feat: add local Docker runtime"
```

If the workspace still has no `.git` directory, record these changed files in the task checkpoint instead of committing.

## Task 3: Local Postgres Schema And Demo Seed Data

**Files:**
- Create: `backend/db/migrations/001_initial_schema.sql`
- Create: `backend/db/seeds/001_demo_seed.sql`

- [ ] **Step 1: Create local schema migration**

Create `backend/db/migrations/001_initial_schema.sql`:

```sql
create extension if not exists pgcrypto;

do $$ begin create type trip_session as enum ('morning', 'afternoon', 'adhoc', 'staff'); exception when duplicate_object then null; end $$;
do $$ begin create type trip_status as enum ('scheduled', 'active', 'delayed', 'issue_reported', 'completed', 'cancelled'); exception when duplicate_object then null; end $$;
do $$ begin create type passenger_type as enum ('student', 'staff'); exception when duplicate_object then null; end $$;
do $$ begin create type trip_passenger_status as enum ('pending', 'boarded', 'dropped', 'absent_admin', 'absent_driver', 'alternative_transport'); exception when duplicate_object then null; end $$;
do $$ begin create type attendance_status as enum ('riding', 'absent', 'alternative_transport'); exception when duplicate_object then null; end $$;
do $$ begin create type event_type as enum ('trip_started', 'passenger_boarded', 'passenger_not_present', 'passenger_dropped', 'trip_ended', 'issue_reported', 'missed_tap', 'admin_correction'); exception when duplicate_object then null; end $$;
do $$ begin create type notification_status as enum ('pending', 'processing', 'sent', 'failed', 'skipped'); exception when duplicate_object then null; end $$;

create table if not exists schools (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  approaching_threshold integer not null default 2 check (approaching_threshold >= 0),
  default_inter_student_minutes integer not null default 6 check (default_inter_student_minutes >= 0),
  created_at timestamptz not null default now()
);

create table if not exists buses (
  id uuid primary key default gen_random_uuid(),
  school_id uuid not null references schools(id) on delete cascade,
  label text not null,
  registration_number text,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  unique (school_id, label),
  unique (id, school_id)
);

create table if not exists drivers (
  id uuid primary key default gen_random_uuid(),
  school_id uuid not null references schools(id) on delete cascade,
  full_name text not null,
  phone text,
  pin_hash text not null,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  unique (id, school_id)
);

create table if not exists driver_sessions (
  id uuid primary key default gen_random_uuid(),
  school_id uuid not null references schools(id) on delete cascade,
  driver_id uuid not null references drivers(id) on delete cascade,
  token_hash text not null unique,
  expires_at timestamptz not null,
  revoked_at timestamptz,
  created_at timestamptz not null default now(),
  unique (id, school_id)
);

create table if not exists students (
  id uuid primary key default gen_random_uuid(),
  school_id uuid not null references schools(id) on delete cascade,
  full_name text not null,
  home_address text not null,
  home_location_note text,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  unique (id, school_id)
);

create table if not exists parent_contacts (
  id uuid primary key default gen_random_uuid(),
  school_id uuid not null references schools(id) on delete cascade,
  student_id uuid not null,
  contact_1_name text not null,
  contact_1_phone text not null check (contact_1_phone ~ '^\+254[0-9]{9}$'),
  contact_1_relationship text not null,
  contact_2_name text,
  contact_2_phone text check (contact_2_phone is null or contact_2_phone ~ '^\+254[0-9]{9}$'),
  contact_2_relationship text,
  created_at timestamptz not null default now(),
  unique (student_id),
  foreign key (student_id, school_id) references students(id, school_id) on delete cascade
);

create table if not exists staff_passengers (
  id uuid primary key default gen_random_uuid(),
  school_id uuid not null references schools(id) on delete cascade,
  full_name text not null,
  phone text,
  home_address text not null,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  unique (id, school_id)
);

create table if not exists trips (
  id uuid primary key default gen_random_uuid(),
  school_id uuid not null references schools(id) on delete cascade,
  bus_id uuid not null,
  driver_id uuid,
  name text not null,
  session trip_session not null,
  service_date date not null,
  scheduled_start time not null,
  status trip_status not null default 'scheduled',
  started_at timestamptz,
  ended_at timestamptz,
  created_at timestamptz not null default now(),
  unique (school_id, bus_id, service_date, name),
  unique (id, school_id),
  foreign key (bus_id, school_id) references buses(id, school_id) on delete restrict,
  foreign key (driver_id, school_id) references drivers(id, school_id) on delete set null (driver_id)
);

create table if not exists trip_passengers (
  id uuid primary key default gen_random_uuid(),
  school_id uuid not null references schools(id) on delete cascade,
  trip_id uuid not null,
  passenger_type passenger_type not null,
  student_id uuid,
  staff_passenger_id uuid,
  sequence_position integer not null check (sequence_position > 0),
  estimated_minutes_from_start integer not null check (estimated_minutes_from_start >= 0),
  actual_pickup_time timestamptz,
  actual_dropoff_time timestamptz,
  status trip_passenger_status not null default 'pending',
  created_at timestamptz not null default now(),
  check (
    (passenger_type = 'student' and student_id is not null and staff_passenger_id is null)
    or
    (passenger_type = 'staff' and staff_passenger_id is not null and student_id is null)
  ),
  unique (trip_id, sequence_position),
  unique (id, school_id),
  foreign key (trip_id, school_id) references trips(id, school_id) on delete cascade,
  foreign key (student_id, school_id) references students(id, school_id) on delete cascade,
  foreign key (staff_passenger_id, school_id) references staff_passengers(id, school_id) on delete cascade
);

create table if not exists daily_attendance (
  id uuid primary key default gen_random_uuid(),
  school_id uuid not null references schools(id) on delete cascade,
  student_id uuid not null,
  attendance_date date not null,
  status attendance_status not null,
  marked_by text,
  marked_at timestamptz not null default now(),
  note text,
  unique (student_id, attendance_date),
  foreign key (student_id, school_id) references students(id, school_id) on delete cascade
);

create table if not exists parent_links (
  id uuid primary key default gen_random_uuid(),
  school_id uuid not null references schools(id) on delete cascade,
  student_id uuid not null,
  token text not null unique check (length(token) >= 32),
  revoked_at timestamptz,
  created_at timestamptz not null default now(),
  unique (id, school_id),
  foreign key (student_id, school_id) references students(id, school_id) on delete cascade
);

create table if not exists trip_events (
  id uuid primary key default gen_random_uuid(),
  school_id uuid not null references schools(id) on delete cascade,
  trip_id uuid not null,
  trip_passenger_id uuid,
  event_type event_type not null,
  created_by_role text not null check (created_by_role in ('admin', 'driver', 'system')),
  created_by_id uuid,
  occurred_at timestamptz not null default now(),
  metadata jsonb not null default '{}'::jsonb,
  unique (id, school_id),
  foreign key (trip_id, school_id) references trips(id, school_id) on delete cascade,
  foreign key (trip_passenger_id, school_id) references trip_passengers(id, school_id) on delete set null (trip_passenger_id)
);

create table if not exists audit_log (
  id uuid primary key default gen_random_uuid(),
  school_id uuid not null references schools(id) on delete cascade,
  entity_table text not null,
  entity_id uuid not null,
  admin_actor text not null default 'local-admin',
  original_value jsonb not null,
  corrected_value jsonb not null,
  reason text not null,
  created_at timestamptz not null default now()
);

create table if not exists notification_outbox (
  id uuid primary key default gen_random_uuid(),
  school_id uuid not null references schools(id) on delete cascade,
  trip_event_id uuid,
  recipient_kind text not null check (recipient_kind in ('parent', 'admin')),
  recipient_phone text,
  push_subscription_id uuid,
  channel text not null check (channel in ('sms', 'push', 'email')),
  template_key text not null,
  payload jsonb not null default '{}'::jsonb,
  status notification_status not null default 'pending',
  attempts integer not null default 0 check (attempts >= 0),
  last_error text,
  claimed_at timestamptz,
  created_at timestamptz not null default now(),
  sent_at timestamptz,
  foreign key (trip_event_id, school_id) references trip_events(id, school_id) on delete cascade
);

create table if not exists push_subscriptions (
  id uuid primary key default gen_random_uuid(),
  school_id uuid not null references schools(id) on delete cascade,
  parent_link_id uuid not null,
  endpoint text not null,
  p256dh text not null,
  auth text not null,
  created_at timestamptz not null default now(),
  unique (parent_link_id, endpoint),
  unique (id, school_id),
  foreign key (parent_link_id, school_id) references parent_links(id, school_id) on delete cascade
);

do $$
begin
  alter table notification_outbox
    add constraint notification_outbox_push_subscription_id_fkey
    foreign key (push_subscription_id) references push_subscriptions(id) on delete set null;
exception when duplicate_object then null;
end $$;

create index if not exists buses_school_id_idx on buses (school_id);
create index if not exists drivers_school_id_idx on drivers (school_id);
create index if not exists driver_sessions_token_hash_idx on driver_sessions (token_hash);
create index if not exists driver_sessions_expires_at_idx on driver_sessions (expires_at);
create index if not exists students_school_id_idx on students (school_id);
create index if not exists parent_contacts_student_id_idx on parent_contacts (student_id);
create index if not exists trips_school_id_service_date_idx on trips (school_id, service_date);
create index if not exists trips_driver_service_date_idx on trips (driver_id, service_date) where driver_id is not null;
create index if not exists trip_passengers_trip_id_idx on trip_passengers (trip_id);
create index if not exists daily_attendance_school_date_idx on daily_attendance (school_id, attendance_date);
create index if not exists parent_links_token_idx on parent_links (token);
create index if not exists trip_events_school_trip_occurred_idx on trip_events (school_id, trip_id, occurred_at desc);
create index if not exists notification_outbox_school_status_idx on notification_outbox (school_id, status, created_at);
create index if not exists push_subscriptions_parent_link_id_idx on push_subscriptions (parent_link_id);
```

- [ ] **Step 2: Create deterministic seed data**

Create `backend/db/seeds/001_demo_seed.sql`:

```sql
insert into schools (id, name, approaching_threshold, default_inter_student_minutes)
values ('11111111-1111-1111-1111-111111111111', 'SafeRide Demo School', 2, 6)
on conflict (id) do update set name = excluded.name;

insert into buses (id, school_id, label, registration_number)
values (
  '22222222-2222-2222-2222-222222222222',
  '11111111-1111-1111-1111-111111111111',
  'Van 1',
  'KDA 123A'
)
on conflict (school_id, label) do update set registration_number = excluded.registration_number;

insert into drivers (id, school_id, full_name, phone, pin_hash)
values (
  '33333333-3333-3333-3333-333333333333',
  '11111111-1111-1111-1111-111111111111',
  'Peter Mwangi',
  '+254700000001',
  'pbkdf2_sha256$200000$demo-driver-salt$ooEh79F7IwGlxeLQ4G000PzDJkAtL1EHMqH7/qj6jb0='
)
on conflict (id) do update set full_name = excluded.full_name, phone = excluded.phone, pin_hash = excluded.pin_hash;

insert into students (id, school_id, full_name, home_address, home_location_note)
values
  ('44444444-4444-4444-4444-444444444441', '11111111-1111-1111-1111-111111111111', 'Amina Otieno', 'Kilimani Road', 'Kilimani stop'),
  ('44444444-4444-4444-4444-444444444442', '11111111-1111-1111-1111-111111111111', 'Brian Mwangi', 'Lavington Green', 'Lavington stop'),
  ('44444444-4444-4444-4444-444444444443', '11111111-1111-1111-1111-111111111111', 'Chao Wanjiku', 'Westlands Avenue', 'Westlands stop')
on conflict (id) do update set full_name = excluded.full_name, home_address = excluded.home_address, home_location_note = excluded.home_location_note;

insert into parent_contacts (
  id,
  school_id,
  student_id,
  contact_1_name,
  contact_1_phone,
  contact_1_relationship,
  contact_2_name,
  contact_2_phone,
  contact_2_relationship
)
values (
  '55555555-5555-5555-5555-555555555551',
  '11111111-1111-1111-1111-111111111111',
  '44444444-4444-4444-4444-444444444441',
  'Grace Otieno',
  '+254700000101',
  'Mother',
  'Daniel Otieno',
  '+254700000102',
  'Father'
)
on conflict (student_id) do update
set contact_1_name = excluded.contact_1_name,
    contact_1_phone = excluded.contact_1_phone,
    contact_1_relationship = excluded.contact_1_relationship,
    contact_2_name = excluded.contact_2_name,
    contact_2_phone = excluded.contact_2_phone,
    contact_2_relationship = excluded.contact_2_relationship;

insert into parent_links (id, school_id, student_id, token)
values (
  '66666666-6666-6666-6666-666666666661',
  '11111111-1111-1111-1111-111111111111',
  '44444444-4444-4444-4444-444444444441',
  'demo-parent-token-00000000000000000001'
)
on conflict (token) do update set revoked_at = null;

insert into trips (id, school_id, bus_id, driver_id, name, session, service_date, scheduled_start, status)
values (
  '77777777-7777-7777-7777-777777777771',
  '11111111-1111-1111-1111-111111111111',
  '22222222-2222-2222-2222-222222222222',
  '33333333-3333-3333-3333-333333333333',
  'Morning Route A',
  'morning',
  current_date,
  '06:30',
  'scheduled'
)
on conflict (school_id, bus_id, service_date, name) do update
set driver_id = excluded.driver_id,
    status = excluded.status,
    started_at = null,
    ended_at = null;

insert into trip_passengers (
  id,
  school_id,
  trip_id,
  passenger_type,
  student_id,
  sequence_position,
  estimated_minutes_from_start,
  status,
  actual_pickup_time,
  actual_dropoff_time
)
values
  ('88888888-8888-8888-8888-888888888881', '11111111-1111-1111-1111-111111111111', '77777777-7777-7777-7777-777777777771', 'student', '44444444-4444-4444-4444-444444444441', 1, 5, 'pending', null, null),
  ('88888888-8888-8888-8888-888888888882', '11111111-1111-1111-1111-111111111111', '77777777-7777-7777-7777-777777777771', 'student', '44444444-4444-4444-4444-444444444442', 2, 11, 'pending', null, null),
  ('88888888-8888-8888-8888-888888888883', '11111111-1111-1111-1111-111111111111', '77777777-7777-7777-7777-777777777771', 'student', '44444444-4444-4444-4444-444444444443', 3, 17, 'pending', null, null)
on conflict (id) do update
set status = excluded.status,
    actual_pickup_time = null,
    actual_dropoff_time = null;

insert into notification_outbox (
  id,
  school_id,
  recipient_kind,
  recipient_phone,
  channel,
  template_key,
  payload,
  status,
  attempts
)
values
  (
    '99999999-9999-9999-9999-999999999991',
    '11111111-1111-1111-1111-111111111111',
    'parent',
    '+254700000101',
    'sms',
    'child_confirmed_on_van',
    '{"body":"SafeRide demo SMS message."}'::jsonb,
    'pending',
    0
  ),
  (
    '99999999-9999-9999-9999-999999999992',
    '11111111-1111-1111-1111-111111111111',
    'parent',
    null,
    'push',
    'child_confirmed_on_van',
    '{"body":"SafeRide demo push message."}'::jsonb,
    'pending',
    0
  )
on conflict (id) do update
set status = 'pending',
    attempts = 0,
    last_error = null,
    claimed_at = null,
    sent_at = null;
```

- [ ] **Step 3: Verify schema and seed with local Postgres**

Run:

```bash
scripts/reset-local-db.sh
docker compose -f docker-compose.local.yml exec -T db psql -U saferide -d saferide -c "select count(*) from schools;"
docker compose -f docker-compose.local.yml exec -T db psql -U saferide -d saferide -c "select count(*) from trip_passengers;"
docker compose -f docker-compose.local.yml exec -T db psql -U saferide -d saferide -c "select count(*) from notification_outbox where status = 'pending';"
```

Expected:

```text
schools count = 1
trip_passengers count = 3
pending notification_outbox count = 2
```

- [ ] **Step 4: Commit or record checkpoint**

If inside a git repository, run:

```bash
git add backend/db/migrations/001_initial_schema.sql backend/db/seeds/001_demo_seed.sql
git commit -m "feat: add local Postgres schema and seed data"
```

If the workspace still has no `.git` directory, record these changed files in the task checkpoint instead of committing.

## Task 4: Admin Backend DAOs, Services, Routes, And Tests

**Files:**
- Create: `backend/app/dao/__init__.py`
- Create: `backend/app/schemas/__init__.py`
- Create: `backend/app/services/__init__.py`
- Create: `backend/app/schemas/admin.py`
- Create: `backend/app/dao/admin_dao.py`
- Create: `backend/app/services/admin_service.py`
- Create: `backend/app/api/admin.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/services/test_admin_service.py`

- [ ] **Step 1: Create package marker files**

Create these empty files:

```text
backend/app/dao/__init__.py
backend/app/schemas/__init__.py
backend/app/services/__init__.py
```

- [ ] **Step 2: Write admin schemas**

Create `backend/app/schemas/admin.py`:

```python
from pydantic import BaseModel, Field


class CreateBusRequest(BaseModel):
    school_id: str = Field(alias="schoolId")
    label: str
    registration_number: str | None = Field(default=None, alias="registrationNumber")


class CreateStudentRequest(BaseModel):
    school_id: str = Field(alias="schoolId")
    full_name: str = Field(alias="fullName")
    home_address: str = Field(alias="homeAddress")
    home_location_note: str | None = Field(default=None, alias="homeLocationNote")


class CreateDriverRequest(BaseModel):
    school_id: str = Field(alias="schoolId")
    full_name: str = Field(alias="fullName")
    phone: str | None = None
    pin: str


class CreateParentContactRequest(BaseModel):
    school_id: str = Field(alias="schoolId")
    student_id: str = Field(alias="studentId")
    contact_1_name: str = Field(alias="contact1Name")
    contact_1_phone: str = Field(alias="contact1Phone")
    contact_1_relationship: str = Field(alias="contact1Relationship")
    contact_2_name: str | None = Field(default=None, alias="contact2Name")
    contact_2_phone: str | None = Field(default=None, alias="contact2Phone")
    contact_2_relationship: str | None = Field(default=None, alias="contact2Relationship")


class CreateParentLinkRequest(BaseModel):
    school_id: str = Field(alias="schoolId")
    student_id: str = Field(alias="studentId")
    token: str


class CreateTripRequest(BaseModel):
    school_id: str = Field(alias="schoolId")
    bus_id: str = Field(alias="busId")
    driver_id: str | None = Field(default=None, alias="driverId")
    name: str
    session: str
    service_date: str = Field(alias="serviceDate")
    scheduled_start: str = Field(alias="scheduledStart")


class CreateTripPassengerRequest(BaseModel):
    school_id: str = Field(alias="schoolId")
    trip_id: str = Field(alias="tripId")
    student_id: str = Field(alias="studentId")
    sequence_position: int = Field(alias="sequencePosition")
    estimated_minutes_from_start: int = Field(alias="estimatedMinutesFromStart")


class MarkDailyAttendanceRequest(BaseModel):
    school_id: str = Field(alias="schoolId")
    student_id: str = Field(alias="studentId")
    attendance_date: str = Field(alias="attendanceDate")
    status: str
    note: str | None = None


class CorrectTripPassengerStatusRequest(BaseModel):
    school_id: str = Field(alias="schoolId")
    trip_passenger_id: str = Field(alias="tripPassengerId")
    corrected_status: str = Field(alias="correctedStatus")
    reason: str
```

- [ ] **Step 3: Write admin DAO with SQL methods**

Create `backend/app/dao/admin_dao.py` with methods named exactly:

```python
from typing import Any

from app.core.db import get_connection


class AdminDao:
    def list_active_trips(self, school_id: str) -> list[dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                select t.id, t.name, t.service_date, t.scheduled_start, t.status, b.label as bus_label
                from trips t
                left join buses b on b.id = t.bus_id and b.school_id = t.school_id
                where t.school_id = %s and t.status in ('active', 'delayed', 'issue_reported')
                order by t.scheduled_start asc
                """,
                (school_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_students(self, school_id: str) -> list[dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                select id, full_name, home_address
                from students
                where school_id = %s and active = true
                order by full_name asc
                """,
                (school_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_buses(self, school_id: str) -> list[dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                select id, label, registration_number
                from buses
                where school_id = %s and active = true
                order by label asc
                """,
                (school_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_drivers(self, school_id: str) -> list[dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                select id, full_name, phone
                from drivers
                where school_id = %s and active = true
                order by full_name asc
                """,
                (school_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_trips(self, school_id: str) -> list[dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                select id, name, service_date, scheduled_start, status
                from trips
                where school_id = %s
                order by service_date desc, scheduled_start asc
                """,
                (school_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_completed_trips(self, school_id: str) -> list[dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                select id, name, service_date, status
                from trips
                where school_id = %s and status = 'completed'
                order by service_date desc
                """,
                (school_id,),
            ).fetchall()
        return [dict(row) for row in rows]
```

Then add insert/update methods with these names and SQL statements:

```text
create_bus(input): insert into buses (school_id, label, registration_number) values (...) returning *
create_student(input): insert into students (school_id, full_name, home_address, home_location_note) values (...) returning *
create_driver(input, pin_hash): insert into drivers (school_id, full_name, phone, pin_hash) values (...) returning id, school_id, full_name, phone
upsert_parent_contact(input): insert into parent_contacts (...) values (...) on conflict (student_id) do update ... returning *
create_parent_link(input): insert into parent_links (school_id, student_id, token) values (...) returning *
create_trip(input): insert into trips (...) values (...) returning *
create_trip_passenger(input): insert into trip_passengers (...) values (...) returning *
upsert_daily_attendance(input): insert into daily_attendance (...) values (...) on conflict (student_id, attendance_date) do update ... returning *
apply_daily_attendance(attendance_row): update trip_passengers set status = case ... from trips ... matching the old apply_daily_attendance_to_trip_passengers logic
get_trip_passenger_for_update(school_id, trip_passenger_id): select trip_passengers joined to trips for correction checks
correct_trip_passenger_status(...): update trip_passengers set status = %s where id = %s and school_id = %s
insert_audit_log(...): insert into audit_log (...) values (...) returning id
```

- [ ] **Step 4: Write admin service tests first**

Create `backend/tests/services/test_admin_service.py`:

```python
import pytest

from app.core.errors import BadRequestError, ConflictError
from app.services.admin_service import AdminService


class FakeAdminDao:
    def __init__(self) -> None:
        self.created_driver = None
        self.applied_attendance = None
        self.trip_passenger = {
            "id": "tp-1",
            "school_id": "school-1",
            "status": "boarded",
            "actual_pickup_time": "2026-05-13T06:35:00+00:00",
            "actual_dropoff_time": None,
            "trip_status": "completed",
        }
        self.audit_record = None

    def create_driver(self, input_data, pin_hash: str):
        self.created_driver = (input_data, pin_hash)
        return {"id": "driver-1", "school_id": input_data.school_id, "full_name": input_data.full_name, "phone": input_data.phone}

    def upsert_daily_attendance(self, input_data):
        return {"id": "attendance-1", "school_id": input_data.school_id, "student_id": input_data.student_id, "attendance_date": input_data.attendance_date, "status": input_data.status}

    def apply_daily_attendance(self, attendance_row):
        self.applied_attendance = attendance_row

    def get_trip_passenger_for_update(self, school_id: str, trip_passenger_id: str):
        return self.trip_passenger

    def correct_trip_passenger_status(self, school_id: str, trip_passenger_id: str, corrected_status: str):
        return None

    def insert_audit_log(self, school_id: str, entity_id: str, original_value: dict, corrected_value: dict, reason: str):
        self.audit_record = (school_id, entity_id, original_value, corrected_value, reason)
        return "audit-1"


class DriverInput:
    school_id = "school-1"
    full_name = "Peter Mwangi"
    phone = "+254700000001"
    pin = "1234"


class AttendanceInput:
    school_id = "school-1"
    student_id = "student-1"
    attendance_date = "2026-05-13"
    status = "absent"
    note = "Sick"


class CorrectionInput:
    school_id = "school-1"
    trip_passenger_id = "tp-1"
    corrected_status = "dropped"
    reason = "Driver corrected record after call"


def test_create_driver_hashes_pin_before_insert() -> None:
    dao = FakeAdminDao()
    service = AdminService(dao)

    result = service.create_driver(DriverInput())

    assert result["id"] == "driver-1"
    assert dao.created_driver[1].startswith("pbkdf2_sha256$")


def test_mark_daily_attendance_applies_attendance_to_trip_passengers() -> None:
    dao = FakeAdminDao()
    service = AdminService(dao)

    row = service.mark_daily_attendance(AttendanceInput())

    assert row["id"] == "attendance-1"
    assert dao.applied_attendance == row


def test_correct_trip_passenger_status_writes_audit_record() -> None:
    dao = FakeAdminDao()
    service = AdminService(dao)

    audit_id = service.correct_trip_passenger_status(CorrectionInput())

    assert audit_id == "audit-1"
    assert dao.audit_record[2]["status"] == "boarded"
    assert dao.audit_record[3] == {"status": "dropped"}


def test_correct_trip_passenger_status_requires_completed_trip() -> None:
    dao = FakeAdminDao()
    dao.trip_passenger["trip_status"] = "active"
    service = AdminService(dao)

    with pytest.raises(ConflictError, match="Only completed trip records can be corrected"):
        service.correct_trip_passenger_status(CorrectionInput())


def test_create_driver_rejects_bad_pin() -> None:
    dao = FakeAdminDao()
    service = AdminService(dao)
    bad_input = DriverInput()
    bad_input.pin = "abc"

    with pytest.raises(BadRequestError, match="Driver PIN must be 4 to 6 digits"):
        service.create_driver(bad_input)
```

- [ ] **Step 5: Run admin service tests and confirm failure**

Run:

```bash
cd backend
pytest tests/services/test_admin_service.py -v
```

Expected: failure because `app.services.admin_service` does not exist.

- [ ] **Step 6: Implement admin service**

Create `backend/app/services/admin_service.py`:

```python
from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.core.security import hash_pin
from app.dao.admin_dao import AdminDao


class AdminService:
    def __init__(self, dao: AdminDao | None = None) -> None:
        self.dao = dao or AdminDao()

    def create_driver(self, input_data):
        try:
            pin_hash = hash_pin(input_data.pin)
        except ValueError as error:
            raise BadRequestError(str(error)) from error
        return self.dao.create_driver(input_data, pin_hash)

    def mark_daily_attendance(self, input_data):
        attendance_row = self.dao.upsert_daily_attendance(input_data)
        self.dao.apply_daily_attendance(attendance_row)
        return attendance_row

    def correct_trip_passenger_status(self, input_data):
        existing = self.dao.get_trip_passenger_for_update(
            input_data.school_id,
            input_data.trip_passenger_id,
        )
        if not existing:
            raise NotFoundError("Trip passenger record not found")
        if existing["trip_status"] != "completed":
            raise ConflictError("Only completed trip records can be corrected")

        original_value = {
            "status": existing["status"],
            "actual_pickup_time": existing["actual_pickup_time"],
            "actual_dropoff_time": existing["actual_dropoff_time"],
        }
        corrected_value = {"status": input_data.corrected_status}
        self.dao.correct_trip_passenger_status(
            input_data.school_id,
            input_data.trip_passenger_id,
            input_data.corrected_status,
        )
        return self.dao.insert_audit_log(
            input_data.school_id,
            input_data.trip_passenger_id,
            original_value,
            corrected_value,
            input_data.reason,
        )
```

- [ ] **Step 7: Add admin routes**

Create `backend/app/api/admin.py` with one route per endpoint listed in the design. Use this route pattern:

```python
from fastapi import APIRouter, HTTPException, Query

from app.core.errors import SafeRideError, to_http_exception
from app.dao.admin_dao import AdminDao
from app.schemas.admin import (
    CorrectTripPassengerStatusRequest,
    CreateBusRequest,
    CreateDriverRequest,
    CreateParentContactRequest,
    CreateParentLinkRequest,
    CreateStudentRequest,
    CreateTripPassengerRequest,
    CreateTripRequest,
    MarkDailyAttendanceRequest,
)
from app.services.admin_service import AdminService

router = APIRouter(prefix="/api/admin", tags=["admin"])
dao = AdminDao()
service = AdminService(dao)


def map_error(error: Exception) -> HTTPException:
    if isinstance(error, SafeRideError):
        return to_http_exception(error)
    return HTTPException(status_code=500, detail="Unexpected backend error")


@router.get("/trips/active")
def list_active_trips(school_id: str = Query(...)):
    return dao.list_active_trips(school_id)


@router.get("/students")
def list_students(school_id: str = Query(...)):
    return dao.list_students(school_id)


@router.get("/buses")
def list_buses(school_id: str = Query(...)):
    return dao.list_buses(school_id)


@router.get("/drivers")
def list_drivers(school_id: str = Query(...)):
    return dao.list_drivers(school_id)


@router.get("/trips")
def list_trips(school_id: str = Query(...)):
    return dao.list_trips(school_id)


@router.get("/trips/completed")
def list_completed_trips(school_id: str = Query(...)):
    return dao.list_completed_trips(school_id)


@router.post("/buses")
def create_bus(request: CreateBusRequest):
    return dao.create_bus(request)


@router.post("/students")
def create_student(request: CreateStudentRequest):
    return dao.create_student(request)


@router.post("/drivers")
def create_driver(request: CreateDriverRequest):
    try:
        return service.create_driver(request)
    except Exception as error:
        raise map_error(error) from error


@router.post("/parent-contacts")
def create_parent_contact(request: CreateParentContactRequest):
    return dao.upsert_parent_contact(request)


@router.post("/parent-links")
def create_parent_link(request: CreateParentLinkRequest):
    return dao.create_parent_link(request)


@router.post("/trips")
def create_trip(request: CreateTripRequest):
    return dao.create_trip(request)


@router.post("/trip-passengers")
def create_trip_passenger(request: CreateTripPassengerRequest):
    return dao.create_trip_passenger(request)


@router.post("/daily-attendance")
def mark_daily_attendance(request: MarkDailyAttendanceRequest):
    try:
        return service.mark_daily_attendance(request)
    except Exception as error:
        raise map_error(error) from error


@router.post("/trip-passenger-corrections")
def correct_trip_passenger_status(request: CorrectTripPassengerStatusRequest):
    try:
        return {"auditId": service.correct_trip_passenger_status(request)}
    except Exception as error:
        raise map_error(error) from error
```

- [ ] **Step 8: Register admin router**

Modify `backend/app/main.py`:

```python
from app.api import admin, health
```

Inside `create_app()` after the health router:

```python
    app.include_router(admin.router)
```

- [ ] **Step 9: Run tests**

Run:

```bash
cd backend
pytest tests/services/test_admin_service.py tests/api/test_health.py -v
```

Expected: pass.

- [ ] **Step 10: Verify admin endpoints against seeded DB**

Run the local API, then:

```bash
curl "http://localhost:8000/api/admin/buses?school_id=11111111-1111-1111-1111-111111111111"
curl "http://localhost:8000/api/admin/students?school_id=11111111-1111-1111-1111-111111111111"
curl "http://localhost:8000/api/admin/trips?school_id=11111111-1111-1111-1111-111111111111"
```

Expected: JSON arrays containing the seeded bus, students, and trip.

- [ ] **Step 11: Commit or record checkpoint**

If inside a git repository, run:

```bash
git add backend/app/dao backend/app/schemas backend/app/services backend/app/api/admin.py backend/app/main.py backend/tests/services/test_admin_service.py
git commit -m "feat: add admin FastAPI layer"
```

If the workspace still has no `.git` directory, record these changed files in the task checkpoint instead of committing.

## Task 5: Driver Backend Session, Trip, Event, Notification Enqueueing, And Tests

**Files:**
- Create: `backend/app/schemas/driver.py`
- Create: `backend/app/dao/driver_dao.py`
- Create: `backend/app/services/driver_service.py`
- Create: `backend/app/api/driver.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/services/test_driver_service.py`

- [ ] **Step 1: Write driver schemas**

Create `backend/app/schemas/driver.py`:

```python
from pydantic import BaseModel, Field


class DriverLoginRequest(BaseModel):
    pin: str


class RecordDriverEventRequest(BaseModel):
    session_token: str = Field(alias="sessionToken")
    trip_id: str = Field(alias="tripId")
    trip_passenger_id: str | None = Field(default=None, alias="tripPassengerId")
    event_type: str = Field(alias="eventType")
    occurred_at: str | None = Field(default=None, alias="occurredAt")
    metadata: dict = Field(default_factory=dict)
```

- [ ] **Step 2: Write driver service tests first**

Create `backend/tests/services/test_driver_service.py`:

```python
import pytest

from app.core.errors import ConflictError, ForbiddenError, UnauthorizedError
from app.core.security import hash_pin
from app.services.driver_service import DriverService


class FakeDriverDao:
    def __init__(self) -> None:
        self.driver = {
            "id": "driver-1",
            "school_id": "school-1",
            "full_name": "Peter Mwangi",
            "pin_hash": hash_pin("1234", salt="demo-driver-salt"),
        }
        self.session = {"driver_id": "driver-1", "school_id": "school-1"}
        self.trip = {"id": "trip-1", "school_id": "school-1", "driver_id": "driver-1", "status": "scheduled"}
        self.passenger = {"id": "tp-1", "status": "pending", "student_id": "student-1"}
        self.inserted_event = None
        self.applied_event = None
        self.enqueued_event = None

    def list_active_drivers(self):
        return [self.driver]

    def create_driver_session(self, school_id: str, driver_id: str, token_hash: str):
        return None

    def get_session(self, session_token: str):
        return self.session if session_token == "valid-session" else None

    def get_assigned_trip(self, school_id: str, driver_id: str, trip_id: str):
        return self.trip

    def get_trip_passenger(self, school_id: str, trip_id: str, trip_passenger_id: str):
        return self.passenger

    def insert_trip_event(self, school_id: str, trip_id: str, trip_passenger_id, event_type: str, driver_id: str, occurred_at, metadata: dict):
        self.inserted_event = (school_id, trip_id, trip_passenger_id, event_type, driver_id, occurred_at, metadata)
        return "event-1"

    def apply_trip_event(self, event_id: str, event_type: str, trip_id: str, school_id: str, trip_passenger_id, occurred_at):
        self.applied_event = (event_id, event_type, trip_id, school_id, trip_passenger_id, occurred_at)

    def enqueue_parent_notifications(self, event_id: str, school_id: str, trip_passenger_id: str, event_type: str, metadata: dict):
        self.enqueued_event = (event_id, school_id, trip_passenger_id, event_type, metadata)


class EventInput:
    session_token = "valid-session"
    trip_id = "trip-1"
    trip_passenger_id = None
    event_type = "trip_started"
    occurred_at = "2026-05-13T06:30:00Z"
    metadata = {}


def test_verify_driver_pin_returns_session() -> None:
    service = DriverService(FakeDriverDao())

    session = service.verify_pin("1234")

    assert session["driverId"] == "driver-1"
    assert session["schoolId"] == "school-1"
    assert session["fullName"] == "Peter Mwangi"
    assert len(session["sessionToken"]) == 64


def test_verify_driver_pin_rejects_unknown_pin() -> None:
    service = DriverService(FakeDriverDao())

    with pytest.raises(UnauthorizedError, match="Invalid driver PIN"):
        service.verify_pin("9999")


def test_record_trip_started_event_for_scheduled_trip() -> None:
    dao = FakeDriverDao()
    service = DriverService(dao)

    event_id = service.record_event(EventInput())

    assert event_id == "event-1"
    assert dao.inserted_event[3] == "trip_started"
    assert dao.applied_event[1] == "trip_started"


def test_passenger_boarding_requires_pending_passenger() -> None:
    dao = FakeDriverDao()
    dao.trip["status"] = "active"
    service = DriverService(dao)
    event = EventInput()
    event.event_type = "passenger_boarded"
    event.trip_passenger_id = "tp-1"

    event_id = service.record_event(event)

    assert event_id == "event-1"
    assert dao.enqueued_event[3] == "passenger_boarded"


def test_driver_cannot_record_unassigned_session() -> None:
    dao = FakeDriverDao()
    service = DriverService(dao)
    event = EventInput()
    event.session_token = "bad-session"

    with pytest.raises(UnauthorizedError, match="Driver session is invalid or expired"):
        service.record_event(event)


def test_driver_event_rejects_invalid_trip_state() -> None:
    dao = FakeDriverDao()
    dao.trip["status"] = "completed"
    service = DriverService(dao)

    with pytest.raises(ConflictError, match="Only scheduled trips can be started"):
        service.record_event(EventInput())
```

- [ ] **Step 3: Run driver service tests and confirm failure**

Run:

```bash
cd backend
pytest tests/services/test_driver_service.py -v
```

Expected: failure because `app.services.driver_service` does not exist.

- [ ] **Step 4: Implement driver service**

Create `backend/app/services/driver_service.py` with:

```python
from app.core.errors import ConflictError, ForbiddenError, UnauthorizedError
from app.core.security import create_session_token, hash_session_token, verify_pin
from app.dao.driver_dao import DriverDao

PASSENGER_EVENT_TYPES = {"passenger_boarded", "passenger_not_present", "passenger_dropped"}
SUPPORTED_DRIVER_EVENT_TYPES = {
    "trip_started",
    "passenger_boarded",
    "passenger_not_present",
    "passenger_dropped",
    "trip_ended",
    "issue_reported",
}


class DriverService:
    def __init__(self, dao: DriverDao | None = None) -> None:
        self.dao = dao or DriverDao()

    def verify_pin(self, pin: str) -> dict:
        matches = [driver for driver in self.dao.list_active_drivers() if verify_pin(pin, driver["pin_hash"])]
        if len(matches) != 1:
            raise UnauthorizedError("Invalid driver PIN")

        driver = matches[0]
        session_token = create_session_token()
        self.dao.create_driver_session(
            driver["school_id"],
            driver["id"],
            hash_session_token(session_token),
        )
        return {
            "id": driver["id"],
            "driverId": driver["id"],
            "schoolId": driver["school_id"],
            "fullName": driver["full_name"],
            "sessionToken": session_token,
        }

    def get_session(self, session_token: str) -> dict:
        session = self.dao.get_session(session_token)
        if not session:
            raise UnauthorizedError("Driver session is invalid or expired")
        return session

    def record_event(self, input_data) -> str:
        if input_data.event_type not in SUPPORTED_DRIVER_EVENT_TYPES:
            raise ConflictError("Unsupported driver event type")

        session = self.get_session(input_data.session_token)
        trip = self.dao.get_assigned_trip(
            session["school_id"],
            session["driver_id"],
            input_data.trip_id,
        )
        if not trip:
            raise ForbiddenError("Trip is not assigned to this driver")

        self._validate_trip_state(input_data.event_type, trip["status"])
        self._validate_passenger_event(input_data, session["school_id"])

        event_id = self.dao.insert_trip_event(
            session["school_id"],
            input_data.trip_id,
            input_data.trip_passenger_id,
            input_data.event_type,
            session["driver_id"],
            input_data.occurred_at,
            input_data.metadata or {},
        )
        self.dao.apply_trip_event(
            event_id,
            input_data.event_type,
            input_data.trip_id,
            session["school_id"],
            input_data.trip_passenger_id,
            input_data.occurred_at,
        )
        if input_data.event_type in PASSENGER_EVENT_TYPES:
            self.dao.enqueue_parent_notifications(
                event_id,
                session["school_id"],
                input_data.trip_passenger_id,
                input_data.event_type,
                input_data.metadata or {},
            )
        return event_id

    def _validate_trip_state(self, event_type: str, trip_status: str) -> None:
        if event_type == "trip_started" and trip_status != "scheduled":
            raise ConflictError("Only scheduled trips can be started")
        if event_type in {"passenger_boarded", "passenger_not_present", "passenger_dropped", "issue_reported"} and trip_status not in {"active", "delayed", "issue_reported"}:
            raise ConflictError("Driver events can only be recorded for active trips")
        if event_type == "trip_ended" and trip_status not in {"active", "delayed", "issue_reported"}:
            raise ConflictError("Only active trips can be completed")

    def _validate_passenger_event(self, input_data, school_id: str) -> None:
        if input_data.event_type not in PASSENGER_EVENT_TYPES:
            return
        if not input_data.trip_passenger_id:
            raise ConflictError("tripPassengerId is required for passenger driver events")
        passenger = self.dao.get_trip_passenger(
            school_id,
            input_data.trip_id,
            input_data.trip_passenger_id,
        )
        if not passenger:
            raise ForbiddenError("Trip passenger cannot be updated for this event")
        valid = (
            input_data.event_type in {"passenger_boarded", "passenger_not_present"} and passenger["status"] == "pending"
        ) or (
            input_data.event_type == "passenger_dropped" and passenger["status"] == "boarded"
        )
        if not valid:
            raise ConflictError("Trip passenger cannot be updated for this event")
```

- [ ] **Step 5: Implement driver DAO SQL**

Create `backend/app/dao/driver_dao.py` with methods:

```text
list_active_drivers(): select id, school_id, full_name, pin_hash from drivers where active = true
create_driver_session(school_id, driver_id, token_hash): insert into driver_sessions (..., expires_at = now() + interval '16 hours')
get_session(session_token): select ds.driver_id, ds.school_id from driver_sessions ds join drivers d ... where token_hash = hash_session_token(session_token), revoked_at is null, expires_at > now()
list_trips_for_today(session_token, service_date): validate session, then select trip summaries with bus label
list_trip_passengers(session_token, trip_id): validate session and assignment, then select passengers excluding absent_admin and alternative_transport
get_assigned_trip(school_id, driver_id, trip_id): select id, school_id, driver_id, status from trips
get_trip_passenger(school_id, trip_id, trip_passenger_id): select id, status, student_id from trip_passengers
insert_trip_event(...): insert into trip_events (...) returning id
apply_trip_event(...): update trips or trip_passengers using the same cases as the old apply_trip_event trigger
enqueue_parent_notifications(...): insert SMS notification_outbox rows for passenger_boarded, passenger_dropped, passenger_not_present without duplicating the same template/phone/passenger
```

Use `hash_session_token()` from `app.core.security` inside `get_session`, `list_trips_for_today`, and `list_trip_passengers`.

- [ ] **Step 6: Add driver routes**

Create `backend/app/api/driver.py`:

```python
from fastapi import APIRouter, HTTPException, Query

from app.core.errors import SafeRideError, to_http_exception
from app.dao.driver_dao import DriverDao
from app.schemas.driver import DriverLoginRequest, RecordDriverEventRequest
from app.services.driver_service import DriverService

router = APIRouter(prefix="/api/driver", tags=["driver"])
dao = DriverDao()
service = DriverService(dao)


def map_error(error: Exception) -> HTTPException:
    if isinstance(error, SafeRideError):
        return to_http_exception(error)
    return HTTPException(status_code=500, detail="Unexpected backend error")


@router.post("/login")
def login(request: DriverLoginRequest):
    try:
        return service.verify_pin(request.pin)
    except Exception as error:
        raise map_error(error) from error


@router.get("/trips/today")
def list_trips_today(session_token: str = Query(...), service_date: str = Query(...)):
    try:
        return dao.list_trips_for_today(session_token, service_date)
    except Exception as error:
        raise map_error(error) from error


@router.get("/trips/{trip_id}/passengers")
def list_trip_passengers(trip_id: str, session_token: str = Query(...)):
    try:
        return dao.list_trip_passengers(session_token, trip_id)
    except Exception as error:
        raise map_error(error) from error


@router.post("/events")
def record_event(request: RecordDriverEventRequest):
    try:
        return {"eventId": service.record_event(request)}
    except Exception as error:
        raise map_error(error) from error
```

- [ ] **Step 7: Register driver router**

Modify `backend/app/main.py`:

```python
from app.api import admin, driver, health
```

Inside `create_app()` after the admin router:

```python
    app.include_router(driver.router)
```

- [ ] **Step 8: Run driver tests**

Run:

```bash
cd backend
pytest tests/services/test_driver_service.py -v
```

Expected: pass.

- [ ] **Step 9: Verify driver API with seeded DB**

Start the local API, then run:

```bash
curl -X POST "http://localhost:8000/api/driver/login" \
  -H "Content-Type: application/json" \
  -d '{"pin":"1234"}'
```

Expected: JSON includes `driverId`, `schoolId`, `fullName`, and `sessionToken`.

Use the returned token:

```bash
curl "http://localhost:8000/api/driver/trips/today?session_token=RETURNED_TOKEN&service_date=$(date +%F)"
```

Expected: JSON includes `Morning Route A`.

- [ ] **Step 10: Commit or record checkpoint**

If inside a git repository, run:

```bash
git add backend/app/schemas/driver.py backend/app/dao/driver_dao.py backend/app/services/driver_service.py backend/app/api/driver.py backend/app/main.py backend/tests/services/test_driver_service.py
git commit -m "feat: add driver FastAPI layer"
```

If the workspace still has no `.git` directory, record these changed files in the task checkpoint instead of committing.

## Task 6: Parent Backend Progress, Push Subscriptions, And Tests

**Files:**
- Create: `backend/app/schemas/parent.py`
- Create: `backend/app/dao/parent_dao.py`
- Create: `backend/app/services/parent_service.py`
- Create: `backend/app/api/parent.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/services/test_parent_service.py`

- [ ] **Step 1: Write parent schemas**

Create `backend/app/schemas/parent.py`:

```python
from pydantic import BaseModel


class PushSubscriptionKeys(BaseModel):
    p256dh: str
    auth: str


class PushSubscription(BaseModel):
    endpoint: str
    keys: PushSubscriptionKeys


class RegisterPushSubscriptionRequest(BaseModel):
    token: str
    subscription: PushSubscription
```

- [ ] **Step 2: Write parent service tests first**

Create `backend/tests/services/test_parent_service.py`:

```python
import pytest

from app.core.errors import ForbiddenError
from app.services.parent_service import ParentService


class FakeParentDao:
    def __init__(self) -> None:
        self.parent_link = {"id": "link-1", "school_id": "school-1", "student_id": "student-1"}
        self.trip = {
            "id": "trip-1",
            "name": "Morning Route A",
            "session": "morning",
            "service_date": "2026-05-13",
            "scheduled_start": "06:30:00",
            "status": "active",
        }
        self.passengers = [
            {"id": "tp-1", "student_id": "student-1", "student_name": "Amina Otieno", "location_label": "Kilimani stop", "sequence_position": 1, "estimated_minutes_from_start": 5, "status": "boarded"},
            {"id": "tp-2", "student_id": "student-2", "student_name": "Brian Mwangi", "location_label": "Lavington stop", "sequence_position": 2, "estimated_minutes_from_start": 11, "status": "pending"},
        ]
        self.subscription = None

    def get_parent_link(self, token: str):
        return self.parent_link if token == "good-token" else None

    def get_active_trip_for_student_today(self, school_id: str, student_id: str):
        return self.trip

    def list_parent_progress_passengers(self, school_id: str, trip_id: str):
        return self.passengers

    def upsert_push_subscription(self, school_id: str, parent_link_id: str, endpoint: str, p256dh: str, auth: str):
        self.subscription = (school_id, parent_link_id, endpoint, p256dh, auth)


def test_parent_progress_hides_other_student_names() -> None:
    service = ParentService(FakeParentDao())

    progress = service.get_trip_progress("good-token")

    assert progress["ownStudentId"] == "student-1"
    assert progress["passengers"][0]["studentName"] == "Amina Otieno"
    assert progress["passengers"][1]["studentName"] is None
    assert progress["passengers"][1]["locationLabel"] == "Stop 2"


def test_parent_progress_rejects_bad_token() -> None:
    service = ParentService(FakeParentDao())

    with pytest.raises(ForbiddenError, match="Invalid or revoked parent link"):
        service.get_trip_progress("bad-token")


def test_register_push_subscription_uses_parent_link_school() -> None:
    dao = FakeParentDao()
    service = ParentService(dao)

    service.register_push_subscription("good-token", "https://push.example/sub", "p256dh", "auth")

    assert dao.subscription == ("school-1", "link-1", "https://push.example/sub", "p256dh", "auth")
```

- [ ] **Step 3: Run parent service tests and confirm failure**

Run:

```bash
cd backend
pytest tests/services/test_parent_service.py -v
```

Expected: failure because `app.services.parent_service` does not exist.

- [ ] **Step 4: Implement parent service**

Create `backend/app/services/parent_service.py`:

```python
from app.core.errors import BadRequestError, ForbiddenError
from app.dao.parent_dao import ParentDao


class ParentService:
    def __init__(self, dao: ParentDao | None = None) -> None:
        self.dao = dao or ParentDao()

    def get_trip_progress(self, token: str) -> dict:
        parent_link = self.dao.get_parent_link(token)
        if not parent_link:
            raise ForbiddenError("Invalid or revoked parent link")

        trip = self.dao.get_active_trip_for_student_today(
            parent_link["school_id"],
            parent_link["student_id"],
        )
        if not trip:
            return {"ownStudentId": parent_link["student_id"], "trip": None, "passengers": []}

        passengers = [
            self._to_parent_safe_passenger(row, parent_link["student_id"])
            for row in self.dao.list_parent_progress_passengers(parent_link["school_id"], trip["id"])
        ]
        return {
            "ownStudentId": parent_link["student_id"],
            "trip": {
                "id": trip["id"],
                "name": trip["name"],
                "session": trip["session"],
                "serviceDate": str(trip["service_date"]),
                "scheduledStart": str(trip["scheduled_start"]),
                "status": trip["status"],
            },
            "passengers": passengers,
        }

    def register_push_subscription(self, token: str, endpoint: str, p256dh: str, auth: str) -> dict:
        if not endpoint.startswith("https://"):
            raise BadRequestError("Invalid subscription endpoint")
        parent_link = self.dao.get_parent_link(token)
        if not parent_link:
            raise ForbiddenError("Invalid or revoked parent link")
        self.dao.upsert_push_subscription(parent_link["school_id"], parent_link["id"], endpoint, p256dh, auth)
        return {"ok": True}

    def _to_parent_safe_passenger(self, row: dict, own_student_id: str) -> dict:
        is_own_child = row["student_id"] == own_student_id
        return {
            "id": row["id"],
            "studentId": row["student_id"] if is_own_child else None,
            "studentName": row["student_name"] if is_own_child else None,
            "locationLabel": row["location_label"] if is_own_child else f"Stop {row['sequence_position']}",
            "sequencePosition": row["sequence_position"],
            "estimatedMinutesFromStart": row["estimated_minutes_from_start"],
            "status": row["status"],
        }
```

- [ ] **Step 5: Implement parent DAO SQL**

Create `backend/app/dao/parent_dao.py` with methods:

```text
get_parent_link(token): select id, school_id, student_id from parent_links where token = %s and revoked_at is null
get_active_trip_for_student_today(school_id, student_id): select today's scheduled/active/delayed/issue_reported trip containing student, ordered active, delayed, issue_reported, scheduled, scheduled_start desc
list_parent_progress_passengers(school_id, trip_id): select trip_passengers with student names and location labels ordered by sequence_position
upsert_push_subscription(school_id, parent_link_id, endpoint, p256dh, auth): insert into push_subscriptions ... on conflict (parent_link_id, endpoint) do update set p256dh = excluded.p256dh, auth = excluded.auth
```

The trip lookup must use Postgres `current_date` for the local seed flow.

- [ ] **Step 6: Add parent routes**

Create `backend/app/api/parent.py`:

```python
from fastapi import APIRouter, HTTPException

from app.core.errors import SafeRideError, to_http_exception
from app.schemas.parent import RegisterPushSubscriptionRequest
from app.services.parent_service import ParentService

router = APIRouter(prefix="/api/parent", tags=["parent"])
service = ParentService()


def map_error(error: Exception) -> HTTPException:
    if isinstance(error, SafeRideError):
        return to_http_exception(error)
    return HTTPException(status_code=500, detail="Unexpected backend error")


@router.get("/trips/{token}")
def get_trip_progress(token: str):
    try:
        return service.get_trip_progress(token)
    except Exception as error:
        raise map_error(error) from error


@router.post("/push-subscriptions")
def register_push_subscription(request: RegisterPushSubscriptionRequest):
    try:
        return service.register_push_subscription(
            request.token,
            request.subscription.endpoint,
            request.subscription.keys.p256dh,
            request.subscription.keys.auth,
        )
    except Exception as error:
        raise map_error(error) from error
```

- [ ] **Step 7: Register parent router**

Modify `backend/app/main.py`:

```python
from app.api import admin, driver, health, parent
```

Inside `create_app()` after the driver router:

```python
    app.include_router(parent.router)
```

- [ ] **Step 8: Run parent tests**

Run:

```bash
cd backend
pytest tests/services/test_parent_service.py -v
```

Expected: pass.

- [ ] **Step 9: Verify parent endpoint**

Start the local API, then run:

```bash
curl "http://localhost:8000/api/parent/trips/demo-parent-token-00000000000000000001"
```

Expected: JSON includes `ownStudentId`, `trip`, and three passengers. Only Amina Otieno has a `studentName`.

- [ ] **Step 10: Commit or record checkpoint**

If inside a git repository, run:

```bash
git add backend/app/schemas/parent.py backend/app/dao/parent_dao.py backend/app/services/parent_service.py backend/app/api/parent.py backend/app/main.py backend/tests/services/test_parent_service.py
git commit -m "feat: add parent FastAPI layer"
```

If the workspace still has no `.git` directory, record these changed files in the task checkpoint instead of committing.

## Task 7: Notification Processor API And Tests

**Files:**
- Create: `backend/app/schemas/notifications.py`
- Create: `backend/app/dao/notification_dao.py`
- Create: `backend/app/services/notification_service.py`
- Create: `backend/app/api/notifications.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/services/test_notification_service.py`

- [ ] **Step 1: Write notification schema**

Create `backend/app/schemas/notifications.py`:

```python
from pydantic import BaseModel


class ProcessNotificationsResponse(BaseModel):
    processed: int
```

- [ ] **Step 2: Write notification service tests first**

Create `backend/tests/services/test_notification_service.py`:

```python
from app.services.notification_service import NotificationService


class FakeNotificationDao:
    def __init__(self) -> None:
        self.messages = [
            {"id": "sms-1", "channel": "sms", "recipient_phone": "+254700000101", "payload": {"body": "Hello"}, "attempts": 0},
            {"id": "push-1", "channel": "push", "recipient_phone": None, "payload": {"body": "Hello"}, "attempts": 0},
        ]
        self.claims = []
        self.sent = []
        self.skipped = []

    def recover_stale_claims(self):
        return None

    def list_pending_messages(self, limit: int):
        return self.messages

    def claim_message(self, message_id: str, attempts: int):
        self.claims.append((message_id, attempts))
        return next(message for message in self.messages if message["id"] == message_id)

    def mark_sent(self, message_id: str):
        self.sent.append(message_id)

    def mark_skipped(self, message_id: str, reason: str):
        self.skipped.append((message_id, reason))

    def mark_failed_or_retry(self, message_id: str, attempts: int, error: str):
        raise AssertionError("No failure expected in this test")


def test_process_notifications_sends_sms_and_skips_push_without_provider() -> None:
    dao = FakeNotificationDao()
    service = NotificationService(dao, simulate_sms=True)

    result = service.process_pending()

    assert result == {"processed": 2}
    assert dao.sent == ["sms-1"]
    assert dao.skipped == [("push-1", "Push delivery is not implemented in local processor")]
```

- [ ] **Step 3: Run notification tests and confirm failure**

Run:

```bash
cd backend
pytest tests/services/test_notification_service.py -v
```

Expected: failure because `app.services.notification_service` does not exist.

- [ ] **Step 4: Implement notification service**

Create `backend/app/services/notification_service.py`:

```python
from app.dao.notification_dao import NotificationDao


class NotificationService:
    def __init__(self, dao: NotificationDao | None = None, simulate_sms: bool = True) -> None:
        self.dao = dao or NotificationDao()
        self.simulate_sms = simulate_sms

    def process_pending(self, limit: int = 50) -> dict[str, int]:
        self.dao.recover_stale_claims()
        processed = 0

        for message in self.dao.list_pending_messages(limit):
            attempts = int(message.get("attempts") or 0) + 1
            claimed = self.dao.claim_message(message["id"], attempts)
            if not claimed:
                continue

            processed += 1
            if claimed["channel"] == "sms":
                self._send_sms(claimed)
                self.dao.mark_sent(claimed["id"])
            elif claimed["channel"] == "push":
                self.dao.mark_skipped(claimed["id"], "Push delivery is not implemented in local processor")
            else:
                self.dao.mark_skipped(claimed["id"], f"{claimed['channel']} delivery is not implemented in local processor")

        return {"processed": processed}

    def _send_sms(self, message: dict) -> None:
        if self.simulate_sms:
            return
        raise RuntimeError("Real SMS delivery is not configured")
```

- [ ] **Step 5: Implement notification DAO SQL**

Create `backend/app/dao/notification_dao.py` with methods:

```text
recover_stale_claims(): set processing rows older than 5 minutes back to pending when attempts < 3; set them failed when attempts >= 3
list_pending_messages(limit): select id, channel, recipient_phone, payload, attempts from notification_outbox where status = 'pending' and attempts < 3 order by created_at asc limit %s
claim_message(message_id, attempts): update notification_outbox set status='processing', attempts=%s, claimed_at=now(), last_error=null where id=%s and status='pending' returning id, channel, recipient_phone, payload, attempts
mark_sent(message_id): update notification_outbox set status='sent', sent_at=now(), claimed_at=null, last_error=null where id=%s
mark_skipped(message_id, reason): update notification_outbox set status='skipped', claimed_at=null, last_error=%s where id=%s
mark_failed_or_retry(message_id, attempts, error): update status to failed when attempts >= 3 else pending, set last_error and clear claimed_at
```

- [ ] **Step 6: Add notification route**

Create `backend/app/api/notifications.py`:

```python
from fastapi import APIRouter

from app.services.notification_service import NotificationService

router = APIRouter(prefix="/api/notifications", tags=["notifications"])
service = NotificationService()


@router.post("/process")
def process_notifications():
    return service.process_pending()
```

- [ ] **Step 7: Register notification router**

Modify `backend/app/main.py`:

```python
from app.api import admin, driver, health, notifications, parent
```

Inside `create_app()` after the parent router:

```python
    app.include_router(notifications.router)
```

- [ ] **Step 8: Run notification tests**

Run:

```bash
cd backend
pytest tests/services/test_notification_service.py -v
```

Expected: pass.

- [ ] **Step 9: Verify notification endpoint with seed data**

Start the local API, then run:

```bash
curl -X POST "http://localhost:8000/api/notifications/process"
docker compose -f docker-compose.local.yml exec -T db psql -U saferide -d saferide -c "select channel, status from notification_outbox order by id;"
```

Expected: response `{"processed":2}`. The seeded SMS row is `sent`; the seeded push row is `skipped`.

- [ ] **Step 10: Commit or record checkpoint**

If inside a git repository, run:

```bash
git add backend/app/schemas/notifications.py backend/app/dao/notification_dao.py backend/app/services/notification_service.py backend/app/api/notifications.py backend/app/main.py backend/tests/services/test_notification_service.py
git commit -m "feat: add notification processor"
```

If the workspace still has no `.git` directory, record these changed files in the task checkpoint instead of committing.

## Task 8: Frontend HTTP Client And Service Migration

**Files:**
- Create: `src/services/httpClient.ts`
- Modify: `src/services/adminApi.ts`
- Modify: `src/services/driverApi.ts`
- Modify: `src/services/parentApi.ts`
- Delete: `src/lib/supabase.ts`
- Modify: `package.json`
- Create: `tests/unit/httpClient.test.ts`

- [ ] **Step 1: Write HTTP client test first**

Create `tests/unit/httpClient.test.ts`:

```ts
import { describe, expect, it, vi } from "vitest";
import { apiGet, apiPost } from "../../src/services/httpClient";

describe("httpClient", () => {
  it("adds query params and returns parsed JSON", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify([{ id: "1" }]), { status: 200 }));

    await expect(apiGet("/api/admin/buses", { school_id: "school-1" })).resolves.toEqual([
      { id: "1" },
    ]);
    expect(fetchMock.mock.calls[0][0].toString()).toContain("school_id=school-1");
    fetchMock.mockRestore();
  });

  it("throws API error detail when request fails", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "Invalid driver PIN" }), { status: 401 }));

    await expect(apiPost("/api/driver/login", { pin: "9999" })).rejects.toThrow("Invalid driver PIN");
    fetchMock.mockRestore();
  });
});
```

- [ ] **Step 2: Run HTTP client test and confirm failure**

Run:

```bash
npm test -- tests/unit/httpClient.test.ts
```

Expected: failure because `src/services/httpClient.ts` does not exist.

- [ ] **Step 3: Implement frontend HTTP client**

Create `src/services/httpClient.ts`:

```ts
const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, "") ?? "http://localhost:8000";

type QueryParams = Record<string, string | number | boolean | null | undefined>;

function buildUrl(path: string, query?: QueryParams) {
  const url = new URL(path, API_BASE_URL);

  Object.entries(query ?? {}).forEach(([key, value]) => {
    if (value !== null && value !== undefined && value !== "") {
      url.searchParams.set(key, String(value));
    }
  });

  return url;
}

async function parseResponse(response: Response) {
  const text = await response.text();
  const body = text ? JSON.parse(text) : null;

  if (!response.ok) {
    const message =
      body && typeof body.detail === "string"
        ? body.detail
        : `Request failed with status ${response.status}`;
    throw new Error(message);
  }

  return body;
}

export async function apiGet(path: string, query?: QueryParams) {
  const response = await fetch(buildUrl(path, query));
  return parseResponse(response);
}

export async function apiPost(path: string, body?: unknown) {
  const response = await fetch(buildUrl(path), {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(body ?? {})
  });

  return parseResponse(response);
}
```

- [ ] **Step 4: Migrate admin service**

Modify `src/services/adminApi.ts`:

```ts
import { apiGet, apiPost } from "./httpClient";
```

Replace each Supabase function body with these calls:

```ts
export async function listActiveTrips(schoolId: string) {
  return apiGet("/api/admin/trips/active", { school_id: schoolId });
}

export async function listStudents(schoolId: string) {
  return apiGet("/api/admin/students", { school_id: schoolId });
}

export async function listBuses(schoolId: string) {
  return apiGet("/api/admin/buses", { school_id: schoolId });
}

export async function listDrivers(schoolId: string) {
  return apiGet("/api/admin/drivers", { school_id: schoolId });
}

export async function listTrips(schoolId: string) {
  return apiGet("/api/admin/trips", { school_id: schoolId });
}

export async function listCompletedTrips(schoolId: string) {
  return apiGet("/api/admin/trips/completed", { school_id: schoolId });
}

export async function createBus(input: CreateBusInput) {
  return apiPost("/api/admin/buses", input);
}

export async function createStudent(input: CreateStudentInput) {
  return apiPost("/api/admin/students", input);
}

export async function createDriver(input: CreateDriverInput) {
  return apiPost("/api/admin/drivers", input);
}

export async function createParentContact(input: CreateParentContactInput) {
  return apiPost("/api/admin/parent-contacts", input);
}

export async function createParentLink(input: CreateParentLinkInput) {
  return apiPost("/api/admin/parent-links", input);
}

export async function createTrip(input: CreateTripInput) {
  return apiPost("/api/admin/trips", input);
}

export async function createTripPassenger(input: CreateTripPassengerInput) {
  return apiPost("/api/admin/trip-passengers", input);
}

export async function markDailyAttendance(input: MarkDailyAttendanceInput) {
  return apiPost("/api/admin/daily-attendance", input);
}

export async function correctTripPassengerStatus(input: CorrectTripPassengerStatusInput) {
  return apiPost("/api/admin/trip-passenger-corrections", input);
}
```

Remove the `supabase` import and remove the old `supabase.auth.getUser()` call.

- [ ] **Step 5: Migrate driver service**

Modify `src/services/driverApi.ts`:

```ts
import { apiGet, apiPost } from "./httpClient";
```

Replace Supabase calls:

```ts
export async function verifyDriverPin(pin: string): Promise<DriverSession> {
  return parseDriverSession(await apiPost("/api/driver/login", { pin }));
}

export async function getDriverTripsForToday(
  sessionToken: string,
  serviceDate: string
): Promise<DriverTripSummary[]> {
  const data = await apiGet("/api/driver/trips/today", {
    session_token: sessionToken,
    service_date: serviceDate
  });

  if (!Array.isArray(data)) {
    throw new Error("Invalid driver trips response: expected a list.");
  }

  return data.map(parseDriverTrip);
}

export async function getDriverTripPassengers(
  sessionToken: string,
  tripId: string
): Promise<DriverTripPassenger[]> {
  const data = await apiGet(`/api/driver/trips/${tripId}/passengers`, {
    session_token: sessionToken
  });

  if (!Array.isArray(data)) {
    throw new Error("Invalid driver trip passengers response: expected a list.");
  }

  return data.map(parseDriverTripPassenger);
}

export async function recordDriverEvent(input: RecordDriverEventInput) {
  if (!isDriverEventType(input.eventType)) {
    throw new Error(`Unsupported driver event type: ${input.eventType}`);
  }

  if (
    passengerEventTypes.includes(input.eventType) &&
    !input.tripPassengerId
  ) {
    throw new Error(
      `tripPassengerId is required for ${input.eventType} driver events.`
    );
  }

  return apiPost("/api/driver/events", input);
}
```

Remove the `supabase` import.

- [ ] **Step 6: Migrate parent service**

Modify `src/services/parentApi.ts`:

```ts
import { apiGet } from "./httpClient";
```

Replace `getParentTripByToken`:

```ts
export async function getParentTripByToken(
  token: string
): Promise<ParentTripProgress> {
  return parseParentTripProgress(await apiGet(`/api/parent/trips/${token}`));
}
```

Remove the `supabase` import.

- [ ] **Step 7: Remove Supabase client and package dependency**

Run:

```bash
rg "supabase" src package.json
```

Expected: only package references remain. Then delete:

```text
src/lib/supabase.ts
```

Modify `package.json` to remove:

```json
"@supabase/supabase-js": "^2.45.4"
```

- [ ] **Step 8: Run frontend checks**

Run:

```bash
npm test -- tests/unit/httpClient.test.ts tests/unit/ParentTrip.test.tsx tests/unit/privacy.test.ts
npm run build
```

Expected: tests pass and build succeeds.

- [ ] **Step 9: Commit or record checkpoint**

If inside a git repository, run:

```bash
git add src/services/httpClient.ts src/services/adminApi.ts src/services/driverApi.ts src/services/parentApi.ts package.json tests/unit/httpClient.test.ts
git rm src/lib/supabase.ts
git commit -m "feat: migrate frontend services to FastAPI"
```

If the workspace still has no `.git` directory, remove `src/lib/supabase.ts` manually and record these changed files in the task checkpoint instead of committing.

## Task 9: Local Full-Flow Verification And E2E Test

**Files:**
- Modify: `tests/e2e/admin-driver-parent.spec.ts`
- Modify: `README.md`

- [ ] **Step 1: Update E2E test for seeded local flow**

Modify `tests/e2e/admin-driver-parent.spec.ts`:

```ts
import { expect, test } from "@playwright/test";

test("seeded local admin, driver, and parent flow", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("School ID").fill("11111111-1111-1111-1111-111111111111");
  await expect(page.getByRole("heading", { name: "Live Fleet" })).toBeVisible();

  await page.goto("/driver");
  await page.getByLabel("Enter your school PIN").fill("1234");
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page).toHaveURL(/\/driver\/trips/);
  await expect(page.getByText("Morning Route A")).toBeVisible();

  await page.goto("/p/demo-parent-token-00000000000000000001");
  await expect(page.getByRole("heading", { name: "SafeRide" })).toBeVisible();
  await expect(page.getByText("Amina Otieno")).toBeVisible();
  await expect(page.getByText("Brian Mwangi")).toHaveCount(0);
});
```

- [ ] **Step 2: Run complete local stack**

In one terminal:

```bash
scripts/reset-local-db.sh
scripts/start-local.sh
```

In a second terminal:

```bash
cp .env.local.example .env.local
npm install
npm run dev -- --host 0.0.0.0
```

Expected:

```text
FastAPI runs at http://localhost:8000
Vite runs at http://localhost:5173
```

- [ ] **Step 3: Verify API health and seeded browser data**

Run:

```bash
curl "http://localhost:8000/api/health"
curl "http://localhost:8000/api/parent/trips/demo-parent-token-00000000000000000001"
```

Expected:

```text
{"status":"ok"}
Parent progress JSON includes Amina Otieno and does not include other student names.
```

- [ ] **Step 4: Run automated checks**

Run:

```bash
cd backend
pytest -v
cd ..
npm test
npm run build
npm run e2e
```

Expected: all backend tests, frontend unit tests, TypeScript build, Vite build, and Playwright tests pass.

- [ ] **Step 5: Update README verification notes**

Add a short "Verified local migration flow" note to `README.md`:

```markdown
## Verified Local Migration Flow

The local FastAPI/Postgres stack is ready when:

- `curl http://localhost:8000/api/health` returns `{"status":"ok"}`.
- The seeded driver PIN `1234` opens the driver trip list.
- The seeded parent token `/p/demo-parent-token-00000000000000000001` shows only the parent's child by name.
- `npm run build`, `npm test`, backend `pytest`, and `npm run e2e` pass.
```

- [ ] **Step 6: Commit or record final checkpoint**

If inside a git repository, run:

```bash
git add tests/e2e/admin-driver-parent.spec.ts README.md
git commit -m "test: verify local FastAPI migration flow"
```

If the workspace still has no `.git` directory, record these changed files in the final checkpoint instead of committing.

## Final Verification

Run the full verification sequence:

```bash
scripts/reset-local-db.sh
curl "http://localhost:8000/api/health"
cd backend
pytest -v
cd ..
npm test
npm run build
npm run e2e
```

Expected:

```text
Local DB resets and seeds successfully.
Health endpoint returns {"status":"ok"}.
All backend tests pass.
All frontend unit tests pass.
The frontend builds successfully.
The Playwright seeded local flow passes.
```

## Spec Coverage Checklist

- Backend skeleton and layered structure: Tasks 1, 4, 5, 6, 7.
- Postgres schema without Supabase Auth/RLS: Task 3.
- Deterministic demo seed data: Task 3.
- Dockerfile, Postgres runtime, and shell scripts: Task 2.
- Admin API without auth: Task 4.
- Driver PIN sessions and trip events: Task 5.
- Parent token privacy: Task 6.
- Notification processing: Task 7.
- Frontend service migration to FastAPI: Task 8.
- Local full-flow verification: Task 9.
