"""Driver lifecycle backend: start gating, afternoon semantics, absences (U6).

Run with the stack up (scripts/start-local.sh):

    RUN_INTEGRATION=1 ../.venv/bin/python -m pytest tests/integration/test_driver_lifecycle.py -q

Covers R24-R28b/R30/R32 and AE8/AE12 (server side): completed-today and
custom-stops start rejections, the afternoon auto-board -> tap-time drop-off ->
silent sweep lifecycle, one-way morning boarding, the driver absent flow
(absence row + run_absences snapshot + status + admin incident, no parent
fan-out), today-scoped admin absence side-effects with the active-run clear
guard, the stale-'absent' self-heal, and delete_run's status reset.

Isolation: this module builds its own throwaway fleet — a driver account
created through POST /api/accounts/drivers WITH a known PIN (the payload
accepts `pin`, so no test ever needs the seeded Simba driver or PIN 0322),
plus a bus, school, morning+afternoon routes and two students. Nothing in
here touches seeded entities, so the completed-today gate can never poison
other suites. Every run a test starts (or an admin creates) is ended when
needed and always DELETED in a finally block — a completed run left behind
for today would block that route for the rest of the day (R24).
"""

import os
import random
import time
import uuid
from datetime import datetime, timedelta
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
    return {"Authorization": f"Bearer {response.json()['token']}"}


def pin_login(client: httpx.Client, pin: str) -> dict:
    response = client.post("/api/auth/pin-login", json={"pin": pin})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


@pytest.fixture(scope="module")
def admin_headers(client):
    return login(client, ADMIN["email"], ADMIN["password"])


def _create_driver(client, admin_headers, marker: str) -> dict:
    """Create a throwaway driver with a known PIN (retry rare PIN collisions)."""
    for _ in range(5):
        pin = str(random.randint(100000, 999999))
        response = client.post(
            "/api/accounts/drivers",
            json={"full_name": f"IT DL Driver {marker}", "email": f"it-dl-driver-{marker}@test.local",
                  "password": "test1234.", "phone": "+254711000090", "pin": pin},
            headers=admin_headers,
        )
        if response.status_code == 200:
            return {**response.json(), "pin": pin}
    pytest.fail(f"could not create throwaway driver: {response.text}")


@pytest.fixture(scope="module")
def fleet(client, admin_headers):
    """Throwaway driver (known PIN) + bus + school + morning/afternoon routes
    + two students at distinct stops. Torn down completely afterwards."""
    marker = uuid.uuid4().hex[:6]
    driver = _create_driver(client, admin_headers, marker)
    bus = school = morning = afternoon = s1 = s2 = None
    try:
        bus = client.post(
            "/api/fleet/buses",
            json={"name": f"IT DL Bus {marker}", "driver_id": driver["id"]},
            headers=admin_headers,
        ).json()
        school = client.post(
            "/api/fleet/schools",
            json={"name": f"IT DL School {marker}", "lat": -1.30, "lng": 36.80},
            headers=admin_headers,
        ).json()
        morning = client.post(
            "/api/fleet/routes",
            json={"name": f"IT DL Morning {marker}", "type": "morning",
                  "bus_id": bus["id"], "school_id": school["id"]},
            headers=admin_headers,
        ).json()
        afternoon = client.post(
            "/api/fleet/routes",
            json={"name": f"IT DL Afternoon {marker}", "type": "afternoon",
                  "bus_id": bus["id"], "school_id": school["id"]},
            headers=admin_headers,
        ).json()

        def make_student(n: int, lat: float, pickup: str) -> dict:
            response = client.post(
                "/api/students",
                json={"name": f"IT DL Kid{n} {marker}", "parent_name": f"IT DL Parent{n}",
                      "parent_phone": f"+25471100009{n}",
                      "parent_email": f"it-dl-p{n}-{marker}@test.local",
                      "home_lat": lat, "home_lng": 36.79, "pickup_time": pickup,
                      "route_ids": [morning["id"], afternoon["id"]]},
                headers=admin_headers,
            )
            assert response.status_code == 200, response.text
            return response.json()

        s1 = make_student(1, -1.28, "06:30")  # morning stop 1 / afternoon stop 3
        s2 = make_student(2, -1.29, "06:45")  # morning stop 2 / afternoon stop 2

        yield {
            "marker": marker,
            "driver": driver,
            "driver_headers": pin_login(client, driver["pin"]),
            "bus": bus, "school": school,
            "morning": morning, "afternoon": afternoon,
            "s1": s1, "s2": s2,
        }
    finally:
        for student in (s1, s2):
            if student:
                _clear_absences_for(client, admin_headers, student["id"])
                client.delete(f"/api/students/{student['id']}", headers=admin_headers)
        for route in (morning, afternoon):
            if route:
                client.delete(f"/api/fleet/routes/{route['id']}", headers=admin_headers)
        if school:
            client.delete(f"/api/fleet/schools/{school['id']}", headers=admin_headers)
        if bus:
            client.delete(f"/api/fleet/buses/{bus['id']}", headers=admin_headers)
        client.delete(f"/api/accounts/drivers/{driver['id']}", headers=admin_headers)


