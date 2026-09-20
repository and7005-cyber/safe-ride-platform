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


# Tenancy (U1): the runtime application role exists locally before migrations,
# so migration 013's grants apply and (from U14) the API can run as a
# non-owner role that row-level security actually binds.
DB_APP_PASSWORD="${DB_APP_PASSWORD:-saferide}"
docker compose -f "$COMPOSE_FILE" exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 <<SQL
do \$\$
begin
  if not exists (select 1 from pg_roles where rolname = 'saferide_app') then
    execute format('create role saferide_app login nobypassrls password %L', '${DB_APP_PASSWORD}');
  else
    execute format('alter role saferide_app with login nobypassrls password %L', '${DB_APP_PASSWORD}');
  end if;
end
\$\$;
SQL

for migration_path in "$MIGRATIONS_DIR"/*.sql; do
  if [ ! -f "$migration_path" ]; then
    echo "No migration files found in $MIGRATIONS_DIR." >&2
    exit 1
  fi

  echo "Applying $(basename "$migration_path")..."
  # ON_ERROR_STOP matches the seed loop below: without it psql reports success
  # after a failing statement, and the rehearsal would pass on a half-applied
  # migration that live — which runs each file as one transaction — would reject.
  docker compose -f "$COMPOSE_FILE" exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    -v ON_ERROR_STOP=1 < "$migration_path"
done

if [ "${APP_ENV:-}" != "local" ]; then
  echo "Refusing to apply demo seeds: APP_ENV is '${APP_ENV:-unset}', not 'local'." >&2
  echo "Demo credentials must never reach a non-local environment." >&2
  exit 1
fi

for seed_path in "$SEEDS_DIR"/*.sql; do
  if [ ! -f "$seed_path" ]; then
    echo "No seed files found in $SEEDS_DIR." >&2
    exit 1
  fi

  echo "Seeding $(basename "$seed_path")..."
  { echo "set saferide.allow_demo_seed = 'yes';"; cat "$seed_path"; } | \
    docker compose -f "$COMPOSE_FILE" exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1
done

docker compose -f "$COMPOSE_FILE" exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" <<SQL
create table if not exists ${MIGRATION_MARKER_TABLE} (id text primary key, applied_at timestamptz not null default now());
SQL

for migration_path in "$MIGRATIONS_DIR"/*.sql; do
  migration_id="$(basename "$migration_path" .sql)"
  docker compose -f "$COMPOSE_FILE" exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
    "insert into ${MIGRATION_MARKER_TABLE} (id) values ('${migration_id}') on conflict (id) do nothing;"
done


# Tenancy (U14): migration 015's constraints and RLS need seeded data, but the
# local order applies migrations before seeds — its empty-DB guard defers every
# data-dependent step to this second pass (double-apply is a designed no-op,
# and by now the local marker table exists so the demo admin stays alive).
echo "Re-applying 015 post-seed (constraints + row security)..."
docker compose -f "$COMPOSE_FILE" exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -v ON_ERROR_STOP=1 < "$MIGRATIONS_DIR/015_tenancy_constraints_rls.sql"

# Tenancy (U1): post-seed integrity assertions. Activate once the data move has
# run locally (the seed tail stamps scopes after migration 014 exists); until
# then they are a no-op. Extended by U14 with orphan and RLS checks.
echo "Running post-seed integrity assertions..."
docker compose -f "$COMPOSE_FILE" exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 <<'SQL'
do $$
declare moved boolean := false; n bigint; t text;
begin
  -- Table references resolve at parse time, so the move-log probe is dynamic.
  if to_regclass('public.tenancy_move_log') is not null then
    execute 'select exists (select 1 from public.tenancy_move_log)' into moved;
  end if;
  if moved then
    foreach t in array array['live_students', 'live_buses', 'live_routes'] loop
      execute format('select count(*) from public.%I where school_id is null', t) into n;
      if n > 0 then
        raise exception 'post-seed: % % rows with NULL school_id', n, t;
      end if;
    end loop;
    -- U14: the second 015 pass must have armed row security everywhere, and
    -- migration 016 lands the four GPS tracking tables already scoped...
    select count(*) into n from pg_class c
    where c.relrowsecurity and c.relname in (
      'live_buses','live_routes','live_students','live_runs','live_fleet_plans',
      'live_incidents','live_student_absences','live_communicated_stops',
      'live_student_routes','live_route_stops','run_stops','run_absences',
      'run_participation','live_admin_audit',
      'run_positions','run_exceptions','run_exception_events','driver_action_keys');
    if n <> 18 then
      raise exception 'post-seed: row security enabled on % tables, expected 18', n;
    end if;
    -- ...and the runtime role must hold its grants.
    if not has_table_privilege('saferide_app', 'public.live_students', 'select') then
      raise exception 'post-seed: saferide_app is missing table grants';
    end if;
  end if;
end
$$;
SQL

echo "Local SafeRide database reset and seeded."
