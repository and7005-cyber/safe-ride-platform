-- 008: ops-refinement schema (plan 2026-07-06-001-feat-ops-refinement, U2).
--
-- One ordered migration carries every schema change for the ops refinement:
-- additive columns first (their NOT NULL DEFAULTs double as the backfill for
-- pre-existing rows), then the constraints that lock the new invariants in,
-- then the widened notification/incident type CHECKs. Live applies this file
-- as one implicit transaction (migrate handler simple-query protocol); local
-- psql is per-statement autocommit — after a failed local rehearsal, reset
-- the database rather than trusting a half-applied state.

-- 1. Absence scope + provenance ------------------------------------------------
-- scope: which part of the day the absence covers ('day' = whole day; partial
-- scopes gate rosters per run type but never the displayed status). source:
-- which kind of actor wrote the row — the one-way provenance ratchet (staff
-- may escalate a parent row, never the reverse) keys on it. The defaults
-- backfill every pre-existing row as a whole-day admin absence, which is
-- exactly what those rows meant. unique (student_id, absence_date) stays:
-- scope and source are transitions on the single row, never a second row.

alter table live_student_absences
  add column if not exists scope text not null default 'day';
alter table live_student_absences
  add column if not exists source text not null default 'admin';

alter table live_student_absences
  drop constraint if exists live_student_absences_scope_check;
alter table live_student_absences
  add constraint live_student_absences_scope_check check (
    scope in ('day', 'morning', 'afternoon')
  );

alter table live_student_absences
  drop constraint if exists live_student_absences_source_check;
alter table live_student_absences
  add constraint live_student_absences_source_check check (
    source in ('parent', 'driver', 'admin')
  );

-- 2. Route ordering authority + durable degradation signal ---------------------
-- manual_stop_order: an admin froze the stop order by hand (regeneration
-- preserves relative order instead of recomputing it). One ordering authority
-- per route: planner-authored custom_stops and manual_stop_order are mutually
-- exclusive — planner save paths clear the manual flag in the same UPDATE and
-- this CHECK is the race-proof backstop. last_recalc_degraded: the last
-- geometry recalculation fell back without Google on both provider signals;
-- persisted so the warning survives a page reload, cleared on the next
-- successful Google recalculation.

alter table live_routes
  add column if not exists manual_stop_order boolean not null default false;
alter table live_routes
  add column if not exists last_recalc_degraded boolean not null default false;

alter table live_routes
  drop constraint if exists live_routes_custom_manual_order_check;
alter table live_routes
  add constraint live_routes_custom_manual_order_check check (
    not (custom_stops and manual_stop_order)
  );

-- 3. Notifications: admit 'admin-notice' and 'ride-cancelled' -------------------
-- Recreate the type CHECK as the verbatim union of 007's nine values plus the
-- two new ones. Never copy the list from an older migration: the local seeds
-- hold no 'student-absent' rows, so a stale list would pass local rehearsal
-- and fail only on live.

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
    'ride-cancelled'
  )
);

-- 4. Incidents: admit 'cancellation' --------------------------------------------
-- Same verbatim-union rule against 004's six values (the CHECK was inline in
-- 004, auto-named live_incidents_type_check).

alter table live_incidents drop constraint if exists live_incidents_type_check;
alter table live_incidents add constraint live_incidents_type_check check (
  type in (
    'breakdown',
    'accident',
    'student',
    'traffic',
    'arrival',
    'other',
    'cancellation'
  )
);