# Helpers ----------------------------------------------------------------------

def _clear_absences_for(client, admin_headers, student_id: str) -> None:
    for a in client.get("/api/students/absences", headers=admin_headers).json():
        if a["student_id"] == student_id:
            client.delete(f"/api/students/absences/{a['id']}", headers=admin_headers)


def _student_status(client, admin_headers, student_id: str) -> str:
    students = client.get("/api/students", headers=admin_headers).json()
    return next(s["status"] for s in students if s["id"] == student_id)


def _end_and_delete(client, admin_headers, driver_headers, run_id: str | None) -> None:
    """Suite hygiene: end the run, then DELETE it so the completed-today gate
    never blocks later lifecycle tests on the same route."""
    if not run_id:
        return
    client.post("/api/runs/driver/end", json={"run_id": run_id}, headers=driver_headers)
    client.delete(f"/api/runs/{run_id}", headers=admin_headers)


def _start_run(client, driver_headers, route_id: str) -> dict:
    response = client.post(
        "/api/runs/driver/start", json={"route_id": route_id}, headers=driver_headers
    )
    assert response.status_code == 200, response.text
    return response.json()


def _arrive(client, driver_headers, run_id: str) -> dict:
    response = client.post(
        "/api/runs/driver/arrive", json={"run_id": run_id}, headers=driver_headers
    )
    assert response.status_code == 200, response.text
    return response.json()["run"]


def _report(client, admin_headers, run_id: str) -> dict:
    response = client.get(f"/api/runs/{run_id}/report", headers=admin_headers)
    assert response.status_code == 200, response.text
    return response.json()


def _wait_for(predicate, timeout: float = 10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.5)
    return None


# Start gating (R24, AE8; R18) ---------------------------------------------------

def test_completed_route_today_blocks_driver_start(client, admin_headers, fleet):
    """A route with a completed run today — whoever created it — cannot be
    started again, and the driver context lists it in
    completed_route_ids_today (AE8)."""
    completed = client.post(
        "/api/runs",
        json={"route_id": fleet["morning"]["id"], "bus_id": fleet["bus"]["id"],
              "type": "morning", "status": "completed"},  # date defaults to today
        headers=admin_headers,
    )
    assert completed.status_code == 200, completed.text
    completed_id = completed.json()["id"]
    try:
        blocked = client.post(
            "/api/runs/driver/start", json={"route_id": fleet["morning"]["id"]},
            headers=fleet["driver_headers"],
        )
        assert blocked.status_code == 409, blocked.text
        assert "already been completed today" in blocked.json()["detail"]

        context = client.get("/api/runs/driver/context", headers=fleet["driver_headers"]).json()
        assert fleet["morning"]["id"] in context["completed_route_ids_today"]
        # The afternoon route stays startable — the gate is per route.
        assert fleet["afternoon"]["id"] not in context["completed_route_ids_today"]
    finally:
        client.delete(f"/api/runs/{completed_id}", headers=admin_headers)

    # Deleting the mistaken run reopens the route (R28 recovery path).
    context = client.get("/api/runs/driver/context", headers=fleet["driver_headers"]).json()
    assert fleet["morning"]["id"] not in context["completed_route_ids_today"]


