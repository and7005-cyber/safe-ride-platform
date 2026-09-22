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
    # Migration 016 (GPS tracking): scoped from birth, policy landed by the
    # migration itself rather than by 015's data-guarded pass.
    "run_positions", "run_exceptions", "run_exception_events", "driver_action_keys",
]

GPS_TABLES = ["run_positions", "run_exceptions", "run_exception_events", "driver_action_keys"]

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


def test_record_audit_survives_a_guc_less_provider_write():
    """Regression: INSERT..RETURNING read-back was refused by the SELECT
    policy on GUC-less connections — record_audit must mint its id client-side
    and succeed for provider-kind rows exactly as the split policy intends."""
    from app.dao.audit_dao import record_audit

    with app_conn() as conn:
        arm(conn)  # deliberately no school
        audit_id = record_audit(
            conn,
            action="provider-step-out",
            actor={"id": None, "full_name": "IT RLS Probe",
                   "provider": {"totp_enrolled": True}},
        )
        assert uuid.UUID(audit_id)
        conn.rollback()


# --- migration 016: the GPS tracking tables (U1) ------------------------------
# run_positions, run_exceptions and run_exception_events are run children
# (composite (run_id, school_id) key, cascade); driver_action_keys is keyed
# (school, driver, key) with a SET NULL run reference. All four carry the
# school_isolation policy from birth.

from contextlib import contextmanager


def test_rls_catalog_matches_the_canonical_tenancy_list():
    # This file's hand-written list and app.core.tenancy must not drift: the
    # verify Lambda and the migrate tooling read the canonical tuple.
    from app.core.tenancy import SCHOOL_OWNED_TABLES

    assert set(SCHOOL_OWNED) == set(SCHOOL_OWNED_TABLES)
    assert set(GPS_TABLES) <= set(SCHOOL_OWNED_TABLES)


@contextmanager
def gps_rows():
    """One throwaway (completed, long past) run per seeded school with one row
    in each 016 table, written as the owner (RLS is enabled, not FORCEd): the
    sandbox the isolation probes read against. Yields per-school ids; the exit
    deletes the key rows and the runs (the run children cascade)."""
    world: dict[str, dict[str, str]] = {}
    with master() as m:
        for school in (SCHOOL_A_ID, SCHOOL_B_ID):
            bus, driver = m.execute(
                "select id, driver_id from live_buses where school_id = %s "
                "order by name limit 1",
                (school,),
            ).fetchone()
            route = m.execute(
                "select id from live_routes where school_id = %s order by name limit 1",
                (school,),
            ).fetchone()[0]
            run = m.execute(
                "insert into live_runs (bus_id, route_id, school_id, driver_id, type, "
                "date, status, total_stops) "
                "values (%s, %s, %s, %s, 'morning', '2020-01-06', 'completed', 2) "
                "returning id",
                (bus, route, school, driver),
            ).fetchone()[0]
            driver = driver or uuid.uuid4()  # the key table has no driver FK
            m.execute(
                "insert into run_positions (school_id, run_id, bus_id, source, lat, lng) "
                "values (%s, %s, %s, 'checkpoint', -1.30, 36.80)",
                (school, run, bus),
            )
            exception = m.execute(
                "insert into run_exceptions (school_id, run_id, stop_order, kind) "
                "values (%s, %s, 1, 'custody-away') returning id",
                (school, run),
            ).fetchone()[0]
            m.execute(
                "insert into run_exception_events "
                "(exception_id, school_id, run_id, prompt_state, response) "
                "values (%s, %s, %s, 'answered', 'confirmed')",
                (exception, school, run),
            )
            key = m.execute(
                "insert into driver_action_keys "
                "(school_id, driver_id, key, run_id, action, request_fingerprint) "
                "values (%s, %s, gen_random_uuid(), %s, 'board', 'IT rls') returning key",
                (school, driver, run),
            ).fetchone()[0]
            world[school] = {
                "run": str(run), "bus": str(bus), "driver": str(driver),
                "exception": str(exception), "key": str(key),
            }
    try:
        yield world
    finally:
        with master() as m:
            for school, ids in world.items():
                m.execute(
                    "delete from driver_action_keys where school_id = %s and key = %s",
                    (school, ids["key"]),
                )
                m.execute("delete from live_runs where id = %s", (ids["run"],))


