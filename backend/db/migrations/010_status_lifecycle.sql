-- 010: status lifecycle schema (plan 2026-07-28-001-feat-status-lifecycle-consistency, U1).
--
-- Schema-only release. No application code reads or writes anything added here
-- until the next release ships: the migrate Lambda resolves migration files
-- from its own deployed package, so the schema has to be live before the code
-- that depends on it, not alongside it.
--
-- One ordered migration carries every schema change: additive tables and
-- columns first (their NOT NULL DEFAULTs double as the backfill for
-- pre-existing rows), then the data backfills that make cutover day safe, then
-- the widened type CHECKs. Live applies this file as one implicit transaction
-- (migrate handler simple-query protocol); local psql is per-statement
-- autocommit — after a failed local rehearsal, reset the database rather than
-- trusting a half-applied state.

-- 1. Per-run participation record ---------------------------------------------
-- The record of what actually happened to each child on each run. Boarding
-- currently exists only in live_students.status — a single mutable column with
-- no history — so a run cannot reconstruct who was on it, which is why ending a
-- run has to sweep and why a read-time staleness derivation exists at all.
--
-- Timestamps carry the facts; there are no redundant boolean mirrors. The one
-- flag is boarded_presumed, which marks the afternoon auto-board as an
-- assumption rather than an observation.
--
-- handover_note and contacted_at/contacted_by live on this row on purpose: the
-- off-route handover and the force-close contact obligation both attach to one
-- child on one run, and putting them anywhere else would force a second
-- migration in a later release.
--
-- student_name is denormalized for the same reason run_absences denormalizes
-- it: student_id is ON DELETE SET NULL, and a name-less record would rot once a
-- student is deleted.

create table if not exists run_participation (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references live_runs (id) on delete cascade,
  student_id uuid references live_students (id) on delete set null,
  student_name text not null,
  boarded_at timestamptz,
  boarded_presumed boolean not null default false,
  dropped_off_at timestamptz,
  handover_at timestamptz,
  handover_note text,
  unaccounted_at timestamptz,
  acting_driver_id uuid references app_users (id) on delete set null,
  contacted_at timestamptz,
  contacted_by uuid references app_users (id) on delete set null,
  created_at timestamptz not null default now(),
  unique (run_id, student_id)
);
create index if not exists run_participation_run_idx on run_participation (run_id);
create index if not exists run_participation_student_idx on run_participation (student_id);

-- 2. Absence: which period was individually marked ------------------------------
-- scope records what the row now covers; marked_period records what the last
-- writer actually witnessed. Widening a parent's morning cancellation with a
-- driver's afternoon mark collapses scope to 'day' and would otherwise destroy
-- the evidence the run report and the admin absence list need to tell an
-- afternoon non-boarding from a morning no-show. Nullable: pre-existing rows
-- have no recorded marking, and 'unknown' is the honest value for them.

alter table live_student_absences add column if not exists marked_period text;

alter table live_student_absences
  drop constraint if exists live_student_absences_marked_period_check;
alter table live_student_absences
  add constraint live_student_absences_marked_period_check check (
    marked_period is null or marked_period in ('day', 'morning', 'afternoon')
  );

-- 3. Run-absence snapshot: the same distinction, in the run report --------------
-- run_absences is what the run report reads. Without a period here the
-- distinction exists in the absence table and cannot reach the report.

alter table run_absences add column if not exists period text;

alter table run_absences drop constraint if exists run_absences_period_check;
alter table run_absences add constraint run_absences_period_check check (
  period is null or period in ('day', 'morning', 'afternoon')
);

-- 4. Stop arrivals: when, not just how many ------------------------------------
-- arrive_next_stop records arrival as a counter (stops_completed) and nothing
-- timestamps a non-gate arrival. The no-progress flag needs elapsed time
-- between arrivals, and the office force-close needs evidence that the bus
-- actually reached the school gate before it asserts a child arrived safely.

alter table run_stops add column if not exists arrived_at timestamptz;

