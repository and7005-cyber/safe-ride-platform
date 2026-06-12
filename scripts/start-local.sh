#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$ROOT_DIR/docker-compose.local.yml"
BACKEND_ENV_FILE="$ROOT_DIR/backend/.env"
BACKEND_ENV_EXAMPLE="$ROOT_DIR/backend/.env.example"
FRONTEND_ENV_FILE="$ROOT_DIR/frontend/.env.local"
FRONTEND_ENV_EXAMPLE="$ROOT_DIR/frontend/.env.local.example"
FRONTEND_DIR="$ROOT_DIR/frontend"
MIGRATIONS_DIR="$ROOT_DIR/backend/db/migrations"
SEEDS_DIR="$ROOT_DIR/backend/db/seeds"
MIGRATION_MARKER_TABLE="saferide_local_migrations"
MAX_DB_WAIT_SECONDS=60
MAX_HTTP_WAIT_SECONDS=60
LOCAL_STATE_DIR="$ROOT_DIR/.local"
FRONTEND_LOG_FILE="$LOCAL_STATE_DIR/frontend.log"
FRONTEND_PID_FILE="$LOCAL_STATE_DIR/frontend.pid"
RESET_DB=false

if [ "${1:-}" = "--reset" ]; then
  RESET_DB=true
fi

cd "$ROOT_DIR"

if [ ! -f "$BACKEND_ENV_FILE" ]; then
  if [ ! -f "$BACKEND_ENV_EXAMPLE" ]; then
    echo "Missing backend env template at $BACKEND_ENV_EXAMPLE." >&2
    exit 1
  fi

  cp "$BACKEND_ENV_EXAMPLE" "$BACKEND_ENV_FILE"
  echo "Created backend/.env from backend/.env.example."
fi

if [ ! -f "$FRONTEND_ENV_FILE" ]; then
  if [ ! -f "$FRONTEND_ENV_EXAMPLE" ]; then
    echo "Missing frontend env template at $FRONTEND_ENV_EXAMPLE." >&2
    exit 1
  fi

  cp "$FRONTEND_ENV_EXAMPLE" "$FRONTEND_ENV_FILE"
  echo "Created frontend/.env.local from frontend/.env.local.example."
fi

set -a
# shellcheck disable=SC1090
source "$BACKEND_ENV_FILE"
# shellcheck disable=SC1090
source "$FRONTEND_ENV_FILE"
set +a

POSTGRES_DB="${POSTGRES_DB:-saferide}"
POSTGRES_USER="${POSTGRES_USER:-saferide}"
API_PORT="${API_PORT:-9001}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
VITE_API_BASE_URL="${VITE_API_BASE_URL:-http://localhost:${API_PORT}}"
API_HEALTH_URL="http://localhost:${API_PORT}/api/health"
FRONTEND_URL="http://localhost:${FRONTEND_PORT}/"
NPM_BIN="$ROOT_DIR/.tools/bin/npm"

if [ ! -x "$NPM_BIN" ]; then
  NPM_BIN="$(command -v npm || true)"
fi

if [ -z "$NPM_BIN" ]; then
  echo "Could not find npm. Install Node/npm or restore .tools/bin/npm." >&2
  exit 1
fi

mkdir -p "$LOCAL_STATE_DIR"

wait_for_database() {
  local waited_seconds=0

  while ! docker compose -f "$COMPOSE_FILE" exec -T db pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1; do
    if [ "$waited_seconds" -ge "$MAX_DB_WAIT_SECONDS" ]; then
      echo "Database did not become ready within ${MAX_DB_WAIT_SECONDS} seconds." >&2
      docker compose -f "$COMPOSE_FILE" logs db >&2 || true
      exit 1
    fi

    sleep 1
    waited_seconds=$((waited_seconds + 1))
  done
}

wait_for_http() {
  local url="$1"
  local name="$2"
  local waited_seconds=0

  while ! curl -fsS "$url" >/dev/null 2>&1; do
    if [ "$waited_seconds" -ge "$MAX_HTTP_WAIT_SECONDS" ]; then
      echo "$name did not become ready within ${MAX_HTTP_WAIT_SECONDS} seconds at $url." >&2
      exit 1
    fi

    sleep 1
    waited_seconds=$((waited_seconds + 1))
  done
}

