"""Student status derived from participation (plan 2026-07-28-001, U3/R2-R6).

The old derivation read a mutable status column and compensated for it with
four staleness branches. It could not say which run a boarding belonged to or
whether anyone observed it, so it got two cases wrong by construction: a child
left at school on an earlier day kept reading 'at school', and a newly created
student read 'at school' before ever travelling.

It also had no way to express two states this work needs: a presumed afternoon
board, and a child the office recorded as unaccounted.

Run with the stack up: RUN_INTEGRATION=1 pytest tests/integration/test_derived_status.py -q
"""

import os
import random
import uuid

import httpx
import psycopg
import pytest

from conftest import purge_accounts, purge_run, school_sandbox

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="needs the local stack; set RUN_INTEGRATION=1",
)

BASE = os.environ.get("INTEGRATION_API_URL", "http://localhost:9001")
DSN = os.environ.get("DATABASE_URL", "postgresql://saferide:saferide@localhost:5432/saferide")
ADMIN = {"email": "admin@test.com", "password": "test1234."}


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE, timeout=30) as c:
        yield c


@pytest.fixture(scope="module")
def sandbox():
    # Post-U6 world provisioning: school creation left the staff API, so the
    # throwaway school (plus its own single-membership admin, whose header
    # fallback lands there) is provisioned by the conftest sandbox instead.
    with school_sandbox("IT DS School", lat=-1.29, lng=36.82) as sb:
        yield sb


