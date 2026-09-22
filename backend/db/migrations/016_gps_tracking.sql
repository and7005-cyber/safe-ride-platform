-- 016: phone GPS bus tracking — trail, exceptions, idempotency, position
-- source and per-school thresholds (U1, Release 1 of
-- docs/plans/2026-09-19-001-feat-gps-bus-tracking-plan.md).
--
-- Schema only, one release ahead of the code that writes to it. Ordered so
-- the ACCESS EXCLUSIVE locks on hot tables come last and are held briefly:
-- (1) four new school-owned tables with their indexes; (2) composite
-- (col, school_id) foreign keys to the run, bus and student parents, added
-- NOT VALID then validated; (3) ENABLE (not FORCE) row level
-- security with the saferide_app school_isolation policy on the four new
-- tables; (4) nullable, default-less columns on live_buses, live_runs and
-- live_schools, one ALTER per table; (5) the three CHECK widenings, each the
-- verbatim union of the CURRENT list plus the new values, one ALTER per
-- table.
--
-- The migrate runner executes this file as ONE implicit transaction
-- (pgconn.exec_), so SET LOCAL is correct here and every statement is
-- idempotent: local psql autocommits per statement, and a failed local apply
-- is half-applied and already marked — reset, never resume.
--
-- No empty-database guard (unlike 015): nothing here depends on data. The
-- composite-key target live_runs (id, school_id) exists from 013, and the
-- policies must land on an empty database too (the local reset applies
-- migrations before seeds).
--
-- Rollback-neutral for older code by construction: every new column on an
-- existing table is nullable with no default; no constraint relates the new
-- position qualifiers to current_lat/lng; RLS and composite FKs touch new
-- tables only.
--
-- Double-apply is a no-op end to end.

set local lock_timeout = '5s';
set local statement_timeout = '90s';

-- (1) New tables ---------------------------------------------------------------

-- The per-run position trail: one row per checkpoint, action fix or ping.
-- Append-only; purged by received_at (server time, never the client's
-- capture time) after the school's retention. fix_reason and flags carry a
-- DAO-owned vocabulary on purpose (none/denied/unavailable/timeout/coarse/
-- invalid; clock-skew/implausible/classification-failed) — no CHECK, so a
-- new reason never needs a migration.
create table if not exists run_positions (
  id uuid primary key default gen_random_uuid(),
  school_id uuid not null references live_schools (id),
  run_id uuid not null,
  bus_id uuid not null,
  source text not null check (source in ('checkpoint', 'action', 'ping')),
  action_kind text,        -- start, arrive, board, dropoff, absent, handover, end
  action_key uuid,         -- the tap's idempotency key (action rows only)
  session_id text,         -- auth session that produced the row (server-stamped)
  device_id text,          -- client-supplied, diagnostic only
  lat double precision,
  lng double precision,
  accuracy_m double precision,
  captured_at timestamptz, -- client capture time
  received_at timestamptz not null default now(),
  fix_reason text,
  flags text[] not null default '{}'
);
-- One trail row per tap; pings dedup on capture time (two Boards inside the
-- 15 s cache window legitimately share a capture time, so action rows do not).
create unique index if not exists run_positions_run_action_key_key
  on run_positions (run_id, action_key) where action_key is not null;
create unique index if not exists run_positions_run_ping_captured_key
  on run_positions (run_id, captured_at) where source = 'ping';
create index if not exists run_positions_run_received_idx
  on run_positions (run_id, received_at desc);
create index if not exists run_positions_school_received_idx
  on run_positions (school_id, received_at);
create index if not exists run_positions_school_idx
  on run_positions (school_id);

-- Stop exceptions: the office's decision record. The fix is denormalised so
-- the exception outlives the trail row; the retention pass nulls fix_lat,
-- fix_lng, fix_accuracy_m and fix_captured_at on rows older than retention
-- while kind, distance, seen_at_stop and review state stay.
create table if not exists run_exceptions (
  id uuid primary key default gen_random_uuid(),
  school_id uuid not null references live_schools (id),
  run_id uuid not null,
  stop_order integer,
  student_id uuid,
  kind text not null check (kind in (
    'custody-away', 'stop-bypassed', 'absent-remote', 'absent-attested',
    'unverified', 'implausible-movement'
  )),
  reason text,
  fix_lat double precision,
  fix_lng double precision,
  fix_accuracy_m double precision,
  fix_captured_at timestamptz,
  distance_m double precision,
  seen_at_stop boolean,
  created_at timestamptz not null default now(),
  reviewed_at timestamptz,
  reviewed_by uuid
);
create index if not exists run_exceptions_run_kind_idx
  on run_exceptions (run_id, kind);
