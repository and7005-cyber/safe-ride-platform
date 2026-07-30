"""Driver same-run correction (plan 2026-07-28-001, U5/R10).

The closure gate makes a mis-tap consequential. A drop-off confirmed on the
wrong child sends that family a false assurance; an absence marked in error both
misrecords the child and blocks their real drop-off from ever being confirmed,
because the drop-off path requires them to be aboard. Neither was recoverable.

Reversal is scoped to this driver's account, this run, still open — and always
notifies the affected parents, because retracting a statement silently would be
worse than the mis-tap.

Run with the stack up: RUN_INTEGRATION=1 pytest tests/integration/test_driver_correction.py -q
"""

import os
import random
import uuid

import httpx
import psycopg
import pytest

from conftest import purge_run

# Parent accounts come from signup; naming an email on a student only links a
# row, and without a linked account no notification is ever produced.
from test_students_parents import signup_parent

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
def admin_headers(client):
    response = client.post("/api/auth/login", json=ADMIN)
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def participation(run_id: str, name: str) -> dict | None:
    with psycopg.connect(DSN, autocommit=True, row_factory=psycopg.rows.dict_row) as pg:
        return pg.execute(
            "select * from run_participation where run_id = %s and student_name = %s",
            (run_id, name),
        ).fetchone()


def notifications_for(student_id: str, run_id: str) -> list[str]:
    with psycopg.connect(DSN, autocommit=True, row_factory=psycopg.rows.dict_row) as pg:
        rows = pg.execute(
            "select type from live_notifications where student_id = %s and run_id = %s",
            (student_id, run_id),
        ).fetchall()
    return [r["type"] for r in rows]


def absence_today(student_id: str) -> dict | None:
    with psycopg.connect(DSN, autocommit=True, row_factory=psycopg.rows.dict_row) as pg:
        return pg.execute(
            "select * from live_student_absences where student_id = %s "
            "and absence_date = (now() at time zone 'Africa/Nairobi')::date",
            (student_id,),
        ).fetchone()


@pytest.fixture(scope="module")
def fleet(client, admin_headers):
    marker = uuid.uuid4().hex[:6]
    created: dict = {"marker": marker}
    pin = str(random.randint(100000, 999999))

    created["driver"] = client.post(
        "/api/accounts/drivers",
        json={"full_name": f"IT RV Driver {marker}", "email": f"it-rv-drv-{marker}@test.local",
              "password": "test1234.", "phone": f"+2547{random.randint(10000000, 99999999)}",
              "pin": pin},
        headers=admin_headers,
    ).json()
    created["pin"] = pin

    created["bus"] = client.post(
        "/api/fleet/buses",
        json={"name": f"IT RV Bus {marker}", "capacity": 20,
              "driver_id": created["driver"]["id"]},
        headers=admin_headers,
    ).json()

    created["school"] = client.post(
        "/api/fleet/schools",
        json={"name": f"IT RV School {marker}", "lat": -1.29, "lng": 36.82},
        headers=admin_headers,
    ).json()

    for period in ("morning", "afternoon"):
        created[period] = client.post(
            "/api/fleet/routes",
            json={"name": f"IT RV {period} {marker}", "type": period,
                  "bus_id": created["bus"]["id"], "school_id": created["school"]["id"]},
            headers=admin_headers,
        ).json()

    created["students"] = []
    created["parent_ids"] = []
    for n, (lat, lng) in enumerate([(-1.30, 36.79), (-1.31, 36.78)], start=1):
        parent_id, parent_email, _ = signup_parent(client, marker, f"rv-p{n}")
        created["parent_ids"].append(parent_id)
        created["students"].append(client.post(
            "/api/students",
            json={"name": f"IT RV Kid{n} {marker}", "parent_name": f"IT RV Parent{n}",
                  "parent_phone": f"+2547{random.randint(10000000, 99999999)}",
                  "parent_email": parent_email,
                  "school_id": created["school"]["id"], "home_lat": lat, "home_lng": lng,
                  "pickup_time": "06:30",
                  "route_ids": [created["morning"]["id"], created["afternoon"]["id"]]},
            headers=admin_headers,
        ).json())

    try:
        yield created
    finally:
        for run in client.get("/api/runs", headers=admin_headers).json():
            if run.get("bus_id") == created["bus"]["id"]:
                purge_run(run['id'])
        for s in created["students"]:
            client.delete(f"/api/students/{s['id']}", headers=admin_headers)
        for period in ("morning", "afternoon"):
            client.delete(f"/api/fleet/routes/{created[period]['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/buses/{created['bus']['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/schools/{created['school']['id']}", headers=admin_headers)
        client.delete(f"/api/accounts/drivers/{created['driver']['id']}", headers=admin_headers)
        for parent_id in created["parent_ids"]:
            client.delete(f"/api/accounts/parents/{parent_id}", headers=admin_headers)


