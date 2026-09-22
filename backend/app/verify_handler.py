"""Read-only database verification Lambda for the SafeRide backend.

Closes the go/no-go gap named in
``docs/work/validation/2026-08-20-fleet-plan-drafting.md``: production RDS
sits in private subnets with no bastion, so post-deploy verification queries
had no executor beyond the migrate Lambda's own success flag. This function
runs a FIXED ALLOWLIST of named check sets and returns their rows as JSON —
it is deliberately not a SQL console:

- Only the check sets registered below can run; the event never carries SQL.
- The single event parameter (``school_id``) is validated as a UUID before use
  and only ever bound through psycopg placeholders.
- Every connection forces ``default_transaction_read_only=on`` at the server,
  so even a rogue registry edit cannot write (belt and braces with the
  SELECT-only registry).

Invoke manually (or via ``infra/scripts/verify-db.sh``); it is not wired to
the API — like the migrate Lambda, IAM is the only door.

Check sets mirror the deployment checklists in the fleet-plan validation
record and the plan's Operational Notes: the Lambda reports OBSERVATIONS;
pass/fail judgment stays with the operator reading the checklist.
"""

from __future__ import annotations

import datetime as dt
import decimal
import os
import uuid

import psycopg
from psycopg.rows import dict_row

from app.core.config import get_settings
from app.core.tenancy import (
    GREENFIELD_SCHOOL_ID,
    SCHOOL_OWNED_TABLES,
    SEED_DEMO_EMAILS,
)

# The system retention default the `gps` set reports against (GPS plan U11):
# the same Settings field the API and the migrate Lambda's purge resolve, so
# GPS_POSITION_RETENTION_DAYS is threaded to this function's env too and the
# observation never disagrees with the purge. A validated int, spliced as a
# literal into the fixed check SQL.
_RETENTION_DEFAULT_DAYS = int(get_settings().gps_position_retention_days)

