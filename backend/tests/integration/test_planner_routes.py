"""Planner persistence: custom routes saved from the route planner (U11).

Run with the stack up (scripts/start-local.sh):

    RUN_INTEGRATION=1 ../.venv/bin/python -m pytest tests/integration/test_planner_routes.py -q

Covers R17/R18 + AE7: saving a calculated option persists its stops verbatim
(student_id NULL) with the polyline/totals and custom_stops = true in the
routes payload; assigning a student flips the flag, clears the polyline and
regenerates student stops; a school update leaves custom stops intact; saving
onto a taken bus 409s; and the student-keyed stop-edit endpoints cannot touch
custom stops. Entities are 'IT '-prefixed and cleaned up in finally blocks.
"""

import os
import uuid

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="needs the local stack; set RUN_INTEGRATION=1",
)

BASE = os.environ.get("INTEGRATION_API_URL", "http://localhost:9001")

ADMIN = {"email": "admin@test.com", "password": "test1234."}

POLYLINE = "e~fFqxbjMabc[wDef@ghI"


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


def _custom_stops(marker: str) -> list[dict]:
    return [
        {"label": f"IT Corner A {marker}", "lat": -1.30, "lng": 36.80, "pickup_time": "07:05"},
        {"label": f"IT Corner B {marker}", "lat": -1.31, "lng": 36.81, "pickup_time": "07:20"},
        {"label": f"IT Gate {marker}", "lat": -1.32, "lng": 36.82, "is_school": True},
    ]


def _save_custom_route(
    client, admin_headers, marker: str, *, bus_id: str | None = None, school_id: str | None = None
) -> httpx.Response:
    return client.post(
        "/api/fleet/routes",
        json={
            "name": f"IT Planned {marker}",
            "type": "morning",
            "bus_id": bus_id,
            "school_id": school_id,
            "stops": _custom_stops(marker),
            "polyline": POLYLINE,
            "total_distance_m": 12345,
            "total_duration_s": 1800,
        },
        headers=admin_headers,
    )


def _get_route(client, admin_headers, route_id: str) -> dict:
    routes = client.get("/api/fleet/routes", headers=admin_headers).json()
    return next(r for r in routes if r["id"] == route_id)


# Saving a planner option ------------------------------------------------------

def test_saved_custom_route_persists_stops_and_polyline(client, admin_headers):
    """A saved option's stops land verbatim (given order, student_id NULL,
    school flagged as gate) and the list payload carries polyline/totals and
    custom_stops = true (R17)."""
    marker = uuid.uuid4().hex[:6]
    created = _save_custom_route(client, admin_headers, marker)
    assert created.status_code == 200, created.text
    route = created.json()
    try:
        assert route["custom_stops"] is True
        assert route["polyline"] == POLYLINE
        assert route["total_distance_m"] == 12345
        assert route["total_duration_s"] == 1800

        listed = _get_route(client, admin_headers, route["id"])
        assert listed["custom_stops"] is True
        assert listed["polyline"] == POLYLINE
        assert listed["total_distance_m"] == 12345
        assert listed["total_duration_s"] == 1800

        stops = listed["route_stops"]
        assert [s["stop_order"] for s in stops] == [1, 2, 3]
        assert [s["name"] for s in stops] == [
            f"IT Corner A {marker}", f"IT Corner B {marker}", f"IT Gate {marker}",
        ]
        assert [s["scheduled_time"] for s in stops] == ["07:05", "07:20", None]
        assert [s["is_school_gate"] for s in stops] == [False, False, True]
        assert all(s["student_id"] is None for s in stops)
        assert stops[0]["lat"] == pytest.approx(-1.30)
        assert stops[0]["lng"] == pytest.approx(36.80)
    finally:
        client.delete(f"/api/fleet/routes/{route['id']}", headers=admin_headers)


def test_custom_route_save_with_taken_bus_conflicts(client, admin_headers):
    """The (bus, type) uniqueness pre-check applies to planner saves too: a
    bus already holding a morning route rejects the save with a friendly 409
    naming both (R1 x R17)."""
    marker = uuid.uuid4().hex[:6]
    bus = client.post(
        "/api/fleet/buses", json={"name": f"IT PlanBus {marker}"}, headers=admin_headers
    ).json()
    holder = client.post(
        "/api/fleet/routes",
        json={"name": f"IT Holder {marker}", "type": "morning", "bus_id": bus["id"]},
        headers=admin_headers,
    ).json()
    saved_id = None
    try:
        saved = _save_custom_route(client, admin_headers, marker, bus_id=bus["id"])
        if saved.status_code == 200:  # unexpected: remember it for cleanup
            saved_id = saved.json()["id"]
        assert saved.status_code == 409, saved.text
        detail = saved.json()["detail"]
        assert f"IT Holder {marker}" in detail, detail
        assert f"IT PlanBus {marker}" in detail, detail
    finally:
        if saved_id:
            client.delete(f"/api/fleet/routes/{saved_id}", headers=admin_headers)
        client.delete(f"/api/fleet/routes/{holder['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/buses/{bus['id']}", headers=admin_headers)