apply_migrations() {
  if [ ! -d "$MIGRATIONS_DIR" ]; then
    echo "Cannot initialize local database: migrations directory is missing at $MIGRATIONS_DIR." >&2
    exit 1
  fi

  docker compose -f "$COMPOSE_FILE" exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" <<SQL
create table if not exists ${MIGRATION_MARKER_TABLE} (id text primary key, applied_at timestamptz not null default now());
SQL

  for migration_path in "$MIGRATIONS_DIR"/*.sql; do
    if [ ! -f "$migration_path" ]; then
      echo "No migration files found in $MIGRATIONS_DIR." >&2
      exit 1
    fi

    migration_id="$(basename "$migration_path" .sql)"
    already_applied="$(
      docker compose -f "$COMPOSE_FILE" exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc \
        "select exists (select 1 from ${MIGRATION_MARKER_TABLE} where id = '${migration_id}');"
    )"

    if [ "$already_applied" = "t" ]; then
      echo "Migration $migration_id already applied."
    else
      echo "Applying local database migration $migration_id..."
      docker compose -f "$COMPOSE_FILE" exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" < "$migration_path"
      docker compose -f "$COMPOSE_FILE" exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
        "insert into ${MIGRATION_MARKER_TABLE} (id) values ('${migration_id}') on conflict (id) do nothing;"
    fi
  done
}

apply_seed() {
  if [ "${APP_ENV:-}" != "local" ]; then
    echo "Refusing to apply demo seeds: APP_ENV is '${APP_ENV:-unset}', not 'local'." >&2
    echo "Demo credentials must never reach a non-local environment." >&2
    exit 1
  fi

  if [ ! -d "$SEEDS_DIR" ]; then
    echo "Cannot initialize local database: seeds directory is missing at $SEEDS_DIR." >&2
    exit 1
  fi

  for seed_path in "$SEEDS_DIR"/*.sql; do
    if [ ! -f "$seed_path" ]; then
      echo "No seed files found in $SEEDS_DIR." >&2
      exit 1
    fi

    echo "Applying local database seed $(basename "$seed_path")..."
    { echo "set saferide.allow_demo_seed = 'yes';"; cat "$seed_path"; } | \
      docker compose -f "$COMPOSE_FILE" exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1
  done
}

apply_migration_and_seed() {
  apply_migrations
  apply_seed
}

start_frontend() {
  if curl -fsS "$FRONTEND_URL" >/dev/null 2>&1; then
    echo "Frontend already running at $FRONTEND_URL"
    return
  fi

  if [ -f "$FRONTEND_PID_FILE" ]; then
    local existing_pid
    existing_pid="$(cat "$FRONTEND_PID_FILE")"

    if [ -n "$existing_pid" ] && kill -0 "$existing_pid" >/dev/null 2>&1; then
      echo "Frontend process $existing_pid is already running; waiting for $FRONTEND_URL"
      wait_for_http "$FRONTEND_URL" "Frontend"
      return
    fi
  fi

  echo "Starting frontend at $FRONTEND_URL with API $VITE_API_BASE_URL"
  (
    cd "$FRONTEND_DIR"
    nohup env VITE_API_BASE_URL="$VITE_API_BASE_URL" "$NPM_BIN" run dev -- --host 0.0.0.0 --port "$FRONTEND_PORT"
  ) > "$FRONTEND_LOG_FILE" 2>&1 < /dev/null &

  local frontend_pid="$!"
  echo "$frontend_pid" > "$FRONTEND_PID_FILE"
  disown "$frontend_pid" >/dev/null 2>&1 || true
  wait_for_http "$FRONTEND_URL" "Frontend"
}

echo "Starting Postgres using backend/.env..."
docker compose -f "$COMPOSE_FILE" up -d db
wait_for_database

if [ "$RESET_DB" = true ]; then
  echo "Resetting local database before seeding..."
  docker compose -f "$COMPOSE_FILE" exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" <<'SQL'
drop schema public cascade;
create schema public;
SQL
  apply_migration_and_seed
else
  marker_exists="$(
    docker compose -f "$COMPOSE_FILE" exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc \
      "select to_regclass('public.${MIGRATION_MARKER_TABLE}') is not null;"
  )"

  apply_migration_and_seed
fi

echo "Starting backend API at $API_HEALTH_URL..."
docker compose -f "$COMPOSE_FILE" up -d --build api
wait_for_http "$API_HEALTH_URL" "Backend API"

start_frontend

echo "SafeRide local stack is ready."
echo "Frontend: $FRONTEND_URL"
echo "Backend:  $API_HEALTH_URL"