-- 5. Incidents: mark the run-lifecycle rows -------------------------------------
-- The parent alerts feed reads live_incidents filtered only on child-stamped
-- rows, so bus-scoped rows reach parents by default — which is why arrival
-- alerts appear in the parent app today. Run-lifecycle alerts must not, and
-- must not inflate the incidents-today tile or the acknowledgement queue
-- either. One marker serves all three exclusions.

alter table live_incidents add column if not exists lifecycle boolean not null default false;
create index if not exists live_incidents_lifecycle_idx
  on live_incidents (created_at desc)
  where lifecycle = false;

-- 6. Bus availability: the one thing no derivation can produce -------------------
-- Bus status becomes a read-time derivation from the bus's current run. Whether
-- a bus is in the workshop is not a function of its runs, so availability stays
-- an explicit office-set attribute and carries the retiring status column's
-- 'offline' meaning forward. live_buses.status itself is NOT dropped here: no
-- column has ever been dropped in this repo and there is no rollback
-- convention, so the write path goes first and the column follows later.

alter table live_buses add column if not exists availability text not null default 'in-service';

alter table live_buses drop constraint if exists live_buses_availability_check;
alter table live_buses add constraint live_buses_availability_check check (
  availability in ('in-service', 'out-of-service')
);

-- 7. Backfills -------------------------------------------------------------------
-- Availability: 'offline' is the literal stored value in 004's CHECK
-- ('idle', 'active', 'delayed', 'offline'). Matching on anything else returns
-- every out-of-service bus silently to service, and the local seeds may hold no
-- offline bus at all — so a wrong predicate would pass rehearsal and fail only
-- on live. Every other value means the bus is available.

update live_buses set availability = 'out-of-service'
where status = 'offline' and availability <> 'out-of-service';

-- Participation, for today's runs only. Without this, a run already in progress
-- when the code release lands has no participation rows: every child on it
-- reads as unaccounted, every parent surface shows them at home, and the driver
-- cannot end the run. Seed from what the status column currently records —
-- boarded for a child on the bus or at school, dropped off for one already
-- home — and write no row for anyone else, so the closure gate surfaces them
-- honestly rather than inventing an outcome. Completed runs from today are
-- included so their children's derived state does not change under them.
--
-- created_at is the timestamp used because live_runs.start_time is free text
-- ('HH24:MI') and a backfilled approximate time is more honest than a parsed
-- one.

insert into run_participation (run_id, student_id, student_name, boarded_at, dropped_off_at, acting_driver_id)
select distinct on (r.id, s.id)
       r.id,
       s.id,
       s.name,
       case when s.status in ('on-bus', 'at-school') then r.created_at end,
       case when s.status = 'dropped-off' then r.created_at end,
       r.driver_id
from live_runs r
join run_stops rs on rs.run_id = r.id and rs.student_id is not null
join live_students s on s.id = rs.student_id
where r.date = (now() at time zone 'Africa/Nairobi')::date
  and s.status in ('on-bus', 'at-school', 'dropped-off')
on conflict (run_id, student_id) do nothing;

-- Arrival timestamps for stops today's runs have already passed. The
-- no-progress flag would otherwise read an in-flight run as having never
-- progressed and flag it the moment the derivation goes live, and the
-- force-close would withhold the arrival notification from a run that did
-- reach the school gate.

update run_stops rs
set arrived_at = r.created_at
from live_runs r
where rs.run_id = r.id
  and r.date = (now() at time zone 'Africa/Nairobi')::date
  and rs.stop_order <= r.stops_completed
  and rs.arrived_at is null;

-- 8. Type CHECKs last ------------------------------------------------------------
-- Each recreated as the verbatim union of its current list plus the new values.
-- Never copy the list from an older migration: the local seeds do not hold
-- every type, so a stale list passes local rehearsal and fails only on live.

-- Incidents: 008's seven values plus the run-lifecycle and closure types.
alter table live_incidents drop constraint if exists live_incidents_type_check;
alter table live_incidents add constraint live_incidents_type_check check (
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
    'action-reversed'
  )
);

-- Notifications: 008's eleven values plus the two driver-correction messages.
-- A correction needs its own type because the notification dedup index keys on
-- (user, run, student, type) — reusing the original type would suppress the
-- correction and leave the parent holding the false assertion.
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
    'absence-corrected'
  )
);