create index if not exists run_exceptions_school_run_unreviewed_idx
  on run_exceptions (school_id, run_id) where reviewed_at is null;
create index if not exists run_exceptions_school_idx
  on run_exceptions (school_id);
-- One live row per key per kind; later taps attach as events.
create unique index if not exists run_exceptions_custody_stop_key
  on run_exceptions (run_id, stop_order) where kind = 'custody-away';
create unique index if not exists run_exceptions_bypassed_stop_key
  on run_exceptions (run_id, stop_order) where kind = 'stop-bypassed';
create unique index if not exists run_exceptions_absent_student_key
  on run_exceptions (run_id, student_id) where kind in ('absent-remote', 'absent-attested');
create unique index if not exists run_exceptions_stop_unverified_key
  on run_exceptions (run_id, stop_order) where kind = 'unverified' and reason = 'stop-unverified';
create unique index if not exists run_exceptions_implausible_run_key
  on run_exceptions (run_id) where kind = 'implausible-movement';

-- Per-tap attachments on an exception: the prompt IS the event row
-- (prompt_state, delivered_at, shown_at, response), plus the call-now ledger
-- that caps the loudest parent message at once per child per run.
create table if not exists run_exception_events (
  id uuid primary key default gen_random_uuid(),
  exception_id uuid not null references run_exceptions (id) on delete cascade,
  school_id uuid not null references live_schools (id),
  run_id uuid not null,
  student_id uuid,
  action_key uuid,
  fix_lat double precision,
  fix_lng double precision,
  fix_accuracy_m double precision,
  fix_captured_at timestamptz,
  distance_m double precision,
  prompt_state text check (prompt_state in ('pending', 'answered', 'unanswered')),
  delivered_at timestamptz,
  shown_at timestamptz,
  response text check (response in (
    'confirmed', 'dismissed', 'retracted', 'told-me', 'not-at-stop', 'undo', 'resolution'
  )),
  call_now_due_at timestamptz,
  call_now_sent_at timestamptz,
  created_at timestamptz not null default now()
);
create index if not exists run_exception_events_run_pending_idx
  on run_exception_events (run_id) where prompt_state = 'pending';
create index if not exists run_exception_events_exception_created_idx
  on run_exception_events (exception_id, created_at);
create index if not exists run_exception_events_school_idx
  on run_exception_events (school_id);

-- Idempotency keys, scoped (school, driver, key) so the table never becomes a
-- per-school cache of children's data. The run reference is SET NULL so a
-- replay after a run delete still short-circuits; rows are purged after
-- seven days by created_at.
create table if not exists driver_action_keys (
  school_id uuid not null references live_schools (id),
  driver_id uuid not null,
  key uuid not null,
  run_id uuid,
  action text not null,
  request_fingerprint text not null,
  response jsonb,
  created_at timestamptz not null default now(),
  primary key (school_id, driver_id, key)
);
create index if not exists driver_action_keys_created_idx
  on driver_action_keys (created_at);
create index if not exists driver_action_keys_school_idx
  on driver_action_keys (school_id);

-- (2) Composite foreign keys ---------------------------------------------------
-- (col, school_id) -> parent (id, school_id), 015's shape for every child
-- reference: a row can never point at a parent of another school, even for
-- the owner. The parent keys exist from 013. ON DELETE mirrors the plain key
-- each column would otherwise carry — cascade with the run (like run_stops /
-- run_absences / run_participation) and with the bus; the column-list SET
-- NULL form for the student and for the idempotency table's run, so a deleted
-- parent nulls the reference but never the school.
do $$
declare
  spec record;
begin
  for spec in
    select * from (values
      ('run_positions',        'run_id',     'live_runs',     'cascade'),
      ('run_positions',        'bus_id',     'live_buses',    'cascade'),
      ('run_exceptions',       'run_id',     'live_runs',     'cascade'),
      ('run_exceptions',       'student_id', 'live_students', 'set null (student_id)'),
      ('run_exception_events', 'run_id',     'live_runs',     'cascade'),
      ('run_exception_events', 'student_id', 'live_students', 'set null (student_id)'),
      ('driver_action_keys',   'run_id',     'live_runs',     'set null (run_id)')
    ) as v(child, col, parent, on_delete)
  loop
    begin
      execute format(
        'alter table public.%I add constraint %I '
        'foreign key (%I, school_id) references public.%I (id, school_id) '
        'on delete %s not valid',
        spec.child, spec.child || '_' || spec.col || '_school_fkey',
        spec.col, spec.parent, spec.on_delete
      );
    exception when duplicate_object then null;
    end;
    execute format(
      'alter table public.%I validate constraint %I',
      spec.child, spec.child || '_' || spec.col || '_school_fkey'
    );
  end loop;
