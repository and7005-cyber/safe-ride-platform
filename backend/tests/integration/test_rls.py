"""Row-level security under the runtime role (U14/AE30).

Connects as ``saferide_app`` (the LOGIN NOBYPASSRLS role the API switches to
in Release 5) and proves the database backstop independently of the API: no
GUC → no rows; the GUC bounds both reads and writes (42501 on a cross-school
write); composite keys make cross-school relationships unrepresentable even
for the owner; the audit's split policy admits NULL-school rows for provider
actors only; and the column-list SET NULL form nulls the reference, never the
school. The master role keeps its full view (RLS is enabled, not FORCEd).
"""

import os
import uuid

import psycopg
import pytest
from psycopg import errors

from conftest import DSN, SCHOOL_A_ID, SCHOOL_B_ID

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="needs the local stack; set RUN_INTEGRATION=1",
)

APP_DSN = os.environ.get(
    "APP_DATABASE_URL", "postgresql://saferide_app:saferide@localhost:5432/saferide"
)

SCHOOL_OWNED = [
    "live_buses", "live_routes", "live_students", "live_runs",
    "live_fleet_plans", "live_incidents", "live_student_absences",
    "live_communicated_stops", "live_student_routes", "live_route_stops",
    "run_stops", "run_absences", "run_participation",
]

USER_OWNED = ["app_users", "auth_sessions", "school_memberships", "live_schools"]


def app_conn():
    return psycopg.connect(APP_DSN, autocommit=False)


def master():
    return psycopg.connect(DSN, autocommit=True)


def arm(conn, *school_ids: str) -> None:
    conn.execute(
        "select set_config('saferide.school_ids', %s, true)",
        (",".join(school_ids),),
    )


# --- reads -------------------------------------------------------------------


def test_no_guc_means_no_rows_on_every_school_owned_table():
    with app_conn() as conn:
        for table in SCHOOL_OWNED:
            count = conn.execute(f"select count(*) from {table}").fetchone()[0]
            assert count == 0, f"{table} leaked {count} rows with no GUC"


def test_guc_bounds_reads_to_that_school():
    with master() as m:
        a_students = m.execute(
            "select count(*) from live_students where school_id = %s", (SCHOOL_A_ID,)
        ).fetchone()[0]
    assert a_students > 0, "seed expectation"
    with app_conn() as conn:
        arm(conn, SCHOOL_A_ID)
        rows = conn.execute("select school_id from live_students").fetchall()
        assert len(rows) == a_students
        assert {str(r[0]) for r in rows} == {SCHOOL_A_ID}
        assert (
            conn.execute(
                "select count(*) from live_buses where school_id = %s", (SCHOOL_B_ID,)
            ).fetchone()[0]
            == 0
        )


def test_parent_guc_carries_both_schools():
    with app_conn() as conn:
        arm(conn, SCHOOL_A_ID, SCHOOL_B_ID)
        schools = {
            str(r[0])
            for r in conn.execute("select distinct school_id from live_students")
        }
        assert schools == {SCHOOL_A_ID, SCHOOL_B_ID}


def test_master_role_keeps_the_full_view():
    with master() as m:
        assert m.execute("select count(*) from live_students").fetchone()[0] > 0
        assert m.execute("select current_user").fetchone()[0] == "saferide"


# --- writes ------------------------------------------------------------------


def test_cross_school_insert_fails_with_42501():
    with app_conn() as conn:
        arm(conn, SCHOOL_A_ID)
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(
                "insert into live_incidents (type, description, school_id) "
                "values ('other', 'IT rls probe', %s)",
                (SCHOOL_B_ID,),
            )
        conn.rollback()


def test_in_scope_write_works_and_is_cleaned_up():
    with app_conn() as conn:
        arm(conn, SCHOOL_A_ID)
        row = conn.execute(
            "insert into live_incidents (type, description, school_id) "
            "values ('other', 'IT rls in-scope', %s) returning id",
            (SCHOOL_A_ID,),
        ).fetchone()
        assert row
        conn.rollback()  # never persist the probe


def test_composite_key_refuses_a_cross_school_bus_even_for_the_owner():
    with master() as m:
        b_bus = m.execute(
            "select id from live_buses where school_id = %s limit 1", (SCHOOL_B_ID,)
        ).fetchone()[0]
    with psycopg.connect(DSN, autocommit=False) as m:
        with pytest.raises(errors.ForeignKeyViolation):
            # afternoon: the seeded B bus already holds a morning route, and the
            # (bus, type, trip) unique key would fire before the composite FK.
            m.execute(
                "insert into live_routes (name, type, bus_id, school_id) "
                "values ('IT RLS xfk', 'afternoon', %s, %s)",
                (b_bus, SCHOOL_A_ID),
            )
        m.rollback()


