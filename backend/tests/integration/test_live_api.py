"""API integration suite against the running local stack.

Run with the stack up (scripts/start-local.sh):

    RUN_INTEGRATION=1 ../.venv/bin/python -m pytest tests/integration -q

These tests certify the deployed API surface: auth, role fencing, admin CRUD,
the driver run lifecycle, the parent portal, and the push-notification
pipeline. They use the seeded demo data and clean up what they create; runs
they start are always ended. Re-apply the seed (scripts/reset-local-db.sh)
to restore pristine demo state afterwards.
"""

import os
import time
import uuid

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="needs the local stack; set RUN_INTEGRATION=1",
)

BASE = os.environ.get("INTEGRATION_API_URL", "http://localhost:9001")

ADMIN = {"email": "admin@test.com", "password": "test1234."}
PARENT = {"email": "and7005@gmail.com", "password": "Test1234"}
DRIVER_PIN = "0322"

# Seeded fixtures (backend/db/seeds/003_local_snapshot.sql). On seed drift,
# update these constants instead of individual tests.
PARENT_CHILD = "Faith Achieng"  # Amina's child on the demo driver's bus (Simba)
PARENT_BUSLESS_CHILD = "Grace Njeri"  # Amina's bus-less child (local-only seed extension)
PARENT_CHILDREN = {PARENT_CHILD, PARENT_BUSLESS_CHILD}
FOREIGN_STUDENT_ID = "50000000-0000-0000-0000-000000000003"  # Happiness Kenesa — not Amina's


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


@pytest.fixture(scope="module")
def parent_headers(client):
    return login(client, PARENT["email"], PARENT["password"])


@pytest.fixture(scope="module")
def driver_headers(client):
    response = client.post("/api/auth/pin-login", json={"pin": DRIVER_PIN})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


@pytest.fixture()
def no_active_run(client, driver_headers):
    """End any active run before and after a lifecycle test."""

    def end_run():
        context = client.get("/api/runs/driver/context", headers=driver_headers)
        active = context.json().get("active_run")
        if active:
            client.post("/api/runs/driver/end", headers=driver_headers, json={"run_id": active["id"]})

    end_run()
    yield
    end_run()


# Health & auth ---------------------------------------------------------------

def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_wrong_password_is_a_clean_401(client):
    response = client.post(
        "/api/auth/login", json={"email": PARENT["email"], "password": "nope"}
    )
    assert response.status_code == 401
    assert "Invalid email or password" in response.json()["detail"]


def test_me_reflects_each_role(client, admin_headers, parent_headers, driver_headers):
    for headers, role in ((admin_headers, "admin"), (parent_headers, "parent"), (driver_headers, "driver")):
        me = client.get("/api/auth/me", headers=headers).json()
        assert me["role"] == role


def test_logout_revokes_the_session(client):
    headers = login(client, PARENT["email"], PARENT["password"])
    assert client.get("/api/auth/me", headers=headers).status_code == 200
    client.post("/api/auth/logout", headers=headers)
    assert client.get("/api/auth/me", headers=headers).status_code == 401


def test_legacy_unauthenticated_admin_api_is_gone(client):
    assert client.get("/api/admin/students").status_code in (401, 404)
    assert client.post("/api/admin/buses", json={}).status_code in (401, 404, 405)


# Role fencing ----------------------------------------------------------------

def test_admin_endpoints_reject_other_roles(client, parent_headers, driver_headers):
    assert client.post("/api/fleet/buses", json={"name": "X"}, headers=parent_headers).status_code == 403
    assert client.post("/api/fleet/buses", json={"name": "X"}, headers=driver_headers).status_code == 403
    assert client.post("/api/fleet/buses", json={"name": "X"}).status_code == 401
    assert client.delete("/api/students/00000000-0000-0000-0000-000000000000", headers=parent_headers).status_code == 403


