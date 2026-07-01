"""Students/parents integration suite (U4: R7, R9–R13) against the local stack.

Run with the stack up (scripts/start-local.sh) and migration 007 applied:

    RUN_INTEGRATION=1 ../.venv/bin/python -m pytest tests/integration -q

Covers the two-parent payload invariant (≥1 phone, ≥1 email), email-driven
parent-account link sync (swap on change, drift preservation on unrelated
edits, signup backfill), the no-status-write update path, and the
reverse-geocode proxy. Everything created here is deleted afterwards.
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