@pytest.fixture(scope="module")
def admin_headers(client, sandbox):
    response = client.post(
        "/api/auth/login",
        json={"email": sandbox["email"], "password": sandbox["password"]},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def admin_status(client, admin_headers, student_id: str) -> str:
    row = next(s for s in client.get("/api/students", headers=admin_headers).json()
               if s["id"] == student_id)
    return row["display_status"]


def parent_status(client, parent_headers, student_id: str) -> str:
    row = next(c for c in client.get("/api/parent-portal/children",
                                     headers=parent_headers).json()
               if c["id"] == student_id)
    return row["display_status"]


def driver_status(client, driver_headers, student_id: str) -> str | None:
    context = client.get("/api/runs/driver/context", headers=driver_headers).json()
    row = next((s for s in context["students"] if s["id"] == student_id), None)
    return row["display_status"] if row else None


def force_status_column(student_id: str, status: str) -> None:
    """Stage a raw column value. Since U3 nothing derives from it — which is
    what several of these tests assert."""
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("update live_students set status = %s where id = %s", (status, student_id))


def mark_unaccounted(run_id: str, student_id: str) -> None:
    """Stage the outcome the office force-close records. The force-close itself
    lands in U6; the derivation has to render it correctly first."""
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(
            "update run_participation set unaccounted_at = now() "
            "where run_id = %s and student_id = %s",
            (run_id, student_id),
        )


@pytest.fixture(scope="module")
def fleet(client, admin_headers, sandbox):
    marker = uuid.uuid4().hex[:6]
    created: dict = {"marker": marker}
    pin = str(random.randint(100000, 999999))

    created["driver"] = client.post(
        "/api/accounts/drivers",
        json={"full_name": f"IT DS Driver {marker}", "email": f"it-ds-drv-{marker}@test.local",
              "password": "test1234.", "phone": f"+2547{random.randint(10000000, 99999999)}",
              "pin": pin},
        headers=admin_headers,
    ).json()
    created["pin"] = pin

    created["bus"] = client.post(
        "/api/fleet/buses",
        json={"name": f"IT DS Bus {marker}", "capacity": 20,
              "driver_id": created["driver"]["id"]},
        headers=admin_headers,
    ).json()

    created["school"] = {"id": sandbox["id"], "name": sandbox["name"]}

    for period in ("morning", "afternoon"):
        created[period] = client.post(
            "/api/fleet/routes",
            json={"name": f"IT DS {period} {marker}", "type": period,
                  "bus_id": created["bus"]["id"], "school_id": created["school"]["id"]},
            headers=admin_headers,
        ).json()

    signup = client.post("/api/auth/signup", json={
        "email": f"it-ds-parent-{marker}@test.local", "password": "ParentPass1!",
        "full_name": f"IT DS Parent {marker}", "role": "parent"}).json()
    created["parent_id"] = signup["user"]["id"]
    created["parent_headers"] = {"Authorization": f"Bearer {signup['token']}"}

    created["student"] = client.post(
        "/api/students",
        json={"name": f"IT DS Kid {marker}", "parent_name": "IT DS Parent",
              "parent_phone": f"+2547{random.randint(10000000, 99999999)}",
              "parent_email": f"it-ds-parent-{marker}@test.local",
              "school_id": created["school"]["id"], "home_lat": -1.30, "home_lng": 36.79,
              "pickup_time": "06:30",
              "route_ids": [created["morning"]["id"], created["afternoon"]["id"]]},
        headers=admin_headers,
    ).json()

    try:
        yield created
    finally:
        for run in client.get("/api/runs", headers=admin_headers).json():
            if run.get("bus_id") == created["bus"]["id"]:
                purge_run(run['id'])
        client.delete(f"/api/students/{created['student']['id']}", headers=admin_headers)
        for period in ("morning", "afternoon"):
            client.delete(f"/api/fleet/routes/{created[period]['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/buses/{created['bus']['id']}", headers=admin_headers)
        client.delete(f"/api/accounts/drivers/{created['driver']['id']}", headers=admin_headers)
        purge_accounts(created['parent_id'])


@pytest.fixture
def driver_headers(client, fleet):
    token = client.post("/api/auth/pin-login", json={"pin": fleet["pin"]}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_a_new_student_reads_at_home_not_at_school(client, admin_headers, fleet):
    """R5: the column defaults to at-school, which claimed a child was at school
    before they had ever travelled. With no participation today, they are at
    home."""
    assert admin_status(client, admin_headers, fleet["student"]["id"]) == "at-home"


def test_the_stored_column_no_longer_decides_anything(client, admin_headers, fleet):
    """R2: staging any value on the column changes nothing.

    This is the whole point of the rewrite — four staleness branches existed
    only to second-guess a column with no provenance.
    """
    for staged in ("on-bus", "dropped-off", "absent", "at-school"):
        force_status_column(fleet["student"]["id"], staged)
        assert admin_status(client, admin_headers, fleet["student"]["id"]) == "at-home", (
            f"staging {staged!r} on the column moved the derived status"
        )
    force_status_column(fleet["student"]["id"], "at-school")


def test_a_confirmed_boarding_reads_on_bus_everywhere(
    client, admin_headers, fleet, driver_headers
):
    """R2: a driver's tap is an observation, and all three surfaces agree."""
    started = client.post("/api/runs/driver/start",
                          json={"route_id": fleet["morning"]["id"]}, headers=driver_headers)
    run_id = started.json()["id"]
    try:
        client.post("/api/runs/driver/arrive", json={"run_id": run_id}, headers=driver_headers)
        client.post("/api/runs/driver/arrive", json={"run_id": run_id}, headers=driver_headers)
        client.post("/api/runs/driver/boarding",
                    json={"student_id": fleet["student"]["id"], "on_bus": True},
                    headers=driver_headers)

        sid = fleet["student"]["id"]
        assert admin_status(client, admin_headers, sid) == "on-bus"
        assert parent_status(client, fleet["parent_headers"], sid) == "on-bus"
        assert driver_status(client, driver_headers, sid) == "on-bus"
    finally:
        client.post("/api/runs/driver/end", json={"run_id": run_id}, headers=driver_headers)
        purge_run(run_id)


def test_a_completed_morning_run_leaves_boarded_children_at_school(
    client, admin_headers, fleet, driver_headers
):
    """R2: at-school is earned by a recorded boarding, not asserted by a sweep."""
    started = client.post("/api/runs/driver/start",
                          json={"route_id": fleet["morning"]["id"]}, headers=driver_headers)
    run_id = started.json()["id"]
    try:
        client.post("/api/runs/driver/arrive", json={"run_id": run_id}, headers=driver_headers)
        client.post("/api/runs/driver/arrive", json={"run_id": run_id}, headers=driver_headers)
        client.post("/api/runs/driver/boarding",
                    json={"student_id": fleet["student"]["id"], "on_bus": True},
                    headers=driver_headers)
        client.post("/api/runs/driver/end", json={"run_id": run_id}, headers=driver_headers)
        assert admin_status(client, admin_headers, fleet["student"]["id"]) == "at-school"
    finally:
        purge_run(run_id)


def test_a_presumed_afternoon_board_is_not_rendered_as_confirmed(
    client, admin_headers, fleet, driver_headers
):
    """R6: the afternoon auto-board is a declared presumption.

    A parent who collected their child from school must not be told the child is
    on the bus on the strength of a run starting.
    """
    started = client.post("/api/runs/driver/start",
                          json={"route_id": fleet["afternoon"]["id"]}, headers=driver_headers)
    run_id = started.json()["id"]
    try:
        sid = fleet["student"]["id"]
        assert admin_status(client, admin_headers, sid) == "expected-on-bus"
        assert parent_status(client, fleet["parent_headers"], sid) == "expected-on-bus"
    finally:
        client.post("/api/runs/driver/end", json={"run_id": run_id}, headers=driver_headers)
        purge_run(run_id)


def test_an_unaccounted_child_never_decays_to_at_home(
    client, admin_headers, fleet, driver_headers
):
    """R2/R4: the office force-close says plainly that nobody knows where the
    child is. The old derivation would have flipped them to 'at home' the moment
    the run completed — asserting they were safely home, which is the exact
    manufactured claim the force-close exists to avoid.
    """
    started = client.post("/api/runs/driver/start",
                          json={"route_id": fleet["afternoon"]["id"]}, headers=driver_headers)
    run_id = started.json()["id"]
    try:
        sid = fleet["student"]["id"]
        mark_unaccounted(run_id, sid)
        assert admin_status(client, admin_headers, sid) == "unaccounted"

        client.post("/api/runs/driver/end", json={"run_id": run_id}, headers=driver_headers)
        assert admin_status(client, admin_headers, sid) == "unaccounted", (
            "completing the run decayed an unaccounted child to at-home"
        )
        assert parent_status(client, fleet["parent_headers"], sid) == "unaccounted"
    finally:
        purge_run(run_id)


def test_the_driver_reads_the_same_value_as_admin_and_parent(
    client, admin_headers, fleet, driver_headers
):
    """R2: the driver phone used to project the raw column, so a stale child
    looked different there from every other surface."""
    started = client.post("/api/runs/driver/start",
                          json={"route_id": fleet["afternoon"]["id"]}, headers=driver_headers)
    run_id = started.json()["id"]
    try:
        sid = fleet["student"]["id"]
        assert (
            driver_status(client, driver_headers, sid)
            == admin_status(client, admin_headers, sid)
            == parent_status(client, fleet["parent_headers"], sid)
        )
    finally:
        client.post("/api/runs/driver/end", json={"run_id": run_id}, headers=driver_headers)
        purge_run(run_id)


def test_a_presumed_child_can_still_have_their_ride_cancelled(
    client, admin_headers, fleet, driver_headers
):
    """R6: the Cancel-a-Ride guard keys on a confirmed boarding.

    Keyed on the presumption, a parent who collected their child from school
    would be locked out of cancelling by an assertion the app says it is not
    making.
    """
    started = client.post("/api/runs/driver/start",
                          json={"route_id": fleet["afternoon"]["id"]}, headers=driver_headers)
    run_id = started.json()["id"]
    try:
        cancelled = client.post(
            "/api/parent-portal/cancel-ride",
            json={"student_id": fleet["student"]["id"], "scope": "afternoon"},
            headers=fleet["parent_headers"],
        )
        assert cancelled.status_code == 200, cancelled.text
    finally:
        client.request("DELETE", "/api/parent-portal/cancel-ride",
                       json={"student_id": fleet["student"]["id"], "scope": "afternoon"},
                       headers=fleet["parent_headers"])
        client.post("/api/runs/driver/end", json={"run_id": run_id}, headers=driver_headers)
        purge_run(run_id)