def _inserts_for(world, school):
    """One INSERT per 016 table that writes a row belonging to `school`."""
    ids = world[school]
    return [
        (
            "run_positions",
            "insert into run_positions (school_id, run_id, bus_id, source) "
            "values (%s, %s, %s, 'checkpoint')",
            (school, ids["run"], ids["bus"]),
        ),
        (
            "run_exceptions",
            "insert into run_exceptions (school_id, run_id, stop_order, kind) "
            "values (%s, %s, 2, 'stop-bypassed')",
            (school, ids["run"]),
        ),
        (
            "run_exception_events",
            "insert into run_exception_events (exception_id, school_id, run_id, prompt_state) "
            "values (%s, %s, %s, 'pending')",
            (ids["exception"], school, ids["run"]),
        ),
        (
            "driver_action_keys",
            "insert into driver_action_keys "
            "(school_id, driver_id, key, run_id, action, request_fingerprint) "
            "values (%s, %s, gen_random_uuid(), %s, 'arrive', 'IT rls')",
            (school, ids["driver"], ids["run"]),
        ),
    ]


def test_gps_tables_hide_the_other_school_under_the_guc():
    with gps_rows() as world, app_conn() as conn:
        arm(conn, SCHOOL_A_ID)
        for table in GPS_TABLES:
            mine = conn.execute(
                f"select count(*) from {table} where school_id = %s", (SCHOOL_A_ID,)
            ).fetchone()[0]
            theirs = conn.execute(
                f"select count(*) from {table} where school_id = %s", (SCHOOL_B_ID,)
            ).fetchone()[0]
            assert mine >= 1, f"{table}: own rows invisible"
            assert theirs == 0, f"{table} leaked {theirs} school-B rows"
        # By run id too: school B's run children are simply not there.
        b_run = world[SCHOOL_B_ID]["run"]
        for table in ("run_positions", "run_exceptions", "run_exception_events"):
            assert (
                conn.execute(
                    f"select count(*) from {table} where run_id = %s", (b_run,)
                ).fetchone()[0]
                == 0
            ), table
        # And a cross-school UPDATE touches nothing (rows outside the GUC do
        # not exist for the runtime role).
        touched = conn.execute(
            "update run_exceptions set reviewed_at = now() where school_id = %s",
            (SCHOOL_B_ID,),
        ).rowcount
        assert touched == 0
        conn.rollback()


def test_gps_tables_refuse_a_cross_school_insert_with_42501():
    with gps_rows() as world, app_conn() as conn:
        for table, sql, params in _inserts_for(world, SCHOOL_B_ID):
            arm(conn, SCHOOL_A_ID)  # set_config(..., true) is transaction-local
            with pytest.raises(errors.InsufficientPrivilege, match="row-level security"):
                conn.execute(sql, params)
            conn.rollback()


def test_gps_tables_without_a_guc_read_empty_and_refuse_every_write():
    with gps_rows() as world, app_conn() as conn:
        for table in GPS_TABLES:
            count = conn.execute(f"select count(*) from {table}").fetchone()[0]
            assert count == 0, f"{table} leaked {count} rows with no GUC"
        for table, sql, params in _inserts_for(world, SCHOOL_A_ID):
            with pytest.raises(errors.InsufficientPrivilege):
                conn.execute(sql, params)
            conn.rollback()
        # UPDATE and DELETE see nothing either.
        assert conn.execute("update run_positions set flags = '{}'").rowcount == 0
        assert conn.execute("delete from driver_action_keys").rowcount == 0
        conn.rollback()


def test_run_exceptions_insert_returning_works_inside_a_scoped_connection():
    # RLS write pitfall named in the plan: the policy must cover the freshly
    # inserted row so INSERT … RETURNING (and the read-back) succeed.
    with gps_rows() as world, app_conn() as conn:
        arm(conn, SCHOOL_A_ID)
        run = world[SCHOOL_A_ID]["run"]
        row = conn.execute(
            "insert into run_exceptions (school_id, run_id, stop_order, kind, reason) "
            "values (%s, %s, 2, 'unverified', 'no-fix') returning id, kind, school_id",
            (SCHOOL_A_ID, run),
        ).fetchone()
        assert row and row[1] == "unverified" and str(row[2]) == SCHOOL_A_ID
        event = conn.execute(
            "insert into run_exception_events (exception_id, school_id, run_id, prompt_state) "
            "values (%s, %s, %s, 'pending') returning id, prompt_state",
            (row[0], SCHOOL_A_ID, run),
        ).fetchone()
        assert event and event[1] == "pending"
        assert (
            conn.execute(
                "select count(*) from run_exceptions where id = %s", (row[0],)
            ).fetchone()[0]
            == 1
        )
        conn.rollback()


def test_composite_key_refuses_a_cross_school_run_child_even_for_the_owner():
    with gps_rows() as world, psycopg.connect(DSN, autocommit=False) as m:
        with pytest.raises(errors.ForeignKeyViolation):
            m.execute(
                "insert into run_positions (school_id, run_id, bus_id, source) "
                "values (%s, %s, %s, 'checkpoint')",
                (SCHOOL_B_ID, world[SCHOOL_A_ID]["run"], world[SCHOOL_B_ID]["bus"]),
            )
        m.rollback()


