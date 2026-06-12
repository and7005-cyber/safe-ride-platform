#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$ROOT_DIR/docker-compose.local.yml"
BACKEND_ENV_FILE="$ROOT_DIR/backend/.env"
MIGRATIONS_DIR="$ROOT_DIR/backend/db/migrations"
SEEDS_DIR="$ROOT_DIR/backend/db/seeds"
MIGRATION_MARKER_TABLE="saferide_local_migrations"
MAX_DB_WAIT_SECONDS=60

cd "$ROOT_DIR"

if [ ! -f "$BACKEND_ENV_FILE" ]; then
  echo "Missing backend/.env. Run scripts/start-local.sh once to create it from backend/.env.example." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$BACKEND_ENV_FILE"
set +a

POSTGRES_DB="${POSTGRES_DB:-saferide}"
POSTGRES_USER="${POSTGRES_USER:-saferide}"

docker compose -f "$COMPOSE_FILE" up -d db

db_wait_seconds=0
while ! docker compose -f "$COMPOSE_FILE" exec -T db pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1; do
  if [ "$db_wait_seconds" -ge "$MAX_DB_WAIT_SECONDS" ]; then
    echo "Database did not become ready within ${MAX_DB_WAIT_SECONDS} seconds." >&2
    docker compose -f "$COMPOSE_FILE" logs db >&2 || true
    exit 1
  fi

  sleep 1
  db_wait_seconds=$((db_wait_seconds + 1))
done

if [ ! -d "$MIGRATIONS_DIR" ]; then
  echo "Cannot reset local database: migrations directory is missing at $MIGRATIONS_DIR." >&2
  exit 1
fi

if [ ! -d "$SEEDS_DIR" ]; then
  echo "Cannot reset local database: seeds directory is missing at $SEEDS_DIR." >&2
  exit 1
fi

docker compose -f "$COMPOSE_FILE" exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" <<'SQL'
drop schema public cascade;
create schema public;
SQL

for migration_path in "$MIGRATIONS_DIR"/*.sql; do
  if [ ! -f "$migration_path" ]; then
    echo "No migration files found in $MIGRATIONS_DIR." >&2
    exit 1
  fi

  echo "Applying $(basename "$migration_path")..."
  docker compose -f "$COMPOSE_FILE" exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" < "$migration_path"
done

for seed_path in "$SEEDS_DIR"/*.sql; do
  if [ ! -f "$seed_path" ]; then
    echo "No seed files found in $SEEDS_DIR." >&2
    exit 1
  fi

  echo "Seeding $(basename "$seed_path")..."
  docker compose -f "$COMPOSE_FILE" exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" < "$seed_path"
done

docker compose -f "$COMPOSE_FILE" exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" <<SQL
create table if not exists ${MIGRATION_MARKER_TABLE} (id text primary key, applied_at timestamptz not null default now());
SQL

for migration_path in "$MIGRATIONS_DIR"/*.sql; do
  migration_id="$(basename "$migration_path" .sql)"
  docker compose -f "$COMPOSE_FILE" exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
    "insert into ${MIGRATION_MARKER_TABLE} (id) values ('${migration_id}') on conflict (id) do nothing;"
done

echo "Local SafeRide database reset and seeded."