# One statement per (label, sql, needs_school) entry. needs_school entries
# bind the validated school_id once per placeholder occurrence.
_CHECK_SETS: dict[str, list[tuple[str, str, bool]]] = {
    # Which migrations/seeds the marker table records, newest last.
    "migrations": [
        (
            "applied-migrations",
            "select id, applied_at from public.saferide_migrations order by applied_at, id",
            False,
        ),
    ],
    # Release-1 go/no-go for migration 011 (fleet plans). Expected values live
    # in the validation record: new tables empty pre-use, zero claims, zero
    # plan_ordered, zero pattern mismatches, 15 CHECK values, both partial
    # uniques plus the plan dedup index present.
    "migration-011": [
        (
            "new-tables-row-counts",
            "select (select count(*) from live_fleet_plans) as plans, "
            "(select count(*) from live_communicated_stops) as baselines, "
            "(select count(*) from live_admin_audit) as audit_rows",
            False,
        ),
        (
            "buses-claimed",
            "select count(*) as buses, count(school_id) as claimed from live_buses",
            False,
        ),
        (
            "plan-ordered-routes",
            "select count(*) as plan_ordered from live_routes where plan_ordered",
            False,
        ),
        (
            "ridership-pattern-distribution",
            "select ridership_pattern, count(*) from live_students "
            "group by 1 order by 2 desc, 1",
            False,
        ),
        (
            "ridership-pattern-mismatches",
            "with links as ("
            "  select s.id,"
            "         count(*) filter (where sr.route_type = 'morning') as am,"
            "         count(*) filter (where sr.route_type = 'afternoon') as pm,"
            "         max(case when sr.route_type = 'morning' then r.bus_id::text end) as am_bus,"
            "         max(case when sr.route_type = 'afternoon' then r.bus_id::text end) as pm_bus"
            "  from live_students s"
            "  left join live_student_routes sr on sr.student_id = s.id"
            "  left join live_routes r on r.id = sr.route_id"
            "  group by s.id)"
            " select count(*) as mismatches"
            " from live_students s join links l on l.id = s.id"
            " where s.ridership_pattern <> case"
            "   when l.am > 0 and l.pm = 0 then 'morning_only'"
            "   when l.am = 0 and l.pm > 0 then 'afternoon_only'"
            "   when l.am > 0 and l.pm > 0 and l.am_bus is not null"
            "        and l.pm_bus is not null and l.am_bus <> l.pm_bus then 'split'"
            "   else 'both_ways' end",
            False,
        ),
        (
            "notification-type-check",
            "select pg_get_constraintdef(oid) as definition from pg_constraint "
            "where conname = 'live_notifications_type_check'",
            False,
        ),
        (
            "plan-partial-uniques",
            "select indexname from pg_indexes where tablename = 'live_fleet_plans' "
            "and indexname in ('live_fleet_plans_school_draft_key', "
            "'live_fleet_plans_school_previous_key') order by indexname",
            False,
        ),
        (
            "notification-plan-dedup-index",
            "select indexname from pg_indexes "
            "where indexname = 'live_notifications_plan_dedup'",
            False,
        ),
    ],
    # Pre-apply capture for a school (record these, compare after apply).
    "baseline": [
        (
            "route-count",
            "select count(*) as routes from live_routes where school_id = %s",
            True,
        ),
        (
            "stop-count",
            "select count(*) as stops from live_route_stops rs "
            "join live_routes r on r.id = rs.route_id where r.school_id = %s",
            True,
        ),
        (
            "link-count",
            "select count(*) as links from live_student_routes sr "
            "join live_routes r on r.id = sr.route_id where r.school_id = %s",
            True,
        ),
        (
            "run-count",
            "select count(*) as runs from live_runs rn "
            "join live_routes r on r.id = rn.route_id where r.school_id = %s",
            True,
        ),
        (
            "baselines-by-leg",
            "select c.route_type, count(*) from live_communicated_stops c "
            "join live_students s on s.id = c.student_id "
            "where s.school_id = %s group by 1 order by 1",
            True,
        ),
    ],
    # First-apply (and any apply/restore) go/no-go for a school.
    "post-apply": [
        (
            "latest-plan-audit-rows",
            "select id, action, actor_name, created_at, detail from live_admin_audit "
            "where school_id = %s and action in ('plan-applied', 'plan-restored') "
            "order by created_at desc limit 5",
            True,
        ),
        (
            "plan-statuses",
            "select status, count(*) from live_fleet_plans "
            "where school_id = %s group by 1 order by 1",
            True,
        ),
        (
            "route-authority-flags",
            "select count(*) filter (where plan_ordered) as plan_ordered, "
            "count(*) filter (where custom_stops) as custom, "
            "count(*) filter (where manual_stop_order) as frozen, "
            "count(*) filter (where last_recalc_degraded) as degraded, "
            "count(*) as total from live_routes where school_id = %s",
            True,
        ),
        (
            # The morning-clock rule: a PM-only child never carries a
            # pickup_time. Expected: 0.
            "pm-only-pickup-time-violations",
            "select count(*) as violations from live_students s "
            "where s.school_id = %s and s.pickup_time is not null "
            "and not exists (select 1 from live_student_routes sr "
            "  where sr.student_id = s.id and sr.route_type = 'morning')",
            True,
        ),
        (
            "baselines-by-leg",
            "select c.route_type, count(*) from live_communicated_stops c "
            "join live_students s on s.id = c.student_id "
            "where s.school_id = %s group by 1 order by 1",
            True,
        ),
        (
            # Distinct families the latest apply/restore act actually wrote
            # feed rows for — must equal the count its response reported.
            "latest-act-notified-families",
            "select count(distinct n.user_id) as families from live_notifications n "
            "where n.plan_audit_id = (select id from live_admin_audit "
            "  where school_id = %s and action in ('plan-applied', 'plan-restored') "
            "  order by created_at desc limit 1)",
            True,
        ),
        (
            "run-count",
            "select count(*) as runs from live_runs rn "
            "join live_routes r on r.id = rn.route_id where r.school_id = %s",
            True,
        ),
    ],
}


# --- Tenancy check sets (U2) -------------------------------------------------
# Gates for the five tenancy releases (plan Operational Notes). The SQL is
# generated from app.core.tenancy's canonical table list at authoring time so
# a table cannot fall out of the checked surface; entries that reference
# objects an environment does not yet have (pre-013 columns, RLS grants) come
# back as labeled per-check errors — observations, not crashes.

