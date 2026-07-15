-- 009: route-planner schema (plan 2026-07-09-001-feat-route-planner, U2).
--
-- One ordered migration carries every schema change for the route planner:
-- additive columns first, then the route_type maintenance triggers, then the
-- backfills (which must run before every constraint and SET NOT NULL), then the
-- one-time allocation dedup + reconciliation, then the constraints that lock the
-- new invariants in. Live applies this file as one implicit transaction (migrate
-- handler simple-query protocol); local psql is per-statement autocommit — after
-- a failed local rehearsal, reset the database rather than trusting a
-- half-applied state. A DEFERRABLE UNIQUE validates existing rows immediately,
-- so the dedup must precede it.

-- 1. Additive columns ---------------------------------------------------------
-- Bell times (Africa/Nairobi HH:MM): the school's default gate anchor — morning
-- arrival, afternoon departure. A route's gate_anchor overrides the school bell
-- (null = inherit); one authority, school default -> route override. trip_index
-- lets one bus hold ordered trips per period. stops_computed is the explicit
-- "was this route geometry-computed" marker, replacing the fragile inference off
-- the gate row's scheduled_time (a typed gate time would make that ambiguous).

alter table live_schools add column if not exists morning_bell text;
alter table live_schools add column if not exists afternoon_bell text;

alter table live_routes add column if not exists gate_anchor text;
alter table live_routes add column if not exists trip_index integer not null default 1;
alter table live_routes add column if not exists stops_computed boolean not null default false;

-- Overnight depot: a bus attribute that enters geometry only as legs on the
-- bus's first-morning / last-afternoon trip (never a stop row). depot_provenance
-- shares the place-provenance enum.
alter table live_buses add column if not exists depot_lat double precision;
alter table live_buses add column if not exists depot_lng double precision;
alter table live_buses add column if not exists depot_address text;
alter table live_buses add column if not exists depot_provenance text;

-- route_type is denormalized from live_routes.type so the cross-period
-- allocation uniqueness can be expressed as a DEFERRABLE constraint (a partial
-- unique index cannot defer). Added NULLABLE: a non-empty table cannot take NOT
-- NULL without a default, and the seed re-insert path omits the column — the
-- trigger below populates it, then the backfill sets it NOT NULL.
alter table live_student_routes add column if not exists route_type text;

-- Coordinate provenance on the student home. Default NULL (NOT 'legacy'): a
-- 'legacy' default would mislabel a freshly typed/picked home and defeat the
-- picked-preservation rule; existing rows are backfilled to 'legacy' explicitly.
-- (live_route_stops intentionally gets NO provenance column — stops are
-- server-regenerated and carry no source provenance, so it would be dead data.)
alter table live_students add column if not exists provenance text;

-- NULL-permitting enum CHECKs, per the 004/008 house style.
alter table live_buses drop constraint if exists live_buses_depot_provenance_check;
alter table live_buses add constraint live_buses_depot_provenance_check check (
  depot_provenance in ('typed', 'picked', 'imported', 'legacy')
);
alter table live_students drop constraint if exists live_students_provenance_check;
alter table live_students add constraint live_students_provenance_check check (
  provenance in ('typed', 'picked', 'imported', 'legacy')
);

-- 2. route_type maintenance triggers ------------------------------------------
-- One mechanism keeps the denormalized route_type consistent everywhere: it is
-- populated from the parent route on link insert/route_id change (covers API
-- inserts, seed inserts that omit the column, and _sync_routes), and a route
-- type-flip cascades to every link. Without this a flipped route would leave
-- stale route_type on its links and silently double-book a student onto two
-- same-type routes — the exact invariant R21/R23 exist to hold.

-- Table refs are schema-qualified: the local snapshot seed restores under an
-- empty search_path (SET search_path = ''), so an unqualified live_routes would
-- raise "relation does not exist" when this fires as an ENABLE ALWAYS trigger.
create or replace function live_student_routes_set_route_type() returns trigger as $$
begin
  select type into new.route_type from public.live_routes where id = new.route_id;
  return new;
end;
$$ language plpgsql;