def test_custom_stops_route_cannot_start(client, admin_headers, fleet):
    """A planner-saved route (custom_stops=true) has no boardable students, so
    driver-start 409s until a student assignment flips the flag (R18). Uses a
    second throwaway driver+bus because the fleet bus already holds one route
    per type."""
    marker = uuid.uuid4().hex[:6]
    driver2 = _create_driver(client, admin_headers, marker)
    bus2 = route = None
    try:
        bus2 = client.post(
            "/api/fleet/buses",
            json={"name": f"IT DL Bus2 {marker}", "driver_id": driver2["id"]},
            headers=admin_headers,
        ).json()
        route = client.post(
            "/api/fleet/routes",
            json={"name": f"IT DL Custom {marker}", "type": "morning",
                  "bus_id": bus2["id"], "school_id": fleet["school"]["id"],
                  "stops": [
                      {"label": "Planner Stop A", "lat": -1.27, "lng": 36.78},
                      {"label": "IT DL School", "lat": -1.30, "lng": 36.80, "is_school": True},
                  ]},
            headers=admin_headers,
        ).json()
        assert route.get("custom_stops") is True, route

        blocked = client.post(
            "/api/runs/driver/start", json={"route_id": route["id"]},
            headers=pin_login(client, driver2["pin"]),
        )
        assert blocked.status_code == 409, blocked.text
        assert "No students are assigned" in blocked.json()["detail"]
    finally:
        if route:
            client.delete(f"/api/fleet/routes/{route['id']}", headers=admin_headers)
        if bus2:
            client.delete(f"/api/fleet/buses/{bus2['id']}", headers=admin_headers)
        client.delete(f"/api/accounts/drivers/{driver2['id']}", headers=admin_headers)


# Afternoon lifecycle (R32, AE12) -------------------------------------------------

