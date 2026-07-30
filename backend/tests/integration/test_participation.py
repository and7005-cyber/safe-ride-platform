"""Per-run participation record (plan 2026-07-28-001, U2/R1).

Boarding existed only in live_students.status — one mutable column with no
history — so a run could not reconstruct who was on it. That is why ending a run
had to sweep, why the run-end path snapshotted boarded children into memory just
to address notifications, and why a read-time staleness derivation exists.

Participation is the record of what happened on a run: who boarded and when,
whether the driver observed it or the app presumed it, whether a drop-off was
confirmed, and who acted.

The status column is still written alongside during the transition; these tests
assert the record, not its removal.

Run with the stack up: RUN_INTEGRATION=1 pytest tests/integration/test_participation.py -q
"""

import os
import random
import uuid

import httpx
import psycopg
import pytest

from conftest import purge_run

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


def participation(run_id: str) -> dict[str, dict]:
    """Participation rows for a run, keyed by student name — there is no HTTP
    surface for them yet, so read the record directly."""
    with psycopg.connect(DSN, autocommit=True, row_factory=psycopg.rows.dict_row) as pg:
        rows = pg.execute(
            "select * from run_participation where run_id = %s", (run_id,)
        ).fetchall()
    return {r["student_name"]: r for r in rows}


def run_row(client, admin_headers, run_id: str) -> dict:
    return next(r for r in client.get("/api/runs", headers=admin_headers).json()
                if r["id"] == run_id)


@pytest.fixture(scope="module")
def fleet(client, admin_headers):
    """Bus + driver + school + morning and afternoon routes with two children."""
    marker = uuid.uuid4().hex[:6]
    created: dict = {"marker": marker}
    pin = str(random.randint(100000, 999999))

    created["driver"] = client.post(
        "/api/accounts/drivers",
        json={"full_name": f"IT PT Driver {marker}", "email": f"it-pt-drv-{marker}@test.local",
              "password": "test1234.", "phone": f"+2547{random.randint(10000000, 99999999)}",
              "pin": pin},
        headers=admin_headers,
    ).json()
    created["pin"] = pin

    created["bus"] = client.post(
        "/api/fleet/buses",
        json={"name": f"IT PT Bus {marker}", "capacity": 20,
              "driver_id": created["driver"]["id"]},
        headers=admin_headers,
    ).json()

    created["school"] = client.post(
        "/api/fleet/schools",
        json={"name": f"IT PT School {marker}", "lat": -1.29, "lng": 36.82},
        headers=admin_headers,
    ).json()

    for period in ("morning", "afternoon"):
        created[period] = client.post(
            "/api/fleet/routes",
            json={"name": f"IT PT {period} {marker}", "type": period,
                  "bus_id": created["bus"]["id"], "school_id": created["school"]["id"]},
            headers=admin_headers,
        ).json()

    created["students"] = []
    for n, (lat, lng) in enumerate([(-1.30, 36.79), (-1.31, 36.78)], start=1):
        student = client.post(
            "/api/students",
            json={"name": f"IT PT Kid{n} {marker}", "parent_name": f"IT PT Parent{n}",
                  "parent_phone": f"+2547{random.randint(10000000, 99999999)}",
                  "parent_email": f"it-pt-p{n}-{marker}@test.local",
                  "school_id": created["school"]["id"], "home_lat": lat, "home_lng": lng,
                  "pickup_time": "06:30",
                  "route_ids": [created["morning"]["id"], created["afternoon"]["id"]]},
            headers=admin_headers,
        ).json()
        created["students"].append(student)

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


