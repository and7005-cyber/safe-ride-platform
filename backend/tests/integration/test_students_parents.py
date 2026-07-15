"""Students/parents integration suite (U4: R7, R9–R13) against the local stack.

Run with the stack up (scripts/start-local.sh) and migration 007 applied:

    RUN_INTEGRATION=1 ../.venv/bin/python -m pytest tests/integration -q

Covers the two-parent payload invariant (≥1 phone, ≥1 email), email-driven
parent-account link sync (swap on change, drift preservation on unrelated
edits, signup backfill), the no-status-write update path, and the
reverse-geocode proxy. Everything created here is deleted afterwards.

Also covers the derived display_status on the admin students list
(ops-refinement U3: R1–R4, AE1): one test per CASE branch of the shared
derivation (app/dao/status_sql.py), each asserting parent/admin parity, plus
the admin-only 'unassigned' wrap for route-less students.
"""

import os
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import psycopg
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="needs the local stack; set RUN_INTEGRATION=1",
)

BASE = os.environ.get("INTEGRATION_API_URL", "http://localhost:9001")
# The local stack's Postgres, published by docker-compose.local.yml. Used only
# to stage states no API can produce (see force_student_status).
DB_URL = os.environ.get(
    "INTEGRATION_DB_URL", "postgresql://saferide:saferide@localhost:5432/saferide"
)

ADMIN = {"email": "admin@test.com", "password": "test1234."}
DRIVER_PIN = "0322"  # seeded driver Daniel Kamau — bus Simba, Express 1 routes

# Africa/Nairobi is UTC+3 year-round (no DST), so "today" is deterministic.
NAIROBI_OFFSET = timedelta(hours=3)


def nairobi_today() -> str:
    return (datetime.now(timezone.utc) + NAIROBI_OFFSET).date().isoformat()


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE, timeout=20) as c:
        yield c


def login(client: httpx.Client, email: str, password: str) -> dict:
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


@pytest.fixture(scope="module")
def admin_headers(client):
    return login(client, ADMIN["email"], ADMIN["password"])


def student_payload(marker: str, **overrides) -> dict:
    """A minimal payload satisfying the two-parent invariant (R9–R10)."""
    payload = {
        "name": f"IT Kid {marker}",
        "grade": "G4",
        "parent_name": f"IT Parent1 {marker}",
        "parent_phone": "+254711000001",
        "parent_email": f"it-p1-{marker}@test.local",
    }
    payload.update(overrides)
    return payload


