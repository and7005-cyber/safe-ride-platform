"""School isolation, slice 1 (U6): fleet schools + buses, drivers, parents.

Runs the real ``create_app()`` in-process (TestClient) against the local
database — the converted routers and scoped DAOs under test live in this
working tree, not in the docker API container. Covers the U6 contract for
these families: another school's record answers 404 and stays untouched
(verified by SQL), lists never leak foreign ids, creation stamps the scope
school regardless of the payload, cross-school relationships answer 409 with
wording that names no other school, and deletes are director-only.

Every row a test creates is removed in a finally block (or by the
``temp_school`` sweep); seeded rows are never deleted.
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
    temp_school,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="needs the local stack; set RUN_INTEGRATION=1",
)

# Seeded identities (backend/db/seeds/003_local_snapshot.sql, U1 tail).
DRIVER_B_USER_ID = "a0000000-0000-0000-0000-000000000015"
DRIVER_B_EMAIL = "driver.b@saferide.test"
DRIVER_B_NAME = "Dan Wekesa"
DRIVER_A_EMAIL = "francis@saferide.test"
PARENT_AMINA_ID = "a0000000-0000-0000-0000-000000000002"
PARENT_AMINA_EMAIL = "and7005@gmail.com"
SCHOOL_B_NAME = "IT Second School"


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


def make_b_bus(marker: str) -> str:
    """A bus positively owned by school B, via SQL (bus creation is scoped
    now, and this suite acts as school A/C staff who must not be able to
    create B rows)."""
    with db() as conn:
        return str(
            conn.execute(
                "insert into live_buses (name, school_id) values (%s, %s) returning id",
                (f"IT U6 B Bus {marker}", SCHOOL_B_ID),
            ).fetchone()["id"]
        )


def purge_bus(bus_id: str) -> None:
    with db() as conn:
        conn.execute("delete from live_buses where id = %s", (bus_id,))


def purge_audit_for(resource_id: str) -> None:
    with db() as conn:
        conn.execute(
            "delete from live_admin_audit where resource_id = %s", (resource_id,)
        )


def audit_actions_for(resource_id: str) -> list[str]:
    with db() as conn:
        return [
            r["action"]
            for r in conn.execute(
                "select action from live_admin_audit where resource_id = %s "
                "order by created_at",
                (resource_id,),
            ).fetchall()
        ]


# --- schools -----------------------------------------------------------------


def test_schools_list_is_the_active_school_only(client):
    listed = client.get("/api/fleet/schools", headers=hdr(client, DIRECTOR_A, SCHOOL_A_ID))
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert [str(s["id"]) for s in body] == [SCHOOL_A_ID]

    settings = client.get("/api/fleet/school", headers=hdr(client, DIRECTOR_A, SCHOOL_A_ID))
    assert settings.status_code == 200, settings.text
    assert str(settings.json()["id"]) == SCHOOL_A_ID
    assert settings.json()["name"] == body[0]["name"]

    listed_b = client.get("/api/fleet/schools", headers=hdr(client, DIRECTOR_B, SCHOOL_B_ID))
    assert [str(s["id"]) for s in listed_b.json()] == [SCHOOL_B_ID]


def test_school_create_and_delete_endpoints_are_gone(client):
    created = client.post(
        "/api/fleet/schools",
        json={"name": "IT U6 Ghost School"},
        headers=hdr(client, DIRECTOR_A, SCHOOL_A_ID),
    )
    assert created.status_code == 405, created.text  # method gone, path remains for GET
    deleted = client.delete(
        f"/api/fleet/schools/{SCHOOL_B_ID}", headers=hdr(client, DIRECTOR_A, SCHOOL_A_ID)
    )
    assert deleted.status_code == 405, deleted.text
    with db() as conn:
        assert conn.execute(
            "select 1 from live_schools where id = %s", (SCHOOL_B_ID,)
        ).fetchone() is not None


def test_update_school_pins_to_the_active_school(client):
    with db() as conn:
        before = conn.execute(
            "select name, address, phone from live_schools where id = %s", (SCHOOL_B_ID,)
        ).fetchone()
    hacked = client.put(
        f"/api/fleet/schools/{SCHOOL_B_ID}",
        json={"name": "IT U6 Hacked"},
        headers=hdr(client, DIRECTOR_A, SCHOOL_A_ID),
    )
    assert hacked.status_code == 404, hacked.text
    with db() as conn:
        after = conn.execute(
            "select name, address, phone from live_schools where id = %s", (SCHOOL_B_ID,)
        ).fetchone()
    assert after == before  # the refused write changed nothing

    marker = uuid.uuid4().hex[:6]
    with temp_school(f"IT U6 SchoolEdit {marker}") as school_c:
        updated = client.put(
            f"/api/fleet/schools/{school_c}",
            json={"name": f"IT U6 SchoolEdit {marker} renamed", "address": "IT Lane 1"},
            headers=hdr(client, DIRECTOR_A, school_c),
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["name"].endswith("renamed")
        assert audit_actions_for(school_c) == ["school-updated"]


# --- buses -------------------------------------------------------------------


def test_foreign_bus_is_invisible_and_untouchable(client):
    marker = uuid.uuid4().hex[:6]
    b_bus = make_b_bus(marker)
    try:
        listed = client.get("/api/fleet/buses", headers=hdr(client, DIRECTOR_A, SCHOOL_A_ID))
        assert listed.status_code == 200, listed.text
        assert b_bus not in {str(b["id"]) for b in listed.json()}
        assert all(str(b["school_id"]) == SCHOOL_A_ID for b in listed.json())

        with db() as conn:
            before = conn.execute(
                "select name, school_id, driver_id from live_buses where id = %s", (b_bus,)
            ).fetchone()
        edited = client.put(
            f"/api/fleet/buses/{b_bus}",
            json={"name": "IT U6 Hacked Bus"},
            headers=hdr(client, DIRECTOR_A, SCHOOL_A_ID),
        )
        assert edited.status_code == 404, edited.text
        deleted = client.delete(
            f"/api/fleet/buses/{b_bus}", headers=hdr(client, DIRECTOR_A, SCHOOL_A_ID)
        )
        assert deleted.status_code == 404, deleted.text
        with db() as conn:
            after = conn.execute(
                "select name, school_id, driver_id from live_buses where id = %s", (b_bus,)
            ).fetchone()
        assert after == before  # still there, byte-identical
    finally:
        purge_bus(b_bus)


def test_create_bus_stamps_the_scope_school(client):
    marker = uuid.uuid4().hex[:6]
    with temp_school(f"IT U6 BusHome {marker}") as school_c:
        created = client.post(
            "/api/fleet/buses",
            # A stray client-supplied school id is ignored, not honoured.
            json={"name": f"IT U6 Bus {marker}", "school_id": SCHOOL_B_ID},
            headers=hdr(client, DIRECTOR_A, school_c),
        )
        assert created.status_code == 200, created.text
        bus = created.json()
        with db() as conn:
            stored = conn.execute(
                "select school_id from live_buses where id = %s", (bus["id"],)
            ).fetchone()
        assert str(stored["school_id"]) == school_c
        listed = client.get("/api/fleet/buses", headers=hdr(client, DIRECTOR_A, school_c))
        assert {str(b["id"]) for b in listed.json()} == {str(bus["id"])}


def test_bus_delete_is_director_only(client):
    marker = uuid.uuid4().hex[:6]
    created = client.post(
        "/api/fleet/buses",
        json={"name": f"IT U6 DelBus {marker}"},
        headers=hdr(client, DIRECTOR_A, SCHOOL_A_ID),
    )
    assert created.status_code == 200, created.text
    bus_id = str(created.json()["id"])
    try:
        refused = client.delete(
            f"/api/fleet/buses/{bus_id}", headers=hdr(client, COORDINATOR_A, SCHOOL_A_ID)
        )
        assert refused.status_code == 403, refused.text
        with db() as conn:
            assert conn.execute(
                "select 1 from live_buses where id = %s", (bus_id,)
            ).fetchone() is not None
        deleted = client.delete(
            f"/api/fleet/buses/{bus_id}", headers=hdr(client, DIRECTOR_A, SCHOOL_A_ID)
        )
        assert deleted.status_code == 200, deleted.text
        with db() as conn:
            assert conn.execute(
                "select 1 from live_buses where id = %s", (bus_id,)
            ).fetchone() is None
        assert audit_actions_for(bus_id) == ["bus-created", "bus-deleted"]
    finally:
        purge_bus(bus_id)
        purge_audit_for(bus_id)


def test_bus_driver_must_hold_a_membership_at_this_school(client):
    marker = uuid.uuid4().hex[:6]
    with temp_school(f"IT U6 DriverCheck {marker}") as school_c:
        headers = hdr(client, DIRECTOR_A, school_c)
        # Create with a foreign driver: refused, nothing created.
        refused = client.post(
            "/api/fleet/buses",
            json={"name": f"IT U6 DrvBus {marker}", "driver_id": DRIVER_B_USER_ID},
            headers=headers,
        )
        assert refused.status_code == 409, refused.text
        detail = refused.json()["detail"]
        assert SCHOOL_B_NAME not in detail  # never names another school
        assert "another school" not in detail.lower()
        with db() as conn:
            assert conn.execute(
                "select 1 from live_buses where name = %s", (f"IT U6 DrvBus {marker}",)
            ).fetchone() is None

        created = client.post(
            "/api/fleet/buses", json={"name": f"IT U6 DrvBus {marker}"}, headers=headers
        )
        assert created.status_code == 200, created.text
        bus_id = str(created.json()["id"])

        # Update to a driver of ANOTHER school (B's seeded driver): 409, and
        # the bus keeps no assignment.
        base = {"name": f"IT U6 DrvBus {marker}"}
        assigned_b = client.put(
            f"/api/fleet/buses/{bus_id}",
            json={**base, "driver_id": DRIVER_B_USER_ID},
            headers=headers,
        )
        assert assigned_b.status_code == 409, assigned_b.text
        assert SCHOOL_B_NAME not in assigned_b.json()["detail"]
        with db() as conn:
            assert conn.execute(
                "select driver_id from live_buses where id = %s", (bus_id,)
            ).fetchone()["driver_id"] is None

        # A driver of this school passes: create one here, then assign.
        driver = client.post(
            "/api/accounts/drivers",
            json={
                "full_name": f"IT U6 Driver {marker}",
                "email": f"it-u6-driver-{marker}@test.local",
                "password": "Test1234",
            },
            headers=headers,
        )
        assert driver.status_code == 200, driver.text
        driver_id = str(driver.json()["id"])
        assigned = client.put(
            f"/api/fleet/buses/{bus_id}",
            json={**base, "driver_id": driver_id},
            headers=headers,
        )
        assert assigned.status_code == 200, assigned.text
        with db() as conn:
            assert str(conn.execute(
                "select driver_id from live_buses where id = %s", (bus_id,)
            ).fetchone()["driver_id"]) == driver_id

        # Removing the driver (director) clears the assignment and, with
        # nothing else held, deletes the identity outright.
        removed = client.delete(f"/api/accounts/drivers/{driver_id}", headers=headers)
        assert removed.status_code == 200, removed.text
        with db() as conn:
            assert conn.execute(
                "select driver_id from live_buses where id = %s", (bus_id,)
            ).fetchone()["driver_id"] is None
            assert conn.execute(
                "select 1 from app_users where id = %s", (driver_id,)
            ).fetchone() is None


# --- confirm-fleet -----------------------------------------------------------


def test_confirm_fleet_refuses_a_foreign_bus(client):
    marker = uuid.uuid4().hex[:6]
    b_bus = make_b_bus(marker)
    try:
        with temp_school(f"IT U6 Confirm {marker}") as school_c:
            headers = hdr(client, DIRECTOR_A, school_c)
            own = client.post(
                "/api/fleet/buses", json={"name": f"IT U6 CBus {marker}"}, headers=headers
            ).json()
            mixed = client.post(
                "/api/fleet-plans/confirm-fleet",
                json={"bus_ids": [own["id"], b_bus]},
                headers=headers,
            )
            assert mixed.status_code == 404, mixed.text
            with db() as conn:
                row = conn.execute(
                    "select school_id from live_buses where id = %s", (b_bus,)
                ).fetchone()
            assert str(row["school_id"]) == SCHOOL_B_ID  # untouched — no claim path

            # The payload's school_id is ignored: the scope decides.
            confirmed = client.post(
                "/api/fleet-plans/confirm-fleet",
                json={"school_id": SCHOOL_B_ID, "bus_ids": [own["id"]]},
                headers=headers,
            )
            assert confirmed.status_code == 200, confirmed.text
            body = confirmed.json()
            assert str(body["school_id"]) == school_c
            assert [b["id"] for b in body["buses"]] == [own["id"]]
            assert body["released"] == []
            assert {n["kind"] for n in body["notices"]} == {"no-depot"}
    finally:
        purge_bus(b_bus)


# --- drivers -----------------------------------------------------------------


def test_driver_list_is_membership_scoped(client):
    listed_a = client.get(
        "/api/accounts/drivers", headers=hdr(client, DIRECTOR_A, SCHOOL_A_ID)
    )
    assert listed_a.status_code == 200, listed_a.text
    emails_a = {d["email"] for d in listed_a.json()}
    assert DRIVER_B_EMAIL not in emails_a
    assert DRIVER_A_EMAIL in emails_a

    listed_b = client.get(
        "/api/accounts/drivers", headers=hdr(client, DIRECTOR_B, SCHOOL_B_ID)
    )
    emails_b = {d["email"] for d in listed_b.json()}
    assert DRIVER_B_EMAIL in emails_b
    assert not emails_b & emails_a  # the two rosters never overlap


def test_create_driver_conflicts_disclose_no_school(client):
    headers = hdr(client, DIRECTOR_A, SCHOOL_A_ID)
    marker = uuid.uuid4().hex[:6]
    # Email held by school B's driver: generic wording, no school named.
    clashed = client.post(
        "/api/accounts/drivers",
        json={"full_name": "IT U6 X", "email": DRIVER_B_EMAIL, "password": "Test1234"},
        headers=headers,
    )
    assert clashed.status_code == 400, clashed.text
    assert clashed.json()["detail"] == "That email is already in use"

    # PIN uniqueness is global (PIN login has no school context): the message
    # is unchanged (R34).
    fresh_email = f"it-u6-pin-{marker}@test.local"
    pinned = client.post(
        "/api/accounts/drivers",
        json={
            "full_name": "IT U6 Y", "email": fresh_email,
            "password": "Test1234", "pin": "0322",
        },
        headers=headers,
    )
    assert pinned.status_code == 409, pinned.text
    assert pinned.json()["detail"] == "That PIN is already in use by another driver"
    with db() as conn:
        assert conn.execute(
            "select 1 from app_users where email = %s", (fresh_email,)
        ).fetchone() is None  # the refused create left nothing behind


def test_foreign_driver_is_404_and_unchanged(client):
    edited = client.put(
        f"/api/accounts/drivers/{DRIVER_B_USER_ID}",
        json={"full_name": "IT U6 Hacked", "email": DRIVER_B_EMAIL},
        headers=hdr(client, DIRECTOR_A, SCHOOL_A_ID),
    )
    assert edited.status_code == 404, edited.text
    deleted = client.delete(
        f"/api/accounts/drivers/{DRIVER_B_USER_ID}",
        headers=hdr(client, DIRECTOR_A, SCHOOL_A_ID),
    )
    assert deleted.status_code == 404, deleted.text
    with db() as conn:
        row = conn.execute(
            "select full_name from app_users where id = %s", (DRIVER_B_USER_ID,)
        ).fetchone()
        assert row["full_name"] == DRIVER_B_NAME
        membership = conn.execute(
            "select removed_at from school_memberships where user_id = %s "
            "and school_id = %s and role = 'driver'",
            (DRIVER_B_USER_ID, SCHOOL_B_ID),
        ).fetchone()
        assert membership["removed_at"] is None


def test_delete_driver_creates_membership_and_spares_shared_identity(client):
    marker = uuid.uuid4().hex[:6]
    driver_id = None
    with temp_school(f"IT U6 SharedDrv {marker}") as school_c:
        headers = hdr(client, DIRECTOR_A, school_c)
        try:
            created = client.post(
                "/api/accounts/drivers",
                json={
                    "full_name": f"IT U6 Shared {marker}",
                    "email": f"it-u6-shared-{marker}@test.local",
                    "password": "Test1234",
                },
                headers=headers,
            )
            assert created.status_code == 200, created.text
            driver_id = str(created.json()["id"])
            with db() as conn:
                membership = conn.execute(
                    "select state, removed_at from school_memberships "
                    "where user_id = %s and school_id = %s and role = 'driver'",
                    (driver_id, school_c),
                ).fetchone()
                assert membership["state"] == "active"
                assert membership["removed_at"] is None
                # A second life elsewhere: an active membership at school B.
                conn.execute(
                    "insert into school_memberships (user_id, school_id, role, state, accepted_at) "
                    "values (%s, %s, 'driver', 'active', now())",
                    (driver_id, SCHOOL_B_ID),
                )
            removed = client.delete(
                f"/api/accounts/drivers/{driver_id}", headers=headers
            )
            assert removed.status_code == 200, removed.text
            with db() as conn:
                # Identity survives: it still holds the B membership.
                assert conn.execute(
                    "select 1 from app_users where id = %s", (driver_id,)
                ).fetchone() is not None
                here = conn.execute(
                    "select removed_at from school_memberships "
                    "where user_id = %s and school_id = %s", (driver_id, school_c),
                ).fetchone()
                assert here["removed_at"] is not None
                there = conn.execute(
                    "select removed_at from school_memberships "
                    "where user_id = %s and school_id = %s", (driver_id, SCHOOL_B_ID),
                ).fetchone()
                assert there["removed_at"] is None
        finally:
            if driver_id:
                with db() as conn:  # cascades both membership rows
                    conn.execute("delete from app_users where id = %s", (driver_id,))


def test_delete_driver_is_director_only(client):
    refused = client.delete(
        f"/api/accounts/drivers/{uuid.uuid4()}",
        headers=hdr(client, COORDINATOR_A, SCHOOL_A_ID),
    )
    assert refused.status_code == 403, refused.text


# --- parents -----------------------------------------------------------------


def test_parent_list_shows_only_this_schools_children(client):
    listed_a = client.get(
        "/api/accounts/parents", headers=hdr(client, DIRECTOR_A, SCHOOL_A_ID)
    )
    assert listed_a.status_code == 200, listed_a.text
    amina_a = next(
        p for p in listed_a.json() if p["email"] == PARENT_AMINA_EMAIL
    )
    assert "Ben Barasa" not in amina_a["students"]  # B's child never shows at A
    assert {"Faith Achieng", "Grace Njeri"} <= set(amina_a["students"])

    listed_b = client.get(
        "/api/accounts/parents", headers=hdr(client, DIRECTOR_B, SCHOOL_B_ID)
    )
    amina_b = next(
        p for p in listed_b.json() if p["email"] == PARENT_AMINA_EMAIL
    )
    assert "Ben Barasa" in amina_b["students"]
    assert "Faith Achieng" not in amina_b["students"]


def test_shared_parent_is_frozen_for_both_schools(client):
    with db() as conn:
        before = conn.execute(
            "select full_name, email, phone from app_users where id = %s",
            (PARENT_AMINA_ID,),
        ).fetchone()
    edited = client.put(
        f"/api/accounts/parents/{PARENT_AMINA_ID}",
        json={"full_name": "IT U6 Hacked", "email": before["email"]},
        headers=hdr(client, DIRECTOR_A, SCHOOL_A_ID),
    )
    assert edited.status_code == 409, edited.text
    detail = edited.json()["detail"]
    assert "shared with another school" in detail
    assert SCHOOL_B_NAME not in detail  # neutral: no other school named
    deleted = client.delete(
        f"/api/accounts/parents/{PARENT_AMINA_ID}",
        headers=hdr(client, DIRECTOR_A, SCHOOL_A_ID),
    )
    assert deleted.status_code == 409, deleted.text
    with db() as conn:
        after = conn.execute(
            "select full_name, email, phone from app_users where id = %s",
            (PARENT_AMINA_ID,),
        ).fetchone()
    assert after == before


def test_shared_parent_unlink_then_delete_succeeds(client):
    """AE18/R33 end-to-end on a synthetic family: 404 where unlinked, 409
    while shared, delete allowed once the other school's link is gone —
    coordinator still refused on the delete throughout."""
    marker = uuid.uuid4().hex[:6]
    parent_id = student_a = student_b = None
    try:
        with db() as conn:
            parent_id = str(conn.execute(
                "insert into app_users (email, password_hash, full_name) "
                "values (%s, 'x', %s) returning id",
                (f"it-u6-parent-{marker}@test.local", f"IT U6 Parent {marker}"),
            ).fetchone()["id"])
            conn.execute(
                "insert into app_user_roles (user_id, role) values (%s, 'parent')",
                (parent_id,),
            )
            student_a = str(conn.execute(
                "insert into live_students (name, school_id) values (%s, %s) returning id",
                (f"IT U6 Kid A {marker}", SCHOOL_A_ID),
            ).fetchone()["id"])
            conn.execute(
                "insert into live_parent_students (parent_id, student_id) values (%s, %s)",
                (parent_id, student_a),
            )

        # Linked at A only: school B has no such parent.
        unlinked = client.put(
            f"/api/accounts/parents/{parent_id}",
            json={"full_name": "IT U6 Nope", "email": f"it-u6-parent-{marker}@test.local"},
            headers=hdr(client, DIRECTOR_B, SCHOOL_B_ID),
        )
        assert unlinked.status_code == 404, unlinked.text

        with db() as conn:
            student_b = str(conn.execute(
                "insert into live_students (name, school_id) values (%s, %s) returning id",
                (f"IT U6 Kid B {marker}", SCHOOL_B_ID),
            ).fetchone()["id"])
            conn.execute(
                "insert into live_parent_students (parent_id, student_id) values (%s, %s)",
                (parent_id, student_b),
            )

        # Shared now: A can neither edit nor delete.
        shared_edit = client.put(
            f"/api/accounts/parents/{parent_id}",
            json={"full_name": "IT U6 Nope", "email": f"it-u6-parent-{marker}@test.local"},
            headers=hdr(client, DIRECTOR_A, SCHOOL_A_ID),
        )
        assert shared_edit.status_code == 409, shared_edit.text
        shared_delete = client.delete(
            f"/api/accounts/parents/{parent_id}",
            headers=hdr(client, DIRECTOR_A, SCHOOL_A_ID),
        )
        assert shared_delete.status_code == 409, shared_delete.text
        with db() as conn:
            assert conn.execute(
                "select 1 from app_users where id = %s", (parent_id,)
            ).fetchone() is not None

        # B's link removed: the parent is A's alone again.
        with db() as conn:
            conn.execute(
                "delete from live_parent_students where parent_id = %s and student_id = %s",
                (parent_id, student_b),
            )
            conn.execute("delete from live_students where id = %s", (student_b,))
            student_b = None

        refused = client.delete(
            f"/api/accounts/parents/{parent_id}",
            headers=hdr(client, COORDINATOR_A, SCHOOL_A_ID),
        )
        assert refused.status_code == 403, refused.text  # account removal: director-only

        deleted = client.delete(
            f"/api/accounts/parents/{parent_id}",
            headers=hdr(client, DIRECTOR_A, SCHOOL_A_ID),
        )
        assert deleted.status_code == 200, deleted.text
        with db() as conn:
            assert conn.execute(
                "select 1 from app_users where id = %s", (parent_id,)
            ).fetchone() is None
            assert conn.execute(
                "select 1 from live_parent_students where parent_id = %s", (parent_id,)
            ).fetchone() is None  # links cascaded with the account
        assert audit_actions_for(parent_id) == ["parent-deleted"]
    finally:
        with db() as conn:
            if parent_id:
                conn.execute("delete from app_users where id = %s", (parent_id,))
                conn.execute(
                    "delete from live_admin_audit where resource_id = %s", (parent_id,)
                )
            for student_id in (student_a, student_b):
                if student_id:
                    conn.execute(
                        "delete from live_students where id = %s", (student_id,)
                    )
