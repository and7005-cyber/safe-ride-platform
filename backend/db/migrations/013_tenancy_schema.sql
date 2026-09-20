-- 013: multi-tenant schools — additive tenancy schema (U3).
-- Plan: docs/plans/2026-08-23-001-feat-multi-tenant-schools-plan.md
--
-- Additive only: nullable columns, new tables, indexes, functions, grants.
-- No NOT NULL, no composite foreign keys, no RLS — those land in 015 after
-- the data move (014) and the scoped application code. Every new school
-- foreign key on a school-OWNED row is plain REFERENCES (NO ACTION) so a
-- school deletion can never silently de-scope rows; session bookkeeping
-- columns use SET NULL because they are not ownership.
--
-- Audit action vocabulary (verbatim union of 011's three values plus one per
-- write family; widening this CHECK later must again be the verbatim union):
--   plan-applied, plan-restored, pin-map-viewed,
--   staff-created, staff-role-offered, staff-offer-accepted,
--   staff-offer-declined, staff-offer-cancelled, staff-role-removed,
--   staff-password-reset,
--   school-created, school-updated,
--   bus-created, bus-updated, bus-deleted,
--   driver-created, driver-updated, driver-deleted,
--   student-created, student-updated, student-deleted, students-imported,
--   route-created, route-updated, route-deleted,
--   run-created, run-updated, run-deleted, run-force-closed,
--   absence-marked, absence-cleared,
--   incident-acknowledged, incident-deleted,
--   plan-drafted, plan-updated, plan-discarded,
--   slot-in-accepted, slot-in-dismissed,
--   broadcast-sent,
--   parent-updated, parent-deleted, parent-link-declined,
--   provider-step-in, provider-step-out,
--   provider-account-created, provider-account-removed, provider-totp-reset

-- 1. Identity & session columns ----------------------------------------------

alter table app_users add column if not exists disabled_at timestamptz;
alter table app_users add column if not exists must_change_password boolean not null default false;
alter table app_users add column if not exists temporary_password_expires_at timestamptz;
alter table app_users add column if not exists password_changed_at timestamptz;

alter table auth_sessions add column if not exists last_school_id uuid references live_schools (id) on delete set null;
alter table auth_sessions add column if not exists totp_verified_at timestamptz;

-- 2. Memberships & provider tables -------------------------------------------

create table if not exists school_memberships (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references app_users (id) on delete cascade,
  school_id uuid not null references live_schools (id),
  role text not null check (role in ('director', 'coordinator', 'driver')),
  state text not null default 'active' check (state in ('offered', 'active')),
  offered_by uuid references app_users (id) on delete set null,
  created_at timestamptz not null default now(),
  accepted_at timestamptz,
  removed_at timestamptz
);
create unique index if not exists school_memberships_live_key
  on school_memberships (user_id, school_id, role)
  where removed_at is null;
create index if not exists school_memberships_school_idx
  on school_memberships (school_id, role) where removed_at is null;
create index if not exists school_memberships_user_idx
  on school_memberships (user_id);

create table if not exists provider_accounts (
  user_id uuid primary key references app_users (id) on delete cascade,
  totp_salt text not null,
  totp_pepper_key text,
  totp_enrolled_at timestamptz,
  totp_last_step bigint,
  created_by uuid references app_users (id) on delete set null,
  created_at timestamptz not null default now(),
  removed_at timestamptz
);

create table if not exists provider_support_sessions (
  id uuid primary key default gen_random_uuid(),
  provider_user_id uuid not null references app_users (id) on delete cascade,
  school_id uuid not null references live_schools (id),
  reason text not null check (char_length(reason) between 1 and 500),
  started_at timestamptz not null default now(),
  ended_at timestamptz,
  end_cause text check (end_cause in ('step-out', 'logout', 'superseded', 'expired', 'revoked')),
  auth_session_id uuid references auth_sessions (id) on delete set null,
  ip text,
  user_agent text
);
create index if not exists provider_support_sessions_provider_idx
  on provider_support_sessions (provider_user_id, started_at desc);
create index if not exists provider_support_sessions_school_idx
  on provider_support_sessions (school_id, started_at desc);

alter table auth_sessions add column if not exists support_session_id uuid
  references provider_support_sessions (id) on delete set null;

