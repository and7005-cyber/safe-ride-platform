-- Live-platform data model (parity with www.saferidekenya.com).
-- Lives alongside the legacy tables; the live frontend only ever sees the
-- unprefixed names via the API mapping layer. Status columns use CHECK
-- constraints carrying the live string enum values verbatim (spec 09 §12).

create extension if not exists pgcrypto;

-- Identity & auth ----------------------------------------------------------

create table if not exists app_users (
  id uuid primary key default gen_random_uuid(),
  email text not null unique,
  password_hash text not null,
  full_name text not null default '',
  phone text,
  pin_hash text,
  created_at timestamptz not null default now()
);

-- Deterministic peppered PIN hash => duplicate PINs are unrepresentable.
create unique index if not exists app_users_pin_hash_key
  on app_users (pin_hash)
  where pin_hash is not null;

create table if not exists app_user_roles (
  user_id uuid primary key references app_users (id) on delete cascade,
  role text not null check (role in ('admin', 'driver', 'parent'))
);

create table if not exists auth_sessions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references app_users (id) on delete cascade,
  token_hash text not null unique,
  expires_at timestamptz not null,
  revoked_at timestamptz,
  created_at timestamptz not null default now()
);
create index if not exists auth_sessions_user_idx on auth_sessions (user_id);

create table if not exists password_reset_tokens (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references app_users (id) on delete cascade,
  token_hash text not null unique,
  expires_at timestamptz not null,
  used_at timestamptz,
  created_at timestamptz not null default now()
);

-- Tenant & transport -------------------------------------------------------

create table if not exists live_schools (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  address text,
  phone text,
  lat double precision,
  lng double precision,
  created_at timestamptz not null default now()
);

create table if not exists live_buses (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  plate_number text,
  driver_id uuid references app_users (id) on delete set null,
  driver_name text,
  driver_phone text,
  capacity integer not null default 45 check (capacity between 1 and 100),
  status text not null default 'idle'
    check (status in ('idle', 'active', 'delayed', 'offline')),
  current_lat double precision,
  current_lng double precision,
  created_at timestamptz not null default now()
);
-- At most one bus per driver (mirrors admin UI intent).
create unique index if not exists live_buses_driver_key
  on live_buses (driver_id)
  where driver_id is not null;

create table if not exists live_routes (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  type text not null default 'morning' check (type in ('morning', 'afternoon')),
  bus_id uuid references live_buses (id) on delete set null,
  school_id uuid references live_schools (id) on delete set null,
  created_at timestamptz not null default now()
);

create table if not exists live_students (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  grade text,
  parent_name text,
  parent_phone text,
  parent_phone2 text,
  parent_email text,
  home_address text,
  home_lat double precision,
  home_lng double precision,
  pickup_time text,
  status text not null default 'at-school'
    check (status in ('at-school', 'on-bus', 'absent', 'dropped-off')),
  bus_id uuid references live_buses (id) on delete set null,
  school_id uuid references live_schools (id) on delete set null,
  boarding_stop_name text,
  created_at timestamptz not null default now()
);

-- Stops generated server-side from student home locations + the school gate.
create table if not exists live_route_stops (
  id uuid primary key default gen_random_uuid(),
  route_id uuid not null references live_routes (id) on delete cascade,
  name text not null,
  stop_order integer not null,
  scheduled_time text,
  lat double precision,
  lng double precision,
  is_school_gate boolean not null default false,
  student_id uuid references live_students (id) on delete set null
);
create index if not exists live_route_stops_route_idx on live_route_stops (route_id, stop_order);

create table if not exists live_student_routes (
  id uuid primary key default gen_random_uuid(),
  student_id uuid not null references live_students (id) on delete cascade,
  route_id uuid not null references live_routes (id) on delete cascade,
  unique (student_id, route_id)
);

create table if not exists live_parent_students (
  id uuid primary key default gen_random_uuid(),
  parent_id uuid not null references app_users (id) on delete cascade,
  student_id uuid not null references live_students (id) on delete cascade,
  unique (parent_id, student_id)
);

-- Runs & per-run frozen stop snapshot --------------------------------------

create table if not exists live_runs (
  id uuid primary key default gen_random_uuid(),
  bus_id uuid references live_buses (id) on delete set null,
  route_id uuid references live_routes (id) on delete set null,
  school_id uuid references live_schools (id) on delete set null,
  driver_id uuid references app_users (id) on delete set null,
  type text not null default 'morning' check (type in ('morning', 'afternoon')),
  date date not null,
  start_time text,
  end_time text,
  status text not null default 'in-progress'
    check (status in ('in-progress', 'completed', 'delayed')),
  total_stops integer not null default 0,
  stops_completed integer not null default 0,
  total_students integer not null default 0,
  students_boarded integer not null default 0,
  incidents integer not null default 0,
  created_at timestamptz not null default now()
);
create index if not exists live_runs_bus_date_idx on live_runs (bus_id, date);

-- Frozen copy of the route's stops at run-start so mid-run regeneration of
-- live_route_stops cannot corrupt an active run (KTD-11).
create table if not exists run_stops (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references live_runs (id) on delete cascade,
  stop_order integer not null,
  name text not null,
  scheduled_time text,
  lat double precision,
  lng double precision,
  is_school_gate boolean not null default false,
  student_id uuid references live_students (id) on delete set null
);
create index if not exists run_stops_run_idx on run_stops (run_id, stop_order);

-- Incidents / alerts -------------------------------------------------------

create table if not exists live_incidents (
  id uuid primary key default gen_random_uuid(),
  run_id uuid references live_runs (id) on delete set null,
  driver_id uuid references app_users (id) on delete set null,
  driver_name text,
  bus_id uuid references live_buses (id) on delete set null,
  bus_name text,
  type text not null
    check (type in ('breakdown', 'accident', 'student', 'traffic', 'arrival', 'other')),
  description text,
  acknowledged boolean not null default false,
  acknowledged_at timestamptz,
  acknowledged_by uuid references app_users (id) on delete set null,
  created_at timestamptz not null default now()
);
create index if not exists live_incidents_created_idx on live_incidents (created_at desc);
create index if not exists live_incidents_bus_idx on live_incidents (bus_id);
-- One arrival incident per run (idempotent double-tap at the school gate).
create unique index if not exists live_incidents_run_arrival_key
  on live_incidents (run_id)
  where type = 'arrival' and run_id is not null;

-- Push subscriptions -------------------------------------------------------

create table if not exists live_push_subscriptions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references app_users (id) on delete cascade,
  endpoint text not null unique,
  p256dh text,
  auth text,
  user_agent text,
  created_at timestamptz not null default now()
);
