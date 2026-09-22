"""Tenancy constants shared by migration tooling, the verify Lambda and tests.

One canonical list (U2): migration 015, the RLS catalog test, the tenancy
check sets and the local post-seed assertion all read this module, so a table
cannot fall out of the isolation surface unnoticed.
"""

# Every table whose rows belong to exactly one school. All of these end up
# with `school_id NOT NULL`, a `(school_id)` index, composite keys and a
# row-level-security policy (migration 015 / U14; the GPS tracking tables
# arrive already scoped in migration 016).
SCHOOL_OWNED_TABLES: tuple[str, ...] = (
    "live_buses",
    "live_routes",
    "live_students",
    "live_runs",
    "live_fleet_plans",
    "live_incidents",
    "live_student_absences",
    "live_communicated_stops",
    "live_student_routes",
    "live_route_stops",
    "run_stops",
    "run_absences",
    "run_participation",
    "run_positions",
    "run_exceptions",
    "run_exception_events",
    "driver_action_keys",
)

# Nullable school (provider-global actions have no school); its own split
# policy in migration 015 rather than the standard school policy.
AUDIT_TABLE = "live_admin_audit"

# The subset that already carried a school_id column before migration 013 —
# the only tables the production preflight can count NULL scope on.
PRE_TENANCY_SCOPED_TABLES: tuple[str, ...] = (
    "live_buses",
    "live_routes",
    "live_students",
    "live_runs",
)

# Seed-003 demo identities that the data move disables in production (U4).
SEED_DEMO_EMAILS: tuple[str, ...] = (
    "admin@test.com",
    "and7005@gmail.com",
    "and7005@yahoo.it",
    "francis@saferide.test",
    "mary@saferide.test",
)

# The empty demo school row production still holds beside the pilot's data.
# The LOCAL seed's Greenfield id only. Production's Greenfield row carries a
# different id, so migration 014 and the verify checks resolve the demo school
# BY NAME — never through this constant.
GREENFIELD_SCHOOL_ID = "5cae0000-0000-0000-0000-000000000001"
