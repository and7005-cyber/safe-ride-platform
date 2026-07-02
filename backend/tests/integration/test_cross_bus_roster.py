"""Cross-bus roster regression (review findings on run-scoped rosters).

A student whose morning route rides bus A and afternoon route rides bus B has
a derived live_students.bus_id of A (morning-preferring rule) while belonging
to B's afternoon run roster. Every roster surface must be run-scoped:

- driver context during B's active run lists the student (actionable UI);
- total_students counts the run's roster, not A's bus roster;
- clearing the student's today-absence is blocked while B's run is active.

Run with the stack up: RUN_INTEGRATION=1 pytest tests/integration/test_cross_bus_roster.py -q
"""

import os
import random
import uuid

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="needs the local stack; set RUN_INTEGRATION=1",
)

BASE = os.environ.get("INTEGRATION_API_URL", "http://localhost:9001")
ADMIN = {"email": "admin@test.com", "password": "test1234."}


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE, timeout=20) as c:
        yield c


def login(client: httpx.Client, email: str, password: str) -> dict:
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def pin_login(client: httpx.Client, pin: str) -> dict:
    response = client.post("/api/auth/pin-login", json={"pin": pin})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


@pytest.fixture(scope="module")
def admin_headers(client):
    return login(client, ADMIN["email"], ADMIN["password"])


def _create_driver(client, admin_headers, marker: str, n: int) -> dict:
    for _ in range(5):
        pin = str(random.randint(100000, 999999))
        response = client.post(
            "/api/accounts/drivers",
            json={"full_name": f"IT XB Driver{n} {marker}",
                  "email": f"it-xb-driver{n}-{marker}@test.local",
                  "password": "test1234.", "phone": f"+2547110001{n}0", "pin": pin},
            headers=admin_headers,
        )
        if response.status_code == 200:
            return {**response.json(), "pin": pin}
    pytest.fail(f"could not create throwaway driver: {response.text}")


def test_cross_bus_afternoon_roster_is_run_scoped(client, admin_headers):
    """The full chain: derived bus A, afternoon run on bus B — context roster,
    total_students, and the absence-clear guard all follow the RUN."""
    marker = uuid.uuid4().hex[:6]
    created: dict[str, list] = {"buses": [], "schools": [], "students": [], "drivers": []}
    run_id = None
    driver_b_headers = None
    try:
        driver_a = _create_driver(client, admin_headers, marker, 1)
        driver_b = _create_driver(client, admin_headers, marker, 2)
        created["drivers"] = [driver_a["id"], driver_b["id"]]
        bus_a = client.post(
            "/api/fleet/buses",
            json={"name": f"IT XB BusA {marker}", "driver_id": driver_a["id"]},
            headers=admin_headers,
        ).json()
        bus_b = client.post(
            "/api/fleet/buses",
            json={"name": f"IT XB BusB {marker}", "driver_id": driver_b["id"]},
            headers=admin_headers,
        ).json()
        created["buses"] = [bus_a["id"], bus_b["id"]]
        school = client.post(
            "/api/fleet/schools",
            json={"name": f"IT XB School {marker}", "lat": -1.30, "lng": 36.80},
            headers=admin_headers,
        ).json()
        created["schools"] = [school["id"]]
        morning = client.post(
            "/api/fleet/routes",
            json={"name": f"IT XB Morning {marker}", "type": "morning",
                  "bus_id": bus_a["id"], "school_id": school["id"]},
            headers=admin_headers,
        ).json()
        afternoon = client.post(
            "/api/fleet/routes",
            json={"name": f"IT XB Afternoon {marker}", "type": "afternoon",
                  "bus_id": bus_b["id"], "school_id": school["id"]},
            headers=admin_headers,
        ).json()
        student = client.post(
            "/api/students",
            json={"name": f"IT XB Kid {marker}", "parent_name": "IT XB Parent",
                  "parent_phone": "+254711000199",
                  "parent_email": f"it-xb-{marker}@test.local",
                  "home_lat": -1.28, "home_lng": 36.79, "pickup_time": "06:30",
                  "route_ids": [morning["id"], afternoon["id"]]},
            headers=admin_headers,
        ).json()
        created["students"] = [student["id"]]

        # Derived bus is the MORNING bus — the divergence under test.
        roster = client.get("/api/students", headers=admin_headers).json()
        row = next(s for s in roster if s["id"] == student["id"])
        assert row["bus_id"] == bus_a["id"]

        # Bus B's driver starts the afternoon run.
        driver_b_headers = pin_login(client, driver_b["pin"])
        started = client.post(
            "/api/runs/driver/start", json={"route_id": afternoon["id"]},
            headers=driver_b_headers,
        )
        assert started.status_code == 200, started.text
        run = started.json()
        run_id = run["id"]

        # total_students counts the run's roster (1), not bus B's derived
        # roster (0) nor bus A's.
        assert run["total_students"] == 1

        # Driver context lists the cross-bus student, auto-boarded.
        context = client.get("/api/runs/driver/context", headers=driver_b_headers).json()
        ctx_students = {s["id"]: s for s in context["students"]}
        assert student["id"] in ctx_students, "cross-bus roster student missing from driver context"
        assert ctx_students[student["id"]]["status"] == "on-bus"

        # Driver marks them absent mid-run; clearing the absence while the
        # run is active must be rejected (run-scoped guard, not bus-scoped).
        marked = client.post(
            "/api/runs/driver/absent", json={"student_id": student["id"]},
            headers=driver_b_headers,
        )
        assert marked.status_code == 200, marked.text
        absences = client.get("/api/students/absences", headers=admin_headers).json()
        absence = next(a for a in absences if a["student_id"] == student["id"])
        cleared = client.delete(
            f"/api/students/absences/{absence['id']}", headers=admin_headers
        )
        assert cleared.status_code == 409, cleared.text
        assert "End the run first" in cleared.json()["detail"]

        # After the run ends, the clear succeeds and resets the status.
        ended = client.post(
            "/api/runs/driver/end", json={"run_id": run_id}, headers=driver_b_headers
        )
        assert ended.status_code == 200, ended.text
        cleared = client.delete(
            f"/api/students/absences/{absence['id']}", headers=admin_headers
        )
        assert cleared.status_code == 200, cleared.text
    finally:
        if run_id:
            if driver_b_headers:
                client.post("/api/runs/driver/end", json={"run_id": run_id},
                            headers=driver_b_headers)
            client.delete(f"/api/runs/{run_id}", headers=admin_headers)
        for sid in created["students"]:
            client.delete(f"/api/students/{sid}", headers=admin_headers)
        for bid in created["buses"]:
            client.delete(f"/api/fleet/buses/{bid}", headers=admin_headers)
        for scid in created["schools"]:
            client.delete(f"/api/fleet/schools/{scid}", headers=admin_headers)
        for did in created["drivers"]:
            client.delete(f"/api/accounts/drivers/{did}", headers=admin_headers)