create table if not exists auth_preauth_tokens (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references app_users (id) on delete cascade,
  token_hash text not null unique,
  expires_at timestamptz not null,
  used_at timestamptz,
  attempts integer not null default 0,
  created_at timestamptz not null default now()
);
create index if not exists auth_preauth_tokens_user_idx on auth_preauth_tokens (user_id);

create table if not exists tenancy_move_log (
  id uuid primary key default gen_random_uuid(),
  phase text not null,
  detail jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

-- 3. School catalog & scope columns ------------------------------------------

alter table live_schools add column if not exists code text;
create unique index if not exists live_schools_code_key
  on live_schools (code) where code is not null;

alter table live_parent_students add column if not exists status text not null default 'accepted';
do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conrelid = 'live_parent_students'::regclass and conname = 'live_parent_students_status_check'
  ) then
    alter table live_parent_students
      add constraint live_parent_students_status_check check (status in ('pending', 'accepted'));
  end if;
end
$$;
alter table live_parent_students add column if not exists school_id uuid references live_schools (id);
alter table live_parent_students add column if not exists offered_at timestamptz;
alter table live_parent_students add column if not exists decided_at timestamptz;

alter table live_incidents add column if not exists school_id uuid references live_schools (id);
alter table live_student_absences add column if not exists school_id uuid references live_schools (id);
alter table live_communicated_stops add column if not exists school_id uuid references live_schools (id);
alter table live_notifications add column if not exists school_id uuid references live_schools (id);
alter table live_student_routes add column if not exists school_id uuid references live_schools (id);
alter table live_route_stops add column if not exists school_id uuid references live_schools (id);
alter table run_stops add column if not exists school_id uuid references live_schools (id);
alter table run_absences add column if not exists school_id uuid references live_schools (id);
alter table run_participation add column if not exists school_id uuid references live_schools (id);

-- Composite-key targets for 015's composite foreign keys.
create unique index if not exists live_buses_id_school_key on live_buses (id, school_id);
create unique index if not exists live_routes_id_school_key on live_routes (id, school_id);
create unique index if not exists live_students_id_school_key on live_students (id, school_id);
create unique index if not exists live_runs_id_school_key on live_runs (id, school_id);

-- Scoped-list and RLS-predicate indexes.
create index if not exists live_buses_school_idx on live_buses (school_id);
create index if not exists live_routes_school_idx on live_routes (school_id);
create index if not exists live_students_school_idx on live_students (school_id);
create index if not exists live_runs_school_date_idx on live_runs (school_id, date desc);
create index if not exists live_incidents_school_created_idx on live_incidents (school_id, created_at desc);
create index if not exists live_student_absences_school_date_idx on live_student_absences (school_id, absence_date);
create index if not exists live_communicated_stops_school_idx on live_communicated_stops (school_id);
create index if not exists live_notifications_school_idx on live_notifications (school_id);
create index if not exists live_student_routes_school_idx on live_student_routes (school_id);
create index if not exists live_route_stops_school_idx on live_route_stops (school_id);
create index if not exists run_stops_school_idx on run_stops (school_id);
create index if not exists run_absences_school_idx on run_absences (school_id);
create index if not exists run_participation_school_idx on run_participation (school_id);
create index if not exists live_parent_students_school_idx on live_parent_students (school_id);

-- 4. Audit extension -----------------------------------------------------------

alter table live_admin_audit add column if not exists actor_kind text not null default 'staff';
do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conrelid = 'live_admin_audit'::regclass and conname = 'live_admin_audit_actor_kind_check'
  ) then
    alter table live_admin_audit
      add constraint live_admin_audit_actor_kind_check check (actor_kind in ('staff', 'provider'));
  end if;
end
$$;
alter table live_admin_audit add column if not exists support_session_id uuid
  references provider_support_sessions (id) on delete set null;
alter table live_admin_audit add column if not exists resource_type text;
alter table live_admin_audit add column if not exists resource_id text;

