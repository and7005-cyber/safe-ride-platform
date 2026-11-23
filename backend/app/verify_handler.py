"""Read-only database verification Lambda for the SafeRide backend.

Closes the go/no-go gap named in
``docs/work/validation/2026-08-20-fleet-plan-drafting.md``: production RDS
sits in private subnets with no bastion, so post-deploy verification queries
had no executor beyond the migrate Lambda's own success flag. This function
runs a FIXED ALLOWLIST of named check sets and returns their rows as JSON —
it is deliberately not a SQL console:

- Only the check sets registered below can run; the event never carries SQL.
- Each registry entry declares the one parameter kind it binds: none,
  ``school`` (the event's ``school_id``, validated as a UUID) or ``timestamp``
  (the event's ``timestamp``, validated with ``datetime.fromisoformat`` and
  required to carry a UTC offset). A value is validated before any
  connection opens and only ever bound through psycopg placeholders.
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
from app.dao.verification_sql import unverified_sql

# The system retention default the `gps` set reports against (GPS plan U11):
# the same Settings field the API and the migrate Lambda's purge resolve, so
# GPS_POSITION_RETENTION_DAYS is threaded to this function's env too and the
# observation never disagrees with the purge. A validated int, spliced as a
# literal into the fixed check SQL.
_RETENTION_DEFAULT_DAYS = int(get_settings().gps_position_retention_days)

# Parameter kinds (execution decision D14). One per entry: a NO_PARAM entry
# runs its SQL verbatim (no placeholder parsing, so a literal % is safe); a
# SCHOOL or TIMESTAMP entry binds the validated event value once per %s.
NO_PARAM = "none"
SCHOOL = "school"
TIMESTAMP = "timestamp"
PARAM_KINDS = (NO_PARAM, SCHOOL, TIMESTAMP)

# One statement per (label, sql, param_kind) entry.
_CHECK_SETS: dict[str, list[tuple[str, str, str]]] = {
    # Which migrations/seeds the marker table records, newest last.
    "migrations": [
        (
            "applied-migrations",
            "select id, applied_at from public.saferide_migrations order by applied_at, id",
            NO_PARAM,
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
            NO_PARAM,
        ),
        (
            "buses-claimed",
            "select count(*) as buses, count(school_id) as claimed from live_buses",
            NO_PARAM,
        ),
        (
            "plan-ordered-routes",
            "select count(*) as plan_ordered from live_routes where plan_ordered",
            NO_PARAM,
        ),
        (
            "ridership-pattern-distribution",
            "select ridership_pattern, count(*) from live_students "
            "group by 1 order by 2 desc, 1",
            NO_PARAM,
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
            NO_PARAM,
        ),
        (
            "notification-type-check",
            "select pg_get_constraintdef(oid) as definition from pg_constraint "
            "where conname = 'live_notifications_type_check'",
            NO_PARAM,
        ),
        (
            "plan-partial-uniques",
            "select indexname from pg_indexes where tablename = 'live_fleet_plans' "
            "and indexname in ('live_fleet_plans_school_draft_key', "
            "'live_fleet_plans_school_previous_key') order by indexname",
            NO_PARAM,
        ),
        (
            "notification-plan-dedup-index",
            "select indexname from pg_indexes "
            "where indexname = 'live_notifications_plan_dedup'",
            NO_PARAM,
        ),
    ],
    # Pre-apply capture for a school (record these, compare after apply).
    "baseline": [
        (
            "route-count",
            "select count(*) as routes from live_routes where school_id = %s",
            SCHOOL,
        ),
        (
            "stop-count",
            "select count(*) as stops from live_route_stops rs "
            "join live_routes r on r.id = rs.route_id where r.school_id = %s",
            SCHOOL,
        ),
        (
            "link-count",
            "select count(*) as links from live_student_routes sr "
            "join live_routes r on r.id = sr.route_id where r.school_id = %s",
            SCHOOL,
        ),
        (
            "run-count",
            "select count(*) as runs from live_runs rn "
            "join live_routes r on r.id = rn.route_id where r.school_id = %s",
            SCHOOL,
        ),
        (
            "baselines-by-leg",
            "select c.route_type, count(*) from live_communicated_stops c "
            "join live_students s on s.id = c.student_id "
            "where s.school_id = %s group by 1 order by 1",
            SCHOOL,
        ),
    ],
    # First-apply (and any apply/restore) go/no-go for a school.
    "post-apply": [
        (
            "latest-plan-audit-rows",
            "select id, action, actor_name, created_at, detail from live_admin_audit "
            "where school_id = %s and action in ('plan-applied', 'plan-restored') "
            "order by created_at desc limit 5",
            SCHOOL,
        ),
        (
            "plan-statuses",
            "select status, count(*) from live_fleet_plans "
            "where school_id = %s group by 1 order by 1",
            SCHOOL,
        ),
        (
            "route-authority-flags",
            "select count(*) filter (where plan_ordered) as plan_ordered, "
            "count(*) filter (where custom_stops) as custom, "
            "count(*) filter (where manual_stop_order) as frozen, "
            "count(*) filter (where last_recalc_degraded) as degraded, "
            "count(*) as total from live_routes where school_id = %s",
            SCHOOL,
        ),
        (
            # The morning-clock rule: a PM-only child never carries a
            # pickup_time. Expected: 0.
            "pm-only-pickup-time-violations",
            "select count(*) as violations from live_students s "
            "where s.school_id = %s and s.pickup_time is not null "
            "and not exists (select 1 from live_student_routes sr "
            "  where sr.student_id = s.id and sr.route_type = 'morning')",
            SCHOOL,
        ),
        (
            "baselines-by-leg",
            "select c.route_type, count(*) from live_communicated_stops c "
            "join live_students s on s.id = c.student_id "
            "where s.school_id = %s group by 1 order by 1",
            SCHOOL,
        ),
        (
            # Distinct families the latest apply/restore act actually wrote
            # feed rows for — must equal the count its response reported.
            "latest-act-notified-families",
            "select count(distinct n.user_id) as families from live_notifications n "
            "where n.plan_audit_id = (select id from live_admin_audit "
            "  where school_id = %s and action in ('plan-applied', 'plan-restored') "
            "  order by created_at desc limit 1)",
            SCHOOL,
        ),
        (
            "run-count",
            "select count(*) as runs from live_runs rn "
            "join live_routes r on r.id = rn.route_id where r.school_id = %s",
            SCHOOL,
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
        NO_PARAM,
    ),
    (
        "users-by-role",
        "select coalesce(r.role, '(none)') as role, count(*) from app_users u "
        "left join app_user_roles r on r.user_id = u.id group by 1 order by 1",
        NO_PARAM,
    ),
    ("null-scope-counts", """select 'live_buses' as tbl, count(*) as null_scope from live_buses where school_id is null union all select 'live_routes' as tbl, count(*) as null_scope from live_routes where school_id is null union all select 'live_students' as tbl, count(*) as null_scope from live_students where school_id is null union all select 'live_runs' as tbl, count(*) as null_scope from live_runs where school_id is null""", NO_PARAM),
    ("school-references", """select 'live_buses' as tbl, school_id::text as school_id, count(*) as row_count from live_buses where school_id is not null group by 1, 2 union all select 'live_routes' as tbl, school_id::text as school_id, count(*) as row_count from live_routes where school_id is not null group by 1, 2 union all select 'live_students' as tbl, school_id::text as school_id, count(*) as row_count from live_students where school_id is not null group by 1, 2 union all select 'live_runs' as tbl, school_id::text as school_id, count(*) as row_count from live_runs where school_id is not null group by 1, 2 union all select 'live_fleet_plans' as tbl, school_id::text as school_id, count(*) as row_count from live_fleet_plans where school_id is not null group by 1, 2 order by 1, 2""", NO_PARAM),
    (
        "audit-by-school",
        "select school_id::text as school_id, count(*) from live_admin_audit "
        "group by 1 order by 1",
        NO_PARAM,
    ),
    (
        "demo-identities",
        "select u.email, u.created_at, "
        "exists (select 1 from live_buses b where b.driver_id = u.id) as drives_live_bus "
        "from app_users u where u.email in ('admin@test.com', 'and7005@gmail.com', 'and7005@yahoo.it', 'francis@saferide.test', 'mary@saferide.test') order by u.email",
        NO_PARAM,
    ),
    (
        "open-runs",
        "select count(*) as open_runs from live_runs where status <> 'completed'",
        NO_PARAM,
    ),
]

_CHECK_SETS["tenancy-post-move"] = [
    ("null-scope-counts", """select 'live_buses' as tbl, count(*) as null_scope from live_buses where school_id is null union all select 'live_routes' as tbl, count(*) as null_scope from live_routes where school_id is null union all select 'live_students' as tbl, count(*) as null_scope from live_students where school_id is null union all select 'live_runs' as tbl, count(*) as null_scope from live_runs where school_id is null union all select 'live_fleet_plans' as tbl, count(*) as null_scope from live_fleet_plans where school_id is null union all select 'live_incidents' as tbl, count(*) as null_scope from live_incidents where school_id is null union all select 'live_student_absences' as tbl, count(*) as null_scope from live_student_absences where school_id is null union all select 'live_communicated_stops' as tbl, count(*) as null_scope from live_communicated_stops where school_id is null union all select 'live_student_routes' as tbl, count(*) as null_scope from live_student_routes where school_id is null union all select 'live_route_stops' as tbl, count(*) as null_scope from live_route_stops where school_id is null union all select 'run_stops' as tbl, count(*) as null_scope from run_stops where school_id is null union all select 'run_absences' as tbl, count(*) as null_scope from run_absences where school_id is null union all select 'run_participation' as tbl, count(*) as null_scope from run_participation where school_id is null union all select 'run_positions' as tbl, count(*) as null_scope from run_positions where school_id is null union all select 'run_exceptions' as tbl, count(*) as null_scope from run_exceptions where school_id is null union all select 'run_exception_events' as tbl, count(*) as null_scope from run_exception_events where school_id is null union all select 'driver_action_keys' as tbl, count(*) as null_scope from driver_action_keys where school_id is null""", NO_PARAM),
    ("school-rows", "select id, name, code from live_schools order by name", NO_PARAM),
    (
        # By NAME (production's Greenfield id differs from the local seed's).
        # Production post-move must show 0; a LOCAL stack shows 1 by design
        # (the demo school keeps the name and is never deleted there).
        "greenfield-row",
        "select count(*) as greenfield_rows from live_schools where name = 'Greenfield Academy'",
        NO_PARAM,
    ),
    (
        "memberships",
        "select role, state, count(*) from school_memberships "
        "where removed_at is null group by 1, 2 order by 1, 2",
        NO_PARAM,
    ),
    (
        "providers",
        "select count(*) as active_providers from provider_accounts where removed_at is null",
        NO_PARAM,
    ),
    (
        "disabled-identities",
        "select email, disabled_at is not null as disabled from app_users "
        "where email in ('admin@test.com', 'and7005@gmail.com', 'and7005@yahoo.it', 'francis@saferide.test', 'mary@saferide.test') order by email",
        NO_PARAM,
    ),
    ("move-log", "select * from tenancy_move_log order by created_at", NO_PARAM),
]

_CHECK_SETS["tenancy-rls"] = [
    # Assume the runtime role for the rest of this connection (membership is
    # granted by the migrate handler's role step; one-statement form because
    # every entry runs as its own autocommit statement).
    ("assume-app-role", "select set_config('role', 'saferide_app', false) as role", NO_PARAM),
    ("current-user", "select current_user, session_user", NO_PARAM),
    (
        "rls-enabled",
        "select relname, relrowsecurity, relforcerowsecurity from pg_class "
        "where relname in ('live_buses', 'live_routes', 'live_students', 'live_runs', 'live_fleet_plans', 'live_incidents', 'live_student_absences', 'live_communicated_stops', 'live_student_routes', 'live_route_stops', 'run_stops', 'run_absences', 'run_participation', 'run_positions', 'run_exceptions', 'run_exception_events', 'driver_action_keys') order by relname",
        NO_PARAM,
    ),
    ("no-guc-row-visibility", """select 'live_buses' as tbl, count(*) as visible_rows from live_buses union all select 'live_routes' as tbl, count(*) as visible_rows from live_routes union all select 'live_students' as tbl, count(*) as visible_rows from live_students union all select 'live_runs' as tbl, count(*) as visible_rows from live_runs union all select 'live_fleet_plans' as tbl, count(*) as visible_rows from live_fleet_plans union all select 'live_incidents' as tbl, count(*) as visible_rows from live_incidents union all select 'live_student_absences' as tbl, count(*) as visible_rows from live_student_absences union all select 'live_communicated_stops' as tbl, count(*) as visible_rows from live_communicated_stops union all select 'live_student_routes' as tbl, count(*) as visible_rows from live_student_routes union all select 'live_route_stops' as tbl, count(*) as visible_rows from live_route_stops union all select 'run_stops' as tbl, count(*) as visible_rows from run_stops union all select 'run_absences' as tbl, count(*) as visible_rows from run_absences union all select 'run_participation' as tbl, count(*) as visible_rows from run_participation union all select 'run_positions' as tbl, count(*) as visible_rows from run_positions union all select 'run_exceptions' as tbl, count(*) as visible_rows from run_exceptions union all select 'run_exception_events' as tbl, count(*) as visible_rows from run_exception_events union all select 'driver_action_keys' as tbl, count(*) as visible_rows from driver_action_keys""", NO_PARAM),
    (
        "set-school-guc",
        "select set_config('saferide.school_ids', %s, false) as school_ids",
        SCHOOL,
    ),
    ("cross-school-visibility", """select 'live_buses' as tbl, count(*) as foreign_rows from live_buses where school_id::text <> %s union all select 'live_routes' as tbl, count(*) as foreign_rows from live_routes where school_id::text <> %s union all select 'live_students' as tbl, count(*) as foreign_rows from live_students where school_id::text <> %s union all select 'live_runs' as tbl, count(*) as foreign_rows from live_runs where school_id::text <> %s union all select 'live_fleet_plans' as tbl, count(*) as foreign_rows from live_fleet_plans where school_id::text <> %s union all select 'live_incidents' as tbl, count(*) as foreign_rows from live_incidents where school_id::text <> %s union all select 'live_student_absences' as tbl, count(*) as foreign_rows from live_student_absences where school_id::text <> %s union all select 'live_communicated_stops' as tbl, count(*) as foreign_rows from live_communicated_stops where school_id::text <> %s union all select 'live_student_routes' as tbl, count(*) as foreign_rows from live_student_routes where school_id::text <> %s union all select 'live_route_stops' as tbl, count(*) as foreign_rows from live_route_stops where school_id::text <> %s union all select 'run_stops' as tbl, count(*) as foreign_rows from run_stops where school_id::text <> %s union all select 'run_absences' as tbl, count(*) as foreign_rows from run_absences where school_id::text <> %s union all select 'run_participation' as tbl, count(*) as foreign_rows from run_participation where school_id::text <> %s union all select 'run_positions' as tbl, count(*) as foreign_rows from run_positions where school_id::text <> %s union all select 'run_exceptions' as tbl, count(*) as foreign_rows from run_exceptions where school_id::text <> %s union all select 'run_exception_events' as tbl, count(*) as foreign_rows from run_exception_events where school_id::text <> %s union all select 'driver_action_keys' as tbl, count(*) as foreign_rows from driver_action_keys where school_id::text <> %s""", SCHOOL),
    ("policy-presence", "select c.relname as tbl, c.relrowsecurity as rls, count(p.polname) as policies from pg_class c left join pg_policy p on p.polrelid = c.oid where c.relname in ('live_buses','live_routes','live_students','live_runs','live_fleet_plans','live_incidents','live_student_absences','live_communicated_stops','live_student_routes','live_route_stops','run_stops','run_absences','run_participation','run_positions','run_exceptions','run_exception_events','driver_action_keys','live_admin_audit') group by c.relname, c.relrowsecurity order by c.relname", NO_PARAM),
    ("provider-health-fn", "select * from provider_school_health()", NO_PARAM),
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
        NO_PARAM,
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
        NO_PARAM,
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
        NO_PARAM,
    ),
    (
        "exceptions-by-kind-today",
        "select kind, count(*) as exceptions, "
        "count(*) filter (where reviewed_at is null) as unreviewed "
        "from run_exceptions "
        "where (created_at at time zone 'Africa/Nairobi')::date "
        "= (now() at time zone 'Africa/Nairobi')::date "
        "group by 1 order by 1",
        NO_PARAM,
    ),
    (
        "pending-prompts-on-completed-runs",
        "select count(*) as pending_on_completed from run_exception_events e "
        "join live_runs r on r.id = e.run_id "
        "where e.prompt_state = 'pending' and r.status = 'completed'",
        NO_PARAM,
    ),
    (
        "call-now-due-unsent-over-10m",
        "select count(*) as overdue from run_exception_events "
        "where call_now_due_at is not null and call_now_sent_at is null "
        "and call_now_due_at < now() - interval '10 minutes'",
        NO_PARAM,
    ),
    (
        "trail-rows-classification-failed",
        "select count(*) as flagged from run_positions "
        "where 'classification-failed' = any(flags)",
        NO_PARAM,
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
        NO_PARAM,
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
        NO_PARAM,
    ),
]

# --- Account hardening check set (migration 017 / U2) --------------------------
# Release 1a go/no-go and the post-deploy observation after every hardening
# release (docs/plans/2026-11-16-001-feat-production-hardening-plan.md, U2, as
# amended by the execution decisions D1–D5). Observations only, all read-only
# SELECTs; ids and counts, never names of people or email addresses. On a
# database before 017 the entries that read 017 objects come back as labelled
# errors — that observation is "017 is not applied". Expectations:
#   017-objects-present                      every row present = true
#   audit-check-widening-017                 all six true; false means an
#                                            older file's CHECK recreation ran
#                                            after 017 (re-apply 017)
#   unverified-parent-accounts-by-age        zero until 1b's sign-up sets the
#                                            flag; no-link over 30 d is what
#                                            the nightly purge removes
#   token-rows-by-purpose                    unpurged counts fall to 0 once the
#                                            nightly job runs
#   driver-pin-hash-versions-per-school      legacy falls to 0 the week after
#                                            1b (upgrade on sign-in, re-issue)
#   drivers-with-several-driver-memberships  should be 0 before 1b: a PIN
#                                            holder drives at one school only
#   schools-without-driver-code              should be 0
#   driver-last-sign-in-per-school           drivers to chase before 1b
#   pin-lock-state                           failures in the 15-minute window
#   notifications-past-90-days               falls to 0 after the nightly job
#   stale-sessions                           falls to 0 after the nightly job
#   last-retention-run                       age under 26 h once 1b's job runs
#   parent-reset-audit-recent                the last five office actions
#   temporary-passwords-outstanding          issued, unchanged, unexpired
#   consent-state                            null until consent is recorded
#   push-subscriptions-by-kind               fcm falls to 0 after Release 2
#   instance-settings                        max_connections, server version
#   open-runs-and-today                      deploy only with 0 open runs
_ACTIVE_DRIVER = "m.role = 'driver' and m.state = 'active' and m.removed_at is null"
_PARENT_ROLE = (
    "exists (select 1 from app_user_roles r where r.user_id = u.id and r.role = 'parent')"
)
_NEW_017_ACTIONS = (
    "'parent-password-reset', 'parent-email-confirmed', "
    "'parent-email-changed', 'driver-code-regenerated'"
)
_NAIROBI_TODAY = "(now() at time zone 'Africa/Nairobi')::date"

_CHECK_SETS["hardening"] = [
    (
        "017-objects-present",
        "with expected (ord, kind, object) as (values"
        " (1, 'column', 'app_users.email_verification_required'),"
        " (2, 'column', 'app_users.email_verified_at'),"
        " (3, 'column', 'app_users.consent_version'),"
        " (4, 'column', 'app_users.consented_at'),"
        " (5, 'column', 'password_reset_tokens.purpose'),"
        " (6, 'constraint', 'password_reset_tokens_purpose_check'),"
        " (7, 'index', 'password_reset_tokens_user_purpose_idx'),"
        " (8, 'function', 'saferide_new_driver_code'),"
        " (9, 'column', 'live_schools.driver_code'),"
        " (10, 'not-null-default', 'live_schools.driver_code'),"
        " (11, 'index', 'live_schools_driver_code_key'),"
        " (12, 'table', 'pin_login_failures'),"
        " (13, 'index', 'pin_login_failures_school_attempted_idx'),"
        " (14, 'table', 'ops_job_runs'),"
        " (15, 'index', 'live_notifications_created_idx'))"
        " select kind, object, case kind"
        "  when 'column' then exists (select 1 from information_schema.columns c"
        "   where c.table_schema = 'public' and c.table_name = split_part(object, '.', 1)"
        "   and c.column_name = split_part(object, '.', 2))"
        "  when 'constraint' then exists (select 1 from pg_constraint where conname = object)"
        "  when 'function' then exists (select 1 from pg_proc p"
        "   join pg_namespace n on n.oid = p.pronamespace"
        "   where n.nspname = 'public' and p.proname = object)"
        "  when 'not-null-default' then exists (select 1 from pg_attribute a"
        "   join pg_attrdef d on d.adrelid = a.attrelid and d.adnum = a.attnum"
        "   where a.attrelid = 'public.live_schools'::regclass and a.attname = 'driver_code'"
        "   and a.attnotnull"
        "   and pg_get_expr(d.adbin, d.adrelid) = 'saferide_new_driver_code()')"
        "  else to_regclass('public.' || object) is not null"
        " end as present"
        " from expected order by ord",
        NO_PARAM,
    ),
    (
        # 017's four actions, plus 016's value and one of 013's as canaries:
        # a stale list recreated after 017 shows here first.
        "audit-check-widening-017",
        "select strpos(d, '''parent-password-reset''') > 0 as parent_password_reset,"
        " strpos(d, '''parent-email-confirmed''') > 0 as parent_email_confirmed,"
        " strpos(d, '''parent-email-changed''') > 0 as parent_email_changed,"
        " strpos(d, '''driver-code-regenerated''') > 0 as driver_code_regenerated,"
        " strpos(d, '''exception-reviewed''') > 0 as exception_reviewed,"
        " strpos(d, '''plan-applied''') > 0 as plan_applied"
        " from (select pg_get_constraintdef(oid) as d from pg_constraint"
        "  where conname = 'live_admin_audit_action_check'"
        "  and conrelid = 'public.live_admin_audit'::regclass) c",
        NO_PARAM,
    ),
    (
        # D1: unverified = required and not verified. Both link states are
        # always reported, so an empty table still answers with zeros.
        "unverified-parent-accounts-by-age",
        "with unverified as ("
        " select u.created_at, exists (select 1 from live_parent_students ps"
        "  where ps.parent_id = u.id) as has_link"
        " from app_users u"
        f" where {unverified_sql('u')} and {_PARENT_ROLE})"
        " select l.has_link,"
        " count(v.created_at) filter (where v.created_at > now() - interval '7 days') as under_7d,"
        " count(v.created_at) filter (where v.created_at <= now() - interval '7 days'"
        "  and v.created_at > now() - interval '30 days') as from_7d_to_30d,"
        " count(v.created_at) filter (where v.created_at <= now() - interval '30 days') as over_30d"
        " from (values (true), (false)) as l(has_link)"
        " left join unverified v on v.has_link = l.has_link"
        " group by l.has_link order by l.has_link desc",
        NO_PARAM,
    ),
    (
        "token-rows-by-purpose",
        "select p.purpose,"
        " count(t.id) filter (where t.used_at is null and t.expires_at > now()) as live,"
        " count(t.id) filter (where t.used_at is null"
        "  and t.expires_at < now() - interval '1 day') as expired_unpurged_over_1d,"
        " count(t.id) filter (where t.used_at < now() - interval '1 day') as used_unpurged_over_1d,"
        " round(extract(epoch from max(t.expires_at - t.created_at)) / 60, 1) as max_ttl_minutes"
        " from (values ('reset'), ('verify')) as p(purpose)"
        " left join password_reset_tokens t on t.purpose = p.purpose"
        " group by p.purpose order by p.purpose",
        NO_PARAM,
    ),
    (
        # v2: is 1b's school-salted form; hmac_sha256$ the legacy global one.
        "driver-pin-hash-versions-per-school",
        "select s.id as school_id, s.name,"
        " count(m.user_id) as active_driver_memberships,"
        " count(m.user_id) filter (where u.pin_hash like 'v2:%') as salted_v2,"
        " count(m.user_id) filter (where u.pin_hash like 'hmac_sha256$%') as legacy_hmac,"
        " count(m.user_id) filter (where u.pin_hash is null) as no_pin"
        " from live_schools s"
        f" left join school_memberships m on m.school_id = s.id and {_ACTIVE_DRIVER}"
        " left join app_users u on u.id = m.user_id"
        " group by s.id, s.name order by s.name, s.id",
        NO_PARAM,
    ),
    (
        "drivers-with-several-driver-memberships",
        "select count(*) as users,"
        " coalesce(array_agg(user_id::text order by user_id), '{}') as user_ids"
        " from (select m.user_id from school_memberships m"
        f"  where {_ACTIVE_DRIVER} group by m.user_id having count(*) > 1) d",
        NO_PARAM,
    ),
    (
        "schools-without-driver-code",
        "select count(*) as schools from live_schools where driver_code is null",
        NO_PARAM,
    ),
    (
        # Never signed in first: the drivers to reach before 1b's re-issue.
        "driver-last-sign-in-per-school",
        "select m.school_id, m.user_id, max(a.created_at) as last_session_at"
        " from school_memberships m"
        " left join auth_sessions a on a.user_id = m.user_id"
        f" where {_ACTIVE_DRIVER}"
        " group by m.school_id, m.user_id"
        " order by m.school_id, last_session_at nulls first, m.user_id",
        NO_PARAM,
    ),
    (
        "pin-lock-state",
        "select s.id as school_id, s.name,"
        " count(f.school_id) as failures_last_15m,"
        " count(distinct f.source) as sources_last_15m,"
        " max(f.attempted_at) as last_failure_at"
        " from live_schools s"
        " left join pin_login_failures f on f.school_id = s.id"
        "  and f.attempted_at > now() - interval '15 minutes'"
        " group by s.id, s.name order by failures_last_15m desc, s.name, s.id",
        NO_PARAM,
    ),
    (
        "notifications-past-90-days",
        "select count(*) filter (where created_at < now() - interval '90 days') as past_90_days,"
        " min(created_at) as oldest_created_at from live_notifications",
        NO_PARAM,
    ),
    (
        "stale-sessions",
        "select 'auth_sessions' as tbl, count(*) as expired_over_7d from auth_sessions"
        " where expires_at < now() - interval '7 days'"
        " union all select 'auth_preauth_tokens' as tbl, count(*) as expired_over_7d"
        " from auth_preauth_tokens where expires_at < now() - interval '7 days'",
        NO_PARAM,
    ),
    (
        "last-retention-run",
        "select id, status, started_at, finished_at, counts,"
        " round(extract(epoch from now() - started_at) / 3600, 1) as age_hours"
        " from ops_job_runs where job = 'retention'"
        " order by started_at desc, id desc limit 1",
        NO_PARAM,
    ),
    (
        "parent-reset-audit-recent",
        "select action, actor_kind, created_at, school_id from live_admin_audit"
        f" where action in ({_NEW_017_ACTIONS})"
        " order by created_at desc limit 5",
        NO_PARAM,
    ),
    (
        "temporary-passwords-outstanding",
        "select coalesce(r.role, '(none)') as role, count(distinct u.id) as users"
        " from app_users u left join app_user_roles r on r.user_id = u.id"
        " where u.must_change_password and u.temporary_password_expires_at > now()"
        " group by 1 order by 1",
        NO_PARAM,
    ),
    (
        "consent-state",
        "select u.consent_version, count(*) as parents from app_users u"
        f" where {_PARENT_ROLE}"
        " group by u.consent_version order by u.consent_version nulls first",
        NO_PARAM,
    ),
    (
        "push-subscriptions-by-kind",
        "select 'web-push' as kind, count(*) as subscriptions,"
        " count(distinct user_id) as users from live_push_subscriptions"
        " union all select 'fcm' as kind, count(*) as subscriptions,"
        " count(distinct user_id) as users from live_fcm_tokens",
        NO_PARAM,
    ),
    (
        "instance-settings",
        "select current_setting('max_connections') as max_connections,"
        " current_setting('server_version') as server_version",
        NO_PARAM,
    ),
    (
        "open-runs-and-today",
        "select count(*) filter (where status <> 'completed') as open_runs,"
        " count(*) filter (where (created_at at time zone 'Africa/Nairobi')::date"
        f"  = {_NAIROBI_TODAY}) as created_today_nairobi"
        " from live_runs",
        NO_PARAM,
    ),
]

# --- Restore comparison (U6 restore rehearsal, D14) --------------------------
# Point the verify Lambda at a point-in-time restore and at the source, pass
# the restore target time, and compare: per append-only table, all rows, the
# rows at or before the timestamp, and the newest time-column value. One entry
# per table, so a table absent from one side (the migrate Lambda's marker
# table on a psql-built local stack) is a labelled error, not a lost report.
_CHECK_SETS["restore-compare"] = [
    (
        "saferide_migrations",
        "select %s::timestamptz as cutoff, 'applied_at' as time_column,"
        " count(*) as total_rows,"
        " count(*) filter (where applied_at <= %s) as rows_at_or_before,"
        " max(applied_at) as max_time from public.saferide_migrations",
        TIMESTAMP,
    ),
    (
        "live_admin_audit",
        "select %s::timestamptz as cutoff, 'created_at' as time_column,"
        " count(*) as total_rows,"
        " count(*) filter (where created_at <= %s) as rows_at_or_before,"
        " max(created_at) as max_time from public.live_admin_audit",
        TIMESTAMP,
    ),
    (
        "run_positions",
        "select %s::timestamptz as cutoff, 'received_at' as time_column,"
        " count(*) as total_rows,"
        " count(*) filter (where received_at <= %s) as rows_at_or_before,"
        " max(received_at) as max_time from public.run_positions",
        TIMESTAMP,
    ),
    (
        "live_runs",
        "select %s::timestamptz as cutoff, 'created_at' as time_column,"
        " count(*) as total_rows,"
        " count(*) filter (where created_at <= %s) as rows_at_or_before,"
        " max(created_at) as max_time from public.live_runs",
        TIMESTAMP,
    ),
    (
        "live_incidents",
        "select %s::timestamptz as cutoff, 'created_at' as time_column,"
        " count(*) as total_rows,"
        " count(*) filter (where created_at <= %s) as rows_at_or_before,"
        " max(created_at) as max_time from public.live_incidents",
        TIMESTAMP,
    ),
    (
        "live_notifications",
        "select %s::timestamptz as cutoff, 'created_at' as time_column,"
        " count(*) as total_rows,"
        " count(*) filter (where created_at <= %s) as rows_at_or_before,"
        " max(created_at) as max_time from public.live_notifications",
        TIMESTAMP,
    ),
]


# The registry's shape (known kinds, unique labels, a placeholder exactly
# when a value is bound) is pinned by tests/core/test_verify_registry.py, not
# asserted here: an import-time failure would take the whole verify Lambda down.

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


def _parse_timestamp(raw) -> dt.datetime | None:
    """An ISO 8601 instant with a UTC offset, or None. A naive value is
    refused rather than guessed: the operator compares a restore target, and
    an hour's ambiguity (Nairobi vs UTC) would shift every count."""
    if not isinstance(raw, str):
        return None
    try:
        value = dt.datetime.fromisoformat(raw)
    except ValueError:
        return None
    return value if value.tzinfo is not None else None


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
    kinds = {kind for _, _, kind in entries}
    # Every value the set binds is validated before any connection opens.
    values: dict[str, object] = {}
    if SCHOOL in kinds:
        try:
            values[SCHOOL] = str(uuid.UUID(str(event.get("school_id"))))
        except (TypeError, ValueError):
            return {
                "status": "error",
                "reason": f"check set {checks!r} requires a valid school_id (uuid)",
            }
    if TIMESTAMP in kinds:
        values[TIMESTAMP] = _parse_timestamp(event.get("timestamp"))
        if values[TIMESTAMP] is None:
            return {
                "status": "error",
                "reason": (
                    f"check set {checks!r} requires a valid timestamp "
                    "(ISO 8601 with a UTC offset, e.g. 2026-11-17T06:00:00+03:00)"
                ),
            }

    results = []
    with _connect() as conn:
        for label, sql, kind in entries:
            # NO_PARAM: None, so the SQL is sent verbatim (a literal % stays a
            # literal); otherwise the validated value once per placeholder.
            params = None if kind == NO_PARAM else (values[kind],) * sql.count("%s")
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
        # Same convention as infra/scripts/verify-db.sh: the second argument
        # is the timestamp for a set that binds one, else the school_id.
        kinds = {kind for _, _, kind in _CHECK_SETS.get(payload["checks"], [])}
        payload["timestamp" if TIMESTAMP in kinds else "school_id"] = sys.argv[2]
    print(json.dumps(handler(payload), indent=2))