def test_driver_endpoints_reject_other_roles(client, admin_headers, parent_headers):
    payload = {"route_id": "40000000-0000-0000-0000-000000000001"}
    assert client.post("/api/runs/driver/start", json=payload, headers=parent_headers).status_code == 403
    assert client.post("/api/runs/driver/start", json=payload, headers=admin_headers).status_code == 403


def test_parent_portal_rejects_other_roles(client, admin_headers, driver_headers):
    assert client.get("/api/parent-portal/children", headers=admin_headers).status_code == 403
    assert client.get("/api/parent-portal/children", headers=driver_headers).status_code == 403


def test_parents_cannot_track_other_children(client, parent_headers):
    response = client.get(
        "/api/parent-portal/track",
        params={"student_id": FOREIGN_STUDENT_ID},
        headers=parent_headers,
    )
    assert response.status_code == 404


# Admin CRUD ------------------------------------------------------------------

def test_bus_crud(client, admin_headers):
    name = f"IT Bus {uuid.uuid4().hex[:6]}"
    created = client.post(
        "/api/fleet/buses",
        json={"name": name, "plate_number": "ITX 001", "capacity": 20, "status": "idle"},
        headers=admin_headers,
    )
    assert created.status_code == 200, created.text
    bus = created.json()

    updated = client.put(
        f"/api/fleet/buses/{bus['id']}",
        json={"name": f"{name} v2", "plate_number": "ITX 002", "capacity": 22, "status": "active"},
        headers=admin_headers,
    )
    assert updated.status_code == 200
    assert updated.json()["plate_number"] == "ITX 002"

    assert client.delete(f"/api/fleet/buses/{bus['id']}", headers=admin_headers).status_code == 200
    names = [b["name"] for b in client.get("/api/fleet/buses", headers=admin_headers).json()]
    assert f"{name} v2" not in names


def test_school_route_and_student_crud(client, admin_headers):
    marker = uuid.uuid4().hex[:6]
    school = client.post(
        "/api/fleet/schools",
        json={"name": f"IT School {marker}", "address": "Test Rd", "phone": "+254700000000", "lat": -1.30, "lng": 36.80},
        headers=admin_headers,
    ).json()
    student = client.post(
        "/api/students",
        json={"name": f"IT Student {marker}", "grade": "G1", "home_lat": -1.29, "home_lng": 36.81,
              "home_address": "Home Rd", "parent_name": "IT Parent", "parent_phone": "+254711000000"},
        headers=admin_headers,
    ).json()
    route = client.post(
        "/api/fleet/routes",
        json={"name": f"IT Route {marker}", "type": "morning", "school_id": school["id"]},
        headers=admin_headers,
    ).json()

    try:
        assert school["name"] == f"IT School {marker}"
        assert student["name"] == f"IT Student {marker}"
        assert route["type"] == "morning"

        renamed = client.put(
            f"/api/students/{student['id']}",
            json={"name": f"IT Student {marker} v2", "grade": "G2"},
            headers=admin_headers,
        )
        assert renamed.status_code == 200
        assert renamed.json()["grade"] == "G2"
    finally:
        client.delete(f"/api/fleet/routes/{route['id']}", headers=admin_headers)
        client.delete(f"/api/students/{student['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/schools/{school['id']}", headers=admin_headers)


