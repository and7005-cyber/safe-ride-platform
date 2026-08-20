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

from conftest import purge_run

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
    # The bulk payload is school-scoped since U10: every committed row is
    # stamped with the school so the draft basis and pin map can see it.
    school = client.post(
        "/api/fleet/schools",
        json={"name": f"IT BulkSchool {marker}", "lat": -1.30, "lng": 36.80},
        headers=admin_headers,
    ).json()
    try:
        response = client.post(
            "/api/students/bulk",
            json={"school_id": school["id"], "students": [
                {"name": good_name, "grade": "G1", "parent_name": "Bulk Parent",
                 "parent_phone": "+254711000002", "parent_email": f"it-bulk-{marker}@test.local"},
                {"name": bad_name, "grade": "G1", "parent_name": "Bulk Parent",
                 "parent_phone": "+254711000003"},
            ]},
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["inserted"] == 1
        assert len(body["errors"]) == 1
        assert bad_name in body["errors"][0]
        assert "email" in body["errors"][0].lower()
    finally:
        for s in client.get("/api/students", headers=admin_headers).json():
            if s["name"] in (good_name, bad_name):
                client.delete(f"/api/students/{s['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/schools/{school['id']}", headers=admin_headers)


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
    """No-op since U3/U4.

    This used to start and immediately end a morning run so the end-of-run
    sweep would normalise seeded roster statuses back to at-school after
    afternoon staging. Nothing derives from that column any more, and the sweep
    itself is gone — the closure gate replaced it. Kept as a no-op so the
    fixtures that call it read unchanged.
    """
    return None


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
        # No end-run here since U4: the gate refuses to close a run with
        # unaccounted children, and this sweep only needs the run gone. Deleting
        # it below is the admin recovery path and does the job.
        bus = context.get("bus") or {}
        today = nairobi_today()
        for run in client.get("/api/runs", headers=admin_headers).json():
            if str(run.get("bus_id")) == str(bus.get("id")) and str(run.get("date")) == today:
                purge_run(run['id'])

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


def complete_run(client, driver_headers, run_id: str):
    """End a run the way a driver must since U4: account for everyone first.

    The closure gate refuses to complete a run while any roster child has no
    recorded outcome — that is what replaced the end-of-run sweep, which used to
    assert 'at school' for children nobody boarded. Tests that want a completed
    run now walk the same path a driver walks: arrive at the stops, board or
    confirm each child, then end.

    Children already covered by an absence are skipped — they are accounted for.
    Returns the end-run response so callers can assert on it.
    """
    context = client.get("/api/runs/driver/context", headers=driver_headers).json()
    run = context.get("active_run") or {}
    for _ in range(len(context.get("run_stops", []))):
        client.post("/api/runs/driver/arrive", json={"run_id": run_id}, headers=driver_headers)

    context = client.get("/api/runs/driver/context", headers=driver_headers).json()
    for student in context.get("students", []):
        if student.get("absent"):
            continue
        if run.get("type") == "afternoon":
            client.post("/api/runs/driver/dropoff",
                        json={"student_id": student["id"]}, headers=driver_headers)
        else:
            client.post("/api/runs/driver/boarding",
                        json={"student_id": student["id"], "on_bus": True},
                        headers=driver_headers)
    return client.post("/api/runs/driver/end", json={"run_id": run_id}, headers=driver_headers)


def force_run_completed(run_id: str) -> None:
    """Stage a completed run that recorded no outcomes — no API can produce one.

    Since U7 the only two completion paths both say something about the
    children: the driver's end refuses until every one is accounted for, and
    the office force-close records the rest as unaccounted. The admin PUT that
    used to complete a run while sweeping nothing is gone, which is the point —
    so the stale state it left behind is now reachable only by SQL.
    """
    with psycopg.connect(DB_URL, autocommit=True) as conn:
        updated = conn.execute(
            "update live_runs set status = 'completed' where id = %s", (run_id,)
        )
        assert updated.rowcount == 1, f"run {run_id} not staged"


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
        # U3: the parent surface reads at-home for a child with no participation
        # today. The admin-only unassigned wrap above is what this test is for.
        assert parent_row(client, parent_headers, student["id"])["display_status"] == "at-home"

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
            # U3: at-school requires a boarding on a completed morning run.
            expected="at-home", raw="at-school",
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
            # U3/R7: the afternoon auto-board is a declared presumption, so it
            # reads as expected-on-bus until the driver confirms or corrects.
            expected="expected-on-bus", raw="on-bus",
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
    carries the student. The run is completed by SQL here — since U7 no API
    path completes a run without recording an outcome per child — which leaves
    exactly the stale on-bus column this derivation must not trust."""
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

        force_run_completed(run["id"])

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
    the student; once no such run holds them the badge decays to at-home, and
    the raw status is never rewritten by the read."""
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
        # Since U4 the run cannot close with unaccounted children. This test
        # stages its state on the status column, which nothing derives from
        # since U3, so the child is confirmed here to let the run close.
        ended = complete_run(client, driver_headers, run_id)
        assert ended.status_code == 200, ended.text

        # A confirmed drop-off on a completed afternoon run today.
        assert_display_parity(
            client, parent_headers, admin_headers, student["id"],
            expected="dropped-off", raw="dropped-off",
        )

        # Deleting a completed run dated today is refused since U7 — this flip
        # is exactly the harm that refusal prevents. Removed out-of-band here to
        # reach the state under test.
        refused = client.delete(f"/api/runs/{run_id}", headers=admin_headers)
        assert refused.status_code == 409, refused.text
        purge_run(run_id)

        assert_display_parity(
            client, parent_headers, admin_headers, student["id"],
            expected="at-home", raw="dropped-off",
        )
    finally:
        purge_run(run_id)
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


# Bulk-upload triage, duplicates, school binding, route-name retirement
# (U10: R16-R18, AE8) -------------------------------------------------------------
#
# Keyless-tier staging (the offline geocoder's determinism): a row with coords
# triages 'resolved' with no network call; an address-only row goes through the
# free Nominatim fallback — a well-known Kenyan place name deterministically
# resolves (provider 'nominatim' -> 'ambiguous'), a junk string deterministically
# doesn't ('failed'). The suite keeps the fallback calls to a handful per run.

ROUTE_COLUMN_NOTE = "route column ignored — routes come from the fleet plan"

# Two loose clusters around the AE8 school (-1.30, 36.80) so a two-bus
# partition is geometrically natural (the draft-suite convention).
AE8_SCHOOL = {"lat": -1.3000, "lng": 36.8000}
AE8_DEPOT_EAST = (-1.285, 36.830)
AE8_DEPOT_WEST = (-1.320, 36.770)


def _bulk_row(marker: str, i: int, **overrides) -> dict:
    """One valid bulk row (name/grade/parent invariant satisfied)."""
    row = {
        "name": f"IT Bulk {marker} {i}",
        "grade": "G4",
        "parent_name": f"IT Bulk Parent {marker} {i}",
        "parent_phone": f"+25471100{i:04d}",
        "parent_email": f"it-bulk-{marker}-{i}@test.local",
    }
    row.update(overrides)
    return row


def _ae8_home(i: int) -> tuple[float, float]:
    """27 distinct homes, east cluster for even i, west for odd."""
    if i % 2 == 0:
        return (-1.290 - (i // 2) * 0.0015, 36.818 + (i // 2) * 0.0011)
    return (-1.308 - (i // 2) * 0.0015, 36.782 - (i // 2) * 0.0011)


def _student_rows(client, admin_headers) -> list[dict]:
    return client.get("/api/students", headers=admin_headers).json()


def _make_plan_bus(client, headers, name: str, capacity: int,
                   depot: tuple[float, float]) -> dict:
    created = client.post(
        "/api/fleet/buses",
        json={"name": name, "capacity": capacity,
              "depot_lat": depot[0], "depot_lng": depot[1]},
        headers=headers,
    )
    assert created.status_code == 200, created.text
    return created.json()


def test_bulk_ae8_triage_confirm_place_commit_and_draft_basis(client, admin_headers):
    """AE8/F5 end-to-end: 30 rows triage 27 resolved / 2 ambiguous / 1 failed;
    the two ambiguous are confirmed in one action each (the proposed pin is
    accepted as-is), the failed one is hand-placed (provenance 'picked'); the
    commit stamps school_id on every row; and the school's next draft basis
    carries all 30 as plannable — including the three repaired rows."""
    marker = uuid.uuid4().hex[:6]
    school = client.post(
        "/api/fleet/schools",
        json={"name": f"IT BulkSchool {marker}", **AE8_SCHOOL},
        headers=admin_headers,
    ).json()
    bus_a = bus_b = None
    student_ids: list[str] = []
    try:
        rows = []
        for i in range(27):
            lat, lng = _ae8_home(i)
            rows.append(_bulk_row(marker, i, home_lat=lat, home_lng=lng,
                                  home_address=f"IT Bulk Home {marker} {i}"))
        # Ambiguous: address-only, resolvable ONLY by the low-confidence
        # fallback (well-known place names near the school).
        rows.append(_bulk_row(marker, 27, home_address="Nairobi"))
        rows.append(_bulk_row(marker, 28, home_address="Westlands, Nairobi"))
        # Failed: address-only junk nothing can locate.
        rows.append(_bulk_row(marker, 29, home_address=f"zzqx unresolvable {marker}"))

        validated = client.post(
            "/api/students/bulk/validate",
            json={"school_id": school["id"], "students": rows},
            headers=admin_headers,
        )
        assert validated.status_code == 200, validated.text
        triage = validated.json()["rows"]
        by_status: dict[str, list[dict]] = {"resolved": [], "ambiguous": [], "failed": []}
        for t in triage:
            by_status[t["status"]].append(t)
        assert len(by_status["resolved"]) == 27, triage
        assert len(by_status["ambiguous"]) == 2, triage
        assert len(by_status["failed"]) == 1, triage
        # An ambiguous row carries the fallback's proposed pin for the
        # one-click confirm; the failed row has nothing to propose.
        for t in by_status["ambiguous"]:
            assert t["provider"] == "nominatim"
            assert t["lat"] is not None and t["lng"] is not None
        assert by_status["failed"][0]["lat"] is None

        # Validate committed nothing (AE8: triage is read-only).
        names_now = {s["name"] for s in _student_rows(client, admin_headers)}
        assert not any(r["name"] in names_now for r in rows)

        # Resolutions: confirming an ambiguous row = accepting the proposed pin
        # (one action -> the proposal's coords, provenance stays imported);
        # the failed row is hand-placed on the map (provenance 'picked').
        for t in by_status["ambiguous"]:
            rows[t["index"]]["home_lat"] = t["lat"]
            rows[t["index"]]["home_lng"] = t["lng"]
        failed_index = by_status["failed"][0]["index"]
        rows[failed_index]["home_lat"] = -1.2955
        rows[failed_index]["home_lng"] = 36.8090
        rows[failed_index]["provenance"] = "picked"

        committed = client.post(
            "/api/students/bulk",
            json={"school_id": school["id"], "students": rows},
            headers=admin_headers,
        )
        assert committed.status_code == 200, committed.text
        body = committed.json()
        assert body["inserted"] == 30, body
        assert body["errors"] == [], body

        # School binding: every committed row is stamped — a school-less
        # student would be invisible to the school-scoped draft basis below.
        mine = [s for s in _student_rows(client, admin_headers)
                if s["name"].startswith(f"IT Bulk {marker} ")]
        student_ids.extend(s["id"] for s in mine)
        assert len(mine) == 30
        assert all(str(s["school_id"]) == str(school["id"]) for s in mine)
        # The hand-placed pin kept its provenance; new intakes default both_ways.
        placed = next(s for s in mine if s["name"] == f"IT Bulk {marker} 29")
        assert placed["provenance"] == "picked"
        assert all(s["ridership_pattern"] == "both_ways" for s in mine)

        # The bulk-imported students appear in this school's next draft basis,
        # every one plannable — including the two confirmed and the one placed.
        bus_a = _make_plan_bus(client, admin_headers,
                               f"IT BulkBus A {marker}", 16, AE8_DEPOT_EAST)
        bus_b = _make_plan_bus(client, admin_headers,
                               f"IT BulkBus B {marker}", 16, AE8_DEPOT_WEST)
        confirmed = client.post(
            "/api/fleet-plans/confirm-fleet",
            json={"school_id": school["id"], "bus_ids": [bus_a["id"], bus_b["id"]]},
            headers=admin_headers,
        )
        assert confirmed.status_code == 200, confirmed.text
        drafted = client.post(
            "/api/fleet-plans/draft",
            json={"school_id": school["id"], "seed": 7},
            headers=admin_headers,
        )
        assert drafted.status_code == 200, drafted.text
        basis = drafted.json()["basis"]
        assert {s["id"] for s in basis["students"]} == set(student_ids)
        assert all(s["plannable"] for s in basis["students"])
        assert drafted.json()["document"]["unplaceable"] == []
    finally:
        # Sweep by name prefix, not collected ids: a failure before the commit
        # assertions must not strand thirty IT rows in the shared stack.
        for s in _student_rows(client, admin_headers):
            if s["name"].startswith(f"IT Bulk {marker} "):
                client.delete(f"/api/students/{s['id']}", headers=admin_headers)
        for bus in (bus_a, bus_b):
            if bus:
                client.delete(f"/api/fleet/buses/{bus['id']}", headers=admin_headers)
        # Deleting the school cascades its plan rows (011 FK).
        client.delete(f"/api/fleet/schools/{school['id']}", headers=admin_headers)


def test_bulk_duplicate_flagged_skip_noops_update_overwrites(client, admin_headers):
    """U10 duplicates: validate flags a row matching an existing student on
    (name, school); committing with skip leaves the original untouched;
    with no choice the row errors (nothing silently doubles); with update the
    contacts and address are overwritten and the address re-triaged."""
    marker = uuid.uuid4().hex[:6]
    school = client.post(
        "/api/fleet/schools",
        json={"name": f"IT BulkSchool {marker}", **AE8_SCHOOL},
        headers=admin_headers,
    ).json()
    name = f"IT Bulk Dup {marker}"
    original = _bulk_row(marker, 1, name=name, home_lat=-1.291, home_lng=36.812,
                         home_address="Old Lane")
    try:
        first = client.post(
            "/api/students/bulk",
            json={"school_id": school["id"], "students": [original]},
            headers=admin_headers,
        )
        assert first.status_code == 200 and first.json()["inserted"] == 1, first.text
        existing = next(s for s in _student_rows(client, admin_headers) if s["name"] == name)

        # Validate flags the duplicate with who it found.
        validated = client.post(
            "/api/students/bulk/validate",
            json={"school_id": school["id"], "students": [original]},
            headers=admin_headers,
        )
        assert validated.status_code == 200, validated.text
        flag = validated.json()["rows"][0]["duplicate_of"]
        assert flag and str(flag["id"]) == str(existing["id"]) and flag["name"] == name

        count_before = len(_student_rows(client, admin_headers))

        # No choice -> the row errors, nothing inserted or updated.
        undecided = client.post(
            "/api/students/bulk",
            json={"school_id": school["id"], "students": [original]},
            headers=admin_headers,
        ).json()
        assert undecided["inserted"] == 0 and undecided["updated"] == 0
        assert len(undecided["errors"]) == 1
        assert "skip or update" in undecided["errors"][0]

        # Skip -> a no-op: same row count, contacts and home untouched.
        skipped = client.post(
            "/api/students/bulk",
            json={"school_id": school["id"],
                  "students": [{**original, "duplicate_action": "skip"}]},
            headers=admin_headers,
        ).json()
        assert (skipped["inserted"], skipped["updated"], skipped["skipped"]) == (0, 0, 1)
        assert skipped["errors"] == []
        untouched = next(s for s in _student_rows(client, admin_headers)
                         if str(s["id"]) == str(existing["id"]))
        assert untouched["parent_phone"] == original["parent_phone"]
        assert untouched["home_address"] == "Old Lane"

        # Update -> contacts/address overwritten, address re-triaged (fresh
        # coords land), same student id, still exactly one row.
        revised = {
            **original,
            "parent_phone": "+254711009999",
            "parent_email": f"it-bulk-upd-{marker}@test.local",
            "home_address": "New Lane",
            "home_lat": -1.2984, "home_lng": 36.8047,
            "duplicate_action": "update",
        }
        updated = client.post(
            "/api/students/bulk",
            json={"school_id": school["id"], "students": [revised]},
            headers=admin_headers,
        ).json()
        assert updated["updated"] == 1 and updated["inserted"] == 0, updated
        assert len(_student_rows(client, admin_headers)) == count_before
        after = next(s for s in _student_rows(client, admin_headers)
                     if str(s["id"]) == str(existing["id"]))
        assert after["parent_phone"] == "+254711009999"
        assert after["parent_email"] == f"it-bulk-upd-{marker}@test.local"
        assert after["home_address"] == "New Lane"
        assert float(after["home_lat"]) == pytest.approx(-1.2984)
        assert float(after["home_lng"]) == pytest.approx(36.8047)
    finally:
        for s in _student_rows(client, admin_headers):
            if s["name"] == name:
                client.delete(f"/api/students/{s['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/schools/{school['id']}", headers=admin_headers)


def test_bulk_identical_reupload_all_skipped_creates_zero_students(client, admin_headers):
    """U10: re-uploading an identical file with every duplicate skipped is a
    complete no-op — zero new students."""
    marker = uuid.uuid4().hex[:6]
    school = client.post(
        "/api/fleet/schools",
        json={"name": f"IT BulkSchool {marker}", **AE8_SCHOOL},
        headers=admin_headers,
    ).json()
    rows = [
        _bulk_row(marker, i, home_lat=-1.29 - i * 0.002, home_lng=36.81 + i * 0.002)
        for i in range(2)
    ]
    try:
        first = client.post(
            "/api/students/bulk",
            json={"school_id": school["id"], "students": rows},
            headers=admin_headers,
        ).json()
        assert first["inserted"] == 2, first
        count_before = len(_student_rows(client, admin_headers))

        again = client.post(
            "/api/students/bulk",
            json={"school_id": school["id"],
                  "students": [{**r, "duplicate_action": "skip"} for r in rows]},
            headers=admin_headers,
        ).json()
        assert again["inserted"] == 0 and again["skipped"] == 2 and again["errors"] == []
        assert len(_student_rows(client, admin_headers)) == count_before
    finally:
        for s in _student_rows(client, admin_headers):
            if s["name"].startswith(f"IT Bulk {marker} "):
                client.delete(f"/api/students/{s['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/schools/{school['id']}", headers=admin_headers)


def test_bulk_route_name_imports_without_assigning_and_notes_it(client, admin_headers):
    """R17 retirement: a row carrying route_name — even one naming a real
    route — still imports the student, assigns NO route, and surfaces the
    informational note per row, at validate and at commit."""
    marker = uuid.uuid4().hex[:6]
    school, make_route = _u5_school_and_route_factory(client, admin_headers, marker)
    route = make_route("Express", "morning")
    name = f"IT Bulk RouteName {marker}"
    row = _bulk_row(marker, 1, name=name, home_lat=-1.3, home_lng=36.8,
                    route_name=f"IT U5 Express {marker}")
    try:
        validated = client.post(
            "/api/students/bulk/validate",
            json={"school_id": school["id"], "students": [row]},
            headers=admin_headers,
        )
        assert validated.status_code == 200, validated.text
        assert validated.json()["rows"][0]["route_note"] == ROUTE_COLUMN_NOTE

        committed = client.post(
            "/api/students/bulk",
            json={"school_id": school["id"], "students": [row]},
            headers=admin_headers,
        )
        assert committed.status_code == 200, committed.text
        body = committed.json()
        assert body["inserted"] == 1, body
        assert body["notes"] == [f"{name}: {ROUTE_COLUMN_NOTE}"], body
        assert "routeAssignments" not in body  # the counter died with the path
        imported = next(s for s in _student_rows(client, admin_headers) if s["name"] == name)
        assert imported["route_ids"] == []  # nothing assigned
    finally:
        for s in _student_rows(client, admin_headers):
            if s["name"] == name:
                client.delete(f"/api/students/{s['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/routes/{route['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/schools/{school['id']}", headers=admin_headers)


def test_bulk_validate_commits_nothing(client, admin_headers):
    """U10/AE8: /bulk/validate is read-only — the student table is unchanged
    after a validate, row for row."""
    marker = uuid.uuid4().hex[:6]
    school = client.post(
        "/api/fleet/schools",
        json={"name": f"IT BulkSchool {marker}", **AE8_SCHOOL},
        headers=admin_headers,
    ).json()
    try:
        count_before = len(_student_rows(client, admin_headers))
        validated = client.post(
            "/api/students/bulk/validate",
            json={"school_id": school["id"], "students": [
                _bulk_row(marker, 1, home_lat=-1.3, home_lng=36.8),
                _bulk_row(marker, 2, home_address=f"zzqx unresolvable {marker}"),
            ]},
            headers=admin_headers,
        )
        assert validated.status_code == 200, validated.text
        statuses = [r["status"] for r in validated.json()["rows"]]
        assert statuses == ["resolved", "failed"]
        assert len(_student_rows(client, admin_headers)) == count_before
    finally:
        client.delete(f"/api/fleet/schools/{school['id']}", headers=admin_headers)


def test_bulk_duplicate_update_regenerates_each_route_exactly_once(monkeypatch):
    """U8 burst guard, carried through U10's route-name retirement: the only
    bulk path still touching routes is the duplicate-update overwrite, and its
    post-loop regeneration collapses duplicates — one call per affected route,
    never per row."""
    from app.dao import student_live_dao

    calls: list[str] = []
    monkeypatch.setattr(student_live_dao, "regenerate_route_stops",
                        lambda conn, rid: calls.append(str(rid)) or True)
    student_live_dao._regenerate_routes(None, ["r1", "r1", "r2", "r1"])
    assert sorted(calls) == ["r1", "r2"]
