-- 015: tenancy constraints + row-level security (U14, Release 5).
--
-- Ordered: (1) catch-up stamp for rows written during the Release 3→4 window
-- and a zero-NULL assertion; (2) retire the shared production login;
-- (3) NOT NULL on every school-owned table and the school code; (4) composite
-- (id, school_id) parent keys and cross-table composite foreign keys, added
-- NOT VALID then validated, mirroring each existing key's ON DELETE and using
-- the column-list SET NULL form so a deleted parent nulls the reference but
-- never the school; (5) ENABLE (not FORCE) row level security with the
-- saferide_app policy on the thirteen school-owned tables and a split policy
-- on the audit (NULL-school writes for provider-kind rows only).
--
-- The migrate runner executes this file as ONE implicit transaction
-- (pgconn.exec_), so SET LOCAL is correct here and plain SET would be wrong:
-- the runner's autocommit session continues into the seed loop.
--
-- Local stacks (saferide_local_migrations marker present) keep the seeded
-- admin@test.com login alive: it is the demo director and the e2e admin.
-- Production has no marker: the shared login is disabled and its hash is
-- rotated to an unusable sentinel (nothing verifies against it), sessions
-- revoked — directors sign in with their own identities from Release 4.
--
-- Double-apply is a no-op end to end.

set local lock_timeout = '5s';
set local statement_timeout = '90s';

-- (1) Catch-up stamp -----------------------------------------------------------
do $$
declare
  target uuid;
  school_count integer;
  t text;
  nulls bigint;
begin
  if (select count(*) from public.live_schools) = 0 then
    raise notice '015: empty database — deferred to the post-seed pass';
    return;
  end if;
  select count(*) into school_count from public.live_schools;
  if school_count = 1 then
    select id into target from public.live_schools;
  else
    -- Local parity: the move log names the demo target school.
    select (detail->>'target_school')::uuid into target
    from public.tenancy_move_log
    where phase in ('act', 'local-tail')
    order by created_at desc limit 1;
  end if;
  if target is null then
    raise exception '015: cannot resolve the catch-up target school';
  end if;

  perform public.tenancy_stamp_school_one(target);

  foreach t in array array[
    'live_buses','live_routes','live_students','live_runs','live_fleet_plans',
    'live_incidents','live_student_absences','live_communicated_stops',
    'live_student_routes','live_route_stops','run_stops','run_absences',
    'run_participation'
  ] loop
    execute format('select count(*) from public.%I where school_id is null', t)
      into nulls;
    if nulls > 0 then
      raise exception '015: % rows of % still have no school after the catch-up stamp', nulls, t;
    end if;
  end loop;
end
$$;

-- (2) Retire the shared login (production only; local keeps the demo admin) ----
do $$
begin
  if (select count(*) from public.live_schools) = 0 then
    raise notice '015: empty database — deferred to the post-seed pass';
    return;
  end if;
  if to_regclass('public.saferide_local_migrations') is null then
    update public.app_users
    set disabled_at = coalesce(disabled_at, now()),
        password_hash = '!retired-by-015'
    where lower(email) = 'admin@test.com';
    update public.auth_sessions s
    set revoked_at = now()
    from public.app_users u
    where u.id = s.user_id
      and lower(u.email) = 'admin@test.com'
      and s.revoked_at is null;
  end if;
end
$$;

-- (3) NOT NULL ----------------------------------------------------------------
do $$
declare
  t text;
begin
  if (select count(*) from public.live_schools) = 0 then
    raise notice '015: empty database — deferred to the post-seed pass';
    return;
  end if;
  foreach t in array array[
    'live_buses','live_routes','live_students','live_runs','live_fleet_plans',
    'live_incidents','live_student_absences','live_communicated_stops',
    'live_student_routes','live_route_stops','run_stops','run_absences',
    'run_participation'
  ] loop
    execute format('alter table public.%I alter column school_id set not null', t);
  end loop;
  alter table public.live_schools alter column code set not null;
end
$$;

-- (4) Composite parent keys and foreign keys -----------------------------------
do $$
declare
  parent_key text;