def test_driver_account_crud_and_parent_link(client, admin_headers):
    marker = uuid.uuid4().hex[:6]
    driver = client.post(
        "/api/accounts/drivers",
        json={"full_name": f"IT Driver {marker}", "email": f"it-driver-{marker}@test.local",
              "password": "DriverPass1!", "phone": "+254722000000", "pin": ""},
        headers=admin_headers,
    )
    assert driver.status_code == 200, driver.text
    driver_id = driver.json()["id"]

    signup = client.post(
        "/api/auth/signup",
        json={"email": f"it-parent-{marker}@test.local", "password": "ParentPass1!",
              "full_name": f"IT Parent {marker}", "role": "parent"},
    )
    assert signup.status_code == 200, signup.text
    parent_id = signup.json()["user"]["id"] if "user" in signup.json() else None
    if parent_id is None:
        parents = client.get("/api/accounts/parents", headers=admin_headers).json()
        parent_id = next(p["id"] for p in parents if p.get("email") == f"it-parent-{marker}@test.local")

    student = client.post(
        "/api/students",
        json={"name": f"IT LinkKid {marker}", "grade": "G3"},
        headers=admin_headers,
    ).json()

    try:
        link = client.post(
            "/api/accounts/parent-students",
            json={"parent_id": parent_id, "student_id": student["id"]},
            headers=admin_headers,
        )
        assert link.status_code == 200, link.text
        link_id = link.json()["id"]

        links = client.get("/api/accounts/parent-students", headers=admin_headers).json()
        assert any(l["id"] == link_id for l in links)

        assert client.delete(
            f"/api/accounts/parent-students/{link_id}", headers=admin_headers
        ).status_code == 200
    finally:
        client.delete(f"/api/students/{student['id']}", headers=admin_headers)
        client.delete(f"/api/accounts/drivers/{driver_id}", headers=admin_headers)
        client.delete(f"/api/accounts/parents/{parent_id}", headers=admin_headers)


# Validation, stops, absences, run uniqueness (June 2026 buglist) -------------

def test_phone_validation_rejects_bad_numbers(client, admin_headers):
    bad_school = client.post(
        "/api/fleet/schools",
        json={"name": "Bad Phone School", "phone": "12345"},
        headers=admin_headers,
    )
    assert bad_school.status_code == 400
    bad_driver = client.post(
        "/api/accounts/drivers",
        json={"full_name": "Bad", "email": "bad-phone@test.local",
              "password": "DriverPass1!", "phone": "0812345678"},
        headers=admin_headers,
    )
    assert bad_driver.status_code == 400


def test_route_stops_named_by_address_and_directional(client, admin_headers):
    marker = uuid.uuid4().hex[:6]
    school = client.post(
        "/api/fleet/schools",
        json={"name": f"Dir School {marker}", "lat": -1.30, "lng": 36.82},
        headers=admin_headers,
    ).json()
    morning = client.post(
        "/api/fleet/routes",
        json={"name": f"Dir AM {marker}", "type": "morning", "school_id": school["id"]},
        headers=admin_headers,
    ).json()
    afternoon = client.post(
        "/api/fleet/routes",
        json={"name": f"Dir PM {marker}", "type": "afternoon", "school_id": school["id"]},
        headers=admin_headers,
    ).json()
    early = client.post(
        "/api/students",
        json={"name": f"Early {marker}", "home_address": "12 Early Lane",
              "home_lat": -1.28, "home_lng": 36.80, "pickup_time": "06:30",
              "route_ids": [morning["id"], afternoon["id"]]},
        headers=admin_headers,
    ).json()
    late = client.post(
        "/api/students",
        json={"name": f"Late {marker}", "home_address": "99 Late Road",
              "home_lat": -1.29, "home_lng": 36.81, "pickup_time": "06:50",
              "route_ids": [morning["id"], afternoon["id"]]},
        headers=admin_headers,
    ).json()
    try:
        routes = client.get("/api/fleet/routes", headers=admin_headers).json()

        am = next(r for r in routes if r["id"] == morning["id"])
        am_stops = sorted(am["route_stops"], key=lambda s: s["stop_order"])
        # Stops are named by home address, ordered by pickup time, gate last.
        assert am_stops[0]["name"] == "12 Early Lane"
        assert am_stops[1]["name"] == "99 Late Road"
        assert am_stops[-1]["is_school_gate"] is True

        pm = next(r for r in routes if r["id"] == afternoon["id"])
        pm_stops = sorted(pm["route_stops"], key=lambda s: s["stop_order"])
        # Afternoon: school first, then reverse pickup order.
        assert pm_stops[0]["is_school_gate"] is True
        assert pm_stops[1]["name"] == "99 Late Road"
        assert pm_stops[2]["name"] == "12 Early Lane"

        # Cancelling a student cancels their stop on the route (#1/#6 cascade).
        client.delete(f"/api/students/{late['id']}", headers=admin_headers)
        routes = client.get("/api/fleet/routes", headers=admin_headers).json()
        am = next(r for r in routes if r["id"] == morning["id"])
        names = {s["name"] for s in am["route_stops"]}
        assert "99 Late Road" not in names
        assert "12 Early Lane" in names
    finally:
        client.delete(f"/api/students/{early['id']}", headers=admin_headers)
        client.delete(f"/api/students/{late['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/routes/{morning['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/routes/{afternoon['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/schools/{school['id']}", headers=admin_headers)


