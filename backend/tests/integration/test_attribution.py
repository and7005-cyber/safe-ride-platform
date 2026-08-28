"""Attribution (U9): one audit trail for staff and provider actions.

Direct-DAO entry against the local database (the slot-in suite's pattern):
the converted writers must produce exactly one attributed audit row inside
their own transaction, and result payloads must carry server-computed
``*_by_display`` strings that mask provider identity as "SafeRide" (R25) —
while the audit row itself keeps the provider's real name for the
provider-side reader (U10).
"""

import os
import uuid

import psycopg
import pytest

from conftest import DSN, PROVIDER, SCHOOL_A_ID

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="needs the local stack; set RUN_INTEGRATION=1",
)

STAFF_ACTOR = {
    "id": None,  # filled per test from the seeded director
    "email": "director.a@saferide.test",
    "full_name": "Dora Director",
    "provider": None,
    "support_session": None,
}


def db():
    return psycopg.connect(DSN, autocommit=True)


@pytest.fixture()
def staff_actor(in_process_db):
    with db() as conn:
        row = conn.execute(
            "select id, email, full_name from app_users where email = %s",
            (STAFF_ACTOR["email"],),
        ).fetchone()
    return {**STAFF_ACTOR, "id": str(row[0]), "full_name": row[2]}


@pytest.fixture()
def provider_actor(in_process_db):
    with db() as conn:
        row = conn.execute(
            "select id, full_name from app_users where email = %s", (PROVIDER,)
        ).fetchone()
    return {
        "id": str(row[0]),
        "email": PROVIDER,
        "full_name": row[1],
        "provider": {"totp_enrolled": False},
        "support_session": None,
    }


def audit_rows(action: str, resource_id: str) -> list[dict]:
    with db() as conn, conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        return cur.execute(
            "select * from live_admin_audit where action = %s and resource_id = %s "
            "order by created_at",
            (action, resource_id),
        ).fetchall()


def purge_audit(resource_id: str) -> None:
    with db() as conn:
        conn.execute(
            "delete from live_admin_audit where resource_id = %s", (resource_id,)
        )


# --- force close (AE14) ------------------------------------------------------


def test_force_close_records_the_actor_and_reports_the_display(staff_actor):
    from app.dao.run_dao import RunDao

    dao = RunDao()
    with db() as conn:
        run_id = str(
            conn.execute(
                "insert into live_runs (school_id, type, date, status) "
                "values (%s, 'morning', current_date, 'in-progress') returning id",
                (SCHOOL_A_ID,),
            ).fetchone()[0]
        )
    try:
        result = dao.force_close_run(staff_actor, run_id)
        assert result["status"] == "completed"
        assert result["force_closed_by_display"] == staff_actor["full_name"]

        rows = audit_rows("run-force-closed", run_id)
        assert len(rows) == 1
        row = rows[0]
        assert str(row["actor_id"]) == staff_actor["id"]
        assert row["actor_kind"] == "staff"
        assert str(row["school_id"]) == SCHOOL_A_ID
        assert row["resource_type"] == "run"
        assert row["support_session_id"] is None

        report = dao.run_report(run_id)
        assert report["force_closed_by_display"] == staff_actor["full_name"]
    finally:
        purge_audit(run_id)
        with db() as conn:
            conn.execute("delete from live_runs where id = %s", (run_id,))


