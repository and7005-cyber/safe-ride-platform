"""Migration 013 (U3): additive tenancy schema against the local stack.

Covers object presence, idempotent double-apply, the accepted default on
existing parent links, the stamp function's zero-NULL guarantee (rolled back),
and the provider aggregate functions.
"""

import os
from pathlib import Path

import psycopg
import pytest

from conftest import DSN, SCHOOL_A_ID, SCHOOL_B_ID

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="needs the local stack; set RUN_INTEGRATION=1",
)

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "db/migrations"
MIGRATION = MIGRATIONS_DIR / "013_tenancy_schema.sql"
# 013 recreates constraints that later files widen (016 recreates the audit
# action CHECK as 013's list plus its own value), so re-applying 013 alone
# would leave the shared local database behind the current schema for every
# suite that runs after this one. Every later idempotent file is re-applied in
# order afterwards; 014 is the one-shot data move and is rehearsed on its own
# scratch database (test_data_move_rehearsal.py).
LATER_MIGRATIONS = [
    path for path in sorted(MIGRATIONS_DIR.glob("*.sql"))
    if path.name > MIGRATION.name and not path.name.startswith("014_")
]

from app.core.tenancy import SCHOOL_OWNED_TABLES


@pytest.fixture()
def conn():
    with psycopg.connect(DSN, autocommit=True) as c:
        yield c


def test_tenancy_objects_exist(conn):
    for table in (
        "school_memberships", "provider_accounts", "provider_support_sessions",
        "auth_preauth_tokens", "tenancy_move_log",
    ):
        assert conn.execute(
            "select to_regclass(%s)", (f"public.{table}",)
        ).fetchone()[0] is not None, table
    for table, column in [
        ("app_users", "disabled_at"), ("app_users", "must_change_password"),
        ("app_users", "password_changed_at"), ("auth_sessions", "last_school_id"),
        ("auth_sessions", "totp_verified_at"), ("live_schools", "code"),
        ("live_parent_students", "status"), ("live_admin_audit", "actor_kind"),
    ] + [(t, "school_id") for t in SCHOOL_OWNED_TABLES if t != "live_fleet_plans"]:
        assert conn.execute(
            "select 1 from information_schema.columns where table_name = %s and column_name = %s",
            (table, column),
        ).fetchone(), f"{table}.{column}"
    for fn in ("tenancy_stamp_school_one", "provider_school_health",
               "provider_audit_rows", "parent_signup_matches"):
        assert conn.execute(
            "select 1 from pg_proc where proname = %s", (fn,)
        ).fetchone(), fn


def test_double_apply_is_a_noop(conn):
    # House rule: rehearse by re-applying to the populated database, then the
    # later files so the database ends at the current schema.
    for path in (MIGRATION, *LATER_MIGRATIONS):
        conn.pgconn.exec_(path.read_text().encode())
        assert not conn.pgconn.error_message, (path.name, conn.pgconn.error_message)


def test_existing_parent_links_defaulted_to_accepted(conn):
    rows = conn.execute(
        "select count(*) filter (where status <> 'accepted') as other, count(*) as total "
        "from live_parent_students"
    ).fetchone()
    assert rows[1] > 0 and rows[0] == 0


def test_seeded_memberships_and_codes(conn):
    rows = dict(conn.execute(
        "select u.email, m.role from school_memberships m "
        "join app_users u on u.id = m.user_id where m.school_id = %s and m.removed_at is null",
        (SCHOOL_A_ID,),
    ).fetchall())
    assert rows.get("director.a@saferide.test") == "director"
    assert rows.get("coordinator.a@saferide.test") == "coordinator"
    assert rows.get("admin@test.com") == "director"
    codes = dict(conn.execute("select id::text, code from live_schools").fetchall())
    assert codes[SCHOOL_A_ID] == "GFA-001" and codes[SCHOOL_B_ID] == "ITS-002"


def test_stamp_function_clears_every_owned_null(conn):
    with psycopg.connect(DSN) as tx:  # not autocommit: one transaction
        tx.execute("select tenancy_stamp_school_one(%s)", (SCHOOL_A_ID,))
        for table in SCHOOL_OWNED_TABLES:
            n = tx.execute(
                f"select count(*) from {table} where school_id is null"
            ).fetchone()[0]
            assert n == 0, f"{table} still has {n} NULL-scope rows"
        tx.rollback()


def test_provider_health_covers_both_schools(conn):
    rows = {r[1]: r for r in conn.execute(
        "select school_id, name, code, setup_state, students, buses, drivers, "
        "runs_today, last_staff_write from provider_school_health()"
    ).fetchall()}
    assert "Greenfield Academy" in rows and "IT Second School" in rows
    assert rows["Greenfield Academy"][4] > 0  # students
    assert rows["IT Second School"][6] >= 1   # driver membership


def test_signup_match_spans_schools(conn):
    schools = {str(r[1]) for r in conn.execute(
        "select student_id, school_id from parent_signup_matches('and7005@gmail.com')"
    ).fetchall()}
    assert {SCHOOL_A_ID, SCHOOL_B_ID} <= schools