_CHECK_SETS["tenancy-preflight"] = [
    (
        "school-rows",
        "select id, name, created_at from live_schools order by created_at",
        False,
    ),
    (
        "users-by-role",
        "select coalesce(r.role, '(none)') as role, count(*) from app_users u "
        "left join app_user_roles r on r.user_id = u.id group by 1 order by 1",
        False,
    ),
    ("null-scope-counts", """select 'live_buses' as tbl, count(*) as null_scope from live_buses where school_id is null union all select 'live_routes' as tbl, count(*) as null_scope from live_routes where school_id is null union all select 'live_students' as tbl, count(*) as null_scope from live_students where school_id is null union all select 'live_runs' as tbl, count(*) as null_scope from live_runs where school_id is null""", False),
    ("school-references", """select 'live_buses' as tbl, school_id::text as school_id, count(*) as row_count from live_buses where school_id is not null group by 1, 2 union all select 'live_routes' as tbl, school_id::text as school_id, count(*) as row_count from live_routes where school_id is not null group by 1, 2 union all select 'live_students' as tbl, school_id::text as school_id, count(*) as row_count from live_students where school_id is not null group by 1, 2 union all select 'live_runs' as tbl, school_id::text as school_id, count(*) as row_count from live_runs where school_id is not null group by 1, 2 union all select 'live_fleet_plans' as tbl, school_id::text as school_id, count(*) as row_count from live_fleet_plans where school_id is not null group by 1, 2 order by 1, 2""", False),
    (
        "audit-by-school",
        "select school_id::text as school_id, count(*) from live_admin_audit "
        "group by 1 order by 1",
        False,
    ),
    (
        "demo-identities",
        "select u.email, u.created_at, "
        "exists (select 1 from live_buses b where b.driver_id = u.id) as drives_live_bus "
        "from app_users u where u.email in ('admin@test.com', 'and7005@gmail.com', 'and7005@yahoo.it', 'francis@saferide.test', 'mary@saferide.test') order by u.email",
        False,
    ),
    (
        "open-runs",
        "select count(*) as open_runs from live_runs where status <> 'completed'",
        False,
    ),
]

_CHECK_SETS["tenancy-post-move"] = [
    ("null-scope-counts", """select 'live_buses' as tbl, count(*) as null_scope from live_buses where school_id is null union all select 'live_routes' as tbl, count(*) as null_scope from live_routes where school_id is null union all select 'live_students' as tbl, count(*) as null_scope from live_students where school_id is null union all select 'live_runs' as tbl, count(*) as null_scope from live_runs where school_id is null union all select 'live_fleet_plans' as tbl, count(*) as null_scope from live_fleet_plans where school_id is null union all select 'live_incidents' as tbl, count(*) as null_scope from live_incidents where school_id is null union all select 'live_student_absences' as tbl, count(*) as null_scope from live_student_absences where school_id is null union all select 'live_communicated_stops' as tbl, count(*) as null_scope from live_communicated_stops where school_id is null union all select 'live_student_routes' as tbl, count(*) as null_scope from live_student_routes where school_id is null union all select 'live_route_stops' as tbl, count(*) as null_scope from live_route_stops where school_id is null union all select 'run_stops' as tbl, count(*) as null_scope from run_stops where school_id is null union all select 'run_absences' as tbl, count(*) as null_scope from run_absences where school_id is null union all select 'run_participation' as tbl, count(*) as null_scope from run_participation where school_id is null union all select 'run_positions' as tbl, count(*) as null_scope from run_positions where school_id is null union all select 'run_exceptions' as tbl, count(*) as null_scope from run_exceptions where school_id is null union all select 'run_exception_events' as tbl, count(*) as null_scope from run_exception_events where school_id is null union all select 'driver_action_keys' as tbl, count(*) as null_scope from driver_action_keys where school_id is null""", False),
    ("school-rows", "select id, name, code from live_schools order by name", False),
    (
        # By NAME (production's Greenfield id differs from the local seed's).
        # Production post-move must show 0; a LOCAL stack shows 1 by design
        # (the demo school keeps the name and is never deleted there).
        "greenfield-row",
        "select count(*) as greenfield_rows from live_schools where name = 'Greenfield Academy'",
        False,
    ),
    (
        "memberships",
        "select role, state, count(*) from school_memberships "
        "where removed_at is null group by 1, 2 order by 1, 2",
        False,
    ),
    (
        "providers",
        "select count(*) as active_providers from provider_accounts where removed_at is null",
        False,
    ),
    (
        "disabled-identities",
        "select email, disabled_at is not null as disabled from app_users "
        "where email in ('admin@test.com', 'and7005@gmail.com', 'and7005@yahoo.it', 'francis@saferide.test', 'mary@saferide.test') order by email",
        False,
    ),
    ("move-log", "select * from tenancy_move_log order by created_at", False),
]

