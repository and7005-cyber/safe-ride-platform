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