def signup_parent(client, marker: str, tag: str) -> tuple[str, str, dict]:
    """Create a parent account; returns (parent_id, email, headers)."""
    email = f"it-{tag}-{marker}@test.local"
    response = client.post(
        "/api/auth/signup",
        json={"email": email, "password": "ParentPass1!", "full_name": f"IT {tag} {marker}",
              "role": "parent"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    return body["user"]["id"], email, {"Authorization": f"Bearer {body['token']}"}


def students_of_parent(client, admin_headers, email: str) -> list[str]:
    """Student names listed under a registered parent on the Parents page."""
    parents = client.get("/api/accounts/parents", headers=admin_headers).json()
    row = next(
        (p for p in parents
         if (p.get("email") or "").lower() == email.lower() and p["status"] == "registered"),
        None,
    )
    return list(row["students"]) if row else []


# Payload invariant (R9–R10) ----------------------------------------------------

def test_create_requires_at_least_one_email(client, admin_headers):
    marker = uuid.uuid4().hex[:6]
    response = client.post(
        "/api/students",
        json=student_payload(marker, parent_email=None),
        headers=admin_headers,
    )
    assert response.status_code == 400
    assert "email" in response.json()["detail"].lower()


def test_create_requires_at_least_one_phone(client, admin_headers):
    marker = uuid.uuid4().hex[:6]
    response = client.post(
        "/api/students",
        json=student_payload(marker, parent_phone=None),
        headers=admin_headers,
    )
    assert response.status_code == 400
    assert "phone" in response.json()["detail"].lower()


def test_create_requires_parent_name(client, admin_headers):
    marker = uuid.uuid4().hex[:6]
    response = client.post(
        "/api/students",
        json=student_payload(marker, parent_name="  "),
        headers=admin_headers,
    )
    assert response.status_code == 400
    assert "parent 1 name" in response.json()["detail"].lower()


def test_create_accepts_contacts_in_parent2_slots_only(client, admin_headers):
    marker = uuid.uuid4().hex[:6]
    created = client.post(
        "/api/students",
        json=student_payload(
            marker,
            parent_phone=None, parent_email=None,
            parent2_name=f"IT Parent2 {marker}",
            parent_phone2="0712345679",
            parent2_email=f"it-p2-{marker}@test.local",
        ),
        headers=admin_headers,
    )
    assert created.status_code == 200, created.text
    student = created.json()
    try:
        assert student["parent2_name"] == f"IT Parent2 {marker}"
        assert student["parent_phone2"] == "+254712345679"
        assert student["parent2_email"] == f"it-p2-{marker}@test.local"
    finally:
        client.delete(f"/api/students/{student['id']}", headers=admin_headers)


def test_update_enforces_the_invariant_too(client, admin_headers):
    marker = uuid.uuid4().hex[:6]
    student = client.post(
        "/api/students", json=student_payload(marker), headers=admin_headers
    ).json()
    try:
        stripped = client.put(
            f"/api/students/{student['id']}",
            json=student_payload(marker, parent_email=None, parent2_email=None),
            headers=admin_headers,
        )
        assert stripped.status_code == 400
        assert "email" in stripped.json()["detail"].lower()
    finally:
        client.delete(f"/api/students/{student['id']}", headers=admin_headers)


def test_bulk_row_missing_emails_errors_that_row_only(client, admin_headers):
    marker = uuid.uuid4().hex[:6]
    good_name = f"IT BulkGood {marker}"
    bad_name = f"IT BulkBad {marker}"
    response = client.post(
        "/api/students/bulk",
        json={"students": [
            {"name": good_name, "grade": "G1", "parent_name": "Bulk Parent",
             "parent_phone": "+254711000002", "parent_email": f"it-bulk-{marker}@test.local"},
            {"name": bad_name, "grade": "G1", "parent_name": "Bulk Parent",
             "parent_phone": "+254711000003"},
        ]},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    try:
        assert body["inserted"] == 1
        assert len(body["errors"]) == 1
        assert bad_name in body["errors"][0]
        assert "email" in body["errors"][0].lower()
    finally:
        for s in client.get("/api/students", headers=admin_headers).json():
            if s["name"] in (good_name, bad_name):
                client.delete(f"/api/students/{s['id']}", headers=admin_headers)


# Link sync (R11) ----------------------------------------------------------------

def test_email_change_swaps_the_link(client, admin_headers):
    marker = uuid.uuid4().hex[:6]
    parent_a, email_a, _ = signup_parent(client, marker, "swap-a")
    parent_b, email_b, _ = signup_parent(client, marker, "swap-b")
    student = client.post(
        "/api/students",
        json=student_payload(marker, parent_email=email_a),
        headers=admin_headers,
    ).json()
    try:
        assert student["name"] in students_of_parent(client, admin_headers, email_a)

        updated = client.put(
            f"/api/students/{student['id']}",
            json=student_payload(marker, parent_email=email_b),
            headers=admin_headers,
        )
        assert updated.status_code == 200, updated.text
        assert student["name"] in students_of_parent(client, admin_headers, email_b)
        assert student["name"] not in students_of_parent(client, admin_headers, email_a)
    finally:
        client.delete(f"/api/students/{student['id']}", headers=admin_headers)
        client.delete(f"/api/accounts/parents/{parent_a}", headers=admin_headers)
        client.delete(f"/api/accounts/parents/{parent_b}", headers=admin_headers)


def test_unrelated_edit_preserves_drifted_link(client, admin_headers):
    """Renaming the account's email drifts it away from the student's slots; a
    later grade-only edit (email slots untouched) must not sever the link."""
    marker = uuid.uuid4().hex[:6]
    parent_id, email, _ = signup_parent(client, marker, "drift")
    renamed_email = f"it-drift-renamed-{marker}@test.local"
    student = client.post(
        "/api/students",
        json=student_payload(marker, parent_email=email),
        headers=admin_headers,
    ).json()
    try:
        assert student["name"] in students_of_parent(client, admin_headers, email)

        renamed = client.put(
            f"/api/accounts/parents/{parent_id}",
            json={"full_name": f"IT drift {marker}", "email": renamed_email, "phone": None},
            headers=admin_headers,
        )
        assert renamed.status_code == 200, renamed.text

        # Unrelated edit: grade changes, both email slots stay as they were.
        edited = client.put(
            f"/api/students/{student['id']}",
            json=student_payload(marker, parent_email=email, grade="G5"),
            headers=admin_headers,
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["grade"] == "G5"
        assert student["name"] in students_of_parent(client, admin_headers, renamed_email)
    finally:
        client.delete(f"/api/students/{student['id']}", headers=admin_headers)
        client.delete(f"/api/accounts/parents/{parent_id}", headers=admin_headers)


def test_same_email_in_both_slots_links_once(client, admin_headers):
    marker = uuid.uuid4().hex[:6]
    parent_id, email, _ = signup_parent(client, marker, "once")
    student = client.post(
        "/api/students",
        json=student_payload(marker, parent_email=email, parent2_email=email),
        headers=admin_headers,
    ).json()
    try:
        names = students_of_parent(client, admin_headers, email)
        assert names.count(student["name"]) == 1
    finally:
        client.delete(f"/api/students/{student['id']}", headers=admin_headers)
        client.delete(f"/api/accounts/parents/{parent_id}", headers=admin_headers)


def test_signup_backfills_links_for_pending_parent(client, admin_headers):
    marker = uuid.uuid4().hex[:6]
    pending_email = f"it-pending-{marker}@test.local"
    student = client.post(
        "/api/students",
        json=student_payload(marker, parent2_email=pending_email),
        headers=admin_headers,
    ).json()
    parent_id = None
    try:
        # Unregistered second-slot email shows as a pending parent (R11/R13).
        parents = client.get("/api/accounts/parents", headers=admin_headers).json()
        pending = next(
            (p for p in parents
             if (p.get("email") or "").lower() == pending_email and p["status"] == "pending"),
            None,
        )
        assert pending is not None
        assert student["name"] in pending["students"]

        parent_id, _, parent_headers = signup_parent(client, marker, "pending")
        # signup_parent builds "it-pending-{marker}@test.local" — same email.
        assert student["name"] in students_of_parent(client, admin_headers, pending_email)

        children = client.get("/api/parent-portal/children", headers=parent_headers).json()
        assert student["name"] in {c["name"] for c in children}
    finally:
        client.delete(f"/api/students/{student['id']}", headers=admin_headers)
        if parent_id:
            client.delete(f"/api/accounts/parents/{parent_id}", headers=admin_headers)


# Status is never written by admin edits (R7) -------------------------------------

def test_put_with_status_does_not_change_live_status(client, admin_headers):
    marker = uuid.uuid4().hex[:6]
    student = client.post(
        "/api/students", json=student_payload(marker), headers=admin_headers
    ).json()
    try:
        assert student["status"] == "at-school"
        updated = client.put(
            f"/api/students/{student['id']}",
            json=student_payload(marker, status="on-bus", grade="G6"),
            headers=admin_headers,
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["status"] == "at-school"  # payload status ignored
        assert updated.json()["grade"] == "G6"  # the rest of the edit landed

        listed = next(
            s for s in client.get("/api/students", headers=admin_headers).json()
            if s["id"] == student["id"]
        )
        assert listed["status"] == "at-school"
    finally:
        client.delete(f"/api/students/{student['id']}", headers=admin_headers)


# Reverse geocoding proxy (R8) -----------------------------------------------------

def test_reverse_geocode_endpoint(client, admin_headers):
    response = client.post(
        "/api/fleet/reverse-geocode",
        json={"lat": -1.286389, "lng": 36.817223},  # Nairobi CBD
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body["found"], bool)
    if body["found"]:  # key configured: a label comes back
        assert body["label"]


def test_reverse_geocode_requires_admin(client):
    response = client.post(
        "/api/fleet/reverse-geocode", json={"lat": -1.29, "lng": 36.82}
    )
    assert response.status_code == 401


# Derived display_status on the admin list (ops-refinement U3: R1–R4, AE1) --------
#
# One test per CASE branch of the shared derivation (app/dao/status_sql.py),
# each asserting parent/admin parity through assert_display_parity. The
# admin-only 'unassigned' wrap is asserted separately (it is the one intended
# divergence between the two surfaces).

U3_HOME = {
    "home_address": "IT Status Lane, Nairobi",
    "home_lat": -1.2921,
    "home_lng": 36.8219,
    # Earlier than every seeded pickup (06:40+): the IT student's stop sorts
    # first on the morning route, so a single arrive makes boarding legal.
    "pickup_time": "06:00",
}


@pytest.fixture(scope="module")
def driver_headers(client):
    response = client.post("/api/auth/pin-login", json={"pin": DRIVER_PIN})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def run_morning_and_end(client, driver_headers) -> None:
    """Start and immediately end a morning run: the end-run sweep normalizes
    the seeded roster back to at-school after afternoon staging left students
    on-bus or dropped-off (same helper as test_parent_feeds)."""
    context = client.get("/api/runs/driver/context", headers=driver_headers).json()
    morning = next(r for r in context["routes"] if r["type"] == "morning")
    started = client.post(
        "/api/runs/driver/start", json={"route_id": morning["id"]}, headers=driver_headers
    )
    if started.status_code == 200:
        client.post(
            "/api/runs/driver/end", json={"run_id": started.json()["id"]}, headers=driver_headers
        )


@pytest.fixture()
def clean_run_slate(client, admin_headers, driver_headers):
    """A known-clean run slate around each run-lifecycle test: end the
    driver's active run and delete today's runs for their bus (a route runs
    once per day, so leftovers gate later starts). Teardown also runs a
    morning start+end cycle so seeded roster statuses disturbed by the
    afternoon staging in these tests return to at-school (mirrors
    test_parent_feeds.no_runs_today)."""

    def sweep():
        context = client.get("/api/runs/driver/context", headers=driver_headers).json()
        active = context.get("active_run")
        if active:
            client.post(
                "/api/runs/driver/end", headers=driver_headers, json={"run_id": active["id"]}
            )
        bus = context.get("bus") or {}
        today = nairobi_today()
        for run in client.get("/api/runs", headers=admin_headers).json():
            if str(run.get("bus_id")) == str(bus.get("id")) and str(run.get("date")) == today:
                client.delete(f"/api/runs/{run['id']}", headers=admin_headers)

    sweep()
    yield
    sweep()
    run_morning_and_end(client, driver_headers)
    sweep()


def driver_route(client, driver_headers, run_type: str) -> dict:
    context = client.get("/api/runs/driver/context", headers=driver_headers).json()
    return next(r for r in context["routes"] if r["type"] == run_type)


def create_linked_student(client, admin_headers, marker: str, route_ids=None, **overrides):
    """An IT student linked to a fresh IT parent account; returns
    (student, parent_id, parent_email, parent_headers). The caller deletes
    the student and the parent account in a finally block."""
    parent_id, email, parent_headers = signup_parent(client, marker, "u3")
    created = client.post(
        "/api/students",
        json=student_payload(
            marker, parent_email=email, route_ids=[str(r) for r in (route_ids or [])],
            **overrides,
        ),
        headers=admin_headers,
    )
    assert created.status_code == 200, created.text
    return created.json(), parent_id, email, parent_headers


def admin_row(client, admin_headers, student_id: str) -> dict:
    return next(
        s for s in client.get("/api/students", headers=admin_headers).json()
        if str(s["id"]) == str(student_id)
    )


def parent_row(client, parent_headers, student_id: str) -> dict:
    return next(
        c for c in client.get("/api/parent-portal/children", headers=parent_headers).json()
        if str(c["id"]) == str(student_id)
    )


def assert_display_parity(
    client, parent_headers, admin_headers, student_id: str, expected: str, raw: str
) -> None:
    """Both surfaces derive the same display_status (the shared fragment) and
    both keep the raw stored status untouched in the payload."""
    parent = parent_row(client, parent_headers, student_id)
    admin = admin_row(client, admin_headers, student_id)
    assert (parent["display_status"], admin["display_status"]) == (expected, expected)
    assert (parent["status"], admin["status"]) == (raw, raw)


def force_student_status(student_id: str, status: str) -> None:
    """Stage a raw status no API can produce. Every HTTP writer of 'absent'
    also writes today's absence row (admin mark, driver /driver/absent), and
    every row-clearing path resets the status, so a stale 'absent' — the
    state a previous day's absence leaves behind — is only reachable by SQL
    against the stack's published Postgres port."""
    with psycopg.connect(DB_URL, autocommit=True) as conn:
        updated = conn.execute(
            "update live_students set status = %s where id = %s", (status, student_id)
        )
        assert updated.rowcount == 1, f"student {student_id} not staged"


def test_route_less_student_shows_unassigned_on_admin_list_only(
    client, admin_headers, driver_headers
):
    """Covers AE1 (R1, R3): a student with zero route assignments displays
    'unassigned' on the admin list, overriding the stored status; assigning a
    route makes the live status appear. The wrap is admin-side only — the
    parent portal keeps the shared derivation."""
    marker = uuid.uuid4().hex[:6]
    student, parent_id, email, parent_headers = create_linked_student(
        client, admin_headers, marker, **U3_HOME
    )
    try:
        listed = admin_row(client, admin_headers, student["id"])
        assert listed["display_status"] == "unassigned"
        assert listed["status"] == "at-school"  # raw status stays in the payload
        assert parent_row(client, parent_headers, student["id"])["display_status"] == "at-school"

        morning = driver_route(client, driver_headers, "morning")
        updated = client.put(
            f"/api/students/{student['id']}",
            json=student_payload(
                marker, parent_email=email, route_ids=[str(morning["id"])], **U3_HOME
            ),
            headers=admin_headers,
        )
        assert updated.status_code == 200, updated.text
        assert_display_parity(
            client, parent_headers, admin_headers, student["id"],
            expected="at-school", raw="at-school",
        )
    finally:
        client.delete(f"/api/students/{student['id']}", headers=admin_headers)
        client.delete(f"/api/accounts/parents/{parent_id}", headers=admin_headers)


def test_on_bus_on_active_run_today_shows_on_bus(
    client, admin_headers, driver_headers, clean_run_slate
):
    """R2: 'on-bus' is trusted while an active run today carries the student
    in run_stops. Afternoon runs auto-board their roster at start."""
    marker = uuid.uuid4().hex[:6]
    afternoon = driver_route(client, driver_headers, "afternoon")
    student, parent_id, _, parent_headers = create_linked_student(
        client, admin_headers, marker, route_ids=[afternoon["id"]], **U3_HOME
    )
    run_id = None
    try:
        started = client.post(
            "/api/runs/driver/start", json={"route_id": afternoon["id"]}, headers=driver_headers
        )
        assert started.status_code == 200, started.text
        run_id = started.json()["id"]

        assert_display_parity(
            client, parent_headers, admin_headers, student["id"],
            expected="on-bus", raw="on-bus",
        )
    finally:
        if run_id:
            client.post("/api/runs/driver/end", json={"run_id": run_id}, headers=driver_headers)
        client.delete(f"/api/students/{student['id']}", headers=admin_headers)
        client.delete(f"/api/accounts/parents/{parent_id}", headers=admin_headers)


def test_today_absence_overrides_stored_on_bus_to_absent(
    client, admin_headers, driver_headers, clean_run_slate
):
    """R2: a today-absence (marked via the admin endpoint — date defaults to
    today; scope defaults to 'day' once U4 lands) overrides everything, even
    a live 'on-bus' written after the mark when the child boards after all."""
    marker = uuid.uuid4().hex[:6]
    morning = driver_route(client, driver_headers, "morning")
    student, parent_id, _, parent_headers = create_linked_student(
        client, admin_headers, marker, route_ids=[morning["id"]], **U3_HOME
    )
    run_id = None
    absence_id = None
    try:
        started = client.post(
            "/api/runs/driver/start", json={"route_id": morning["id"]}, headers=driver_headers
        )
        assert started.status_code == 200, started.text
        run_id = started.json()["id"]
        # The 06:00 pickup makes the IT student's stop the first one: a single
        # arrive reaches it, so boarding becomes legal.
        arrived = client.post(
            "/api/runs/driver/arrive", json={"run_id": run_id}, headers=driver_headers
        )
        assert arrived.status_code == 200, arrived.text

        marked = client.post(
            "/api/students/absences", json={"student_id": student["id"]}, headers=admin_headers
        )
        assert marked.status_code == 200, marked.text
        absence_id = marked.json()["id"]

        # The child shows up after all and the driver boards them: the raw
        # status flips back to 'on-bus' while today's absence row stands.
        boarded = client.post(
            "/api/runs/driver/boarding",
            json={"student_id": student["id"], "on_bus": True},
            headers=driver_headers,
        )
        assert boarded.status_code == 200, boarded.text

        assert_display_parity(
            client, parent_headers, admin_headers, student["id"],
            expected="absent", raw="on-bus",
        )
    finally:
        if run_id:  # end before clearing: clearing mid-run 409s ("End the run first")
            client.post("/api/runs/driver/end", json={"run_id": run_id}, headers=driver_headers)
        if absence_id:
            client.delete(f"/api/students/absences/{absence_id}", headers=admin_headers)
        client.delete(f"/api/students/{student['id']}", headers=admin_headers)
        client.delete(f"/api/accounts/parents/{parent_id}", headers=admin_headers)


def test_stale_on_bus_decays_to_at_home(client, admin_headers, driver_headers, clean_run_slate):
    """R2: raw 'on-bus' is only trusted while a non-completed run today
    carries the student. Completing the run via admin PUT (which, unlike the
    driver's end-run, sweeps nothing) leaves the stale on-bus behind."""
    marker = uuid.uuid4().hex[:6]
    afternoon = driver_route(client, driver_headers, "afternoon")
    student, parent_id, _, parent_headers = create_linked_student(
        client, admin_headers, marker, route_ids=[afternoon["id"]], **U3_HOME
    )
    try:
        started = client.post(
            "/api/runs/driver/start", json={"route_id": afternoon["id"]}, headers=driver_headers
        )
        assert started.status_code == 200, started.text
        run = started.json()

        completed = client.put(
            f"/api/runs/{run['id']}",
            json={
                "bus_id": run["bus_id"],
                "route_id": run["route_id"],
                "type": run["type"],
                "date": str(run["date"]),
                "start_time": run["start_time"],
                "end_time": run["end_time"],
                "status": "completed",
                "total_stops": run["total_stops"],
                "stops_completed": run["stops_completed"],
                "total_students": run["total_students"],
                "students_boarded": run["students_boarded"],
                "incidents": run["incidents"],
            },
            headers=admin_headers,
        )
        assert completed.status_code == 200, completed.text
        assert completed.json()["status"] == "completed"

        assert_display_parity(
            client, parent_headers, admin_headers, student["id"],
            expected="at-home", raw="on-bus",
        )
    finally:
        # clean_run_slate's teardown deletes the completed run and restores
        # the seeded roster statuses via a morning start+end cycle.
        client.delete(f"/api/students/{student['id']}", headers=admin_headers)
        client.delete(f"/api/accounts/parents/{parent_id}", headers=admin_headers)


def test_stale_dropped_off_decays_to_at_home(
    client, admin_headers, driver_headers, clean_run_slate
):
    """R2: 'dropped-off' is only trusted while an afternoon run today contains
    the student; once the completed run is deleted the badge decays to
    at-home while the raw status is never rewritten by the read."""
    marker = uuid.uuid4().hex[:6]
    afternoon = driver_route(client, driver_headers, "afternoon")
    student, parent_id, _, parent_headers = create_linked_student(
        client, admin_headers, marker, route_ids=[afternoon["id"]], **U3_HOME
    )
    run_id = None
    try:
        started = client.post(
            "/api/runs/driver/start", json={"route_id": afternoon["id"]}, headers=driver_headers
        )
        assert started.status_code == 200, started.text
        run_id = started.json()["id"]
        ended = client.post(
            "/api/runs/driver/end", json={"run_id": run_id}, headers=driver_headers
        )
        assert ended.status_code == 200, ended.text

        # A completed afternoon run today still contains the student:
        # dropped-off is trusted (the else branch passes the raw through).
        assert_display_parity(
            client, parent_headers, admin_headers, student["id"],
            expected="dropped-off", raw="dropped-off",
        )

        deleted = client.delete(f"/api/runs/{run_id}", headers=admin_headers)
        assert deleted.status_code == 200, deleted.text

        assert_display_parity(
            client, parent_headers, admin_headers, student["id"],
            expected="at-home", raw="dropped-off",
        )
    finally:
        client.delete(f"/api/students/{student['id']}", headers=admin_headers)
        client.delete(f"/api/accounts/parents/{parent_id}", headers=admin_headers)


def test_stale_absent_decays_to_at_home(client, admin_headers, driver_headers):
    """R2: raw 'absent' with no today-absence row displays at-home. Staged by
    SQL (force_student_status): every API writer of 'absent' also writes
    today's absence row and every clear path resets the status, so the stale
    state — what a previous day's absence leaves behind — cannot be produced
    over HTTP (test_parent_feeds documents the same fixture gap). The student
    sits on a route so the admin 'unassigned' wrap cannot mask the branch,
    and no run is started so nothing heals the status."""
    marker = uuid.uuid4().hex[:6]
    morning = driver_route(client, driver_headers, "morning")
    student, parent_id, _, parent_headers = create_linked_student(
        client, admin_headers, marker, route_ids=[morning["id"]], **U3_HOME
    )
    try:
        force_student_status(student["id"], "absent")

        assert_display_parity(
            client, parent_headers, admin_headers, student["id"],
            expected="at-home", raw="absent",
        )
    finally:
        client.delete(f"/api/students/{student['id']}", headers=admin_headers)
        client.delete(f"/api/accounts/parents/{parent_id}", headers=admin_headers)


# One-per-type allocation (U5, R21-R23) --------------------------------------------

def _u5_school_and_route_factory(client, admin_headers, marker):
    school = client.post(
        "/api/fleet/schools",
        json={"name": f"IT U5 School {marker}", "lat": -1.30, "lng": 36.82},
        headers=admin_headers,
    ).json()

    def make_route(name, rtype):
        r = client.post(
            "/api/fleet/routes",
            json={"name": f"IT U5 {name} {marker}", "type": rtype, "school_id": school["id"]},
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text
        return r.json()

    return school, make_route


def _route_ids_of(client, admin_headers, student_id):
    for s in client.get("/api/students", headers=admin_headers).json():
        if s["id"] == student_id:
            return [str(r) for r in (s.get("route_ids") or [])]
    return None


def test_same_period_move_is_not_a_409(client, admin_headers):
    """U5/R22: moving a student from one morning route to another (delete-before
    -insert) succeeds — the deferrable backstop is never tripped by the move."""
    marker = uuid.uuid4().hex[:6]
    school, make_route = _u5_school_and_route_factory(client, admin_headers, marker)
    m_a, m_b = make_route("MorningA", "morning"), make_route("MorningB", "morning")
    created = []
    try:
        s = client.post(
            "/api/students", json=student_payload(marker, route_ids=[m_a["id"]]), headers=admin_headers
        )
        assert s.status_code == 200, s.text
        student = s.json()
        created.append(student)
        upd = client.put(
            f"/api/students/{student['id']}",
            json=student_payload(marker, route_ids=[m_b["id"]]),
            headers=admin_headers,
        )
        assert upd.status_code == 200, upd.text  # a move, not a conflict
        assert _route_ids_of(client, admin_headers, student["id"]) == [str(m_b["id"])]
    finally:
        for st in created:
            client.delete(f"/api/students/{st['id']}", headers=admin_headers)
        for r in (m_a, m_b):
            client.delete(f"/api/fleet/routes/{r['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/schools/{school['id']}", headers=admin_headers)


def test_two_same_period_routes_in_one_payload_is_a_friendly_409(client, admin_headers):
    """U5/R21: a payload naming two morning routes is refused with a friendly
    409, not a raw deferred-constraint 500."""
    marker = uuid.uuid4().hex[:6]
    school, make_route = _u5_school_and_route_factory(client, admin_headers, marker)
    m_a, m_b = make_route("MorningA", "morning"), make_route("MorningB", "morning")
    try:
        s = client.post(
            "/api/students",
            json=student_payload(marker, route_ids=[m_a["id"], m_b["id"]]),
            headers=admin_headers,
        )
        assert s.status_code == 409, s.text
    finally:
        for r in (m_a, m_b):
            client.delete(f"/api/fleet/routes/{r['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/schools/{school['id']}", headers=admin_headers)


def test_one_morning_and_one_afternoon_is_allowed(client, admin_headers):
    """U5/R23: the constraint is per-period — a student may hold one morning AND
    one afternoon route at once."""
    marker = uuid.uuid4().hex[:6]
    school, make_route = _u5_school_and_route_factory(client, admin_headers, marker)
    m, a = make_route("Morning", "morning"), make_route("Afternoon", "afternoon")
    created = []
    try:
        s = client.post(
            "/api/students",
            json=student_payload(marker, route_ids=[m["id"], a["id"]]),
            headers=admin_headers,
        )
        assert s.status_code == 200, s.text
        created.append(s.json())
        assert set(_route_ids_of(client, admin_headers, s.json()["id"])) == {str(m["id"]), str(a["id"])}
    finally:
        for st in created:
            client.delete(f"/api/students/{st['id']}", headers=admin_headers)
        for r in (m, a):
            client.delete(f"/api/fleet/routes/{r['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/schools/{school['id']}", headers=admin_headers)


def test_home_provenance_round_trips_on_create(client, admin_headers):
    """U4/R11: the student home provenance (PlacePicker sends 'picked' for a
    deliberate pin) is stored and read back."""
    marker = uuid.uuid4().hex[:6]
    created = []
    try:
        s = client.post(
            "/api/students",
            json=student_payload(
                marker, home_address="Pin Lane", home_lat=-1.3, home_lng=36.8, provenance="picked"
            ),
            headers=admin_headers,
        )
        assert s.status_code == 200, s.text
        created.append(s.json())
        row = next(x for x in client.get("/api/students", headers=admin_headers).json()
                   if x["id"] == s.json()["id"])
        assert row["provenance"] == "picked"
    finally:
        for st in created:
            client.delete(f"/api/students/{st['id']}", headers=admin_headers)


def test_route_type_flip_cascades_to_links_and_frees_the_period(client, admin_headers):
    """U2/U5 System-Wide Impact: flipping a route's type cascades route_type to
    its student links (the AFTER UPDATE trigger), so the student's morning slot
    is freed and a different morning route can be added without a phantom 409."""
    marker = uuid.uuid4().hex[:6]
    school, make_route = _u5_school_and_route_factory(client, admin_headers, marker)
    m_a, m_b = make_route("MorningA", "morning"), make_route("MorningB", "morning")
    created = []
    try:
        s = client.post(
            "/api/students", json=student_payload(marker, route_ids=[m_a["id"]]), headers=admin_headers
        )
        assert s.status_code == 200, s.text
        student = s.json()
        created.append(student)

        flip = client.put(
            f"/api/fleet/routes/{m_a['id']}",
            json={"name": f"IT U5 MorningA {marker}", "type": "afternoon", "school_id": school["id"]},
            headers=admin_headers,
        )
        assert flip.status_code == 200, flip.text

        with psycopg.connect(DB_URL) as conn:
            rt = conn.execute(
                "select route_type from live_student_routes where student_id=%s and route_id=%s",
                (student["id"], m_a["id"]),
            ).fetchone()
            assert rt[0] == "afternoon"  # cascaded from the route type-flip

        # The morning slot is now free -> adding morning-B alongside is allowed.
        upd = client.put(
            f"/api/students/{student['id']}",
            json=student_payload(marker, route_ids=[m_a["id"], m_b["id"]]),
            headers=admin_headers,
        )
        assert upd.status_code == 200, upd.text  # m_a is afternoon, m_b morning -> one each
    finally:
        for st in created:
            client.delete(f"/api/students/{st['id']}", headers=admin_headers)
        for r in (m_a, m_b):
            client.delete(f"/api/fleet/routes/{r['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/schools/{school['id']}", headers=admin_headers)


# CSV geocode triage + route_name wiring (U8, R15-R18) ---------------------------

def test_bulk_validate_triages_rows_and_resolves_route_name(client, admin_headers):
    """U8/R15-R16: /bulk/validate geocodes every row and tags it resolved (coords
    supplied) / failed (no coords, key-less container can't geocode), and reports
    whether route_name resolves — WITHOUT inserting anything."""
    marker = uuid.uuid4().hex[:6]
    school, make_route = _u5_school_and_route_factory(client, admin_headers, marker)
    route = make_route("Express", "morning")
    try:
        payload = {"students": [
            {"name": f"Coords {marker}", "grade": "G4", "parent_name": "P",
             "parent_phone": "+254711000010", "parent_email": f"c-{marker}@t.local",
             "home_lat": -1.3, "home_lng": 36.8, "route_name": f"IT U5 Express {marker}"},
            {"name": f"NoCoords {marker}", "grade": "G4", "parent_name": "P",
             "parent_phone": "+254711000011", "parent_email": f"n-{marker}@t.local",
             "home_address": "Somewhere Unresolvable", "route_name": "no-such-route"},
        ]}
        r = client.post("/api/students/bulk/validate", json=payload, headers=admin_headers)
        assert r.status_code == 200, r.text
        rows = r.json()["rows"]
        assert rows[0]["status"] == "resolved" and rows[0]["route_found"] is True
        assert rows[1]["status"] == "failed" and rows[1]["route_found"] is False
        # Nothing was inserted by validation.
        names = {s["name"] for s in client.get("/api/students", headers=admin_headers).json()}
        assert f"Coords {marker}" not in names
    finally:
        client.delete(f"/api/fleet/routes/{route['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/schools/{school['id']}", headers=admin_headers)


def test_bulk_route_name_assigns_students_to_the_route(client, admin_headers):
    """U8/R18: a bulk row's route_name assigns the student to that route through
    the same _sync_routes choke point (the R21 constraint applies)."""
    marker = uuid.uuid4().hex[:6]
    school, make_route = _u5_school_and_route_factory(client, admin_headers, marker)
    route = make_route("Express", "morning")
    route_name = f"IT U5 Express {marker}"
    created_names = [f"Bulk A {marker}", f"Bulk B {marker}"]
    try:
        payload = {"students": [
            {"name": n, "grade": "G4", "parent_name": "P", "parent_phone": "+254711000012",
             "parent_email": f"{n.replace(' ', '')}@t.local", "home_lat": -1.3, "home_lng": 36.8,
             "route_name": route_name}
            for n in created_names
        ]}
        r = client.post("/api/students/bulk", json=payload, headers=admin_headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["inserted"] == 2 and body["routeAssignments"] == 2
        rostered = {s["name"] for s in client.get("/api/students", headers=admin_headers).json()
                    if str(route["id"]) in [str(x) for x in (s.get("route_ids") or [])]}
        assert set(created_names) <= rostered
    finally:
        for s in client.get("/api/students", headers=admin_headers).json():
            if s["name"] in created_names:
                client.delete(f"/api/students/{s['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/routes/{route['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/schools/{school['id']}", headers=admin_headers)


def test_bulk_link_regenerates_each_route_exactly_once(client, admin_headers, monkeypatch):
    """U8: the burst guard — regeneration is batched per route, so N students
    onto one route trigger ONE regenerate, not N (a per-row O(rows) Google burst
    in one Lambda invocation is the verified risk)."""
    from app.dao import student_live_dao

    marker = uuid.uuid4().hex[:6]
    school, make_route = _u5_school_and_route_factory(client, admin_headers, marker)
    route = make_route("Express", "morning")
    ids = []
    try:
        for i in range(4):
            s = client.post("/api/students", json=student_payload(f"{marker}{i}"), headers=admin_headers)
            ids.append(s.json()["id"])
        calls = []
        monkeypatch.setattr(student_live_dao, "regenerate_route_stops",
                            lambda conn, rid: calls.append(str(rid)) or True)
        monkeypatch.setattr(student_live_dao, "_derive_student_bus", lambda conn, sid: None)
        # Pass an explicit localhost connection (the host can't resolve the
        # container's 'db' hostname); roll back so the test links don't persist.
        with psycopg.connect(DB_URL) as conn:
            student_live_dao._bulk_link_and_regenerate(conn, [(sid, route["id"]) for sid in ids])
            conn.rollback()
        # 4 students onto one route -> exactly ONE regeneration, independent of count.
        assert calls.count(str(route["id"])) == 1
        assert len(calls) == 1
    finally:
        for sid in ids:
            client.delete(f"/api/students/{sid}", headers=admin_headers)
        client.delete(f"/api/fleet/routes/{route['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/schools/{school['id']}", headers=admin_headers)
