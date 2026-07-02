"""Run report backend: absence snapshot, boarded recount, report endpoint (U5).

Run with the stack up (scripts/start-local.sh):

    RUN_INTEGRATION=1 ../.venv/bin/python -m pytest tests/integration/test_run_report.py -q

Covers R14-R16 / AE6 (data side): start_run snapshots today's absences for
the ROUTE's membership into run_absences with denormalized names (auditable
after student deletion), toggle_boarding recounts students_boarded from the
run's own run_stops roster (idempotent under repeated taps), end_run persists
the final pre-sweep count, and GET /api/runs/{run_id}/report returns the run
row + bus/route/driver names + the absent_students snapshot — with the
legacy fallback (route but no snapshot and no run_stops) flagged
approximate=true. Entities are 'IT '-prefixed and cleaned up in finally
blocks; runs the tests start are always ended (and deleted) afterwards.
"""

import datetime as _dt
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
DRIVER_PIN = "0322"


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
def driver_headers(client):
    response = client.post("/api/auth/pin-login", json={"pin": DRIVER_PIN})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


@pytest.fixture()
def no_active_run(client, driver_headers, admin_headers):
    """End and DELETE today's runs for the driver's bus before and after a
    lifecycle test — completed runs block same-day restarts (R28)."""

    def reset_today_runs():
        context = client.get("/api/runs/driver/context", headers=driver_headers).json()
        active = context.get("active_run")
        if active:
            client.post("/api/runs/driver/end", headers=driver_headers, json={"run_id": active["id"]})
        bus_id = (context.get("bus") or {}).get("id")
        if not bus_id:
            return
        today = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=3))).date().isoformat()
        for run in client.get("/api/runs", headers=admin_headers).json():
            if run.get("bus_id") == bus_id and str(run.get("date")) == today:
                client.delete(f"/api/runs/{run['id']}", headers=admin_headers)

    reset_today_runs()
    yield
    reset_today_runs()


def _get_report(client, admin_headers, run_id: str) -> dict:
    response = client.get(f"/api/runs/{run_id}/report", headers=admin_headers)
    assert response.status_code == 200, response.text
    return response.json()


def _clear_absences_for(client, admin_headers, student_id: str) -> None:
    absences = client.get("/api/students/absences", headers=admin_headers).json()
    for a in absences:
        if a["student_id"] == student_id:
            client.delete(f"/api/students/absences/{a['id']}", headers=admin_headers)


# Absence snapshot -------------------------------------------------------------

def test_report_snapshots_absent_student_and_survives_deletion(
    client, admin_headers, driver_headers, no_active_run
):
    """Starting a run snapshots today's absences for the ROUTE's membership
    into run_absences with the student's name denormalized: the report lists
    the absentee by name and reason, and keeps doing so after the student is
    deleted (R14, R16)."""
    marker = uuid.uuid4().hex[:6]
    context = client.get("/api/runs/driver/context", headers=driver_headers).json()
    morning = next(r for r in context["routes"] if r["type"] == "morning")

    student = client.post(
        "/api/students",
        json={"name": f"IT Absent Kid {marker}", "parent_name": "IT Absent Parent",
              "parent_phone": "+254711000042", "parent_email": f"it-report-{marker}@test.local",
              "route_ids": [morning["id"]]},
        headers=admin_headers,
    )
    assert student.status_code == 200, student.text
    student = student.json()
    student_deleted = False
    run_id = None
    try:
        marked = client.post(
            "/api/students/absences",
            json={"student_id": student["id"], "reason": f"IT flu {marker}"},
            headers=admin_headers,
        )
        assert marked.status_code == 200, marked.text

        run = client.post(
            "/api/runs/driver/start", json={"route_id": morning["id"]}, headers=driver_headers
        )
        assert run.status_code == 200, run.text
        run_id = run.json()["id"]

        report = _get_report(client, admin_headers, run_id)
        assert report["approximate"] is False
        entry = next(
            (a for a in report["absent_students"] if a["student_name"] == f"IT Absent Kid {marker}"),
            None,
        )
        assert entry is not None, report["absent_students"]
        assert entry["student_id"] == student["id"]
        assert entry["reason"] == f"IT flu {marker}"

        # The denormalized name keeps the report auditable after deletion.
        deleted = client.delete(f"/api/students/{student['id']}", headers=admin_headers)
        assert deleted.status_code == 200, deleted.text
        student_deleted = True

        report = _get_report(client, admin_headers, run_id)
        entry = next(
            (a for a in report["absent_students"] if a["student_name"] == f"IT Absent Kid {marker}"),
            None,
        )
        assert entry is not None, report["absent_students"]
        assert entry["student_id"] is None
        assert entry["reason"] == f"IT flu {marker}"
    finally:
        if run_id:
            client.post("/api/runs/driver/end", json={"run_id": run_id}, headers=driver_headers)
            client.delete(f"/api/runs/{run_id}", headers=admin_headers)
        if not student_deleted:
            _clear_absences_for(client, admin_headers, student["id"])
            client.delete(f"/api/students/{student['id']}", headers=admin_headers)


# Boarded recount ----------------------------------------------------------------