def test_coordinateless_student_gets_address_named_stop(client, admin_headers):
    """A student with an address but no coordinates still gets their own stop
    named by that address — never collapsed into a generic 'School Pickup' (#4)."""
    marker = uuid.uuid4().hex[:6]
    address = f"Pickup Point {marker}, Nairobi"
    school = client.post(
        "/api/fleet/schools",
        json={"name": f"Addr School {marker}", "lat": -1.30, "lng": 36.82},
        headers=admin_headers,
    ).json()
    route = client.post(
        "/api/fleet/routes",
        json={"name": f"Addr Route {marker}", "type": "morning", "school_id": school["id"]},
        headers=admin_headers,
    ).json()
    student = client.post(
        "/api/students",
        json={"name": f"NoCoords {marker}", "home_address": address, "pickup_time": "06:45",
              "route_ids": [route["id"]]},
        headers=admin_headers,
    ).json()
    try:
        r = next(x for x in client.get("/api/fleet/routes", headers=admin_headers).json()
                 if x["id"] == route["id"])
        names = [s["name"] for s in r["route_stops"]]
        assert address in names, names
        assert "School Pickup" not in names, names
    finally:
        client.delete(f"/api/students/{student['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/routes/{route['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/schools/{school['id']}", headers=admin_headers)


def test_route_options_orders_stops(client, admin_headers):
    body = {
        "type": "morning",
        "stops": [
            {"label": "Far", "lat": -1.33, "lng": 36.86, "pickup_time": "06:20"},
            {"label": "Near", "lat": -1.31, "lng": 36.83, "pickup_time": "06:50"},
        ],
    }
    resp = client.post("/api/fleet/route-options", json=body, headers=admin_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data["options"]) == 2
    assert all("stops" in o and o["stops"] for o in data["options"])
    by_time = next(o for o in data["options"] if o["strategy"] == "By pickup time")
    assert [s["label"] for s in by_time["stops"]] == ["Far", "Near"]


def test_student_keeps_both_routes_on_update(client, admin_headers):
    """A student keeps both a morning and an afternoon route across edits (#5).

    Regression: _sync_routes compared incoming string ids against psycopg UUID
    keys, so editing a student deleted the route that was already saved.
    """
    marker = uuid.uuid4().hex[:6]
    school = client.post(
        "/api/fleet/schools", json={"name": f"Both School {marker}", "lat": -1.30, "lng": 36.82},
        headers=admin_headers,
    ).json()
    morning = client.post(
        "/api/fleet/routes",
        json={"name": f"Both AM {marker}", "type": "morning", "school_id": school["id"]},
        headers=admin_headers,
    ).json()
    afternoon = client.post(
        "/api/fleet/routes",
        json={"name": f"Both PM {marker}", "type": "afternoon", "school_id": school["id"]},
        headers=admin_headers,
    ).json()
    # Created with only the morning route, mirroring the reported flow.
    student = client.post(
        "/api/students",
        json={"name": f"Both Kid {marker}", "route_ids": [morning["id"]]},
        headers=admin_headers,
    ).json()

    def route_ids() -> set:
        s = next(x for x in client.get("/api/students", headers=admin_headers).json() if x["id"] == student["id"])
        return set(s["route_ids"])

    try:
        assert route_ids() == {morning["id"]}

        # Edit to add the afternoon route while keeping the morning one.
        client.put(
            f"/api/students/{student['id']}",
            json={"name": f"Both Kid {marker}", "route_ids": [morning["id"], afternoon["id"]]},
            headers=admin_headers,
        )
        assert route_ids() == {morning["id"], afternoon["id"]}

        # Idempotent re-save with both already present must not drop either.
        client.put(
            f"/api/students/{student['id']}",
            json={"name": f"Both Kid {marker}", "route_ids": [afternoon["id"], morning["id"]]},
            headers=admin_headers,
        )
        assert route_ids() == {morning["id"], afternoon["id"]}

        # Removing one still works.
        client.put(
            f"/api/students/{student['id']}",
            json={"name": f"Both Kid {marker}", "route_ids": [afternoon["id"]]},
            headers=admin_headers,
        )
        assert route_ids() == {afternoon["id"]}
    finally:
        client.delete(f"/api/students/{student['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/routes/{morning['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/routes/{afternoon['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/schools/{school['id']}", headers=admin_headers)