begin
  if (select count(*) from public.live_schools) = 0 then
    raise notice '015: empty database — deferred to the post-seed pass';
    return;
  end if;
  foreach parent_key in array array['live_buses','live_routes','live_students','live_runs'] loop
    begin
      execute format(
        'alter table public.%I add constraint %I unique (id, school_id)',
        parent_key, parent_key || '_id_school_key'
      );
    exception when duplicate_table or duplicate_object then null;
    end;
  end loop;
end
$$;

do $$
declare
  spec record;
begin
  if (select count(*) from public.live_schools) = 0 then
    raise notice '015: empty database — deferred to the post-seed pass';
    return;
  end if;
  for spec in
    select * from (values
      ('live_routes',             'bus_id',     'live_buses',    'set null (bus_id)'),
      ('live_students',           'bus_id',     'live_buses',    'set null (bus_id)'),
      ('live_student_routes',     'student_id', 'live_students', 'cascade'),
      ('live_student_routes',     'route_id',   'live_routes',   'cascade'),
      ('live_route_stops',        'route_id',   'live_routes',   'cascade'),
      ('live_route_stops',        'student_id', 'live_students', 'set null (student_id)'),
      ('run_stops',               'run_id',     'live_runs',     'cascade'),
      ('run_stops',               'student_id', 'live_students', 'set null (student_id)'),
      ('run_absences',            'run_id',     'live_runs',     'cascade'),
      ('run_absences',            'student_id', 'live_students', 'set null (student_id)'),
      ('run_participation',       'run_id',     'live_runs',     'cascade'),
      ('run_participation',       'student_id', 'live_students', 'set null (student_id)'),
      ('live_incidents',          'bus_id',     'live_buses',    'set null (bus_id)'),
      ('live_incidents',          'run_id',     'live_runs',     'set null (run_id)'),
      ('live_incidents',          'student_id', 'live_students', 'set null (student_id)'),
      ('live_student_absences',   'student_id', 'live_students', 'cascade'),
      ('live_communicated_stops', 'student_id', 'live_students', 'cascade'),
      ('live_communicated_stops', 'bus_id',     'live_buses',    'set null (bus_id)'),
      ('live_parent_students',    'student_id', 'live_students', 'cascade')
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

-- (5) Row-level security --------------------------------------------------------
do $$
declare
  t text;
  guc_pred constant text :=
    'school_id = any(string_to_array('
    || 'nullif(current_setting(''saferide.school_ids'', true), ''''), '','')::uuid[])';
begin
  if (select count(*) from public.live_schools) = 0 then
    raise notice '015: empty database — deferred to the post-seed pass';
    return;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'saferide_app') then
    raise exception '015: the saferide_app role is missing — run the role sync first';
  end if;

  foreach t in array array[
    'live_buses','live_routes','live_students','live_runs','live_fleet_plans',
    'live_incidents','live_student_absences','live_communicated_stops',
    'live_student_routes','live_route_stops','run_stops','run_absences',
    'run_participation'
  ] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('drop policy if exists school_isolation on public.%I', t);
    execute format(
      'create policy school_isolation on public.%I for all to saferide_app '
      'using (%s) with check (%s)',
      t, guc_pred, guc_pred
    );
  end loop;

  -- The audit's split policy: reads stay inside the caller's school set; a
  -- NULL-school (or any-school) write is admitted for provider-kind rows only
  -- (the provider console writes on a GUC-less connection); staff rows must
  -- land inside the armed school. No UPDATE/DELETE policy exists at all: the
  -- runtime role can never rewrite history.
  alter table public.live_admin_audit enable row level security;
  drop policy if exists audit_school_read on public.live_admin_audit;
  execute format(
    'create policy audit_school_read on public.live_admin_audit '
    'for select to saferide_app using (%s)', guc_pred);
  drop policy if exists audit_split_write on public.live_admin_audit;
  execute format(
    'create policy audit_split_write on public.live_admin_audit '
    'for insert to saferide_app with check (actor_kind = ''provider'' or (%s))',
    guc_pred);
end
$$;