def test_gps_tables_carry_school_isolation_without_force_and_validated_keys():
    with master() as m:
        for table in GPS_TABLES:
            rls, force = m.execute(
                "select relrowsecurity, relforcerowsecurity from pg_class "
                "where oid = %s::regclass",
                (table,),
            ).fetchone()
            assert rls and not force, table
            policies = m.execute(
                "select polname, polcmd, polroles::regrole[]::text[] from pg_policy "
                "where polrelid = %s::regclass",
                (table,),
            ).fetchall()
            assert [(p[0], p[1]) for p in policies] == [("school_isolation", "*")], table
            assert policies[0][2] == ["saferide_app"], table
            assert m.execute(
                "select has_table_privilege('saferide_app', %s, "
                "'select, insert, update, delete')",
                (table,),
            ).fetchone()[0], f"{table}: runtime role lacks grants"
        # 015's shape for every child reference: (col, school_id) -> parent
        # (id, school_id), validated, ON DELETE mirroring the plain key and
        # never nulling the school.
        for child, col, parent, on_delete in (
            ("run_positions", "run_id", "live_runs", "ON DELETE CASCADE"),
            ("run_positions", "bus_id", "live_buses", "ON DELETE CASCADE"),
            ("run_exceptions", "run_id", "live_runs", "ON DELETE CASCADE"),
            ("run_exceptions", "student_id", "live_students", "ON DELETE SET NULL (student_id)"),
            ("run_exception_events", "run_id", "live_runs", "ON DELETE CASCADE"),
            ("run_exception_events", "student_id", "live_students", "ON DELETE SET NULL (student_id)"),
            ("driver_action_keys", "run_id", "live_runs", "ON DELETE SET NULL (run_id)"),
        ):
            row = m.execute(
                "select convalidated, pg_get_constraintdef(oid) from pg_constraint "
                "where conname = %s",
                (f"{child}_{col}_school_fkey",),
            ).fetchone()
            assert row and row[0], f"{child}.{col}: composite key missing or NOT VALID"
            assert f"REFERENCES {parent}(id, school_id)" in row[1], row[1]
            assert on_delete in row[1], row[1]


def test_sandbox_teardown_survives_rows_in_all_four_gps_tables():
    # The hand-written teardown lists in conftest must sweep the 016 tables:
    # the run children go with the run, and the key table must be emptied
    # before the school row (it references live_schools without cascade).
    from conftest import school_sandbox

    with school_sandbox("IT GPS Teardown", lat=-1.3, lng=36.8) as sandbox:
        school = sandbox["id"]
        with master() as m:
            bus = m.execute(
                "insert into live_buses (name, plate_number, capacity, status, school_id) "
                "values ('IT GPS Bus', 'IT-GPS-1', 10, 'idle', %s) returning id",
                (school,),
            ).fetchone()[0]
            run = m.execute(
                "insert into live_runs (bus_id, school_id, type, date, status, total_stops) "
                "values (%s, %s, 'morning', '2020-01-07', 'completed', 1) returning id",
                (bus, school),
            ).fetchone()[0]
            m.execute(
                "insert into run_positions (school_id, run_id, bus_id, source, lat, lng) "
                "values (%s, %s, %s, 'action', -1.3, 36.8)",
                (school, run, bus),
            )
            exception = m.execute(
                "insert into run_exceptions (school_id, run_id, kind) "
                "values (%s, %s, 'implausible-movement') returning id",
                (school, run),
            ).fetchone()[0]
            m.execute(
                "insert into run_exception_events (exception_id, school_id, run_id) "
                "values (%s, %s, %s)",
                (exception, school, run),
            )
            m.execute(
                "insert into driver_action_keys "
                "(school_id, driver_id, key, run_id, action, request_fingerprint) "
                "values (%s, %s, gen_random_uuid(), %s, 'start', 'IT gps')",
                (school, sandbox["admin_id"], run),
            )
    with master() as m:
        assert (
            m.execute(
                "select count(*) from live_schools where id = %s", (school,)
            ).fetchone()[0]
            == 0
        )
        for table in GPS_TABLES:
            assert (
                m.execute(
                    f"select count(*) from {table} where school_id = %s", (school,)
                ).fetchone()[0]
                == 0
            ), table


def test_migration_016_double_applies_cleanly():
    from pathlib import Path

    sql = (
        Path(__file__).resolve().parents[2] / "db" / "migrations" / "016_gps_tracking.sql"
    ).read_text()
    from psycopg.pq import ExecStatus

    with psycopg.connect(DSN, autocommit=True) as m:
        result = m.pgconn.exec_(sql.encode())
        assert result.status in (
            ExecStatus.COMMAND_OK, ExecStatus.TUPLES_OK, ExecStatus.EMPTY_QUERY
        ), result.error_message.decode()