def test_admin_cannot_create_duplicate_active_run(client, admin_headers):
    marker = uuid.uuid4().hex[:6]
    bus = client.post(
        "/api/fleet/buses", json={"name": f"Dup Bus {marker}"}, headers=admin_headers
    ).json()
    run1 = client.post(
        "/api/runs", json={"bus_id": bus["id"], "type": "morning", "status": "in-progress"},
        headers=admin_headers,
    )
    assert run1.status_code == 200, run1.text
    run2 = client.post(
        "/api/runs", json={"bus_id": bus["id"], "type": "morning", "status": "in-progress"},
        headers=admin_headers,
    )
    assert run2.status_code == 409
    client.delete(f"/api/runs/{run1.json()['id']}", headers=admin_headers)
    client.delete(f"/api/fleet/buses/{bus['id']}", headers=admin_headers)


def test_absence_suppresses_driver_stop(client, admin_headers, driver_headers, no_active_run):
    students = client.get("/api/students", headers=admin_headers).json()
    child = next(s for s in students if s["name"] == PARENT_CHILD)
    marked = client.post(
        "/api/students/absences", json={"student_id": child["id"]}, headers=admin_headers
    )
    assert marked.status_code == 200, marked.text
    try:
        context = client.get("/api/runs/driver/context", headers=driver_headers).json()
        morning = next(r for r in context["routes"] if r["type"] == "morning")
        run = client.post(
            "/api/runs/driver/start", json={"route_id": morning["id"]}, headers=driver_headers
        )
        assert run.status_code == 200, run.text
        ctx = client.get("/api/runs/driver/context", headers=driver_headers).json()
        stop_student_ids = {s["student_id"] for s in ctx["run_stops"]}
        assert child["id"] not in stop_student_ids
    finally:
        absences = client.get(
            "/api/students/absences", headers=admin_headers
        ).json()
        for a in absences:
            if a["student_id"] == child["id"]:
                client.delete(f"/api/students/absences/{a['id']}", headers=admin_headers)


# Driver lifecycle & notification pipeline -------------------------------------

