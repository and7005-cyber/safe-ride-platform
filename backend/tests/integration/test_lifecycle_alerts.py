"""Office run-lifecycle alerts (plan 2026-07-28-001, U16/R29-R30).

The office has no run lifecycle in its alert feed at all today: completion shows
only on the Runs page, which nobody watches during a route. In the morning the
school-gate arrival alert happens to double as a finished signal; in the
afternoon there is no equivalent, so a route ends and the office learns nothing.

These alerts share live_incidents with real incidents, so they carry a lifecycle
marker that keeps them out of three places:
- the parent alerts feed (which filtered only child-stamped rows, and so has
  been showing parents the 'arrival' rows all along)
- the parent push fan-out
- the incident counters behind the Dashboard tile and the acknowledgement badge

Run with the stack up: RUN_INTEGRATION=1 pytest tests/integration/test_lifecycle_alerts.py -q
"""

import os
import random
import uuid

import httpx
import pytest

# Cross-module helper reuse, as the other integration suites do — parent
# accounts are created through signup, not by naming an email on a student.
from test_students_parents import complete_run, signup_parent

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


@pytest.fixture(scope="module")
def admin_headers(client):
    response = client.post("/api/auth/login", json=ADMIN)
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


@pytest.fixture(scope="module")
def fleet(client, admin_headers):
    """Throwaway bus + driver + school + morning route with one linked parent."""
    marker = uuid.uuid4().hex[:6]
    created: dict = {"marker": marker}
    pin = str(random.randint(100000, 999999))

    response = client.post(
        "/api/accounts/drivers",
        json={"full_name": f"IT LC Driver {marker}",
              "email": f"it-lc-driver-{marker}@test.local", "password": "test1234.",
              "phone": f"+2547{random.randint(10000000, 99999999)}", "pin": pin},
        headers=admin_headers,
    )
    assert response.status_code in (200, 201), response.text
    created["driver"] = response.json()
    created["pin"] = pin

    response = client.post(
        "/api/fleet/buses",
        json={"name": f"IT LC Bus {marker}", "capacity": 20,
              "driver_id": created["driver"]["id"]},
        headers=admin_headers,
    )
    assert response.status_code in (200, 201), response.text
    created["bus"] = response.json()

    response = client.post(
        "/api/fleet/schools",
        json={"name": f"IT LC School {marker}", "lat": -1.29, "lng": 36.82},
        headers=admin_headers,
    )
    assert response.status_code in (200, 201), response.text
    created["school"] = response.json()

    response = client.post(
        "/api/fleet/routes",
        json={"name": f"IT LC Route {marker}", "type": "morning",
              "bus_id": created["bus"]["id"], "school_id": created["school"]["id"]},
        headers=admin_headers,
    )
    assert response.status_code in (200, 201), response.text
    created["route"] = response.json()

    parent_id, parent_email, parent_headers = signup_parent(client, marker, "lc-parent")
    created["parent_id"] = parent_id
    created["parent_headers"] = parent_headers

    response = client.post(
        "/api/students",
        json={"name": f"IT LC Kid {marker}", "parent_name": "IT LC Parent",
              "parent_phone": f"+2547{random.randint(10000000, 99999999)}",
              "parent_email": parent_email, "school_id": created["school"]["id"],
              "home_lat": -1.30, "home_lng": 36.79, "pickup_time": "06:30",
              "route_ids": [created["route"]["id"]]},
        headers=admin_headers,
    )
    assert response.status_code in (200, 201), response.text
    created["student"] = response.json()

    try:
        yield created
    finally:
        for run in client.get("/api/runs", headers=admin_headers).json():
            if run.get("bus_id") == created["bus"]["id"]:
                client.delete(f"/api/runs/{run['id']}", headers=admin_headers)
        client.delete(f"/api/students/{created['student']['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/routes/{created['route']['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/buses/{created['bus']['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/schools/{created['school']['id']}", headers=admin_headers)
        client.delete(f"/api/accounts/drivers/{created['driver']['id']}", headers=admin_headers)
        client.delete(f"/api/accounts/parents/{created['parent_id']}", headers=admin_headers)


def _driver_headers(client, fleet):
    token = client.post("/api/auth/pin-login", json={"pin": fleet["pin"]}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _alerts_for_bus(client, admin_headers, bus_id: str) -> list[dict]:
    rows = client.get("/api/incidents", headers=admin_headers).json()
    return [a for a in rows if a.get("bus_id") == bus_id]


def _wait_for(predicate, timeout: float = 10.0):
    """Background tasks run inside the request lifecycle but the feed row lands
    asynchronously enough to race a fast assertion."""
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.25)
    return predicate()


