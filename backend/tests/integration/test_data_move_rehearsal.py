"""Migration 014 rehearsal (U4): the production data move against an isolated,
production-shaped scratch database.

The local stack cannot rehearse 014 directly — locally the Greenfield id IS
the populated school, and 014's exit L skips on the local marker table — so
this suite creates its own database, applies every migration (014 skips on
empty), inserts the pinned pre-move shape, exercises the raise paths, then the
act and its idempotent re-apply.
"""

import os
import uuid
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
GREENFIELD = "5cae0000-0000-0000-0000-000000000001"

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

    msingi = str(uuid.uuid4())
    conn.execute(
        "insert into live_schools (id, name, address, lat, lng) values "
        "(%s, 'Greenfield Academy', 'Ngong Road', -1.30, 36.76), "
        "(%s, 'Msingi Bora', 'Thika Road', -1.20, 36.90)",
        (GREENFIELD, msingi),
    )
    users = {
        "admin@test.com": ("admin", None),
        "and7005@gmail.com": ("parent", None),
        "and7005@yahoo.it": ("driver", "hmac_sha256$rehearsal-yahoo"),
        "francis@saferide.test": ("driver", "hmac_sha256$rehearsal-francis"),
        "mary@saferide.test": ("driver", "hmac_sha256$rehearsal-mary"),
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
        "insert into auth_sessions (user_id, token_hash, expires_at) "
        "values (%s, 'rehearsal-parent-token', now() + interval '8 hours')",
        (ids["and7005@gmail.com"],),
    )

    bus = conn.execute(
        "insert into live_buses (name, capacity) values ('Rehearsal Bus', 30) returning id"
    ).fetchone()[0]
    route = conn.execute(
        "insert into live_routes (name, type, bus_id, school_id) "
        "values ('Rehearsal — Morning', 'morning', %s, %s) returning id",
        (bus, msingi),
    ).fetchone()[0]
    s1 = conn.execute(
        "insert into live_students (name, school_id, bus_id, parent_email) "
        "values ('Move Child One', %s, %s, 'and7005@gmail.com') returning id",
        (msingi, bus),
    ).fetchone()[0]
    s2 = conn.execute(
        "insert into live_students (name, parent_email) "
        "values ('Move Child Two', 'and7005@gmail.com') returning id"
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
        (ids["and7005@gmail.com"], s2),
    )
    conn.execute(
        "insert into live_admin_audit (actor_id, actor_name, actor_email, action, school_id) "
        "values (%s, 'Greenfield Admin', 'admin@test.com', 'pin-map-viewed', %s)",
        (ids["admin@test.com"], GREENFIELD),
    )

    yield conn, msingi, ids

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
    conn, msingi, ids = rehearsal

    # Raise 1: a third school row.
    extra = conn.execute(
        "insert into live_schools (name, lat, lng) values ('Intruder', 0, 0) returning id"
    ).fetchone()[0]
    _expect_raise(conn, "expected exactly 2 school rows")
    conn.execute("delete from live_schools where id = %s", (extra,))

    # Raise 2: an open run.
    open_run = conn.execute(
        "insert into live_runs (type, date, status) "
        "values ('morning', current_date, 'in-progress') returning id"
    ).fetchone()[0]
    _expect_raise(conn, "not completed")
    conn.execute("delete from live_runs where id = %s", (open_run,))

    # Raise 3: a demo driver still drives a live bus.
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
    assert conn.execute(
        "select count(*) from live_schools"
    ).fetchone()[0] == 1
    assert conn.execute(
        "select code from live_schools where id = %s", (msingi,)
    ).fetchone()[0] == "MSB-001"
    memberships = dict(conn.execute(
        "select u.email, m.role from school_memberships m "
        "join app_users u on u.id = m.user_id where m.school_id = %s", (msingi,)
    ).fetchall())
    assert memberships["admin@test.com"] == "director"
    assert memberships["and7005@yahoo.it"] == "driver"
    assert len([r for r in memberships.values() if r == "driver"]) == 3
    disabled = dict(conn.execute(
        "select email, disabled_at is not null from app_users"
    ).fetchall())
    assert disabled["admin@test.com"] is False
    for email in ("and7005@gmail.com", "and7005@yahoo.it",
                  "francis@saferide.test", "mary@saferide.test"):
        assert disabled[email] is True, email
    assert conn.execute(
        "select count(*) from auth_sessions where revoked_at is null"
    ).fetchone()[0] == 0
    assert conn.execute(
        "select school_id from live_admin_audit where action = 'pin-map-viewed'"
    ).fetchone()[0] is None, "Greenfield audit rows are released to NULL, not re-stamped"
    log = conn.execute(
        "select detail from tenancy_move_log where phase = 'move'"
    ).fetchall()
    assert len(log) == 1
    assert log[0][0]["target_school"] == str(msingi)
    assert log[0][0]["before"]["live_students"] == 2

    # Idempotence: a second apply is exit M.
    _run_file(conn, MOVE_SQL)
    assert conn.execute(
        "select count(*) from tenancy_move_log where phase = 'move'"
    ).fetchone()[0] == 1