@pytest.fixture
def driver_headers(client, fleet):
    token = client.post("/api/auth/pin-login", json={"pin": fleet["pin"]}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def morning_run(client, admin_headers, fleet, driver_headers):
    started = client.post("/api/runs/driver/start",
                          json={"route_id": fleet["morning"]["id"]}, headers=driver_headers)
    assert started.status_code == 200, started.text
    run_id = started.json()["id"]
    yield run_id
    client.post("/api/runs/driver/end", json={"run_id": run_id}, headers=driver_headers)
    purge_run(run_id)


@pytest.fixture
def afternoon_run(client, admin_headers, fleet, driver_headers):
    started = client.post("/api/runs/driver/start",
                          json={"route_id": fleet["afternoon"]["id"]}, headers=driver_headers)
    assert started.status_code == 200, started.text
    run_id = started.json()["id"]
    yield run_id
    client.post("/api/runs/driver/end", json={"run_id": run_id}, headers=driver_headers)
    purge_run(run_id)


def test_boarding_records_who_when_and_that_it_was_observed(
    client, admin_headers, fleet, driver_headers, morning_run
):
    """R1: a boarding is recorded against this run, with its actor and time.

    Before, boarding wrote a global status column with no actor, no timestamp
    and no run reference — the reason a driver's own action could not be
    identified later.
    """
    kid = fleet["students"][0]
    client.post("/api/runs/driver/arrive", json={"run_id": morning_run}, headers=driver_headers)
    client.post("/api/runs/driver/arrive", json={"run_id": morning_run}, headers=driver_headers)
    boarded = client.post("/api/runs/driver/boarding",
                          json={"student_id": kid["id"], "on_bus": True}, headers=driver_headers)
    assert boarded.status_code == 200, boarded.text

    rows = participation(morning_run)
    row = rows[kid["name"]]
    assert row["boarded_at"] is not None
    assert row["boarded_presumed"] is False, "a driver tap is an observation, not a presumption"
    assert str(row["acting_driver_id"]) == str(fleet["driver"]["id"])
    assert row["dropped_off_at"] is None


def test_repeat_boarding_taps_leave_one_row_and_a_stable_count(
    client, admin_headers, fleet, driver_headers, morning_run
):
    """R1: mobile retry is the documented threat model — repeated taps on a
    flaky connection must not create rows or drift the counter."""
    kid = fleet["students"][0]
    client.post("/api/runs/driver/arrive", json={"run_id": morning_run}, headers=driver_headers)
    client.post("/api/runs/driver/arrive", json={"run_id": morning_run}, headers=driver_headers)
    for _ in range(3):
        client.post("/api/runs/driver/boarding",
                    json={"student_id": kid["id"], "on_bus": True}, headers=driver_headers)

    rows = participation(morning_run)
    assert len([r for r in rows.values() if r["boarded_at"]]) == 1
    assert run_row(client, admin_headers, morning_run)["students_boarded"] == 1


def test_afternoon_start_presumes_the_roster_aboard(
    client, admin_headers, fleet, driver_headers, afternoon_run
):
    """R1: the auto-board is recorded as a presumption, not an observation.

    That distinction is what stops the app asserting a boarding nobody made —
    the manufactured claim this work exists to remove.
    """
    rows = participation(afternoon_run)
    assert len(rows) == len(fleet["students"]), "the whole roster should be presumed aboard"
    for row in rows.values():
        assert row["boarded_at"] is not None
        assert row["boarded_presumed"] is True
        assert row["dropped_off_at"] is None


def test_confirming_a_dropoff_succeeds_for_a_presumed_child(
    client, admin_headers, fleet, driver_headers, afternoon_run
):
    """R1: the drop-off precondition reads participation, not the status column.

    Keyed on status, every afternoon confirmation would refuse the moment
    boarding stopped writing it, and no afternoon run could pass the closure
    gate.
    """
    kid = fleet["students"][0]
    total = len(client.get("/api/runs/driver/context", headers=driver_headers).json()["run_stops"])
    for _ in range(total):
        client.post("/api/runs/driver/arrive", json={"run_id": afternoon_run}, headers=driver_headers)

    confirmed = client.post("/api/runs/driver/dropoff",
                            json={"student_id": kid["id"]}, headers=driver_headers)
    assert confirmed.status_code == 200, confirmed.text

    rows = participation(afternoon_run)
    assert rows[kid["name"]]["dropped_off_at"] is not None
    assert rows[kid["name"]]["boarded_presumed"] is False, "confirming settles the presumption"
    other = fleet["students"][1]
    assert rows[other["name"]]["dropped_off_at"] is None, "other children were touched"


def test_confirming_the_same_dropoff_twice_is_refused(
    client, admin_headers, fleet, driver_headers, afternoon_run
):
    """R1: the record is the authority — a second confirmation is not a fact."""
    kid = fleet["students"][0]
    total = len(client.get("/api/runs/driver/context", headers=driver_headers).json()["run_stops"])
    for _ in range(total):
        client.post("/api/runs/driver/arrive", json={"run_id": afternoon_run}, headers=driver_headers)
    client.post("/api/runs/driver/dropoff", json={"student_id": kid["id"]}, headers=driver_headers)
    again = client.post("/api/runs/driver/dropoff",
                        json={"student_id": kid["id"]}, headers=driver_headers)
    assert again.status_code == 409, again.text


def test_absence_retracts_the_presumed_board(
    client, admin_headers, fleet, driver_headers, afternoon_run
):
    """R1: a child marked absent was not aboard, so their participation goes.

    On an afternoon run this retracts the presumption the auto-board wrote —
    the correction the presumption exists to allow.
    """
    kid = fleet["students"][0]
    assert participation(afternoon_run)[kid["name"]]["boarded_at"] is not None

    marked = client.post("/api/runs/driver/absent",
                         json={"student_id": kid["id"]}, headers=driver_headers)
    assert marked.status_code == 200, marked.text

    rows = participation(afternoon_run)
    assert kid["name"] not in rows, "an absent child still had a boarding recorded"
    assert fleet["students"][1]["name"] in rows


def test_counters_read_participation_not_the_status_column(
    client, admin_headers, fleet, driver_headers, morning_run
):
    """R1: students_boarded is recomputed from participation.

    Counting the status column would freeze the counter once boarding stops
    writing it.
    """
    client.post("/api/runs/driver/arrive", json={"run_id": morning_run}, headers=driver_headers)
    client.post("/api/runs/driver/arrive", json={"run_id": morning_run}, headers=driver_headers)
    for kid in fleet["students"]:
        client.post("/api/runs/driver/boarding",
                    json={"student_id": kid["id"], "on_bus": True}, headers=driver_headers)

    rows = participation(morning_run)
    recorded = len([r for r in rows.values() if r["boarded_at"]])
    assert run_row(client, admin_headers, morning_run)["students_boarded"] == recorded


def test_arrival_recipients_exclude_a_presumed_board(
    client, admin_headers, fleet, driver_headers, afternoon_run
):
    """R1: the arrival notification keys on confirmed boardings only.

    A presumed board is not evidence a child rode, and asserting arrival for one
    would be a false safety claim — the defect class this work removes.
    """
    with psycopg.connect(DSN, autocommit=True, row_factory=psycopg.rows.dict_row) as pg:
        confirmed = pg.execute(
            "select count(*) as n from run_participation "
            "where run_id = %s and boarded_at is not null and boarded_presumed = false",
            (afternoon_run,),
        ).fetchone()
    assert confirmed["n"] == 0, "presumed boards must not count as confirmed"
