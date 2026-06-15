-- 006: per-date student absences (#7) + single-active-run guard (#12).
--
-- Adds an explicit per-date absence record so a student can be marked absent
-- for a specific day without mutating their persistent status, and a partial
-- unique index that makes it impossible for one bus to have two non-completed
-- runs on the same date (the same route is tied to one bus, so this also
-- prevents the same route being active twice at once).

create table if not exists live_student_absences (
  id uuid primary key default gen_random_uuid(),
  student_id uuid not null references live_students (id) on delete cascade,
  absence_date date not null,
  reason text,
  marked_by uuid references app_users (id) on delete set null,
  created_at timestamptz not null default now(),
  unique (student_id, absence_date)
);
create index if not exists live_student_absences_date_idx
  on live_student_absences (absence_date);

-- At most one non-completed run per bus per date. Completed runs are excluded
-- so a bus can still run its morning route and, after that completes, its
-- afternoon route on the same day.
create unique index if not exists live_runs_active_bus_date_key
  on live_runs (bus_id, date)
  where status <> 'completed';
