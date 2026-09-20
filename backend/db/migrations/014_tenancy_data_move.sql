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

do $$
declare
  greenfield constant uuid := '5cae0000-0000-0000-0000-000000000001';
  msingi_code constant text := 'MSB-001';
  demo_disable constant text[] := array[
    'and7005@gmail.com', 'and7005@yahoo.it', 'francis@saferide.test', 'mary@saferide.test'
  ];
  target uuid;
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
  if not exists (select 1 from live_schools where id = greenfield) then
    raise exception 'tenancy move: the Greenfield demo row (%) is missing', greenfield;
  end if;
  select id into target from live_schools where id <> greenfield;

  -- The populated school is the target; the demo row owns nothing.
  select count(*) into n from live_students where school_id = target;
  if n = 0 then
    raise exception 'tenancy move: target school % has no students — wrong pin?', target;
  end if;

  -- Catalog-driven: no foreign key to live_schools may reference Greenfield
  -- (the audit table is excluded: its rows may point there and are released
  -- to NULL by its pre-tenancy ON DELETE SET NULL).
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
      raise exception 'tenancy move: % rows of % still reference Greenfield (%)',
        n, scan.tbl, scan.col;
    end if;
  end loop;

  -- Every non-NULL scope already names the target.
  for scan in
    select c.conrelid::regclass::text as tbl, a.attname as col
    from pg_constraint c
    join pg_attribute a on a.attrelid = c.conrelid and a.attnum = any (c.conkey)
    where c.confrelid = 'live_schools'::regclass and c.contype = 'f'
      and c.conrelid <> 'live_admin_audit'::regclass
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

  -- The seed-003 identities exist, and no demo driver drives a live bus.
  select count(*) into n from app_users
  where email = any (demo_disable || array['admin@test.com']);
  if n <> 5 then
    raise exception 'tenancy move: expected the 5 seed identities, found %', n;
  end if;
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
    'demo_disabled', demo_disable,
    'greenfield_deleted', greenfield
  ));

  raise notice 'tenancy move: complete — target %, stamped %', target, stamp_counts;
end
$$;
