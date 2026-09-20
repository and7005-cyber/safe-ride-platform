"""Migration 014 rehearsal (U4, re-shaped 2026-09-20): the production data
move against an isolated scratch database matching the RECORDED Release 1
preflight contract (docs/validation/2026-09-20-multi-tenant-release-1.md).

The local stack cannot rehearse 014 directly — locally the Greenfield-named
school IS the populated school and 014's exit L skips on the local marker
table — so this suite creates its own database, applies every migration
(014 and 015 defer on empty), inserts the contract shape, exercises the
raise paths, then the act and its idempotent re-apply.

Contract shape rehearsed here: production ids for both schools (Greenfield
resolves BY NAME, never by the local seed's id), Greenfield demo residue
(completed runs, one fleet plan, a run-linked incident), Msingi populated
(students, bus, route, runs), and only two of the five seeded demo
identities still existing (admin@test.com, and7005@yahoo.it) beside real
pilot users whose sessions must survive the move.
"""

import os
from pathlib import Path

import psycopg
import pytest

from conftest import DSN

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="needs the local stack; set RUN_INTEGRATION=1",
)

MIGRATIONS = sorted(
    (Path(__file__).resolve().parents[2] / "db/migrations").glob("*.sql")
)
MOVE_SQL = next(p for p in MIGRATIONS if p.stem == "014_tenancy_data_move").read_text()
REHEARSAL_DB = "saferide_it_rehearsal"
# Production ids from the recorded contract — deliberately NOT the local
# seed's ids: the move must resolve Greenfield by name.
PROD_GREENFIELD = "23e0cb4c-95bb-4f4b-b7cb-4e7078aee144"
PROD_MSINGI = "bd2b680c-5cb1-42c1-902a-7b3aaa8a2769"

SCHOOL_OWNED = (
    "live_buses", "live_routes", "live_students", "live_runs", "live_fleet_plans",
    "live_incidents", "live_student_absences", "live_communicated_stops",
    "live_student_routes", "live_route_stops", "run_stops", "run_absences",
    "run_participation",
)


def _run_file(conn: psycopg.Connection, sql_text: str) -> None:
    """psql-style whole-file execution (one implicit transaction)."""
    from app.migrate_handler import _run_script

    _run_script(conn, sql_text)