def test_afternoon_auto_board_dropoff_and_silent_sweep(client, admin_headers, fleet):
    """Afternoon start auto-boards the run's roster with no per-student
    boarding taps; drop-offs are confirmed per stop once reached; the end-run
    sweep normalizes unconfirmed students without inventing confirmations.
    students_boarded counts confirmed drop-offs throughout (R32)."""
    driver_headers = fleet["driver_headers"]
    run = _start_run(client, driver_headers, fleet["afternoon"]["id"])
    run_id = run["id"]
    try:
        # Auto-board: the whole roster is on the bus, zero taps.
        assert run["students_boarded"] == 0  # counts drop-offs, not riders
        assert _student_status(client, admin_headers, fleet["s1"]["id"]) == "on-bus"
        assert _student_status(client, admin_headers, fleet["s2"]["id"]) == "on-bus"

        # The boarding endpoint has no business on an afternoon run.
        boarding = client.post(
            "/api/runs/driver/boarding",
            json={"student_id": fleet["s2"]["id"], "on_bus": True},
            headers=driver_headers,
        )
        assert boarding.status_code == 409, boarding.text
        assert "drop-off" in boarding.json()["detail"].lower()

        # Drop-off before the stop is reached is rejected.
        early = client.post(
            "/api/runs/driver/dropoff", json={"student_id": fleet["s2"]["id"]},
            headers=driver_headers,
        )
        assert early.status_code == 409, early.text
        assert "not been reached" in early.json()["detail"]

        # Arrive the school gate (stop 1) and s2's stop (stop 2; afternoon
        # runs the morning order backwards, so the later pickup drops first).
        _arrive(client, driver_headers, run_id)
        _arrive(client, driver_headers, run_id)

        # s1's stop (3) is still ahead.
        not_there = client.post(
            "/api/runs/driver/dropoff", json={"student_id": fleet["s1"]["id"]},
            headers=driver_headers,
        )
        assert not_there.status_code == 409, not_there.text

        dropped = client.post(
            "/api/runs/driver/dropoff", json={"student_id": fleet["s2"]["id"]},
            headers=driver_headers,
        )
        assert dropped.status_code == 200, dropped.text
        assert dropped.json()["status"] == "dropped-off"
        assert _report(client, admin_headers, run_id)["students_boarded"] == 1

        # A retried tap is a clean conflict, not a double count.
        retry = client.post(
            "/api/runs/driver/dropoff", json={"student_id": fleet["s2"]["id"]},
            headers=driver_headers,
        )
        assert retry.status_code == 409, retry.text
        assert _report(client, admin_headers, run_id)["students_boarded"] == 1

        # End with s1 unconfirmed: status is swept to dropped-off, but the
        # persisted count stays at the confirmed drop-offs (AE12 data side —
        # the sweep must never inflate confirmations).
        ended = client.post(
            "/api/runs/driver/end", json={"run_id": run_id}, headers=driver_headers
        )
        assert ended.status_code == 200, ended.text
        assert ended.json()["students_boarded"] == 1
        assert _student_status(client, admin_headers, fleet["s1"]["id"]) == "dropped-off"
    finally:
        _end_and_delete(client, admin_headers, driver_headers, run_id)


def test_delete_of_in_progress_run_resets_auto_boarded_students(client, admin_headers, fleet):
    """Deleting a mistakenly started (non-completed) run restores its roster's
    on-bus students to at-school — R28's recovery path must not strand an
    auto-boarded roster."""
    run = _start_run(client, fleet["driver_headers"], fleet["afternoon"]["id"])
    deleted = False
    try:
        assert _student_status(client, admin_headers, fleet["s1"]["id"]) == "on-bus"
        response = client.delete(f"/api/runs/{run['id']}", headers=admin_headers)
        assert response.status_code == 200, response.text
        deleted = True
        assert _student_status(client, admin_headers, fleet["s1"]["id"]) == "at-school"
        assert _student_status(client, admin_headers, fleet["s2"]["id"]) == "at-school"
    finally:
        if not deleted:
            _end_and_delete(client, admin_headers, fleet["driver_headers"], run["id"])


# Morning boarding is one-way (R26) ------------------------------------------------

def test_morning_unboarding_is_rejected(client, admin_headers, fleet):
    driver_headers = fleet["driver_headers"]
    run = _start_run(client, driver_headers, fleet["morning"]["id"])
    try:
        _arrive(client, driver_headers, run["id"])  # stop 1 = s1's stop
        boarded = client.post(
            "/api/runs/driver/boarding",
            json={"student_id": fleet["s1"]["id"], "on_bus": True},
            headers=driver_headers,
        )
        assert boarded.status_code == 200, boarded.text
        assert boarded.json()["status"] == "on-bus"

        unboard = client.post(
            "/api/runs/driver/boarding",
            json={"student_id": fleet["s1"]["id"], "on_bus": False},
            headers=driver_headers,
        )
        assert unboard.status_code == 409, unboard.text
        assert "Un-boarding is disabled" in unboard.json()["detail"]
        assert _student_status(client, admin_headers, fleet["s1"]["id"]) == "on-bus"
    finally:
        _end_and_delete(client, admin_headers, driver_headers, run["id"])


# Driver absent flow (R30) ---------------------------------------------------------