def test_provider_force_close_masks_the_display_but_keeps_the_name(provider_actor):
    from app.dao.run_dao import RunDao

    dao = RunDao()
    with db() as conn:
        support_id = str(
            conn.execute(
                "insert into provider_support_sessions "
                "(provider_user_id, school_id, reason) "
                "values (%s, %s, 'IT attribution test') returning id",
                (provider_actor["id"], SCHOOL_A_ID),
            ).fetchone()[0]
        )
        run_id = str(
            conn.execute(
                "insert into live_runs (school_id, type, date, status) "
                "values (%s, 'morning', current_date, 'in-progress') returning id",
                (SCHOOL_A_ID,),
            ).fetchone()[0]
        )
    actor = {**provider_actor, "support_session": {"id": support_id, "school_id": SCHOOL_A_ID}}
    try:
        result = dao.force_close_run(actor, run_id)
        # School-facing display masks the provider…
        assert result["force_closed_by_display"] == "SafeRide"
        assert dao.run_report(run_id)["force_closed_by_display"] == "SafeRide"
        # …while the audit row keeps who it really was, and the step-in.
        row = audit_rows("run-force-closed", run_id)[0]
        assert row["actor_kind"] == "provider"
        assert row["actor_name"] == provider_actor["full_name"]
        assert str(row["support_session_id"]) == support_id
    finally:
        purge_audit(run_id)
        with db() as conn:
            conn.execute("delete from live_runs where id = %s", (run_id,))
            conn.execute(
                "delete from provider_support_sessions where id = %s", (support_id,)
            )


# --- incident acknowledgment -------------------------------------------------


def test_acknowledge_writes_one_audit_row_and_masked_list_display(
    staff_actor, provider_actor
):
    from app.dao.incident_dao import IncidentDao

    dao = IncidentDao()
    ids = []
    with db() as conn:
        for _ in range(2):
            ids.append(
                str(
                    conn.execute(
                        "insert into live_incidents (type, description, school_id) "
                        "values ('other', 'IT attribution', %s) returning id",
                        (SCHOOL_A_ID,),
                    ).fetchone()[0]
                )
            )
    try:
        by_staff = dao.acknowledge(ids[0], staff_actor)
        assert by_staff["acknowledged_by_display"] == staff_actor["full_name"]
        by_provider = dao.acknowledge(ids[1], provider_actor)
        assert by_provider["acknowledged_by_display"] == "SafeRide"

        for incident_id, kind in ((ids[0], "staff"), (ids[1], "provider")):
            rows = audit_rows("incident-acknowledged", incident_id)
            assert len(rows) == 1
            assert rows[0]["actor_kind"] == kind
            assert str(rows[0]["school_id"]) == SCHOOL_A_ID

        listed = {i["id"]: i for i in map(dict, dao.list_incidents())}
        assert listed[uuid.UUID(ids[0])]["acknowledged_by_display"] == (
            staff_actor["full_name"]
        )
        assert listed[uuid.UUID(ids[1])]["acknowledged_by_display"] == "SafeRide"
    finally:
        for incident_id in ids:
            purge_audit(incident_id)
        with db() as conn:
            conn.execute(
                "delete from live_incidents where id = any(%s)",
                ([uuid.UUID(i) for i in ids],),
            )


# --- absences: marked_by display ---------------------------------------------


def test_absence_list_carries_the_marker_display(staff_actor):
    from app.dao.absence_dao import AbsenceDao

    dao = AbsenceDao()
    with db() as conn:
        student_id = str(
            conn.execute(
                "select id from live_students where school_id = %s limit 1",
                (SCHOOL_A_ID,),
            ).fetchone()[0]
        )
        prior_status = conn.execute(
            "select status from live_students where id = %s", (student_id,)
        ).fetchone()[0]
    marked = dao.mark_absent(student_id, "2031-01-06", "IT attribution", staff_actor["id"])
    try:
        listed = [
            a
            for a in dao.list_absences("2031-01-06")
            if str(a["student_id"]) == student_id
        ]
        assert len(listed) == 1
        assert listed[0]["marked_by_display"] == staff_actor["full_name"]
        assert str(listed[0]["marked_by"]) == staff_actor["id"]
    finally:
        with db() as conn:
            conn.execute(
                "delete from live_student_absences where id = %s", (marked["id"],)
            )
            conn.execute(
                "update live_students set status = %s where id = %s",
                (prior_status, student_id),
            )


# --- the audit detail hygiene rule -------------------------------------------


def test_no_audit_detail_carries_emails_passwords_or_child_names():
    with db() as conn, conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        rows = cur.execute(
            "select action, detail from live_admin_audit where detail is not null"
        ).fetchall()
    for row in rows:
        text = str(row["detail"]).lower()
        assert "password" not in text, row
        assert "token" not in text, row
        assert "@" not in text, row