drop trigger if exists trg_live_student_routes_route_type on live_student_routes;
create trigger trg_live_student_routes_route_type
  before insert or update of route_id on live_student_routes
  for each row execute function live_student_routes_set_route_type();
-- ENABLE ALWAYS so it fires even under session_replication_role = replica: the
-- local snapshot seed (003_local_snapshot.sql) restores with triggers disabled,
-- and its links would otherwise land with a NULL route_type and trip the NOT
-- NULL below. The dump inserts live_routes before live_student_routes, so the
-- parent route is always present when this derives its type.
alter table live_student_routes enable always trigger trg_live_student_routes_route_type;

create or replace function live_routes_cascade_route_type() returns trigger as $$
begin
  if new.type is distinct from old.type then
    update public.live_student_routes set route_type = new.type where route_id = new.id;
  end if;
  return new;
end;
$$ language plpgsql;

drop trigger if exists trg_live_routes_cascade_route_type on live_routes;
create trigger trg_live_routes_cascade_route_type
  after update of type on live_routes
  for each row execute function live_routes_cascade_route_type();

-- 3. Backfills (strictly before every constraint / SET NOT NULL) --------------
-- stops_computed = true where the route already carries a computed gate time
-- (only the geometry path writes a gate scheduled_time) — reproduces today's
-- inferred "previously computed" behaviour at the cutover.
update live_routes r set stops_computed = true
where exists (
  select 1 from live_route_stops s
  where s.route_id = r.id and s.is_school_gate and s.scheduled_time is not null
);

-- route_type from the route join for pre-existing rows (the trigger only fires
-- on new inserts / route_id changes), then lock it NOT NULL so the deferrable
-- unique actually enforces (unique treats NULLs as distinct).
update live_student_routes sr
set route_type = r.type
from live_routes r
where sr.route_id = r.id and sr.route_type is null;
alter table live_student_routes alter column route_type set not null;

-- Existing home coordinates predate provenance tracking: stamp them 'legacy'.
update live_students
set provenance = 'legacy'
where provenance is null
  and (home_lat is not null or nullif(btrim(coalesce(home_address, '')), '') is not null);

-- 4. One-time allocation dedup + reconciliation -------------------------------
-- Keep the earliest link per (student, route_type) — ordered by the route's
-- creation (live_student_routes has no timestamp of its own) — and detach the
-- rest, so the deferrable unique below can be created against clean data.
with ranked as (
  select sr.id,
         row_number() over (
           partition by sr.student_id, r.type
           order by r.created_at asc, sr.id asc
         ) as rn
  from live_student_routes sr
  join live_routes r on r.id = sr.route_id
)
delete from live_student_routes sr
using ranked
where sr.id = ranked.id and ranked.rn > 1;

-- Re-derive live_students.bus_id for all students (mirrors 007 / _derive_student
-- _bus): a pure-SQL migration runs no application logic, so the denormalized bus
-- must be refreshed or it could point at a detached route.
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

-- Drop stop rows orphaned by the dedup: a student stop whose (route_id,
-- student_id) no longer has a link. regenerate_route_stops rebuilds full
-- fidelity (order/times) on the route's next roster mutation.
delete from live_route_stops s
where s.student_id is not null
  and not exists (
    select 1 from live_student_routes sr
    where sr.route_id = s.route_id and sr.student_id = s.student_id
  );

-- 5. Constraints last ---------------------------------------------------------
-- Relax one-route-per-(bus,type) to allow ordered multi-trip. Dropping the old
-- 2-column index is mandatory: leaving it would silently forbid multi-trip with
-- no schema error.
drop index if exists live_routes_bus_type_key;
create unique index if not exists live_routes_bus_type_key
  on live_routes (bus_id, type, trip_index)
  where bus_id is not null;

-- One morning + one afternoon route per student, race-proof. Non-partial (so it
-- can defer) on the denormalized route_type; DEFERRABLE INITIALLY DEFERRED so a
-- same-period move (delete-before-insert in one transaction) never trips it.
alter table live_student_routes drop constraint if exists live_student_routes_student_type_key;
alter table live_student_routes add constraint live_student_routes_student_type_key
  unique (student_id, route_type) deferrable initially deferred;