def test_driver_absent_flow(client, admin_headers, fleet):
    """Driver marks a roster student absent: absence row + run_absences
    snapshot + 'absent' status land atomically; the school side gets a
    student-stamped incident; the stop stays visible with an absent flag; a
    repeat mark is a no-op edit; the absence can only be cleared after the
    run ends, and clearing resets the status."""
    driver_headers = fleet["driver_headers"]
    s2 = fleet["s2"]
    run = _start_run(client, driver_headers, fleet["morning"]["id"])
    run_id = run["id"]
    incident_id = None
    try:
        marked = client.post(
            "/api/runs/driver/absent", json={"student_id": s2["id"]}, headers=driver_headers
        )
        assert marked.status_code == 200, marked.text
        assert marked.json()["status"] == "absent"

        # The absence row is today's, stamped by the driver flow's reason.
        absence = next(
            a for a in client.get("/api/students/absences", headers=admin_headers).json()
            if a["student_id"] == s2["id"]
        )
        assert absence["reason"] == "Marked absent by driver at stop"

        # Snapshot: the run report lists the absentee immediately.
        report = _report(client, admin_headers, run_id)
        assert [a["student_id"] for a in report["absent_students"]] == [s2["id"]]

        # The stop stays visible (no mid-run renumbering) and the student
        # entry carries the absent flag for the driver UI (R25b).
        context = client.get("/api/runs/driver/context", headers=driver_headers).json()
        assert s2["id"] in {s["student_id"] for s in context["run_stops"]}
        flags = {s["id"]: s["absent"] for s in context["students"]}
        assert flags[s2["id"]] is True
        assert flags[fleet["s1"]["id"]] is False

        # School-side channel: a student-stamped incident (admin Alerts),
        # delivered post-commit via BackgroundTasks — poll briefly.
        def find_incident():
            for i in client.get("/api/incidents", headers=admin_headers).json():
                if i.get("student_id") == s2["id"] and i.get("run_id") == run_id:
                    return i
            return None

        incident = _wait_for(find_incident)
        assert incident is not None, "student-stamped absence incident never appeared"
        incident_id = incident["id"]
        assert incident["type"] == "student"
        assert s2["name"] in incident["description"]
        assert fleet["morning"]["name"] in incident["description"]

        # A repeat mark mid-run is a reason edit, never a 500 or a second
        # snapshot row.
        again = client.post(
            "/api/runs/driver/absent", json={"student_id": s2["id"]}, headers=driver_headers
        )
        assert again.status_code == 200, again.text
        report = _report(client, admin_headers, run_id)
        assert len(report["absent_students"]) == 1

        # Clearing today's absence during the active run is rejected (R25b).
        blocked = client.delete(
            f"/api/students/absences/{absence['id']}", headers=admin_headers
        )
        assert blocked.status_code == 409, blocked.text
        assert "End the run first" in blocked.json()["detail"]

        # After the run ends the clear succeeds and resets the status.
        ended = client.post(
            "/api/runs/driver/end", json={"run_id": run_id}, headers=driver_headers
        )
        assert ended.status_code == 200, ended.text
        assert _student_status(client, admin_headers, s2["id"]) == "absent"  # sweep skips absent
        cleared = client.delete(
            f"/api/students/absences/{absence['id']}", headers=admin_headers
        )
        assert cleared.status_code == 200, cleared.text
        assert _student_status(client, admin_headers, s2["id"]) == "at-school"
    finally:
        _end_and_delete(client, admin_headers, driver_headers, run_id)
        _clear_absences_for(client, admin_headers, s2["id"])
        if incident_id:
            client.delete(f"/api/incidents/{incident_id}", headers=admin_headers)


# Admin absence side-effects (R25b) --------------------------------------------------

