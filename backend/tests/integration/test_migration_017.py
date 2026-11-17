"""Migration 017 (hardening U2): account hardening schema against the local
stack — the verification flag and consent columns (no backfill, D1), the
token purpose, the per-school driver code and its generator (D2), the
PIN-failure window (D3) and the job-run witness (D4), and the audit CHECK
widening (D5).

Every probe that writes runs inside a transaction that is rolled back, so the
shared local database is left exactly as the reset script made it. The
double-apply rehearsal lives in test_tenancy_schema.py with the other
re-apply checks.
"""

import os
import re

import psycopg
import pytest
from psycopg import errors

from conftest import DSN, SCHOOL_A_ID

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="needs the local stack; set RUN_INTEGRATION=1",
)

APP_DSN = os.environ.get(
    "APP_DATABASE_URL", "postgresql://saferide_app:saferide@localhost:5432/saferide"
)
# Seeded parent (backend/db/seeds/003_local_snapshot.sql), present before 017
# ran on every local reset.
SEEDED_PARENT_ID = "a0000000-0000-0000-0000-000000000002"
CROCKFORD = re.compile(r"^[0-9A-HJKMNP-TV-Z]{8}$")
NEW_ACTIONS = (
    "parent-password-reset", "parent-email-confirmed", "parent-email-changed",
    "driver-code-regenerated",
)


@pytest.fixture()
def conn():
    with psycopg.connect(DSN, autocommit=True) as c:
        yield c


@pytest.fixture()
def tx():
    """A master-role transaction that is always rolled back."""
    with psycopg.connect(DSN) as c:
        try:
            yield c
        finally:
            c.rollback()


def _column(conn, table, column):
    return conn.execute(
        "select is_nullable, column_default, data_type from information_schema.columns "
        "where table_schema = 'public' and table_name = %s and column_name = %s",
        (table, column),
    ).fetchone()


# --- columns and defaults ------------------------------------------------------


def test_new_columns_exist_with_their_defaults(conn):
    assert _column(conn, "app_users", "email_verification_required") == (
        "NO", "false", "boolean",
    )
    for column in ("email_verified_at", "consented_at"):
        assert _column(conn, "app_users", column) == (
            "YES", None, "timestamp with time zone",
        ), column
    assert _column(conn, "app_users", "consent_version") == ("YES", None, "text")
    assert _column(conn, "password_reset_tokens", "purpose") == (
        "NO", "'reset'::text", "text",
    )
    assert _column(conn, "live_schools", "driver_code") == (
        "NO", "saferide_new_driver_code()", "text",
    )


def test_existing_accounts_are_grandfathered_not_backfilled(conn):
    # D1: no backfill. A parent that existed before 017 is neither required
    # to verify nor marked verified; only 1b's code sets the flag.
    row = conn.execute(
        "select email_verification_required, email_verified_at, consent_version, consented_at "
        "from app_users where id = %s",
        (SEEDED_PARENT_ID,),
    ).fetchone()
    assert row == (False, None, None, None)
    assert conn.execute(
        "select exists (select 1 from app_user_roles where user_id = %s and role = 'parent')",
        (SEEDED_PARENT_ID,),
    ).fetchone()[0]


# --- token purpose ---------------------------------------------------------------


def test_a_token_inserted_without_purpose_is_a_reset_token(tx):
    purpose = tx.execute(
        "insert into password_reset_tokens (user_id, token_hash, expires_at) "
        "values (%s, 'it-017-no-purpose', now() + interval '30 minutes') returning purpose",
        (SEEDED_PARENT_ID,),
    ).fetchone()[0]
    assert purpose == "reset"
    verify = tx.execute(
        "insert into password_reset_tokens (user_id, token_hash, expires_at, purpose) "
        "values (%s, 'it-017-verify', now() + interval '24 hours', 'verify') returning purpose",
        (SEEDED_PARENT_ID,),
    ).fetchone()[0]
    assert verify == "verify"


def test_an_unknown_token_purpose_is_refused(tx):
    with pytest.raises(errors.CheckViolation):
        tx.execute(
            "insert into password_reset_tokens (user_id, token_hash, expires_at, purpose) "
            "values (%s, 'it-017-other', now() + interval '30 minutes', 'other')",
            (SEEDED_PARENT_ID,),
        )


# --- driver code -----------------------------------------------------------------


def test_every_school_has_a_distinct_crockford_driver_code(conn):
    codes = [
        row[0] for row in conn.execute("select driver_code from live_schools").fetchall()
    ]
    assert codes, "the seeds insert schools"
    assert all(code is not None and CROCKFORD.match(code) for code in codes), codes
    assert len(codes) == len(set(codes))


def test_a_school_inserted_without_naming_the_column_draws_a_code(tx):
    # The provider console's insert (provider_dao) and the seeds never name
    # driver_code; the column default must cover them.
    codes = [
        tx.execute(
            "insert into live_schools (name, code, lat, lng) values (%s, %s, -1.3, 36.8) "
            "returning driver_code",
            (f"IT 017 School {n}", f"I17-00{n}"),
        ).fetchone()[0]
        for n in range(3)
    ]
    assert all(CROCKFORD.match(code) for code in codes), codes
    assert len(set(codes)) == 3