def test_students_boarded_recount_is_idempotent(client, admin_headers, driver_headers, no_active_run):
    """students_boarded is recounted from the run's run_stops roster on every
    tap — repeated taps never drift the counter, and driver un-boarding is
    rejected outright (R16, R30). end_run persists the final pre-sweep count."""
    context = client.get("/api/runs/driver/context", headers=driver_headers).json()
    morning = next(r for r in context["routes"] if r["type"] == "morning")

    run = client.post(
        "/api/runs/driver/start", json={"route_id": morning["id"]}, headers=driver_headers
    )
    assert run.status_code == 200, run.text
    run_id = run.json()["id"]
    try:
        # Arrive every stop so all students' stops are reachable for boarding.
        for _ in range(run.json()["total_stops"]):
            arrived = client.post(
                "/api/runs/driver/arrive", json={"run_id": run_id}, headers=driver_headers
            )
            assert arrived.status_code == 200, arrived.text

        ctx = client.get("/api/runs/driver/context", headers=driver_headers).json()
        student_ids = list({
            s["student_id"] for s in ctx["run_stops"] if s["student_id"] is not None
        })
        assert student_ids, "seeded morning run has no student stops"

        def board(student_id: str):
            response = client.post(
                "/api/runs/driver/boarding",
                json={"student_id": student_id, "on_bus": True},
                headers=driver_headers,
            )
            assert response.status_code == 200, response.text

        def boarded_count() -> int:
            return _get_report(client, admin_headers, run_id)["students_boarded"]

        # A repeated tap on the same student is a no-op, not an increment.
        board(student_ids[0])
        assert boarded_count() == 1
        board(student_ids[0])
        assert boarded_count() == 1

        expected = 1
        if len(student_ids) > 1:
            board(student_ids[1])
            assert boarded_count() == 2
            board(student_ids[1])
            assert boarded_count() == 2
            expected = 2

        # Driver un-boarding is disabled by design (R30): the endpoint rejects
        # on_bus=false and the counter stays put.
        rejected = client.post(
            "/api/runs/driver/boarding",
            json={"student_id": student_ids[0], "on_bus": False},
            headers=driver_headers,
        )
        assert rejected.status_code == 409, rejected.text
        assert boarded_count() == expected

        # end_run persists the final pre-sweep on-bus count for morning runs.
        ended = client.post("/api/runs/driver/end", json={"run_id": run_id}, headers=driver_headers)
        assert ended.status_code == 200, ended.text
        assert ended.json()["students_boarded"] == expected
        assert boarded_count() == expected
    finally:
        client.post("/api/runs/driver/end", json={"run_id": run_id}, headers=driver_headers)
        client.delete(f"/api/runs/{run_id}", headers=admin_headers)


# Report shapes ------------------------------------------------------------------

def test_report_for_routeless_run_is_empty_and_exact(client, admin_headers, driver_headers):
    """An admin-created run with no route reports zeros and an empty absence
    list without erroring — and never flags approximate (R16)."""
    created = client.post(
        "/api/runs", json={"type": "morning", "status": "completed"}, headers=admin_headers
    )
    assert created.status_code == 200, created.text
    run = created.json()
    try:
        report = _get_report(client, admin_headers, run["id"])
        assert report["id"] == run["id"]
        assert report["bus_name"] is None
        assert report["route_name"] is None
        assert report["driver_name"] is None
        assert report["students_boarded"] == 0
        assert report["total_students"] == 0
        assert report["absent_students"] == []
        assert report["approximate"] is False

        # The report is admin-only, like the rest of the run CRUD surface.
        fenced = client.get(f"/api/runs/{run['id']}/report", headers=driver_headers)
        assert fenced.status_code == 403, fenced.text
    finally:
        client.delete(f"/api/runs/{run['id']}", headers=admin_headers)


def test_report_missing_run_is_404(client, admin_headers):
    response = client.get(f"/api/runs/{uuid.uuid4()}/report", headers=admin_headers)
    assert response.status_code == 404, response.text


def test_legacy_run_falls_back_to_live_absences_flagged_approximate(client, admin_headers):
    """A run with a route but no snapshot and no run_stops (legacy or
    admin-created) reports today's live absences intersected with the route's
    membership, flagged approximate=true (R16)."""
    marker = uuid.uuid4().hex[:6]
    route = client.post(
        "/api/fleet/routes",
        json={"name": f"IT Legacy Route {marker}", "type": "morning"},
        headers=admin_headers,
    )
    assert route.status_code == 200, route.text
    route = route.json()
    student = client.post(
        "/api/students",
        json={"name": f"IT Legacy Kid {marker}", "parent_name": "IT Legacy Parent",
              "parent_phone": "+254711000043", "parent_email": f"it-legacy-{marker}@test.local",
              "route_ids": [route["id"]]},
        headers=admin_headers,
    ).json()
    run_id = None
    try:
        marked = client.post(
            "/api/students/absences",
            json={"student_id": student["id"], "reason": f"IT travel {marker}"},
            headers=admin_headers,
        )
        assert marked.status_code == 200, marked.text

        created = client.post(
            "/api/runs",
            json={"route_id": route["id"], "type": "morning", "status": "completed"},
            headers=admin_headers,
        )
        assert created.status_code == 200, created.text
        run_id = created.json()["id"]

        report = _get_report(client, admin_headers, run_id)
        assert report["approximate"] is True
        entry = next(
            (a for a in report["absent_students"] if a["student_id"] == student["id"]),
            None,
        )
        assert entry is not None, report["absent_students"]
        assert entry["student_name"] == f"IT Legacy Kid {marker}"
        assert entry["reason"] == f"IT travel {marker}"
    finally:
        if run_id:
            client.delete(f"/api/runs/{run_id}", headers=admin_headers)
        _clear_absences_for(client, admin_headers, student["id"])
        client.delete(f"/api/students/{student['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/routes/{route['id']}", headers=admin_headers)