do $$
declare c text;
begin
  select conname into c from pg_constraint
  where conrelid = 'live_admin_audit'::regclass and contype = 'c'
    and pg_get_constraintdef(oid) like '%action%plan-applied%';
  if c is not null then
    execute format('alter table live_admin_audit drop constraint %I', c);
  end if;
  alter table live_admin_audit add constraint live_admin_audit_action_check check (action in (
    'plan-applied', 'plan-restored', 'pin-map-viewed',
    'staff-created', 'staff-role-offered', 'staff-offer-accepted',
    'staff-offer-declined', 'staff-offer-cancelled', 'staff-role-removed',
    'staff-password-reset',
    'school-created', 'school-updated',
    'bus-created', 'bus-updated', 'bus-deleted',
    'driver-created', 'driver-updated', 'driver-deleted',
    'student-created', 'student-updated', 'student-deleted', 'students-imported',
    'route-created', 'route-updated', 'route-deleted',
    'run-created', 'run-updated', 'run-deleted', 'run-force-closed',
    'absence-marked', 'absence-cleared',
    'incident-acknowledged', 'incident-deleted',
    'plan-drafted', 'plan-updated', 'plan-discarded',
    'slot-in-accepted', 'slot-in-dismissed',
    'broadcast-sent',
    'parent-updated', 'parent-deleted', 'parent-link-declined',
    'provider-step-in', 'provider-step-out',
    'provider-account-created', 'provider-account-removed', 'provider-totp-reset'
  ));
exception when duplicate_object then null;
end
$$;

-- 5. Stamp function (data move + local-tail parity + 015 catch-up) -----------

create or replace function tenancy_stamp_school_one(target uuid)
returns jsonb
language plpgsql
set search_path = public
as $$
declare
  counts jsonb := '{}'::jsonb;
  n bigint;
begin
  -- Owner tables: the single-real-school argument is the source of truth.
  update live_buses set school_id = target where school_id is null;
  get diagnostics n = row_count; counts := counts || jsonb_build_object('live_buses', n);
  update live_routes set school_id = target where school_id is null;
  get diagnostics n = row_count; counts := counts || jsonb_build_object('live_routes', n);
  update live_students set school_id = target where school_id is null;
  get diagnostics n = row_count; counts := counts || jsonb_build_object('live_students', n);
  update live_runs set school_id = target where school_id is null;
  get diagnostics n = row_count; counts := counts || jsonb_build_object('live_runs', n);

  -- Child tables: derive from the parent, then fall back to the argument.
  update live_student_routes sr set school_id = coalesce(r.school_id, target)
    from live_routes r where sr.school_id is null and r.id = sr.route_id;
  get diagnostics n = row_count; counts := counts || jsonb_build_object('live_student_routes', n);
  update live_student_routes set school_id = target where school_id is null;

  update live_route_stops rs set school_id = coalesce(r.school_id, target)
    from live_routes r where rs.school_id is null and r.id = rs.route_id;
  get diagnostics n = row_count; counts := counts || jsonb_build_object('live_route_stops', n);
  update live_route_stops set school_id = target where school_id is null;

  update run_stops s set school_id = coalesce(r.school_id, target)
    from live_runs r where s.school_id is null and r.id = s.run_id;
  get diagnostics n = row_count; counts := counts || jsonb_build_object('run_stops', n);
  update run_stops set school_id = target where school_id is null;

  update run_absences s set school_id = coalesce(r.school_id, target)
    from live_runs r where s.school_id is null and r.id = s.run_id;
  get diagnostics n = row_count; counts := counts || jsonb_build_object('run_absences', n);
  update run_absences set school_id = target where school_id is null;

  update run_participation s set school_id = coalesce(r.school_id, target)
    from live_runs r where s.school_id is null and r.id = s.run_id;
  get diagnostics n = row_count; counts := counts || jsonb_build_object('run_participation', n);
  update run_participation set school_id = target where school_id is null;

  update live_student_absences a set school_id = coalesce(s.school_id, target)
    from live_students s where a.school_id is null and s.id = a.student_id;
  get diagnostics n = row_count; counts := counts || jsonb_build_object('live_student_absences', n);
  update live_student_absences set school_id = target where school_id is null;

  update live_communicated_stops c set school_id = coalesce(s.school_id, target)
    from live_students s where c.school_id is null and s.id = c.student_id;
  get diagnostics n = row_count; counts := counts || jsonb_build_object('live_communicated_stops', n);
  update live_communicated_stops set school_id = target where school_id is null;

  update live_parent_students l set school_id = coalesce(s.school_id, target)
    from live_students s where l.school_id is null and s.id = l.student_id;
  get diagnostics n = row_count; counts := counts || jsonb_build_object('live_parent_students', n);
  update live_parent_students set school_id = target where school_id is null;

  update live_incidents i set school_id = coalesce(b.school_id, target)
    from live_buses b where i.school_id is null and b.id = i.bus_id;
  update live_incidents i set school_id = coalesce(s.school_id, target)
    from live_students s where i.school_id is null and s.id = i.student_id;
  update live_incidents set school_id = target where school_id is null;
  get diagnostics n = row_count; counts := counts || jsonb_build_object('live_incidents', n);

  -- User-level feed rows: derivable only — never force a school onto them.
  update live_notifications x set school_id = r.school_id
    from live_runs r where x.school_id is null and r.id = x.run_id and r.school_id is not null;
  update live_notifications x set school_id = s.school_id
    from live_students s where x.school_id is null and s.id = x.student_id and s.school_id is not null;

  return counts;