@pytest.fixture(scope="module")
def rehearsal():
    admin = psycopg.connect(DSN, autocommit=True)
    admin.execute(
        "select pg_terminate_backend(pid) from pg_stat_activity where datname = %s",
        (REHEARSAL_DB,),
    )
    admin.execute(f"drop database if exists {REHEARSAL_DB}")
    admin.execute(f"create database {REHEARSAL_DB}")
    scratch_dsn = DSN.rsplit("/", 1)[0] + f"/{REHEARSAL_DB}"
    conn = psycopg.connect(scratch_dsn, autocommit=True)
    for path in MIGRATIONS:
        _run_file(conn, path.read_text())

    conn.execute(
        "insert into live_schools (id, name, address, lat, lng) values "
        "(%s, 'Greenfield Academy', 'Ngong Road', -1.30, 36.76), "
        "(%s, 'Msingi Bora', 'Thika Road', -1.20, 36.90)",
        (PROD_GREENFIELD, PROD_MSINGI),
    )
    # Identities per the contract: admin + the yahoo demo driver are the only
    # seed identities left; the rest are real pilot users the move must not
    # touch (their sessions included).
    users = {
        "admin@test.com": ("admin", None),
        "and7005@yahoo.it": ("driver", "hmac_sha256$rehearsal-yahoo"),
        "driver.one@pilot.test": ("driver", "hmac_sha256$rehearsal-d1"),
        "driver.two@pilot.test": ("driver", "hmac_sha256$rehearsal-d2"),
        "driver.three@pilot.test": ("driver", "hmac_sha256$rehearsal-d3"),
        "parent.one@pilot.test": ("parent", None),
        "parent.two@pilot.test": ("parent", None),
    }
    ids = {}
    for email, (role, pin) in users.items():
        uid = conn.execute(
            "insert into app_users (email, password_hash, full_name, pin_hash) "
            "values (%s, 'pbkdf2_sha256$1$s$h', %s, %s) returning id",
            (email, email.split("@")[0], pin),
        ).fetchone()[0]
        conn.execute(
            "insert into app_user_roles (user_id, role) values (%s, %s)", (uid, role)
        )
        ids[email] = uid
    conn.execute(
        "insert into auth_sessions (user_id, token_hash, expires_at) values "
        "(%s, 'rehearsal-demo-driver-token', now() + interval '8 hours'), "
        "(%s, 'rehearsal-real-parent-token', now() + interval '8 hours')",
        (ids["and7005@yahoo.it"], ids["parent.one@pilot.test"]),
    )

    # Msingi: the populated pilot school.
    bus = conn.execute(
        "insert into live_buses (name, capacity) values ('Rehearsal Bus', 30) returning id"
    ).fetchone()[0]
    route = conn.execute(
        "insert into live_routes (name, type, bus_id, school_id) "
        "values ('Rehearsal — Morning', 'morning', %s, %s) returning id",
        (bus, PROD_MSINGI),
    ).fetchone()[0]
    s1 = conn.execute(
        "insert into live_students (name, school_id, bus_id, parent_email) "
        "values ('Move Child One', %s, %s, 'parent.one@pilot.test') returning id",
        (PROD_MSINGI, bus),
    ).fetchone()[0]
    s2 = conn.execute(
        "insert into live_students (name, parent_email) "
        "values ('Move Child Two', 'parent.one@pilot.test') returning id"
    ).fetchone()[0]
    conn.execute(
        "insert into live_student_routes (student_id, route_id) values (%s, %s)",
        (s1, route),
    )
    conn.execute(
        "insert into live_route_stops (route_id, name, stop_order, is_school_gate) "
        "values (%s, 'Rehearsal Gate', 1, true)",
        (route,),
    )
    run = conn.execute(
        "insert into live_runs (bus_id, route_id, type, date, status) "
        "values (%s, %s, 'morning', current_date - 1, 'completed') returning id",
        (bus, route),
    ).fetchone()[0]
    conn.execute(
        "insert into run_stops (run_id, stop_order, name) values (%s, 1, 'Rehearsal Gate')",
        (run,),
    )
    conn.execute(
        "insert into live_incidents (type, description) values ('other', 'rehearsal note')"
    )
    conn.execute(
        "insert into live_parent_students (parent_id, student_id) values (%s, %s)",
        (ids["parent.one@pilot.test"], s2),
    )

    # Greenfield demo residue per the contract: completed runs, one fleet
    # plan, an incident hanging off a demo run. The audit row keeps its
    # pre-tenancy release-to-NULL path.
    gf_run = None
    for i in range(3):
        gf_run = conn.execute(
            "insert into live_runs (type, date, status, school_id) "
            "values ('morning', current_date - %s - 30, 'completed', %s) returning id",
            (i, PROD_GREENFIELD),
        ).fetchone()[0]
    conn.execute(
        "insert into live_fleet_plans (school_id) values (%s)", (PROD_GREENFIELD,)
    )
    conn.execute(
        "insert into live_incidents (type, description, run_id) "
        "values ('other', 'greenfield demo incident', %s)",
        (gf_run,),
    )
    conn.execute(
        "insert into live_admin_audit (actor_id, actor_name, actor_email, action, school_id) "
        "values (%s, 'Greenfield Admin', 'admin@test.com', 'pin-map-viewed', %s)",
        (ids["admin@test.com"], PROD_GREENFIELD),
    )

    yield conn, ids

    conn.close()
    admin.execute(
        "select pg_terminate_backend(pid) from pg_stat_activity where datname = %s",
        (REHEARSAL_DB,),
    )
    admin.execute(f"drop database if exists {REHEARSAL_DB}")
    admin.close()


def _expect_raise(conn, fragment):
    with pytest.raises(RuntimeError, match=fragment):
        _run_file(conn, MOVE_SQL)
    assert conn.execute(
        "select count(*) from tenancy_move_log where phase = 'move'"
    ).fetchone()[0] == 0, "a failed move must roll back entirely"


