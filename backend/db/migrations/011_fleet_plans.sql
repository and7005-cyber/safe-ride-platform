-- 011: fleet-plan drafting schema (plan 2026-08-19-001-feat-fleet-plan-drafting, U1).
--
-- Schema-only release. No application code reads or writes anything added here
-- until the later fleet-plan releases ship: the migrate Lambda resolves
-- migration files from its own deployed package, so the schema has to be live
-- before the code that depends on it, not alongside it.
--
-- One ordered migration carries every schema change: additive tables and
-- columns first, then the ridership-pattern backfill, then the constraints
-- that lock it in, then the widened notification type CHECK. Live applies
-- this file as one implicit transaction (migrate handler simple-query
-- protocol); local psql is per-statement autocommit — after a failed local
-- rehearsal, reset the database rather than trusting a half-applied state.

-- 1. Fleet plans: drafts as documents -------------------------------------------
-- One row per plan. document holds the per-bus AM/PM stop sequences, student
-- ids, times, ride times, unplaceable list, pins, and warnings; basis
-- snapshots student ids + coords + patterns + fleet config at generation.
-- Both denormalize student and bus names alongside ids so later gates can
-- name departed children — the 007/010 name-rot precedent. Plans are working
-- documents, not queryable facts (the custom_stops JSON precedent): apply
-- materializes them into live_routes / live_route_stops / live_student_routes.
--
-- school_id is ON DELETE CASCADE: an orphaned document full of child
-- coordinates must not outlive its school. created_by is ON DELETE SET NULL —
-- the plan outlives the admin who drew it.

create table if not exists live_fleet_plans (
  id uuid primary key default gen_random_uuid(),
  school_id uuid not null references live_schools (id) on delete cascade,
  status text not null default 'draft'
    check (status in ('draft', 'applied', 'previous', 'superseded', 'discarded')),
  document jsonb,
  basis jsonb,
  solver_seed bigint,
  degraded boolean not null default false,
  created_by uuid references app_users (id) on delete set null,
  created_at timestamptz not null default now(),
  applied_at timestamptz
);
create index if not exists live_fleet_plans_school_idx
  on live_fleet_plans (school_id);

-- One open draft and one preserved previous plan per school. Partial indexes
-- cannot defer, so the apply/restore status flips are ordered
-- demote-before-promote. 'applied' uniqueness is deliberately NOT enforced
-- here: it stays DAO-enforced so the restore swap needs no intermediate
-- status.
create unique index if not exists live_fleet_plans_school_draft_key
  on live_fleet_plans (school_id)
  where status = 'draft';
create unique index if not exists live_fleet_plans_school_previous_key
  on live_fleet_plans (school_id)
  where status = 'previous';

-- 2. Communicated-stop baseline -------------------------------------------------
-- The last place/time/bus each family was told, per (student, leg) — the R15
-- notification baseline. live_students.pickup_time cannot serve: it is
-- morning-clock only and overwritten by every regeneration. Written only on
-- send; one shared diff function reads it for both the preview count and the
-- actual fan-out, so they cannot drift.
--
-- student_id is ON DELETE CASCADE — SET NULL would multiply orphans under the
-- unique. bus_id is SET NULL per the 004 house style. route_type carries the
-- live_routes.type vocabulary; scheduled_time is text HH:MM like every other
-- communicated clock value.

create table if not exists live_communicated_stops (
  id uuid primary key default gen_random_uuid(),
  student_id uuid not null references live_students (id) on delete cascade,
  route_type text not null check (route_type in ('morning', 'afternoon')),
  stop_name text,
  stop_lat double precision,
  stop_lng double precision,
  scheduled_time text,
  bus_id uuid references live_buses (id) on delete set null,
  communicated_at timestamptz not null default now(),
  unique (student_id, route_type)
);

-- 3. Admin audit ------------------------------------------------------------------
-- One row per audited admin act: applying a plan, restoring one, viewing the
-- aggregate pin map. actor_id is ON DELETE SET NULL with the actor's name and
-- email denormalized alongside — the 007/010 name-rot precedent (run_absences
-- and run_participation denormalize student_name for the same reason): an
-- audit row must still name its actor after the account is gone. detail
-- carries the act's specifics (plan id, counts, acknowledgments) as JSONB.
-- Append-only by convention: no code path updates or deletes rows, and no
-- trigger enforces it — the same trust model as the rest of the live schema.

create table if not exists live_admin_audit (
  id uuid primary key default gen_random_uuid(),
  actor_id uuid references app_users (id) on delete set null,
  actor_name text not null,
  actor_email text not null,
  action text not null
    check (action in ('plan-applied', 'plan-restored', 'pin-map-viewed')),
  school_id uuid references live_schools (id) on delete set null,
  detail jsonb,
  created_at timestamptz not null default now()
);
create index if not exists live_admin_audit_school_idx
  on live_admin_audit (school_id, created_at desc);

-- 4. Bus↔school claim -------------------------------------------------------------
-- Set during fleet confirmation; a bus claimed by one school is unavailable
-- to another school's draft or apply for overlapping use. Nullable —
-- unclaimed buses exist — and ON DELETE SET NULL per the 004 house style:
-- deleting a school releases its buses rather than deleting them.

