"""School isolation, slice 1 (U6): students, bulk import, pin map, absences.

Runs the real ``create_app()`` in-process (TestClient) against the local
database. Covers: scoped lists, creation stamping the scope school over any
payload-supplied id, foreign records answering 404 and staying untouched
(SQL-verified), ``_sync_routes`` refusing another school's routes, bulk
upload taking its school from the scope with one 'students-imported' audit
row, the scope-bound audited pin map, and absences scoped through the
student. Deletes are director-only; absence mark AND clear stay staff-wide.

Every row a test creates is removed in a finally block; seeded rows are
never deleted.
"""

import os
import uuid

import psycopg
import pytest
from psycopg.rows import dict_row

from conftest import (
    COORDINATOR_A,
    DIRECTOR_A,
    DIRECTOR_B,
    DSN,
    SCHOOL_A_ID,
    SCHOOL_B_ID,
    TEST_PASSWORD,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="needs the local stack; set RUN_INTEGRATION=1",
)

# Seeded school-B fixtures (backend/db/seeds/003_local_snapshot.sql, U1 tail).
STUDENT_B_ID = "50000000-0000-0000-0000-00000000000b"
STUDENT_B_NAME = "Ben Barasa"
ROUTE_B_ID = "40000000-0000-0000-0000-00000000000b"
STUDENT_A_FAITH_ID = "50000000-0000-0000-0000-000000000001"


@pytest.fixture(scope="module")
def client(in_process_db):
    from fastapi.testclient import TestClient

    from app.main import create_app

    return TestClient(create_app())


_tokens: dict[str, str] = {}


def tok(client, email: str) -> str:
    if email not in _tokens:
        resp = client.post(
            "/api/auth/login", json={"email": email, "password": TEST_PASSWORD}
        )
        assert resp.status_code == 200, resp.text
        _tokens[email] = resp.json()["token"]
    return _tokens[email]


