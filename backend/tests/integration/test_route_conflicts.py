"""Route/bus conflict enforcement and the active-runs filter (U3).

Run with the stack up (scripts/start-local.sh):

    RUN_INTEGRATION=1 ../.venv/bin/python -m pytest tests/integration/test_route_conflicts.py -q

Covers R1/R2/R3/R5: one route per (bus, type) with a friendly 409 naming the
conflicting route and bus on create and edit, live_students.bus_id
re-derivation when a route's bus changes, the friendly 409 on run edits that
would double-book a bus, and the today-scoped GET /api/runs?active=true
filter. Entities are 'IT '-prefixed and cleaned up in finally blocks.
"""

import datetime as dt
import os
import uuid
from zoneinfo import ZoneInfo

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="needs the local stack; set RUN_INTEGRATION=1",
)

BASE = os.environ.get("INTEGRATION_API_URL", "http://localhost:9001")

ADMIN = {"email": "admin@test.com", "password": "test1234."}

NAIROBI = ZoneInfo("Africa/Nairobi")


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE, timeout=20) as c:
        yield c


def login(client: httpx.Client, email: str, password: str) -> dict:
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    token = response.json()["token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def admin_headers(client):
    return login(client, ADMIN["email"], ADMIN["password"])


def _create_bus(client, admin_headers, name: str) -> dict:
    created = client.post("/api/fleet/buses", json={"name": name}, headers=admin_headers)
    assert created.status_code == 200, created.text
    return created.json()


def _create_route(client, admin_headers, name: str, type_: str, bus_id: str | None) -> httpx.Response:
    return client.post(
        "/api/fleet/routes",
        json={"name": name, "type": type_, "bus_id": bus_id},
        headers=admin_headers,
    )


# Route (bus, type) uniqueness -------------------------------------------------

def test_second_same_type_route_on_bus_conflicts(client, admin_headers):
    """A bus can carry only one morning route; the 409 names both the
    conflicting route and the bus (R1)."""
    marker = uuid.uuid4().hex[:6]
    bus = _create_bus(client, admin_headers, f"IT ConflictBus {marker}")
    first = _create_route(client, admin_headers, f"IT Morning A {marker}", "morning", bus["id"])
    assert first.status_code == 200, first.text
    route = first.json()
    second_id = None
    try:
        second = _create_route(client, admin_headers, f"IT Morning B {marker}", "morning", bus["id"])
        if second.status_code == 200:  # unexpected: remember it for cleanup
            second_id = second.json()["id"]
        assert second.status_code == 409, second.text
        detail = second.json()["detail"]
        assert f"IT Morning A {marker}" in detail, detail
        assert f"IT ConflictBus {marker}" in detail, detail
    finally:
        if second_id:
            client.delete(f"/api/fleet/routes/{second_id}", headers=admin_headers)
        client.delete(f"/api/fleet/routes/{route['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/buses/{bus['id']}", headers=admin_headers)


def test_editing_route_to_taken_bus_conflicts(client, admin_headers):
    """Reassigning an existing route onto a bus that already has a route of
    the same type 409s with the friendly message (R1)."""
    marker = uuid.uuid4().hex[:6]
    bus = _create_bus(client, admin_headers, f"IT TakenBus {marker}")
    holder = _create_route(client, admin_headers, f"IT Holder {marker}", "morning", bus["id"]).json()
    floater = _create_route(client, admin_headers, f"IT Floater {marker}", "morning", None).json()
    try:
        edit = client.put(
            f"/api/fleet/routes/{floater['id']}",
            json={"name": f"IT Floater {marker}", "type": "morning", "bus_id": bus["id"]},
            headers=admin_headers,
        )
        assert edit.status_code == 409, edit.text
        detail = edit.json()["detail"]
        assert f"IT Holder {marker}" in detail, detail
        assert f"IT TakenBus {marker}" in detail, detail
    finally:
        client.delete(f"/api/fleet/routes/{floater['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/routes/{holder['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/buses/{bus['id']}", headers=admin_headers)


def test_same_route_edit_noop_succeeds(client, admin_headers):
    """Re-saving a route with its own bus unchanged must not self-conflict."""
    marker = uuid.uuid4().hex[:6]
    bus = _create_bus(client, admin_headers, f"IT NoopBus {marker}")
    route = _create_route(client, admin_headers, f"IT Noop {marker}", "morning", bus["id"]).json()
    try:
        edit = client.put(
            f"/api/fleet/routes/{route['id']}",
            json={"name": f"IT Noop {marker} v2", "type": "morning", "bus_id": bus["id"]},
            headers=admin_headers,
        )
        assert edit.status_code == 200, edit.text
        assert edit.json()["name"] == f"IT Noop {marker} v2"
    finally:
        client.delete(f"/api/fleet/routes/{route['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/buses/{bus['id']}", headers=admin_headers)


def test_afternoon_route_on_same_bus_succeeds(client, admin_headers):
    """Uniqueness is per (bus, type): morning + afternoon coexist on one bus."""
    marker = uuid.uuid4().hex[:6]
    bus = _create_bus(client, admin_headers, f"IT PairBus {marker}")
    morning = _create_route(client, admin_headers, f"IT Pair AM {marker}", "morning", bus["id"]).json()
    afternoon_id = None
    try:
        afternoon = _create_route(client, admin_headers, f"IT Pair PM {marker}", "afternoon", bus["id"])
        assert afternoon.status_code == 200, afternoon.text
        afternoon_id = afternoon.json()["id"]
    finally:
        if afternoon_id:
            client.delete(f"/api/fleet/routes/{afternoon_id}", headers=admin_headers)
        client.delete(f"/api/fleet/routes/{morning['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/buses/{bus['id']}", headers=admin_headers)


def test_route_bus_change_rederives_student_bus(client, admin_headers):
    """Reassigning a route's bus (incl. to NULL) re-derives the denormalised
    live_students.bus_id of the route's students (R2)."""
    marker = uuid.uuid4().hex[:6]
    bus_a = _create_bus(client, admin_headers, f"IT DeriveBus A {marker}")
    bus_b = _create_bus(client, admin_headers, f"IT DeriveBus B {marker}")
    route = _create_route(client, admin_headers, f"IT Derive {marker}", "morning", bus_a["id"]).json()
    student = client.post(
        "/api/students",
        json={"name": f"IT Derive Kid {marker}", "parent_name": "IT Derive Parent",
              "parent_phone": "+254711000006", "parent_email": f"it-derive-{marker}@test.local",
              "route_ids": [route["id"]]},
        headers=admin_headers,
    ).json()

    def student_bus_id():
        s = next(x for x in client.get("/api/students", headers=admin_headers).json()
                 if x["id"] == student["id"])
        return s["bus_id"]

    try:
        assert student_bus_id() == bus_a["id"]

        moved = client.put(
            f"/api/fleet/routes/{route['id']}",
            json={"name": f"IT Derive {marker}", "type": "morning", "bus_id": bus_b["id"]},
            headers=admin_headers,
        )
        assert moved.status_code == 200, moved.text
        assert student_bus_id() == bus_b["id"]

        cleared = client.put(
            f"/api/fleet/routes/{route['id']}",
            json={"name": f"IT Derive {marker}", "type": "morning", "bus_id": None},
            headers=admin_headers,
        )
        assert cleared.status_code == 200, cleared.text
        assert student_bus_id() is None
    finally:
        client.delete(f"/api/students/{student['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/routes/{route['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/buses/{bus_a['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/buses/{bus_b['id']}", headers=admin_headers)


# Run edit conflicts ------------------------------------------------------------

def test_run_edit_conflict_is_friendly_409(client, admin_headers):
    """Moving a completed run back to in-progress on a bus that already has an
    active run 409s with the friendly message, not a raw unique-violation
    'Record already exists' (R3). Editing the active run itself must not
    self-conflict."""
    marker = uuid.uuid4().hex[:6]
    bus = _create_bus(client, admin_headers, f"IT RunBus {marker}")
    active = client.post(
        "/api/runs", json={"bus_id": bus["id"], "type": "morning", "status": "in-progress"},
        headers=admin_headers,
    )
    assert active.status_code == 200, active.text
    active_run = active.json()
    completed = client.post(
        "/api/runs", json={"bus_id": bus["id"], "type": "morning", "status": "completed"},
        headers=admin_headers,
    )
    assert completed.status_code == 200, completed.text
    completed_run = completed.json()
    try:
        revived = client.put(
            f"/api/runs/{completed_run['id']}",
            json={"bus_id": bus["id"], "type": "morning", "status": "in-progress"},
            headers=admin_headers,
        )
        assert revived.status_code == 409, revived.text
        assert "already has an active run" in revived.json()["detail"]

        # Editing the active run itself is excluded from its own check.
        self_edit = client.put(
            f"/api/runs/{active_run['id']}",
            json={"bus_id": bus["id"], "type": "morning", "status": "in-progress",
                  "total_students": 5},
            headers=admin_headers,
        )
        assert self_edit.status_code == 200, self_edit.text
        assert self_edit.json()["total_students"] == 5
    finally:
        client.delete(f"/api/runs/{completed_run['id']}", headers=admin_headers)
        client.delete(f"/api/runs/{active_run['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/buses/{bus['id']}", headers=admin_headers)


# Active-runs filter -------------------------------------------------------------

def test_active_filter_excludes_completed_and_prior_date_runs(client, admin_headers):
    """GET /api/runs?active=true returns only today's (Africa/Nairobi)
    non-completed runs, still carrying the joined bus/route names (R5)."""
    marker = uuid.uuid4().hex[:6]
    bus = _create_bus(client, admin_headers, f"IT ActiveBus {marker}")
    yesterday = (dt.datetime.now(NAIROBI).date() - dt.timedelta(days=1)).isoformat()

    active_today = client.post(
        "/api/runs", json={"bus_id": bus["id"], "type": "morning", "status": "in-progress"},
        headers=admin_headers,
    ).json()
    completed_today = client.post(
        "/api/runs", json={"bus_id": bus["id"], "type": "morning", "status": "completed"},
        headers=admin_headers,
    ).json()
    stale_in_progress = client.post(
        "/api/runs",
        json={"bus_id": bus["id"], "type": "morning", "status": "in-progress", "date": yesterday},
        headers=admin_headers,
    ).json()
    try:
        response = client.get("/api/runs", params={"active": "true"}, headers=admin_headers)
        assert response.status_code == 200, response.text
        runs = response.json()
        ids = {r["id"] for r in runs}
        assert active_today["id"] in ids
        assert completed_today["id"] not in ids
        assert stale_in_progress["id"] not in ids

        entry = next(r for r in runs if r["id"] == active_today["id"])
        assert entry["bus_name"] == f"IT ActiveBus {marker}"
        assert "route_name" in entry and "plate_number" in entry

        # The unfiltered listing still returns everything.
        all_ids = {r["id"] for r in client.get("/api/runs", headers=admin_headers).json()}
        assert {active_today["id"], completed_today["id"], stale_in_progress["id"]} <= all_ids
    finally:
        for run in (active_today, completed_today, stale_in_progress):
            client.delete(f"/api/runs/{run['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/buses/{bus['id']}", headers=admin_headers)