# Handover to student-based stops ----------------------------------------------

def test_student_assignment_flips_flag_and_regenerates(client, admin_headers):
    """Assigning a student to a custom route hands it back to the students:
    custom_stops flips off, the polyline/totals clear, and the stops rebuild
    from the assigned student (AE7 / R18)."""
    marker = uuid.uuid4().hex[:6]
    route = _save_custom_route(client, admin_headers, marker).json()
    student = client.post(
        "/api/students",
        json={
            "name": f"IT Plan Kid {marker}",
            "parent_name": "IT Plan Parent",
            "parent_phone": "+254711000031",
            "parent_email": f"it-plan-{marker}@test.local",
            "home_address": f"IT Plan Home {marker}",
            "home_lat": -1.29,
            "home_lng": 36.79,
            "pickup_time": "06:45",
            "route_ids": [route["id"]],
        },
        headers=admin_headers,
    ).json()
    try:
        listed = _get_route(client, admin_headers, route["id"])
        assert listed["custom_stops"] is False
        assert listed["polyline"] is None
        assert listed["total_distance_m"] is None
        assert listed["total_duration_s"] is None

        stops = listed["route_stops"]
        # Rebuilt from the student roster: the saved planner stops are gone.
        assert not any(s["name"].startswith("IT Corner") for s in stops)
        student_stops = [s for s in stops if s["student_id"] == student["id"]]
        assert len(student_stops) == 1
        assert student_stops[0]["name"] == f"IT Plan Home {marker}"
        assert student_stops[0]["scheduled_time"] == "06:45"
    finally:
        client.delete(f"/api/students/{student['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/routes/{route['id']}", headers=admin_headers)


# School updates ------------------------------------------------------------------

def test_school_update_leaves_custom_stops_intact(client, admin_headers):
    """Editing a school regenerates its routes' stops — but a custom route is
    skipped: saved stops, flag and polyline all survive (R18)."""
    marker = uuid.uuid4().hex[:6]
    school = client.post(
        "/api/fleet/schools",
        json={"name": f"IT PlanSchool {marker}", "lat": -1.33, "lng": 36.83},
        headers=admin_headers,
    ).json()
    route = _save_custom_route(client, admin_headers, marker, school_id=school["id"]).json()
    try:
        updated = client.put(
            f"/api/fleet/schools/{school['id']}",
            json={"name": f"IT PlanSchool {marker} v2", "lat": -1.34, "lng": 36.84},
            headers=admin_headers,
        )
        assert updated.status_code == 200, updated.text

        listed = _get_route(client, admin_headers, route["id"])
        assert listed["custom_stops"] is True
        assert listed["polyline"] == POLYLINE
        assert [s["name"] for s in listed["route_stops"]] == [
            f"IT Corner A {marker}", f"IT Corner B {marker}", f"IT Gate {marker}",
        ]
        assert all(s["student_id"] is None for s in listed["route_stops"])
    finally:
        client.delete(f"/api/fleet/routes/{route['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/schools/{school['id']}", headers=admin_headers)


# Stop-edit endpoints -------------------------------------------------------------

def test_stop_edit_endpoints_cannot_touch_custom_stops(client, admin_headers):
    """PUT/DELETE /routes/{id}/stops/{student_id} are student-keyed; custom
    stops have no student, so the calls are harmless no-ops (or 404) and the
    saved stops survive untouched."""
    marker = uuid.uuid4().hex[:6]
    route = _save_custom_route(client, admin_headers, marker).json()
    phantom_student = str(uuid.uuid4())
    try:
        retime = client.put(
            f"/api/fleet/routes/{route['id']}/stops/{phantom_student}",
            json={"pickup_time": "09:00"},
            headers=admin_headers,
        )
        assert retime.status_code in (200, 404), retime.text

        cancel = client.delete(
            f"/api/fleet/routes/{route['id']}/stops/{phantom_student}",
            headers=admin_headers,
        )
        assert cancel.status_code in (200, 404), cancel.text

        listed = _get_route(client, admin_headers, route["id"])
        assert listed["custom_stops"] is True
        assert listed["polyline"] == POLYLINE
        assert [s["name"] for s in listed["route_stops"]] == [
            f"IT Corner A {marker}", f"IT Corner B {marker}", f"IT Gate {marker}",
        ]
        assert [s["scheduled_time"] for s in listed["route_stops"]] == ["07:05", "07:20", None]
    finally:
        client.delete(f"/api/fleet/routes/{route['id']}", headers=admin_headers)