def test_run_start_and_completion_each_raise_one_office_alert(client, admin_headers, fleet):
    """R29: the office finally sees a route start and end, naming route, period
    and bus — the defect being that a route ends and the office learns nothing."""
    driver_headers = _driver_headers(client, fleet)
    started = client.post("/api/runs/driver/start",
                          json={"route_id": fleet["route"]["id"]}, headers=driver_headers)
    assert started.status_code == 200, started.text
    run_id = started.json()["id"]
    try:
        alerts = _wait_for(lambda: [a for a in _alerts_for_bus(client, admin_headers, fleet["bus"]["id"])
                                    if a["type"] == "run-started"])
        assert len(alerts) == 1, f"expected one run-started alert, got {len(alerts)}"
        assert fleet["route"]["name"] in (alerts[0]["description"] or "")
        assert fleet["bus"]["name"] in (alerts[0]["description"] or "")
        assert "morning" in (alerts[0]["description"] or "")

        complete_run(client, driver_headers, run_id)
        ended = _wait_for(lambda: [a for a in _alerts_for_bus(client, admin_headers, fleet["bus"]["id"])
                                   if a["type"] == "run-completed"])
        assert len(ended) == 1, f"expected one run-completed alert, got {len(ended)}"
        assert ended[0]["type"] != alerts[0]["type"], "start and end are indistinguishable"
    finally:
        client.delete(f"/api/runs/{run_id}", headers=admin_headers)


def test_lifecycle_alerts_never_reach_the_parent(client, admin_headers, fleet):
    """R30: office-only. Parents already get their own messages for anything
    that concerns them; these would arrive as duplicate, vaguer news."""
    driver_headers = _driver_headers(client, fleet)
    started = client.post("/api/runs/driver/start",
                          json={"route_id": fleet["route"]["id"]}, headers=driver_headers)
    assert started.status_code == 200, started.text
    run_id = started.json()["id"]
    try:
        _wait_for(lambda: [a for a in _alerts_for_bus(client, admin_headers, fleet["bus"]["id"])
                           if a["type"] == "run-started"])
        complete_run(client, driver_headers, run_id)
        _wait_for(lambda: [a for a in _alerts_for_bus(client, admin_headers, fleet["bus"]["id"])
                           if a["type"] == "run-completed"])

        feed = client.get("/api/parent-portal/alerts", headers=fleet["parent_headers"])
        assert feed.status_code == 200, feed.text
        types = {row.get("type") for row in feed.json()}
        assert "run-started" not in types
        assert "run-completed" not in types
        # The pre-existing 'arrival' rows were reaching parents all along,
        # because the only feed filter was on child-stamped rows.
        assert "arrival" not in types
    finally:
        client.delete(f"/api/runs/{run_id}", headers=admin_headers)


def test_lifecycle_alerts_do_not_move_the_incident_counters(client, admin_headers, fleet):
    """R30: roughly four of these per bus per day. Counted, they would turn the
    Dashboard's incidents tile red on an ordinary day and fill the
    acknowledgement badge with items nobody needs to acknowledge."""
    driver_headers = _driver_headers(client, fleet)
    before_today = client.get("/api/incidents/today-count", headers=admin_headers).json()["count"]
    before_unread = client.get("/api/incidents/unread-count", headers=admin_headers).json()["count"]
    before_real = len([a for a in _alerts_for_bus(client, admin_headers, fleet["bus"]["id"])
                       if a["type"] not in ("run-started", "run-completed")])

    started = client.post("/api/runs/driver/start",
                          json={"route_id": fleet["route"]["id"]}, headers=driver_headers)
    assert started.status_code == 200, started.text
    run_id = started.json()["id"]
    try:
        _wait_for(lambda: [a for a in _alerts_for_bus(client, admin_headers, fleet["bus"]["id"])
                           if a["type"] == "run-started"])
        complete_run(client, driver_headers, run_id)
        _wait_for(lambda: [a for a in _alerts_for_bus(client, admin_headers, fleet["bus"]["id"])
                           if a["type"] == "run-completed"])

        # Deltas, not absolutes: the fixture is module-scoped, so earlier tests
        # in this file have already raised lifecycle alerts on the same bus.
        rows = _alerts_for_bus(client, admin_headers, fleet["bus"]["id"])
        lifecycle = [a for a in rows if a["type"] in ("run-started", "run-completed")]
        real = [a for a in rows if a["type"] not in ("run-started", "run-completed")]
        assert len(lifecycle) >= 2, "the lifecycle alerts were not raised"

        # Driving the run to the school gate raises a genuine 'arrival' incident,
        # which the office has always counted and still should. The point is that
        # the two lifecycle rows contribute nothing on top of it.
        after_today = client.get("/api/incidents/today-count", headers=admin_headers).json()["count"]
        after_unread = client.get("/api/incidents/unread-count", headers=admin_headers).json()["count"]
        assert after_today - before_today == len(real) - before_real, (
            "lifecycle alerts moved the incidents-today tile"
        )
        assert after_unread - before_unread == len(real) - before_real, (
            "lifecycle alerts landed in the acknowledgement queue"
        )
    finally:
        client.delete(f"/api/runs/{run_id}", headers=admin_headers)