def test_a_duplicate_driver_code_is_refused(tx):
    existing = tx.execute(
        "select driver_code from live_schools where id = %s", (SCHOOL_A_ID,)
    ).fetchone()[0]
    with pytest.raises(errors.UniqueViolation):
        tx.execute(
            "insert into live_schools (name, code, lat, lng, driver_code) "
            "values ('IT 017 Duplicate', 'I17-DUP', -1.3, 36.8, %s)",
            (existing,),
        )


def test_the_generator_draws_uniform_crockford_codes(conn):
    # 4,000 draws: every code well-formed and distinct (40 bits), and every
    # one of the 32 symbols appears in every position (no excluded letter
    # sneaks in, no symbol is unreachable).
    codes = [
        row[0]
        for row in conn.execute(
            "select saferide_new_driver_code() from generate_series(1, 4000)"
        ).fetchall()
    ]
    assert all(CROCKFORD.match(code) for code in codes)
    assert len(set(codes)) == len(codes)
    alphabet = set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")
    for position in range(8):
        assert {code[position] for code in codes} == alphabet, position


# --- audit CHECK -----------------------------------------------------------------


@pytest.mark.parametrize(
    "action", [*NEW_ACTIONS, "exception-reviewed", "staff-password-reset"]
)
def test_the_audit_check_accepts_the_new_and_the_kept_actions(tx, action):
    tx.execute(
        "insert into live_admin_audit (actor_name, actor_email, action, school_id) "
        "values ('IT 017', 'it-017@saferide.test', %s, %s)",
        (action, SCHOOL_A_ID),
    )


def test_the_audit_check_still_refuses_an_unknown_action(tx):
    with pytest.raises(errors.CheckViolation):
        tx.execute(
            "insert into live_admin_audit (actor_name, actor_email, action, school_id) "
            "values ('IT 017', 'it-017@saferide.test', 'parent-impersonated', %s)",
            (SCHOOL_A_ID,),
        )


# --- the two new tables: user-owned, no RLS --------------------------------------


def test_the_new_tables_carry_no_row_security(conn):
    rows = conn.execute(
        "select relname, relrowsecurity, relforcerowsecurity, "
        "(select count(*) from pg_policy p where p.polrelid = c.oid) as policies "
        "from pg_class c where relname in ('pin_login_failures', 'ops_job_runs') "
        "order by relname"
    ).fetchall()
    assert rows == [
        ("ops_job_runs", False, False, 0),
        ("pin_login_failures", False, False, 0),
    ]


def test_the_runtime_role_writes_counts_and_clears_pin_failures_without_a_scope():
    # The sign-in path runs before any school scope exists, so the runtime
    # role works the table with NO saferide.school_ids GUC set.
    with psycopg.connect(APP_DSN) as app:
        try:
            for source in ("198.51.100.7", "198.51.100.7", "203.0.113.9"):
                app.execute(
                    "insert into pin_login_failures (school_id, source) values (%s, %s)",
                    (SCHOOL_A_ID, source),
                )
            failures, sources = app.execute(
                "select count(*), count(distinct source) from pin_login_failures "
                "where school_id = %s and attempted_at > now() - interval '15 minutes'",
                (SCHOOL_A_ID,),
            ).fetchone()
            assert (failures, sources) == (3, 2)
            deleted = app.execute(
                "delete from pin_login_failures where school_id = %s and source = %s",
                (SCHOOL_A_ID, "198.51.100.7"),
            ).rowcount
            assert deleted == 2
        finally:
            app.rollback()


def test_pin_failures_cascade_with_their_school(tx):
    school = tx.execute(
        "insert into live_schools (name, code, lat, lng) "
        "values ('IT 017 Cascade', 'I17-CAS', 0, 0) returning id"
    ).fetchone()[0]
    tx.execute(
        "insert into pin_login_failures (school_id, source) values (%s, '192.0.2.1')",
        (school,),
    )
    tx.execute("delete from live_schools where id = %s", (school,))
    assert tx.execute(
        "select count(*) from pin_login_failures where school_id = %s", (school,)
    ).fetchone()[0] == 0


def test_the_job_witness_takes_a_run_row_and_the_runtime_role_reads_it(tx):
    # The migrate Lambda (master role) writes the witness; the API side only
    # reads it.
    run_id = tx.execute(
        "insert into ops_job_runs (job, status) values ('retention', 'running') returning id"
    ).fetchone()[0]
    tx.execute(
        "update ops_job_runs set status = 'stopped', finished_at = now(), "
        "counts = '{\"live_notifications\": 2000}' where id = %s",
        (run_id,),
    )
    status, counts = tx.execute(
        "select status, counts from ops_job_runs where id = %s", (run_id,)
    ).fetchone()
    assert (status, counts) == ("stopped", {"live_notifications": 2000})
    with pytest.raises(errors.CheckViolation):
        tx.execute("insert into ops_job_runs (job, status) values ('retention', 'done')")
    tx.rollback()
    with psycopg.connect(APP_DSN) as app:
        assert app.execute("select count(*) from ops_job_runs").fetchone()[0] >= 0
        app.rollback()


def test_the_runtime_role_can_read_the_driver_code_and_draw_one(conn):
    assert conn.execute(
        "select has_column_privilege('saferide_app', 'public.live_schools', "
        "'driver_code', 'select'), has_function_privilege('saferide_app', "
        "'public.saferide_new_driver_code()', 'execute')"
    ).fetchone() == (True, True)