alter table live_buses add column if not exists school_id uuid
  references live_schools (id) on delete set null;

-- 5. Plan order as an ordering authority --------------------------------------------
-- The authority order becomes custom_stops > manual_stop_order > plan > auto.
-- Regeneration preserves a plan-ordered route's stop order and recomputes
-- times and geometry along the fixed sequence; Recalculate clears the flag
-- behind explicit copy. Without this, the first roster edit after apply would
-- silently hand the reviewed order back to Google's single-route optimiser.

alter table live_routes add column if not exists plan_ordered boolean not null default false;

-- 6. Ridership pattern (additive; backfill + constraints below) ---------------------
-- Explicit, seeded, review-editable solver input. Added NULLABLE so the
-- backfill can tell pre-existing rows from already-stamped ones; the default,
-- NOT NULL, and CHECK land after the backfill.

alter table live_students add column if not exists ridership_pattern text;

-- 7. Notification plumbing for plan fan-out (column now; CHECK widened last) --------
-- plan_audit_id ties a route-updated / route-unassigned feed row to the apply
-- or restore act that produced it, and is the dedup arbiter for those types:
-- the shipped run_dedup index excludes run-less rows entirely, and the later
-- fleet-plan releases carry no migration, so the arbiter ships now. ON DELETE
-- SET NULL — the feed row outlives the audit row.

alter table live_notifications add column if not exists plan_audit_id uuid
  references live_admin_audit (id) on delete set null;

-- 8. Backfill: ridership pattern from today's links ---------------------------------
-- Seeded from live_student_routes (at most one link per leg since 009's
-- (student_id, route_type) unique; route_type is 009's trigger-maintained
-- denormalization of live_routes.type). split only when the student has BOTH
-- a morning and an afternoon link AND both routes carry a bus AND the buses
-- differ — plain <>, deliberately NOT IS DISTINCT FROM: bus-less routes
-- exist, and IS DISTINCT FROM would mark a bus-less pair split. A
-- morning-only link backfills morning_only; afternoon-only backfills
-- afternoon_only; everything else — no links, both legs on one bus, or a
-- pair where either route lacks a bus — backfills both_ways. Only NULL rows
-- are touched, so a second apply is a no-op.

with pattern_source as (
  select s.id as student_id,
         am.route_id as am_route_id,
         pm.route_id as pm_route_id,
         am_r.bus_id as am_bus_id,
         pm_r.bus_id as pm_bus_id
  from live_students s
  left join live_student_routes am
    on am.student_id = s.id and am.route_type = 'morning'
  left join live_routes am_r on am_r.id = am.route_id
  left join live_student_routes pm
    on pm.student_id = s.id and pm.route_type = 'afternoon'
  left join live_routes pm_r on pm_r.id = pm.route_id
)
update live_students s
set ridership_pattern = case
  when p.am_route_id is not null and p.pm_route_id is null then 'morning_only'
  when p.am_route_id is null and p.pm_route_id is not null then 'afternoon_only'
  when p.am_bus_id is not null and p.pm_bus_id is not null
       and p.am_bus_id <> p.pm_bus_id then 'split'
  else 'both_ways'
end
from pattern_source p
where p.student_id = s.id
  and s.ridership_pattern is null;

-- 9. Ridership pattern: default, NOT NULL, CHECK (strictly after the backfill) ------
-- both_ways is the default for new intakes (the bulk-upload front door seeds
-- real values later). On a fresh local stack migrations run before seeds, so
-- every seeded student lands on the default — tests must never rely on seeded
-- patterns.

alter table live_students alter column ridership_pattern set default 'both_ways';
alter table live_students alter column ridership_pattern set not null;

alter table live_students drop constraint if exists live_students_ridership_pattern_check;
alter table live_students add constraint live_students_ridership_pattern_check check (
  ridership_pattern in ('both_ways', 'morning_only', 'afternoon_only', 'split')
);

-- Dedup for the plan-scoped notification types: at most one route-updated /
-- route-unassigned feed row per (parent, student, type) per apply act.
-- Mirrors the shipped live_notifications_run_dedup, keyed on the audit row
-- instead of the run.
create unique index if not exists live_notifications_plan_dedup
  on live_notifications (user_id, student_id, type, plan_audit_id)
  where type in ('route-updated', 'route-unassigned');

-- 10. Notification type CHECK last ---------------------------------------------------
-- Recreated as the verbatim union of 010's thirteen values plus the two plan
-- fan-out types. Never copy the list from an older migration: the local seeds
-- do not hold every type, so a stale list passes local rehearsal and fails
-- only on live.

alter table live_notifications drop constraint if exists live_notifications_type_check;
alter table live_notifications add constraint live_notifications_type_check check (
  type in (
    'run-started',
    'student-boarded',
    'bus-approaching',
    'reached-school',
    'on-way-home',
    'dropped-off',
    'incident',
    'custom',
    'student-absent',
    'admin-notice',
    'ride-cancelled',
    'dropoff-corrected',
    'absence-corrected',
    'route-updated',
    'route-unassigned'
  )
);