# --- the audit split policy --------------------------------------------------


def test_audit_null_school_rows_are_provider_only():
    with app_conn() as conn:
        arm(conn)  # no school set
        conn.execute(
            "insert into live_admin_audit "
            "(actor_name, actor_email, actor_kind, action, detail) "
            "values ('IT RLS', 'unknown', 'provider', 'provider-step-out', '{}')"
        )
        conn.rollback()
    with app_conn() as conn:
        arm(conn)
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(
                "insert into live_admin_audit "
                "(actor_name, actor_email, actor_kind, action, detail) "
                "values ('IT RLS', 'unknown', 'staff', 'bus-updated', '{}')"
            )
        conn.rollback()


def test_audit_staff_rows_write_inside_their_school_guc():
    with app_conn() as conn:
        arm(conn, SCHOOL_A_ID)
        conn.execute(
            "insert into live_admin_audit "
            "(actor_name, actor_email, actor_kind, action, school_id, detail) "
            "values ('IT RLS', 'unknown', 'staff', 'bus-updated', %s, '{}')",
            (SCHOOL_A_ID,),
        )
        conn.rollback()


# --- structure ---------------------------------------------------------------


def test_every_school_owned_table_has_row_security_enabled():
    with master() as m:
        for table in SCHOOL_OWNED + ["live_admin_audit"]:
            enabled = m.execute(
                "select relrowsecurity from pg_class where oid = %s::regclass",
                (table,),
            ).fetchone()[0]
            assert enabled, f"{table} has no row security"
            policies = m.execute(
                "select count(*) from pg_policies where tablename = %s", (table,)
            ).fetchone()[0]
            assert policies >= 1, f"{table} has no policy"


def test_user_owned_tables_carry_no_school_policy():
    with master() as m:
        for table in USER_OWNED:
            policies = m.execute(
                "select count(*) from pg_policies where tablename = %s", (table,)
            ).fetchone()[0]
            assert policies == 0, f"{table} unexpectedly has a policy"


def test_app_role_is_not_the_owner_and_cannot_bypass():
    with app_conn() as conn:
        assert conn.execute("select current_user").fetchone()[0] == "saferide_app"
        bypass = conn.execute(
            "select rolbypassrls from pg_roles where rolname = 'saferide_app'"
        ).fetchone()[0]
        assert bypass is False


def test_null_scope_is_impossible_on_school_owned_tables():
    with master() as m:
        for table in SCHOOL_OWNED:
            nullable = m.execute(
                "select is_nullable from information_schema.columns "
                "where table_name = %s and column_name = 'school_id'",
                (table,),
            ).fetchone()[0]
            assert nullable == "NO", f"{table}.school_id is still nullable"
        code_nullable = m.execute(
            "select is_nullable from information_schema.columns "
            "where table_name = 'live_schools' and column_name = 'code'"
        ).fetchone()[0]
        assert code_nullable == "NO"


# --- delete semantics --------------------------------------------------------


def test_bus_delete_nulls_the_reference_never_the_school():
    with master() as m:
        bus = m.execute(
            "insert into live_buses (name, plate_number, capacity, status, school_id) "
            "values ('IT RLS Bus', 'IT-RLS-1', 10, 'idle', %s) returning id",
            (SCHOOL_A_ID,),
        ).fetchone()[0]
        route = m.execute(
            "insert into live_routes (name, type, bus_id, school_id) "
            "values ('IT RLS Route', 'morning', %s, %s) returning id",
            (bus, SCHOOL_A_ID),
        ).fetchone()[0]
        try:
            m.execute("delete from live_buses where id = %s", (bus,))
            row = m.execute(
                "select bus_id, school_id from live_routes where id = %s", (route,)
            ).fetchone()
            assert row[0] is None
            assert str(row[1]) == SCHOOL_A_ID
        finally:
            m.execute("delete from live_routes where id = %s", (route,))


def test_migration_015_double_applies_cleanly():
    from pathlib import Path

    sql = (
        Path(__file__).resolve().parents[2]
        / "db" / "migrations" / "015_tenancy_constraints_rls.sql"
    ).read_text()
    from psycopg.pq import ExecStatus

    with psycopg.connect(DSN, autocommit=True) as m:
        result = m.pgconn.exec_(sql.encode())
        assert result.status in (
            ExecStatus.COMMAND_OK, ExecStatus.TUPLES_OK, ExecStatus.EMPTY_QUERY
        ), result.error_message.decode()