end
$$;

-- 6. Provider aggregate functions (the one named, reviewable RLS bypass) -----

create or replace function provider_school_health()
returns table (
  school_id uuid,
  name text,
  code text,
  setup_state text,
  students bigint,
  buses bigint,
  drivers bigint,
  runs_today bigint,
  last_staff_write timestamptz
)
language sql
security definer
set search_path = public
as $$
  select
    sc.id,
    sc.name,
    sc.code,
    case
      when coalesce(st.students, 0) > 0 and coalesce(bu.buses, 0) > 0 then 'ready'
      else 'setup'
    end as setup_state,
    coalesce(st.students, 0),
    coalesce(bu.buses, 0),
    coalesce(dr.drivers, 0),
    coalesce(ru.runs_today, 0),
    au.last_staff_write
  from live_schools sc
  left join (select school_id, count(*) students from live_students group by 1) st on st.school_id = sc.id
  left join (select school_id, count(*) buses from live_buses group by 1) bu on bu.school_id = sc.id
  left join (
    select school_id, count(*) drivers from school_memberships
    where role = 'driver' and state = 'active' and removed_at is null group by 1
  ) dr on dr.school_id = sc.id
  left join (
    select school_id, count(*) runs_today from live_runs
    where date = (now() at time zone 'Africa/Nairobi')::date group by 1
  ) ru on ru.school_id = sc.id
  left join (
    select school_id, max(created_at) last_staff_write from live_admin_audit
    where actor_kind = 'staff' and action <> 'pin-map-viewed' group by 1
  ) au on au.school_id = sc.id
  order by sc.name
$$;

create or replace function provider_audit_rows(p_school uuid default null, p_support uuid default null)
returns setof live_admin_audit
language sql
security definer
set search_path = public
as $$
  select * from live_admin_audit
  where (p_school is null or school_id = p_school)
    and (p_support is null or support_session_id = p_support)
  order by created_at desc
  limit 500
$$;

create or replace function parent_signup_matches(p_email text)
returns table (student_id uuid, school_id uuid)
language sql
security definer
set search_path = public
as $$
  select s.id, s.school_id
  from live_students s
  where lower(trim(s.parent_email)) = lower(trim(p_email))
     or lower(trim(s.parent2_email)) = lower(trim(p_email))
$$;

-- 7. Runtime-role grants (idempotent; the migrate Lambda's role step repeats
-- them for production, this block keeps local psql-applied stacks aligned).

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'saferide_app') then
    grant usage on schema public to saferide_app;
    grant select, insert, update, delete on all tables in schema public to saferide_app;
    grant usage, select on all sequences in schema public to saferide_app;
    alter default privileges in schema public
      grant select, insert, update, delete on tables to saferide_app;
    alter default privileges in schema public
      grant usage, select on sequences to saferide_app;
    grant execute on function provider_school_health() to saferide_app;
    grant execute on function provider_audit_rows(uuid, uuid) to saferide_app;
    grant execute on function parent_signup_matches(text) to saferide_app;
    grant execute on function tenancy_stamp_school_one(uuid) to saferide_app;
    grant saferide_app to current_user;
  end if;
end
$$;