end
$$;

-- (3) Row-level security --------------------------------------------------------
do $$
declare
  t text;
  guc_pred constant text :=
    'school_id = any(string_to_array('
    || 'nullif(current_setting(''saferide.school_ids'', true), ''''), '','')::uuid[])';
begin
  if not exists (select 1 from pg_roles where rolname = 'saferide_app') then
    raise exception '016: the saferide_app role is missing — run the role sync first';
  end if;

  foreach t in array array[
    'run_positions', 'run_exceptions', 'run_exception_events', 'driver_action_keys'
  ] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('drop policy if exists school_isolation on public.%I', t);
    execute format(
      'create policy school_isolation on public.%I for all to saferide_app '
      'using (%s) with check (%s)',
      t, guc_pred, guc_pred
    );
    -- 013's default privileges already cover tables the master role creates;
    -- the explicit grant keeps this file self-sufficient and is idempotent.
    execute format(
      'grant select, insert, update, delete on public.%I to saferide_app', t
    );
  end loop;
end
$$;

-- (4) Columns on existing tables (one ALTER per hot table; nullable, no defaults)

-- The served position is current_lat/lng; the three qualifiers describe that
-- pair and are never read while it is null. Deliberately NO constraint ties
-- them to the pair: older writers null the pair and leave the qualifiers,
-- and the read rule tolerates it (rollback-neutral).
alter table live_buses
  add column if not exists position_source text
    check (position_source in ('checkpoint', 'action', 'ping')),
  add column if not exists position_at timestamptz,
  add column if not exists position_accuracy_m double precision;

-- The auth session that started the run; Phase 2 pings must match it.
alter table live_runs
  add column if not exists started_session_id text;

-- Per-school overrides of the system defaults (null = default). Bounds match
-- the plan: radii and thresholds 25–2000 m, retention 7–365 days, ping 5–60 s.
alter table live_schools
  add column if not exists custody_threshold_m integer
    check (custody_threshold_m between 25 and 2000),
  add column if not exists vicinity_radius_m integer
    check (vicinity_radius_m between 25 and 2000),
  add column if not exists fix_accuracy_cap_m integer
    check (fix_accuracy_cap_m between 25 and 2000),
  add column if not exists position_retention_days integer
    check (position_retention_days between 7 and 365),
  add column if not exists ping_interval_s integer
    check (ping_interval_s between 5 and 60);

-- (5) CHECK widenings last -------------------------------------------------------
-- Each recreated in ONE statement as the verbatim union of its CURRENT list
-- plus the new values. Never copy the list from an older migration: the local
-- seeds do not hold every type, so a stale list passes local rehearsal and
-- fails only on live.

-- Notifications: 011's fifteen values plus the remote-absent call-now notice
-- and the boarding retraction (its own type because the dedup index keys on
-- (user, run, student, type)).
alter table live_notifications
  drop constraint if exists live_notifications_type_check,
  add constraint live_notifications_type_check check (
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
      'route-unassigned',
      'absent-call-now',
      'boarding-corrected'
    )
  );

-- Incidents: 010's thirteen values plus the two office-only lifecycle
-- incidents the safety-critical exception kinds raise.
alter table live_incidents
  drop constraint if exists live_incidents_type_check,
  add constraint live_incidents_type_check check (
    type in (
      'breakdown',
      'accident',
      'student',
      'traffic',
      'arrival',
      'other',
      'cancellation',
      'run-started',
      'run-completed',
      'closure-refused',
      'force-closed',
      'handover-recorded',
      'action-reversed',
      'stop-bypassed',
      'absent-remote'
    )
  );

-- Audit: 013's forty-seven values plus the exception review.
alter table live_admin_audit
  drop constraint if exists live_admin_audit_action_check,
  add constraint live_admin_audit_action_check check (
    action in (
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
      'provider-account-created', 'provider-account-removed', 'provider-totp-reset',
      'exception-reviewed'
    )
  );