def test_admin_today_absence_syncs_status_and_active_run_snapshot(client, admin_headers, fleet):
    driver_headers = fleet["driver_headers"]
    s1 = fleet["s1"]
    run = _start_run(client, driver_headers, fleet["morning"]["id"])
    try:
        marked = client.post(
            "/api/students/absences",
            json={"student_id": s1["id"], "reason": "IT admin flu"},
            headers=admin_headers,
        )
        assert marked.status_code == 200, marked.text
        assert _student_status(client, admin_headers, s1["id"]) == "absent"
        # The active run's snapshot gains the row mid-run.
        report = _report(client, admin_headers, run["id"])
        entry = next(a for a in report["absent_students"] if a["student_id"] == s1["id"])
        assert entry["reason"] == "IT admin flu"
    finally:
        _end_and_delete(client, admin_headers, driver_headers, run["id"])
        _clear_absences_for(client, admin_headers, s1["id"])
    assert _student_status(client, admin_headers, s1["id"]) == "at-school"


def test_dated_absences_have_no_status_side_effects(client, admin_headers, fleet):
    """Marking or clearing a non-today absence is pure bookkeeping: the live
    status never moves (R25b)."""
    s1 = fleet["s1"]
    today = datetime.now(NAIROBI).date()
    for date in (today + timedelta(days=1), today - timedelta(days=1)):
        before = _student_status(client, admin_headers, s1["id"])
        marked = client.post(
            "/api/students/absences",
            json={"student_id": s1["id"], "date": str(date), "reason": "IT trip"},
            headers=admin_headers,
        )
        assert marked.status_code == 200, marked.text
        assert _student_status(client, admin_headers, s1["id"]) == before

        cleared = client.delete(
            f"/api/students/absences/{marked.json()['id']}", headers=admin_headers
        )
        assert cleared.status_code == 200, cleared.text
        assert _student_status(client, admin_headers, s1["id"]) == before


# Stale-'absent' self-heal ------------------------------------------------------------

def _make_stale_absent_student(client, admin_headers, fleet, marker: str) -> dict:
    """A student whose status says 'absent' with NO today-absence row — the
    stuck state left when nothing cleared yesterday's mark (create honors the
    status field; updates never touch it)."""
    response = client.post(
        "/api/students",
        json={"name": f"IT DL Stale {marker}", "parent_name": "IT DL Stale Parent",
              "parent_phone": "+254711000099", "parent_email": f"it-dl-stale-{marker}@test.local",
              "home_lat": -1.27, "home_lng": 36.79, "pickup_time": "06:20",
              "status": "absent",
              "route_ids": [fleet["morning"]["id"], fleet["afternoon"]["id"]]},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    student = response.json()
    assert student["status"] == "absent"
    return student


def test_stale_absent_student_self_heals_on_morning_start(client, admin_headers, fleet):
    marker = uuid.uuid4().hex[:6]
    stale = _make_stale_absent_student(client, admin_headers, fleet, marker)
    run_id = None
    try:
        run = _start_run(client, fleet["driver_headers"], fleet["morning"]["id"])
        run_id = run["id"]
        # The stop was NOT suppressed (no today-absence) and the status healed.
        context = client.get("/api/runs/driver/context", headers=fleet["driver_headers"]).json()
        assert stale["id"] in {s["student_id"] for s in context["run_stops"]}
        assert _student_status(client, admin_headers, stale["id"]) == "at-school"
    finally:
        _end_and_delete(client, admin_headers, fleet["driver_headers"], run_id)
        client.delete(f"/api/students/{stale['id']}", headers=admin_headers)


def test_stale_absent_student_joins_afternoon_auto_board(client, admin_headers, fleet):
    marker = uuid.uuid4().hex[:6]
    stale = _make_stale_absent_student(client, admin_headers, fleet, marker)
    run_id = None
    try:
        run = _start_run(client, fleet["driver_headers"], fleet["afternoon"]["id"])
        run_id = run["id"]
        assert _student_status(client, admin_headers, stale["id"]) == "on-bus"
    finally:
        _end_and_delete(client, admin_headers, fleet["driver_headers"], run_id)
        client.delete(f"/api/students/{stale['id']}", headers=admin_headers)