_CHECK_SETS["tenancy-rls"] = [
    # Assume the runtime role for the rest of this connection (membership is
    # granted by the migrate handler's role step; one-statement form because
    # every entry runs as its own autocommit statement).
    ("assume-app-role", "select set_config('role', 'saferide_app', false) as role", False),
    ("current-user", "select current_user, session_user", False),
    (
        "rls-enabled",
        "select relname, relrowsecurity, relforcerowsecurity from pg_class "
        "where relname in ('live_buses', 'live_routes', 'live_students', 'live_runs', 'live_fleet_plans', 'live_incidents', 'live_student_absences', 'live_communicated_stops', 'live_student_routes', 'live_route_stops', 'run_stops', 'run_absences', 'run_participation', 'run_positions', 'run_exceptions', 'run_exception_events', 'driver_action_keys') order by relname",
        False,
    ),
    ("no-guc-row-visibility", """select 'live_buses' as tbl, count(*) as visible_rows from live_buses union all select 'live_routes' as tbl, count(*) as visible_rows from live_routes union all select 'live_students' as tbl, count(*) as visible_rows from live_students union all select 'live_runs' as tbl, count(*) as visible_rows from live_runs union all select 'live_fleet_plans' as tbl, count(*) as visible_rows from live_fleet_plans union all select 'live_incidents' as tbl, count(*) as visible_rows from live_incidents union all select 'live_student_absences' as tbl, count(*) as visible_rows from live_student_absences union all select 'live_communicated_stops' as tbl, count(*) as visible_rows from live_communicated_stops union all select 'live_student_routes' as tbl, count(*) as visible_rows from live_student_routes union all select 'live_route_stops' as tbl, count(*) as visible_rows from live_route_stops union all select 'run_stops' as tbl, count(*) as visible_rows from run_stops union all select 'run_absences' as tbl, count(*) as visible_rows from run_absences union all select 'run_participation' as tbl, count(*) as visible_rows from run_participation union all select 'run_positions' as tbl, count(*) as visible_rows from run_positions union all select 'run_exceptions' as tbl, count(*) as visible_rows from run_exceptions union all select 'run_exception_events' as tbl, count(*) as visible_rows from run_exception_events union all select 'driver_action_keys' as tbl, count(*) as visible_rows from driver_action_keys""", False),
    (
        "set-school-guc",
        "select set_config('saferide.school_ids', %s, false) as school_ids",
        True,
    ),
    ("cross-school-visibility", """select 'live_buses' as tbl, count(*) as foreign_rows from live_buses where school_id::text <> %s union all select 'live_routes' as tbl, count(*) as foreign_rows from live_routes where school_id::text <> %s union all select 'live_students' as tbl, count(*) as foreign_rows from live_students where school_id::text <> %s union all select 'live_runs' as tbl, count(*) as foreign_rows from live_runs where school_id::text <> %s union all select 'live_fleet_plans' as tbl, count(*) as foreign_rows from live_fleet_plans where school_id::text <> %s union all select 'live_incidents' as tbl, count(*) as foreign_rows from live_incidents where school_id::text <> %s union all select 'live_student_absences' as tbl, count(*) as foreign_rows from live_student_absences where school_id::text <> %s union all select 'live_communicated_stops' as tbl, count(*) as foreign_rows from live_communicated_stops where school_id::text <> %s union all select 'live_student_routes' as tbl, count(*) as foreign_rows from live_student_routes where school_id::text <> %s union all select 'live_route_stops' as tbl, count(*) as foreign_rows from live_route_stops where school_id::text <> %s union all select 'run_stops' as tbl, count(*) as foreign_rows from run_stops where school_id::text <> %s union all select 'run_absences' as tbl, count(*) as foreign_rows from run_absences where school_id::text <> %s union all select 'run_participation' as tbl, count(*) as foreign_rows from run_participation where school_id::text <> %s union all select 'run_positions' as tbl, count(*) as foreign_rows from run_positions where school_id::text <> %s union all select 'run_exceptions' as tbl, count(*) as foreign_rows from run_exceptions where school_id::text <> %s union all select 'run_exception_events' as tbl, count(*) as foreign_rows from run_exception_events where school_id::text <> %s union all select 'driver_action_keys' as tbl, count(*) as foreign_rows from driver_action_keys where school_id::text <> %s""", True),
    ("policy-presence", "select c.relname as tbl, c.relrowsecurity as rls, count(p.polname) as policies from pg_class c left join pg_policy p on p.polrelid = c.oid where c.relname in ('live_buses','live_routes','live_students','live_runs','live_fleet_plans','live_incidents','live_student_absences','live_communicated_stops','live_student_routes','live_route_stops','run_stops','run_absences','run_participation','run_positions','run_exceptions','run_exception_events','driver_action_keys','live_admin_audit') group by c.relname, c.relrowsecurity order by c.relname", False),
    ("provider-health-fn", "select * from provider_school_health()", False),
]

