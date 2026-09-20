-- 014: multi-tenant schools — production data move to school #1 (U4).
-- Plan: docs/plans/2026-08-23-001-feat-multi-tenant-schools-plan.md
--
-- One DO block, four exits:
--   L: local stack (saferide_local_migrations exists) -> skip; the local seed
--      tail creates the equivalent state (the local "Greenfield" id IS the
--      populated school, so the production pin below cannot apply locally).
--   E: empty database (fresh bootstrap) -> skip.
--   M: already moved (tenancy_move_log has a 'move' row) -> skip.
--   Pinned pre-move state -> act. Anything else -> RAISE, rolling the whole
--   file back (one file = one implicit transaction; marker only on success).
--
-- The pin is identity and invariants, never live row counts (they drift daily
-- on a live school); counts are RECORDED into tenancy_move_log as evidence
-- and compared by the tenancy-post-move check set.
--
-- Amended 2026-09-20 against the Release 1 preflight contract
-- (docs/validation/2026-09-20-multi-tenant-release-1.md): Greenfield is
-- resolved BY NAME (production's id differs from the local seed's), its
-- recorded demo residue (runs and a fleet plan) is deleted by the act with
-- pinned counts, and the identity pin requires only the seeded admin —
-- the demo-disable list disables whichever of its addresses still exist.

do $$
declare
  greenfield uuid;
  msingi_code constant text := 'MSB-001';
  demo_disable constant text[] := array[
    'and7005@gmail.com', 'and7005@yahoo.it', 'francis@saferide.test', 'mary@saferide.test'
  ];
  target uuid;
  demo_present text[];
  gf_runs bigint := 0;
  gf_plans bigint := 0;
  gf_incidents bigint := 0;
  n bigint;
  scan record;
  stamp_counts jsonb;
  before_counts jsonb;
  after_counts jsonb;