def hdr(client, email: str, school: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {tok(client, email)}"}
    if school is not None:
        headers["X-School-Id"] = school
    return headers


def db():
    return psycopg.connect(DSN, autocommit=True, row_factory=dict_row)


def student_payload(marker: str, **overrides) -> dict:
    payload = {
        "name": f"IT U6 Student {marker}",
        "grade": "Grade 1",
        "parent_name": f"IT U6 Parent {marker}",
        "parent_phone": "+254711000200",
        "parent_email": f"it-u6-st-{marker}@test.local",
    }
    payload.update(overrides)
    return payload


def purge_student(student_id: str | None) -> None:
    if not student_id:
        return
    with db() as conn:
        conn.execute("delete from live_students where id = %s", (student_id,))


def purge_audit_for(resource_id: str | None) -> None:
    if not resource_id:
        return
    with db() as conn:
        conn.execute(
            "delete from live_admin_audit where resource_id = %s", (resource_id,)
        )


def student_row(student_id: str) -> dict | None:
    with db() as conn:
        return conn.execute(
            "select name, grade, parent_email, school_id, status from live_students "
            "where id = %s",
            (student_id,),
        ).fetchone()


# --- lists and creation ------------------------------------------------------


def test_student_lists_are_scoped(client):
    listed_a = client.get("/api/students", headers=hdr(client, DIRECTOR_A, SCHOOL_A_ID))
    assert listed_a.status_code == 200, listed_a.text
    ids_a = {str(s["id"]) for s in listed_a.json()}
    assert STUDENT_B_ID not in ids_a
    assert all(str(s["school_id"]) == SCHOOL_A_ID for s in listed_a.json())

    listed_b = client.get("/api/students", headers=hdr(client, DIRECTOR_B, SCHOOL_B_ID))
    ids_b = {str(s["id"]) for s in listed_b.json()}
    assert STUDENT_B_ID in ids_b
    assert STUDENT_A_FAITH_ID not in ids_b


def test_create_student_stamps_the_scope_school(client):
    marker = uuid.uuid4().hex[:6]
    student_id = None
    try:
        # Coordinator may create (add/edit is staff-wide); the payload's
        # school_id says B and is overridden by the scope.
        created = client.post(
            "/api/students",
            json=student_payload(marker, school_id=SCHOOL_B_ID),
            headers=hdr(client, COORDINATOR_A, SCHOOL_A_ID),
        )
        assert created.status_code == 200, created.text
        student_id = str(created.json()["id"])
        assert str(student_row(student_id)["school_id"]) == SCHOOL_A_ID

        # Delete is director-only: the coordinator is refused, the director
        # succeeds, and the pair of audit rows names the child by id only.
        refused = client.delete(
            f"/api/students/{student_id}",
            headers=hdr(client, COORDINATOR_A, SCHOOL_A_ID),
        )
        assert refused.status_code == 403, refused.text
        assert student_row(student_id) is not None
        deleted = client.delete(
            f"/api/students/{student_id}",
            headers=hdr(client, DIRECTOR_A, SCHOOL_A_ID),
        )
        assert deleted.status_code == 200, deleted.text
        assert student_row(student_id) is None
        with db() as conn:
            actions = [
                r["action"]
                for r in conn.execute(
                    "select action from live_admin_audit where resource_id = %s "
                    "order by created_at",
                    (student_id,),
                ).fetchall()
            ]
        assert actions == ["student-created", "student-deleted"]
    finally:
        purge_student(student_id)
        purge_audit_for(student_id)


def test_foreign_student_is_404_and_unchanged(client):
    before = student_row(STUDENT_B_ID)
    edited = client.put(
        f"/api/students/{STUDENT_B_ID}",
        json=student_payload("hack", name="IT U6 Hacked"),
        headers=hdr(client, DIRECTOR_A, SCHOOL_A_ID),
    )
    assert edited.status_code == 404, edited.text
    deleted = client.delete(
        f"/api/students/{STUDENT_B_ID}", headers=hdr(client, DIRECTOR_A, SCHOOL_A_ID)
    )
    assert deleted.status_code == 404, deleted.text
    assert student_row(STUDENT_B_ID) == before
    assert before["name"] == STUDENT_B_NAME


# --- route sync refuses foreign routes ---------------------------------------


def test_sync_routes_refuses_another_schools_route(client):
    marker = uuid.uuid4().hex[:6]
    student_id = None
    try:
        with db() as conn:
            stops_before = conn.execute(
                "select count(*) as n from live_route_stops where route_id = %s",
                (ROUTE_B_ID,),
            ).fetchone()["n"]

        # Creation naming B's route: the whole enrolment rolls back.
        refused = client.post(
            "/api/students",
            json=student_payload(marker, route_ids=[ROUTE_B_ID]),
            headers=hdr(client, DIRECTOR_A, SCHOOL_A_ID),
        )
        assert refused.status_code == 404, refused.text
        with db() as conn:
            assert conn.execute(
                "select 1 from live_students where name = %s",
                (f"IT U6 Student {marker}",),
            ).fetchone() is None

        created = client.post(
            "/api/students",
            json=student_payload(marker),
            headers=hdr(client, DIRECTOR_A, SCHOOL_A_ID),
        )
        assert created.status_code == 200, created.text
        student_id = str(created.json()["id"])

        # An update naming B's route is refused too, leaving no link behind.
        linked = client.put(
            f"/api/students/{student_id}",
            json=student_payload(marker, route_ids=[ROUTE_B_ID]),
            headers=hdr(client, DIRECTOR_A, SCHOOL_A_ID),
        )
        assert linked.status_code == 404, linked.text
        with db() as conn:
            assert conn.execute(
                "select 1 from live_student_routes where student_id = %s",
                (student_id,),
            ).fetchone() is None
            stops_after = conn.execute(
                "select count(*) as n from live_route_stops where route_id = %s",
                (ROUTE_B_ID,),
            ).fetchone()["n"]
        assert stops_after == stops_before  # B's route was never regenerated
    finally:
        purge_student(student_id)
        purge_audit_for(student_id)


# --- bulk import -------------------------------------------------------------


def test_bulk_upload_takes_the_school_from_scope(client):
    marker = uuid.uuid4().hex[:6]
    name = f"IT U6 Bulk {marker}"
    row = {
        "name": name,
        "grade": "Grade 2",
        "parent_name": f"IT U6 BulkParent {marker}",
        "parent_phone": "+254711000201",
        "parent_email": f"it-u6-bulk-{marker}@test.local",
    }
    student_id = None
    audit_ids: list[str] = []
    try:
        # The stray payload school_id (B) is ignored: the scope stamps A.
        uploaded = client.post(
            "/api/students/bulk",
            json={"school_id": SCHOOL_B_ID, "students": [row]},
            headers=hdr(client, DIRECTOR_A, SCHOOL_A_ID),
        )
        assert uploaded.status_code == 200, uploaded.text
        body = uploaded.json()
        assert body["inserted"] == 1 and body["errors"] == []
        with db() as conn:
            stored = conn.execute(
                "select id, school_id from live_students where name = %s", (name,)
            ).fetchone()
            assert str(stored["school_id"]) == SCHOOL_A_ID
            student_id = str(stored["id"])
            audit = conn.execute(
                "select id, detail, school_id from live_admin_audit "
                "where action = 'students-imported' "
                "order by created_at desc limit 1",
            ).fetchone()
        # Exactly one summary row per upload: counts only, stamped A.
        assert str(audit["school_id"]) == SCHOOL_A_ID
        assert audit["detail"]["inserted"] == 1
        assert name not in str(audit["detail"])
        audit_ids.append(str(audit["id"]))

        # validate flags the (name, school) duplicate at THIS school only.
        validated = client.post(
            "/api/students/bulk/validate",
            json={"students": [row]},
            headers=hdr(client, DIRECTOR_A, SCHOOL_A_ID),
        )
        assert validated.status_code == 200, validated.text
        assert str(validated.json()["rows"][0]["duplicate_of"]["id"]) == student_id
        validated_b = client.post(
            "/api/students/bulk/validate",
            json={"students": [row]},
            headers=hdr(client, DIRECTOR_B, SCHOOL_B_ID),
        )
        assert validated_b.json()["rows"][0]["duplicate_of"] is None
    finally:
        purge_student(student_id)
        purge_audit_for(student_id)
        with db() as conn:
            for audit_id in audit_ids:
                conn.execute(
                    "delete from live_admin_audit where id = %s", (audit_id,)
                )


# --- pin map -----------------------------------------------------------------


def test_pin_map_is_scope_bound_and_audited(client):
    # The legacy school_id query param is ignored: the scope school answers.
    viewed = client.get(
        "/api/students/pin-map",
        params={"school_id": SCHOOL_B_ID},
        headers=hdr(client, DIRECTOR_A, SCHOOL_A_ID),
    )
    assert viewed.status_code == 200, viewed.text
    body = viewed.json()
    assert str(body["school_id"]) == SCHOOL_A_ID
    everyone = {p["id"] for p in body["placed"]} | {p["id"] for p in body["unresolved"]}
    assert STUDENT_B_ID not in everyone
    assert STUDENT_A_FAITH_ID in everyone
    with db() as conn:
        audit = conn.execute(
            "select id, school_id from live_admin_audit "
            "where action = 'pin-map-viewed' order by created_at desc limit 1",
        ).fetchone()
        assert str(audit["school_id"]) == SCHOOL_A_ID
        conn.execute("delete from live_admin_audit where id = %s", (audit["id"],))


# --- absences ----------------------------------------------------------------


def test_absences_are_scoped_through_the_student(client):
    marker = uuid.uuid4().hex[:6]
    date = "2031-03-04"  # never today: no run guards, no status side-effects
    student_id = None
    b_absence_id = None
    absence_id = None
    try:
        # Marking another school's student: 404, and no row appears.
        refused = client.post(
            "/api/students/absences",
            json={"student_id": STUDENT_B_ID, "date": date},
            headers=hdr(client, COORDINATOR_A, SCHOOL_A_ID),
        )
        assert refused.status_code == 404, refused.text
        with db() as conn:
            assert conn.execute(
                "select 1 from live_student_absences where student_id = %s "
                "and absence_date = %s::date",
                (STUDENT_B_ID, date),
            ).fetchone() is None

        created = client.post(
            "/api/students",
            json=student_payload(marker),
            headers=hdr(client, DIRECTOR_A, SCHOOL_A_ID),
        )
        assert created.status_code == 200, created.text
        student_id = str(created.json()["id"])

        # Coordinator marks (staff-wide), the row lands stamped with A.
        marked = client.post(
            "/api/students/absences",
            json={"student_id": student_id, "date": date, "reason": "IT U6"},
            headers=hdr(client, COORDINATOR_A, SCHOOL_A_ID),
        )
        assert marked.status_code == 200, marked.text
        absence_id = str(marked.json()["id"])
        with db() as conn:
            stored = conn.execute(
                "select school_id from live_student_absences where id = %s",
                (absence_id,),
            ).fetchone()
        assert str(stored["school_id"]) == SCHOOL_A_ID

        # A same-date absence for B's student (SQL) never shows in A's list.
        with db() as conn:
            b_absence_id = str(conn.execute(
                "insert into live_student_absences "
                "(student_id, absence_date, scope, source, school_id) "
                "values (%s, %s::date, 'day', 'admin', "
                "(select school_id from live_students where id = %s)) returning id",
                (STUDENT_B_ID, date, STUDENT_B_ID),
            ).fetchone()["id"])
        listed = client.get(
            "/api/students/absences",
            params={"date": date},
            headers=hdr(client, DIRECTOR_A, SCHOOL_A_ID),
        )
        assert listed.status_code == 200, listed.text
        listed_ids = {str(a["id"]) for a in listed.json()}
        assert absence_id in listed_ids
        assert b_absence_id not in listed_ids

        # Clearing B's absence from A: 404, row untouched.
        blocked = client.delete(
            f"/api/students/absences/{b_absence_id}",
            headers=hdr(client, DIRECTOR_A, SCHOOL_A_ID),
        )
        assert blocked.status_code == 404, blocked.text
        with db() as conn:
            assert conn.execute(
                "select 1 from live_student_absences where id = %s", (b_absence_id,)
            ).fetchone() is not None

        # The coordinator clears their own school's row (operational, not
        # history destruction — stays require_staff by design).
        cleared = client.delete(
            f"/api/students/absences/{absence_id}",
            headers=hdr(client, COORDINATOR_A, SCHOOL_A_ID),
        )
        assert cleared.status_code == 200, cleared.text
        with db() as conn:
            assert conn.execute(
                "select 1 from live_student_absences where id = %s", (absence_id,)
            ).fetchone() is None
            actions = [
                r["action"]
                for r in conn.execute(
                    "select action from live_admin_audit where resource_id = %s "
                    "order by created_at",
                    (absence_id,),
                ).fetchall()
            ]
        assert actions == ["absence-marked", "absence-cleared"]
    finally:
        with db() as conn:
            if b_absence_id:
                conn.execute(
                    "delete from live_student_absences where id = %s", (b_absence_id,)
                )
            if absence_id:
                conn.execute(
                    "delete from live_student_absences where id = %s", (absence_id,)
                )
                conn.execute(
                    "delete from live_admin_audit where resource_id = %s", (absence_id,)
                )
        purge_student(student_id)
        purge_audit_for(student_id)