@pytest.fixture
def driver_headers(client, fleet):
    token = client.post("/api/auth/pin-login", json={"pin": fleet["pin"]}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def afternoon_run(client, admin_headers, fleet, driver_headers):
    """An afternoon run with every stop arrived, so drop-offs are confirmable."""
    started = client.post("/api/runs/driver/start",
                          json={"route_id": fleet["afternoon"]["id"]}, headers=driver_headers)
    assert started.status_code == 200, started.text
    run_id = started.json()["id"]
    context = client.get("/api/runs/driver/context", headers=driver_headers).json()
    for _ in range(len(context["run_stops"])):
        client.post("/api/runs/driver/arrive", json={"run_id": run_id}, headers=driver_headers)
    yield run_id
    purge_run(run_id)


def test_reversing_a_dropoff_puts_the_child_back_in_the_blocking_set(
    client, admin_headers, fleet, driver_headers, afternoon_run
):
    """R10: the wrong child was confirmed. Undoing it returns them to the set
    the closure gate refuses to close over — which is the point: their real
    outcome has not been recorded yet."""
    kid = fleet["students"][0]
    client.post("/api/runs/driver/dropoff", json={"student_id": kid["id"]},
                headers=driver_headers)
    assert participation(afternoon_run, kid["name"])["dropped_off_at"] is not None

    reversed_ = client.post("/api/runs/driver/reverse", json={"student_id": kid["id"]},
                            headers=driver_headers)
    assert reversed_.status_code == 200, reversed_.text

    row = participation(afternoon_run, kid["name"])
    assert row["dropped_off_at"] is None, "the drop-off survived the reversal"
    assert row["boarded_at"] is not None, "the reversal also erased the boarding"

    refused = client.post("/api/runs/driver/end", json={"run_id": afternoon_run},
                          headers=driver_headers)
    assert refused.status_code == 409, refused.text
    assert kid["name"] in refused.json()["detail"]


def test_the_parents_are_told_and_the_false_message_is_retracted(
    client, admin_headers, fleet, driver_headers, afternoon_run
):
    """R10: retracting a statement silently would be worse than the mis-tap.

    The superseded message is removed too — the dedup index is unique on
    (user, run, student, type), so leaving it would suppress the driver's
    genuine second confirmation and the family would keep only the false one.
    """
    kid = fleet["students"][0]
    client.post("/api/runs/driver/dropoff", json={"student_id": kid["id"]},
                headers=driver_headers)
    assert "dropped-off" in notifications_for(kid["id"], afternoon_run)

    client.post("/api/runs/driver/reverse", json={"student_id": kid["id"]},
                headers=driver_headers)

    types = notifications_for(kid["id"], afternoon_run)
    assert "dropoff-corrected" in types, "the parents were not told"
    assert "dropped-off" not in types, "the retracted claim is still in the feed"


def test_the_real_dropoff_still_reaches_the_parents_afterwards(
    client, admin_headers, fleet, driver_headers, afternoon_run
):
    """R10: the failure this guards against — a correction that leaves the
    family unable to receive the true message, because the dedup index treats
    it as a repeat of the one that was retracted."""
    kid = fleet["students"][0]
    client.post("/api/runs/driver/dropoff", json={"student_id": kid["id"]},
                headers=driver_headers)
    client.post("/api/runs/driver/reverse", json={"student_id": kid["id"]},
                headers=driver_headers)

    again = client.post("/api/runs/driver/dropoff", json={"student_id": kid["id"]},
                        headers=driver_headers)
    assert again.status_code == 200, again.text
    assert "dropped-off" in notifications_for(kid["id"], afternoon_run), (
        "the genuine drop-off notification was suppressed as a duplicate"
    )


def test_reversing_an_absence_restores_the_child_to_the_run(
    client, admin_headers, fleet, driver_headers, afternoon_run
):
    """R10: an absence marked in error is the worse case — it also blocks the
    child's real drop-off, because that path requires them to be aboard."""
    kid = fleet["students"][1]
    client.post("/api/runs/driver/absent", json={"student_id": kid["id"]},
                headers=driver_headers)
    assert absence_today(kid["id"]) is not None
    blocked = client.post("/api/runs/driver/dropoff", json={"student_id": kid["id"]},
                          headers=driver_headers)
    assert blocked.status_code == 409, "an absent child should not be droppable"

    reversed_ = client.post("/api/runs/driver/reverse", json={"student_id": kid["id"]},
                            headers=driver_headers)
    assert reversed_.status_code == 200, reversed_.text
    assert absence_today(kid["id"]) is None, "the absence row survived"
    assert "absence-corrected" in notifications_for(kid["id"], afternoon_run)

    now_droppable = client.post("/api/runs/driver/dropoff", json={"student_id": kid["id"]},
                                headers=driver_headers)
    assert now_droppable.status_code == 200, now_droppable.text


def test_reversal_is_refused_once_the_run_is_closed(
    client, admin_headers, fleet, driver_headers
):
    """R10: scoped to an open run. Correcting a closed run stays an office
    phone call — reversal is for a mis-tap, not an audit trail."""
    started = client.post("/api/runs/driver/start",
                          json={"route_id": fleet["afternoon"]["id"]}, headers=driver_headers)
    run_id = started.json()["id"]
    try:
        context = client.get("/api/runs/driver/context", headers=driver_headers).json()
        for _ in range(len(context["run_stops"])):
            client.post("/api/runs/driver/arrive", json={"run_id": run_id},
                        headers=driver_headers)
        for kid in fleet["students"]:
            client.post("/api/runs/driver/dropoff", json={"student_id": kid["id"]},
                        headers=driver_headers)
        ended = client.post("/api/runs/driver/end", json={"run_id": run_id},
                            headers=driver_headers)
        assert ended.status_code == 200, ended.text

        too_late = client.post("/api/runs/driver/reverse",
                               json={"student_id": fleet["students"][0]["id"]},
                               headers=driver_headers)
        assert too_late.status_code == 403, too_late.text
    finally:
        purge_run(run_id)


def test_there_is_nothing_to_undo_for_an_untouched_child(
    client, admin_headers, fleet, driver_headers, afternoon_run
):
    """R10: a reversal is not a way to un-board. The child is aboard and
    nothing has been recorded about them, so there is no statement to retract."""
    response = client.post("/api/runs/driver/reverse",
                           json={"student_id": fleet["students"][0]["id"]},
                           headers=driver_headers)
    assert response.status_code == 409, response.text
    assert "undo" in response.json()["detail"].lower()


def test_un_boarding_is_still_disabled_on_the_boarding_endpoint(
    client, admin_headers, fleet, driver_headers, afternoon_run
):
    """R10 guardrail: reversal is a separate path on purpose.

    The boarding toggle's rejection of on_bus=false is a stale-client
    concurrency guard with a justification independent of the finality
    argument, so it must not have been relaxed on the way in.
    """
    response = client.post("/api/runs/driver/boarding",
                           json={"student_id": fleet["students"][0]["id"], "on_bus": False},
                           headers=driver_headers)
    assert response.status_code == 409, response.text