begin
  -- Exit L: local development stack.
  if to_regclass('public.saferide_local_migrations') is not null then
    raise notice 'tenancy move: local stack detected — skipped (seed tail handles parity)';
    return;
  end if;

  -- Exit E: empty database.
  if (select count(*) from live_schools) = 0 and (select count(*) from app_users) = 0 then
    raise notice 'tenancy move: empty database — skipped';
    return;
  end if;

  -- Exit M: already moved.
  if exists (select 1 from tenancy_move_log where phase = 'move') then
    raise notice 'tenancy move: already applied — skipped';
    return;
  end if;

  -- ---- Pinned pre-move state (raise on any mismatch) ----------------------

  select count(*) into n from live_schools;
  if n <> 2 then
    raise exception 'tenancy move: expected exactly 2 school rows, found %', n;
  end if;
  -- By NAME: production's Greenfield id is not the local seed's id.
  select id into greenfield from live_schools where name = 'Greenfield Academy';
  if greenfield is null then
    raise exception 'tenancy move: no school named Greenfield Academy — demo row missing or renamed';
  end if;
  select id into target from live_schools where id <> greenfield;

  -- The populated school is the target; the demo row owns nothing.
  select count(*) into n from live_students where school_id = target;
  if n = 0 then
    raise exception 'tenancy move: target school % has no students — wrong pin?', target;
  end if;

  -- Catalog-driven: no foreign key to live_schools may reference Greenfield
  -- (the audit table is excluded: its rows may point there and are released
  -- to NULL by its pre-tenancy ON DELETE SET NULL; live_runs and
  -- live_fleet_plans are excluded because the recorded contract shows demo
  -- residue exactly there — the act deletes it with pinned counts below).
  for scan in
    select c.conrelid::regclass::text as tbl, a.attname as col
    from pg_constraint c
    join pg_attribute a on a.attrelid = c.conrelid and a.attnum = any (c.conkey)
    where c.confrelid = 'live_schools'::regclass and c.contype = 'f'
      and c.conrelid not in ('live_admin_audit'::regclass,
                             'live_runs'::regclass, 'live_fleet_plans'::regclass)
  loop
    execute format('select count(*) from %s where %I = $1', scan.tbl, scan.col)
      into n using greenfield;
    if n > 0 then
      raise exception 'tenancy move: % rows of % still reference Greenfield (%)',
        n, scan.tbl, scan.col;
    end if;
  end loop;
  select count(*) into gf_runs from live_runs where school_id = greenfield;
  select count(*) into gf_plans from live_fleet_plans where school_id = greenfield;

  -- Every non-NULL scope already names the target — except runs and plans,
  -- which may carry the Greenfield residue (and nothing but those two ids).
  for scan in
    select c.conrelid::regclass::text as tbl, a.attname as col
    from pg_constraint c
    join pg_attribute a on a.attrelid = c.conrelid and a.attnum = any (c.conkey)
    where c.confrelid = 'live_schools'::regclass and c.contype = 'f'
      and c.conrelid not in ('live_admin_audit'::regclass,
                             'live_runs'::regclass, 'live_fleet_plans'::regclass)
  loop
    execute format(
      'select count(*) from %s where %I is not null and %I <> $1',
      scan.tbl, scan.col, scan.col
    ) into n using target;
    if n > 0 then
      raise exception 'tenancy move: % rows of % carry a scope that is neither NULL nor the target',
        n, scan.tbl;
    end if;
  end loop;
  select count(*) into n from live_runs
  where school_id is not null and school_id not in (greenfield, target);
  if n > 0 then
    raise exception 'tenancy move: % runs name a third school', n;
  end if;
  select count(*) into n from live_fleet_plans
  where school_id is not null and school_id not in (greenfield, target);
  if n > 0 then
    raise exception 'tenancy move: % fleet plans name a third school', n;
  end if;

  -- The seeded admin must exist (it becomes the interim director); of the
  -- demo-disable list, production retains only a subset — record and act on
  -- whatever is actually there.
  if not exists (select 1 from app_users where email = 'admin@test.com') then
    raise exception 'tenancy move: the seeded admin (interim director) is missing';
  end if;
  select coalesce(array_agg(email order by email), '{}') into demo_present
  from app_users where email = any (demo_disable);
  if exists (
    select 1 from live_buses b join app_users u on u.id = b.driver_id
    where u.email = any (demo_disable)
  ) then
    raise exception 'tenancy move: a demo driver still drives a live bus — reassign it first';
  end if;

  select count(*) into n from live_runs where status <> 'completed';
  if n > 0 then
    raise exception 'tenancy move: % runs are not completed — run the move outside route hours', n;
  end if;

  -- ---- Act -----------------------------------------------------------------

  select jsonb_object_agg(t.tbl, t.c) into before_counts from (
    select 'live_buses' as tbl, count(*) as c from live_buses union all
    select 'live_routes', count(*) from live_routes union all
    select 'live_students', count(*) from live_students union all
    select 'live_runs', count(*) from live_runs union all
    select 'live_incidents', count(*) from live_incidents union all
    select 'live_student_absences', count(*) from live_student_absences union all
    select 'app_users', count(*) from app_users
  ) t;

  -- Demo residue (pinned above): Greenfield's run history and plan go first,
  -- so the stamp never derives children from rows that are about to vanish.
  -- Incidents hanging off those runs are demo rows too; notification feed
  -- rows release their run_id to NULL through their own FK.
  delete from live_incidents
  where run_id in (select id from live_runs where school_id = greenfield);
  get diagnostics gf_incidents = row_count;
  delete from live_runs where school_id = greenfield;
  get diagnostics n = row_count;
  if n <> gf_runs then
    raise exception 'tenancy move: deleted % Greenfield runs, pinned %', n, gf_runs;
  end if;
  delete from live_fleet_plans where school_id = greenfield;
  get diagnostics n = row_count;
  if n <> gf_plans then
    raise exception 'tenancy move: deleted % Greenfield fleet plans, pinned %', n, gf_plans;
  end if;

  select tenancy_stamp_school_one(target) into stamp_counts;

  -- Interim director for the seeded admin; a driver membership per driver.
  insert into school_memberships (user_id, school_id, role, state, accepted_at)
  select u.id, target, 'director', 'active', now()
  from app_users u
  where u.email = 'admin@test.com'
  on conflict do nothing;

  insert into school_memberships (user_id, school_id, role, state, accepted_at)
  select r.user_id, target, 'driver', 'active', now()
  from app_user_roles r
  where r.role = 'driver'
  on conflict do nothing;

  update live_schools set code = msingi_code where id = target and code is null;

  -- Demo identities: disabled, sessions revoked. The seeded admin stays as
  -- the interim director; its disable and password rotation land with 015.
  update app_users set disabled_at = now()
  where email = any (demo_disable) and disabled_at is null;
  update auth_sessions s set revoked_at = now()
  from app_users u
  where u.id = s.user_id and u.email = any (demo_disable) and s.revoked_at is null;

  -- Greenfield: re-assert zero references immediately before the delete.
  for scan in
    select c.conrelid::regclass::text as tbl, a.attname as col
    from pg_constraint c
    join pg_attribute a on a.attrelid = c.conrelid and a.attnum = any (c.conkey)
    where c.confrelid = 'live_schools'::regclass and c.contype = 'f'
      and c.conrelid <> 'live_admin_audit'::regclass
  loop
    execute format('select count(*) from %s where %I = $1', scan.tbl, scan.col)
      into n using greenfield;
    if n > 0 then
      raise exception 'tenancy move: % rows of % gained a Greenfield reference mid-move', n, scan.tbl;
    end if;
  end loop;
  delete from live_schools where id = greenfield;
  get diagnostics n = row_count;
  if n <> 1 then
    raise exception 'tenancy move: Greenfield delete removed % rows', n;
  end if;

  -- Post-move: zero NULL scope on every school-owned table.
  for scan in
    select unnest(array[
      'live_buses', 'live_routes', 'live_students', 'live_runs', 'live_fleet_plans',
      'live_incidents', 'live_student_absences', 'live_communicated_stops',
      'live_student_routes', 'live_route_stops', 'run_stops', 'run_absences',
      'run_participation'
    ]) as tbl
  loop
    execute format('select count(*) from %s where school_id is null', scan.tbl) into n;
    if n > 0 then
      raise exception 'tenancy move: % NULL-scope rows remain in %', n, scan.tbl;
    end if;
  end loop;

  select jsonb_object_agg(t.tbl, t.c) into after_counts from (
    select 'live_buses' as tbl, count(*) as c from live_buses union all
    select 'live_routes', count(*) from live_routes union all
    select 'live_students', count(*) from live_students union all
    select 'live_runs', count(*) from live_runs union all
    select 'live_incidents', count(*) from live_incidents union all
    select 'live_student_absences', count(*) from live_student_absences union all
    select 'app_users', count(*) from app_users
  ) t;

  insert into tenancy_move_log (phase, detail) values ('move', jsonb_build_object(
    'target_school', target,
    'target_code', msingi_code,
    'stamped', stamp_counts,
    'before', before_counts,
    'after', after_counts,
    'demo_disabled', to_jsonb(demo_present),
    'greenfield_deleted', greenfield,
    'greenfield_runs_deleted', gf_runs,
    'greenfield_plans_deleted', gf_plans,
    'greenfield_incidents_deleted', gf_incidents
  ));

  raise notice 'tenancy move: complete — target %, stamped %', target, stamp_counts;
end
$$;