def test_raise_paths_then_the_move_then_idempotence(rehearsal):
    conn, ids = rehearsal

    # Raise 1: a third school row.
    extra = conn.execute(
        "insert into live_schools (name, lat, lng) values ('Intruder', 0, 0) returning id"
    ).fetchone()[0]
    _expect_raise(conn, "expected exactly 2 school rows")
    conn.execute("delete from live_schools where id = %s", (extra,))

    # Raise 2: the seeded admin (interim director) is missing.
    conn.execute(
        "delete from app_user_roles where user_id = %s", (ids["admin@test.com"],)
    )
    conn.execute("delete from app_users where id = %s", (ids["admin@test.com"],))
    _expect_raise(conn, "seeded admin")
    uid = conn.execute(
        "insert into app_users (email, password_hash, full_name) "
        "values ('admin@test.com', 'pbkdf2_sha256$1$s$h', 'admin') returning id"
    ).fetchone()[0]
    conn.execute("insert into app_user_roles (user_id, role) values (%s, 'admin')", (uid,))
    ids["admin@test.com"] = uid

    # Raise 3: Greenfield residue is allowed ONLY in runs and fleet plans —
    # a Greenfield bus still aborts the move.
    gf_bus = conn.execute(
        "insert into live_buses (name, capacity, school_id) "
        "values ('Greenfield Bus', 10, %s) returning id",
        (PROD_GREENFIELD,),
    ).fetchone()[0]
    _expect_raise(conn, "still reference Greenfield")
    conn.execute("delete from live_buses where id = %s", (gf_bus,))

    # (014's "runs/plans name a third school" pin is deliberately not
    # exercised: school_id's FK plus the exactly-2-schools pin — which fires
    # first — make it unreachable; it stays in the SQL as belt-and-braces.)

    # Raise 4: an open run.
    open_run = conn.execute(
        "insert into live_runs (type, date, status) "
        "values ('morning', current_date, 'in-progress') returning id"
    ).fetchone()[0]
    _expect_raise(conn, "not completed")
    conn.execute("delete from live_runs where id = %s", (open_run,))

    # Raise 5: a demo driver still drives a live bus.
    conn.execute(
        "update live_buses set driver_id = %s where name = 'Rehearsal Bus'",
        (ids["and7005@yahoo.it"],),
    )
    _expect_raise(conn, "demo driver still drives")
    conn.execute("update live_buses set driver_id = null where name = 'Rehearsal Bus'")

    # The act.
    _run_file(conn, MOVE_SQL)

    for table in SCHOOL_OWNED:
        assert conn.execute(
            f"select count(*) from {table} where school_id is null"
        ).fetchone()[0] == 0, table
    assert conn.execute("select count(*) from live_schools").fetchone()[0] == 1
    assert conn.execute(
        "select code from live_schools where id = %s", (PROD_MSINGI,)
    ).fetchone()[0] == "MSB-001"

    # Greenfield residue is gone: runs, the plan, the run-linked incident.
    assert conn.execute(
        "select count(*) from live_runs where school_id = %s", (PROD_GREENFIELD,)
    ).fetchone()[0] == 0
    assert conn.execute(
        "select count(*) from live_fleet_plans where school_id = %s", (PROD_GREENFIELD,)
    ).fetchone()[0] == 0
    assert conn.execute(
        "select count(*) from live_incidents where description = 'greenfield demo incident'"
    ).fetchone()[0] == 0

    memberships = dict(conn.execute(
        "select u.email, m.role from school_memberships m "
        "join app_users u on u.id = m.user_id where m.school_id = %s", (PROD_MSINGI,)
    ).fetchall())
    assert memberships["admin@test.com"] == "director"
    assert memberships["and7005@yahoo.it"] == "driver"
    assert len([r for r in memberships.values() if r == "driver"]) == 4

    disabled = dict(conn.execute(
        "select email, disabled_at is not null from app_users"
    ).fetchall())
    assert disabled["admin@test.com"] is False
    assert disabled["and7005@yahoo.it"] is True
    assert disabled["parent.one@pilot.test"] is False
    assert disabled["driver.one@pilot.test"] is False

    # Only the demo identity's session is revoked; real pilot sessions live.
    sessions = dict(conn.execute(
        "select token_hash, revoked_at is not null from auth_sessions"
    ).fetchall())
    assert sessions["rehearsal-demo-driver-token"] is True
    assert sessions["rehearsal-real-parent-token"] is False

    assert conn.execute(
        "select school_id from live_admin_audit where action = 'pin-map-viewed'"
    ).fetchone()[0] is None, "Greenfield audit rows are released to NULL, not re-stamped"

    log = conn.execute(
        "select detail from tenancy_move_log where phase = 'move'"
    ).fetchall()
    assert len(log) == 1
    detail = log[0][0]
    assert detail["target_school"] == PROD_MSINGI
    assert detail["greenfield_deleted"] == PROD_GREENFIELD
    assert detail["greenfield_runs_deleted"] == 3
    assert detail["greenfield_plans_deleted"] == 1
    assert detail["greenfield_incidents_deleted"] == 1
    assert detail["demo_disabled"] == ["and7005@yahoo.it"]
    assert detail["before"]["live_students"] == 2

    # Idempotence: a second apply is exit M.
    _run_file(conn, MOVE_SQL)
    assert conn.execute(
        "select count(*) from tenancy_move_log where phase = 'move'"
    ).fetchone()[0] == 1