# --- GPS tracking check set (U1) ---------------------------------------------
# Release 1 go/no-go and the post-deploy verification after every GPS release
# (docs/plans/2026-09-19-001-feat-gps-bus-tracking-plan.md, Operational
# Notes). Observations only, all read-only SELECTs. "Today" is the Nairobi
# day, as every date predicate in the DAOs. On a fresh database every entry
# returns zero counts (or an empty list) without error. Expectations:
#   trail-rows-per-run-today          one row per run with a trail today
#   trail-rows-per-run-per-minute     load per run; peak matters in Release 4
#   fix-coverage-ratio-today          nine in ten action rows carry a fix
#                                     (target after two weeks of Release 3)
#   exceptions-by-kind-today          reviewed weekly against the run count
#   pending-prompts-on-completed-runs should be 0 (End Run/close auto-resolve)
#   call-now-due-unsent-over-10m      should be 0 (the next action or poll
#                                     re-attempts due-but-unsent rows)
#   trail-rows-classification-failed  should be 0; each is a logged tap
#   trail-rows-past-retention         per school; non-zero for a school with
#                                     no Start Run inside its retention window
#                                     means the on-demand purge is due
#   check-widenings                   all three true; false means an older
#                                     file's CHECK recreation ran after 016
#                                     (re-apply 016)
_CHECK_SETS["gps"] = [
    (
        "trail-rows-per-run-today",
        "select run_id, source, count(*) as rows from run_positions "
        "where (received_at at time zone 'Africa/Nairobi')::date "
        "= (now() at time zone 'Africa/Nairobi')::date "
        "group by 1, 2 order by 1, 2",
        False,
    ),
    (
        "trail-rows-per-run-per-minute",
        "with per_minute as ("
        "  select run_id, date_trunc('minute', received_at) as minute, count(*) as rows"
        "  from run_positions"
        "  where (received_at at time zone 'Africa/Nairobi')::date"
        "    = (now() at time zone 'Africa/Nairobi')::date"
        "  group by 1, 2)"
        " select run_id, sum(rows) as rows, count(*) as active_minutes,"
        " round(avg(rows), 2) as avg_rows_per_minute, max(rows) as peak_rows_per_minute"
        " from per_minute group by run_id order by peak_rows_per_minute desc, run_id",
        False,
    ),
    (
        "fix-coverage-ratio-today",
        "select count(*) filter (where lat is not null and lng is not null) as with_fix, "
        "count(*) as action_rows, "
        "round(count(*) filter (where lat is not null and lng is not null)::numeric "
        "/ nullif(count(*), 0), 3) as ratio "
        "from run_positions where source = 'action' "
        "and (received_at at time zone 'Africa/Nairobi')::date "
        "= (now() at time zone 'Africa/Nairobi')::date",
        False,
    ),
    (
        "exceptions-by-kind-today",
        "select kind, count(*) as exceptions, "
        "count(*) filter (where reviewed_at is null) as unreviewed "
        "from run_exceptions "
        "where (created_at at time zone 'Africa/Nairobi')::date "
        "= (now() at time zone 'Africa/Nairobi')::date "
        "group by 1 order by 1",
        False,
    ),
    (
        "pending-prompts-on-completed-runs",
        "select count(*) as pending_on_completed from run_exception_events e "
        "join live_runs r on r.id = e.run_id "
        "where e.prompt_state = 'pending' and r.status = 'completed'",
        False,
    ),
    (
        "call-now-due-unsent-over-10m",
        "select count(*) as overdue from run_exception_events "
        "where call_now_due_at is not null and call_now_sent_at is null "
        "and call_now_due_at < now() - interval '10 minutes'",
        False,
    ),
    (
        "trail-rows-classification-failed",
        "select count(*) as flagged from run_positions "
        "where 'classification-failed' = any(flags)",
        False,
    ),
    (
        "trail-rows-past-retention",
        "select s.id as school_id, s.name, "
        f"coalesce(s.position_retention_days, {_RETENTION_DEFAULT_DAYS}) as retention_days, "
        "count(p.id) as rows_past_retention from live_schools s "
        "left join run_positions p on p.school_id = s.id "
        # Seconds, as the purge's own cutoff (position_dao.RETENTION_CUTOFF_SQL):
        # an interval's day field is session-zone calendar arithmetic.
        "and p.received_at < now() - make_interval(secs => "
        f"coalesce(s.position_retention_days, {_RETENTION_DEFAULT_DAYS}) * 86400) "
        "group by 1, 2, 3 order by 4 desc, 2",
        False,
    ),
    (
        "check-widenings",
        "select conname, case conname "
        "when 'live_notifications_type_check' then "
        "strpos(pg_get_constraintdef(oid), 'absent-call-now') > 0 "
        "and strpos(pg_get_constraintdef(oid), 'boarding-corrected') > 0 "
        "when 'live_incidents_type_check' then "
        "strpos(pg_get_constraintdef(oid), 'stop-bypassed') > 0 "
        "and strpos(pg_get_constraintdef(oid), 'absent-remote') > 0 "
        "when 'live_admin_audit_action_check' then "
        "strpos(pg_get_constraintdef(oid), 'exception-reviewed') > 0 "
        "end as widened_by_016 "
        "from pg_constraint where conname in ('live_notifications_type_check', "
        "'live_incidents_type_check', 'live_admin_audit_action_check') order by 1",
        False,
    ),
]