def test_run_lifecycle_notifies_parents(client, admin_headers, parent_headers, driver_headers, no_active_run):
    context = client.get("/api/runs/driver/context", headers=driver_headers).json()
    morning = next(r for r in context["routes"] if r["type"] == "morning")

    run = client.post(
        "/api/runs/driver/start", json={"route_id": morning["id"]}, headers=driver_headers
    )
    assert run.status_code == 200, run.text
    run_id = run.json()["id"]

    # GPS near the first stop fires bus-approaching.
    track_children = client.get("/api/parent-portal/children", headers=parent_headers).json()
    child = next(c for c in track_children if c["name"] == PARENT_CHILD)
    assert client.post(
        "/api/runs/driver/position", json={"lat": -1.2902, "lng": 36.7823}, headers=driver_headers
    ).status_code == 200

    # Reach stop 1 and board the child.
    assert client.post(
        "/api/runs/driver/arrive", json={"run_id": run_id}, headers=driver_headers
    ).status_code == 200
    boarded = client.post(
        "/api/runs/driver/boarding",
        json={"student_id": child["id"], "on_bus": True},
        headers=driver_headers,
    )
    assert boarded.status_code == 200
    assert boarded.json()["status"] == "on-bus"

    # End the run (sweeps students to at-school and emits reached-school).
    ended = client.post("/api/runs/driver/end", json={"run_id": run_id}, headers=driver_headers)
    assert ended.status_code == 200
    assert ended.json()["status"] == "completed"

    # BackgroundTasks deliver after the response; poll briefly.
    expected = {"run-started", "bus-approaching", "student-boarded", "reached-school"}
    deadline = time.time() + 10
    seen: set = set()
    while time.time() < deadline and not expected.issubset(seen):
        feed = client.get("/api/push/notifications", headers=parent_headers).json()
        seen = {n["type"] for n in feed if n.get("run_id") == run_id}
        time.sleep(0.5)
    assert expected.issubset(seen), f"missing notification types: {expected - seen}"


def test_incident_notifies_bus_parents(client, parent_headers, driver_headers):
    marker = f"IT incident {uuid.uuid4().hex[:6]}"
    reported = client.post(
        "/api/incidents/driver",
        json={"type": "breakdown", "description": marker},
        headers=driver_headers,
    )
    assert reported.status_code == 200

    deadline = time.time() + 10
    entry = None
    while time.time() < deadline and entry is None:
        feed = client.get("/api/push/notifications", headers=parent_headers).json()
        entry = next((n for n in feed if n["body"] == marker), None)
        time.sleep(0.5)
    assert entry is not None
    assert entry["type"] == "incident"
    assert entry["title"] == "Vehicle breakdown"

    # The parent alerts view (incidents) carries it too.
    alerts = client.get("/api/parent-portal/alerts", headers=parent_headers).json()
    assert any(a["description"] == marker for a in alerts)


def test_notifications_mark_read(client, parent_headers):
    client.get("/api/push/notifications", headers=parent_headers)
    client.post("/api/push/notifications/mark-read", headers=parent_headers)
    count = client.get("/api/push/notifications/unread-count", headers=parent_headers).json()
    assert count == {"count": 0}


# Push registration ------------------------------------------------------------

def test_push_config_is_public_and_secretless(client):
    config = client.get("/api/push/config").json()
    assert set(config.keys()) == {"firebase", "firebaseVapidKey", "vapidPublicKey"}


def test_fcm_token_register_and_unregister(client, parent_headers):
    token = f"it-token-{uuid.uuid4().hex}"
    assert client.post(
        "/api/push/fcm-token", json={"token": token, "user_agent": "pytest"}, headers=parent_headers
    ).json() == {"ok": True}
    assert client.post(
        "/api/push/fcm-token/unregister", json={"token": token}, headers=parent_headers
    ).json() == {"ok": True}


def test_push_endpoints_require_auth(client):
    assert client.post("/api/push/fcm-token", json={"token": "x"}).status_code == 401
    assert client.get("/api/push/notifications").status_code == 401


# Parent portal ----------------------------------------------------------------

def test_parent_children_and_track(client, parent_headers):
    children = client.get("/api/parent-portal/children", headers=parent_headers).json()
    names = {c["name"] for c in children}
    assert PARENT_CHILDREN <= names

    child = next(c for c in children if c["name"] == PARENT_CHILD)
    track = client.get(
        "/api/parent-portal/track", params={"student_id": child["id"]}, headers=parent_headers
    ).json()
    assert track["student"]["name"] == PARENT_CHILD
    assert any(s["is_school_gate"] for s in track["stops"])
    # Sibling-shared stops still mark the requesting child's own stop.
    assert any(s["is_own"] for s in track["stops"])
