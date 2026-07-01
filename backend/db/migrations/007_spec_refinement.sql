-- 007: spec-refinement schema (plan 2026-07-01-001-feat-spec-refinement-three-views, U1).
--
-- One ordered migration carries every schema change for the spec refinement:
-- additive columns first, then the run_absences snapshot table, then the
-- notification/incident run_type plumbing, then data cleanup (route dedup,
-- bus re-derivation, parent-email backfill) with the constraints that lock
-- the cleaned state in. Parent 2 reuses the existing parent_phone2 column
-- for their phone, so only parent2_name / parent2_email are added.

-- 1. Planner persistence on routes ------------------------------------------
-- polyline / totals come from the saved planner option; custom_stops flags
-- routes whose stops were authored in the planner (regeneration skips them).

alter table live_routes add column if not exists polyline text;
alter table live_routes add column if not exists total_distance_m integer;
alter table live_routes add column if not exists total_duration_s integer;
alter table live_routes add column if not exists custom_stops boolean not null default false;

-- 2. Second-parent contact slots ---------------------------------------------
-- No parent2_phone: the pre-existing live_students.parent_phone2 is Parent 2's
-- phone (no rename, no data copy).

alter table live_students add column if not exists parent2_name text;
alter table live_students add column if not exists parent2_email text;

-- 3. Per-run absence snapshot -------------------------------------------------
-- student_name is denormalized on purpose: run_stops.student_id is
-- ON DELETE SET NULL elsewhere, and a name-less snapshot would rot once a
-- student is deleted. One absence row per (run, student).

create table if not exists run_absences (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references live_runs (id) on delete cascade,
  student_id uuid references live_students (id) on delete set null,
  student_name text not null,
  reason text,
  created_at timestamptz not null default now(),
  unique (run_id, student_id)
);
create index if not exists run_absences_run_idx on run_absences (run_id);

-- 4. Notifications: persisted run_type + 'student-absent' type ---------------
-- run_id is ON DELETE SET NULL, so deriving the period via a join silently
-- loses it when admins delete runs; persist it instead and backfill from the
-- runs that still survive.

alter table live_notifications add column if not exists run_type text
  check (run_type in ('morning', 'afternoon'));

update live_notifications n
set run_type = r.type
from live_runs r
where n.run_id = r.id
  and n.run_type is null;

-- Widen the type CHECK (auto-named live_notifications_type_check by the
-- inline constraint in 005) to admit 'student-absent'.
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
    'student-absent'
  )
);

-- 5. Incidents: student stamp + run_type --------------------------------------
-- Student-stamped incidents are the school-side absence channel; parent feeds
-- exclude them (app layer). Same persisted run_type rationale as above.

alter table live_incidents add column if not exists student_id uuid
  references live_students (id) on delete set null;
alter table live_incidents add column if not exists run_type text
  check (run_type in ('morning', 'afternoon'));

-- 6. Route dedup, then one route per (bus, type) -------------------------------
-- For each (bus_id, type) group with more than one route keep the earliest
-- created_at (tie-break on id) and detach the rest, so the partial unique
-- index below can be created against live data.

with ranked as (
  select id,
         row_number() over (
           partition by bus_id, type
           order by created_at asc, id asc
         ) as rn
  from live_routes
  where bus_id is not null
)
update live_routes r
set bus_id = null
from ranked
where r.id = ranked.id
  and ranked.rn > 1;

create unique index if not exists live_routes_bus_type_key
  on live_routes (bus_id, type)
  where bus_id is not null;

-- 7. Re-derive live_students.bus_id -------------------------------------------
-- Mirrors StudentLiveDao._derive_student_bus exactly: among the student's
-- routes with a bus, the morning route wins, then earliest created_at; NULL
-- only when no linked route has a bus. Run for ALL students so the dedup
-- above can't leave the denormalized bus_id pointing at a detached route.

update live_students s
set bus_id = (
  select r.bus_id
  from live_student_routes sr
  join live_routes r on r.id = sr.route_id
  where sr.student_id = s.id
    and r.bus_id is not null
  order by case when r.type = 'morning' then 0 else 1 end, r.created_at asc
  limit 1
);

-- 8. Backfill empty parent-email slots from linked accounts --------------------
-- Emails only (parent2_name stays empty until the first form edit): for each
-- student with live_parent_students links, fill parent_email first when
-- empty, then parent2_email, taking linked app_users accounts ordered by
-- account creation then link id, skipping emails already present in either
-- slot case-insensitively, at most 2 slots. Never overwrites a non-empty
-- slot.

do $$
declare
  student record;
  cand record;
  slot1 text;
  slot2 text;
begin
  for student in
    select s.id,
           nullif(btrim(coalesce(s.parent_email, '')), '') as email1,
           nullif(btrim(coalesce(s.parent2_email, '')), '') as email2
    from live_students s
    where exists (
      select 1 from live_parent_students ps where ps.student_id = s.id
    )
  loop
    slot1 := student.email1;
    slot2 := student.email2;

    for cand in
      select u.email
      from live_parent_students ps
      join app_users u on u.id = ps.parent_id
      where ps.student_id = student.id
        and nullif(btrim(coalesce(u.email, '')), '') is not null
      order by u.created_at asc, ps.id asc
    loop
      exit when slot1 is not null and slot2 is not null;
      if (slot1 is not null and lower(slot1) = lower(cand.email))
         or (slot2 is not null and lower(slot2) = lower(cand.email)) then
        continue;
      end if;
      if slot1 is null then
        slot1 := cand.email;
        update live_students set parent_email = cand.email where id = student.id;
      else
        slot2 := cand.email;
        update live_students set parent2_email = cand.email where id = student.id;
      end if;
    end loop;
  end loop;
end $$;