# The canonical list and the generated SQL must not drift.
assert set(SCHOOL_OWNED_TABLES) == {'live_buses', 'live_routes', 'live_students', 'live_runs', 'live_fleet_plans', 'live_incidents', 'live_student_absences', 'live_communicated_stops', 'live_student_routes', 'live_route_stops', 'run_stops', 'run_absences', 'run_participation', 'run_positions', 'run_exceptions', 'run_exception_events', 'driver_action_keys'}
assert GREENFIELD_SCHOOL_ID == '5cae0000-0000-0000-0000-000000000001'
assert set(SEED_DEMO_EMAILS) == {'admin@test.com', 'and7005@gmail.com', 'and7005@yahoo.it', 'francis@saferide.test', 'mary@saferide.test'}


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return url


def _connect() -> psycopg.Connection:
    """Server-enforced read-only session: even a registry mistake cannot write."""
    return psycopg.connect(
        _database_url(),
        autocommit=True,
        options="-c default_transaction_read_only=on",
        row_factory=dict_row,
    )


def _jsonable(value):
    if isinstance(value, (uuid.UUID, dt.datetime, dt.date, dt.time)):
        return str(value)
    if isinstance(value, decimal.Decimal):
        return float(value)
    return value


def handler(event=None, context=None) -> dict:
    event = event or {}
    checks = event.get("checks")
    if checks not in _CHECK_SETS:
        return {
            "status": "error",
            "reason": f"unknown check set {checks!r}",
            "valid_check_sets": sorted(_CHECK_SETS),
        }

    entries = _CHECK_SETS[checks]
    school_id = event.get("school_id")
    if any(needs_school for _, _, needs_school in entries):
        try:
            school_id = str(uuid.UUID(str(school_id)))
        except (TypeError, ValueError):
            return {
                "status": "error",
                "reason": f"check set {checks!r} requires a valid school_id (uuid)",
            }

    results = []
    with _connect() as conn:
        for label, sql, needs_school in entries:
            params = (school_id,) * sql.count("%s") if needs_school else ()
            # Per-check error capture: one broken check (e.g. a table that
            # only exists on live, like the migrate Lambda's marker table on
            # a local stack) must not hide the others' observations.
            try:
                rows = conn.execute(sql, params).fetchall()
            except psycopg.Error as error:
                results.append({"label": label, "error": str(error).strip()})
                continue
            results.append(
                {
                    "label": label,
                    "rows": [
                        {k: _jsonable(v) for k, v in row.items()} for row in rows
                    ],
                }
            )

    return {"status": "ok", "checks": checks, "results": results}


if __name__ == "__main__":  # local manual run against DATABASE_URL
    import json
    import sys

    payload = {"checks": sys.argv[1] if len(sys.argv) > 1 else "migrations"}
    if len(sys.argv) > 2:
        payload["school_id"] = sys.argv[2]
    print(json.dumps(handler(payload), indent=2))
