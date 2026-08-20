"""Pin-map audited-access integration suite (U11: R19) against the local stack.

Run with the stack up (scripts/start-local.sh) and migration 011 applied:

    RUN_INTEGRATION=1 ../.venv/bin/python -m pytest tests/integration/test_pin_map_audit.py -q

GET /api/students/pin-map is the aggregate view of every child's home for a
school — a higher-value target than any single record — so the access itself
is the audited event: every 200 writes exactly one 'pin-map-viewed'
live_admin_audit row (actor identity denormalized, school, pin counts) in the
same request, and a refused request writes none. Everything created here is
deleted afterwards; the audit rows the tests provoke are removed by SQL in
teardown (deliberately — the product offers no delete API for audit rows).
"""

import os
import uuid

import httpx
import psycopg
import pytest
from psycopg.rows import dict_row

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="needs the local stack; set RUN_INTEGRATION=1",
)

BASE = os.environ.get("INTEGRATION_API_URL", "http://localhost:9001")
# The local stack's Postgres, published by docker-compose.local.yml. Used to
# read the audit trail (the product exposes no audit-read API yet) and to
# purge the rows these tests provoke (teardown only).
DB_URL = os.environ.get(
    "INTEGRATION_DB_URL", "postgresql://saferide:saferide@localhost:5432/saferide"
)

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


def pin_map_audit_rows(school_id: str) -> list[dict]:
    """The school's 'pin-map-viewed' audit rows, oldest first. Read via SQL:
    the audit trail has no read API yet, and going around the product here
    checks the contract rather than hiding one — the row is a side effect the
    HTTP response deliberately does not echo."""
    with psycopg.connect(DB_URL, row_factory=dict_row) as conn:
        rows = conn.execute(
            "select * from live_admin_audit "
            "where action = 'pin-map-viewed' and school_id = %s "
            "order by created_at asc",
            (school_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def purge_pin_map_audit(school_id: str) -> None:
    """Remove the audit rows a test provoked. **Teardown only** — the product
    has no delete API for audit rows on purpose (an erasable audit trail is
    not one), so cleanup drops to SQL like purge_run does."""
    with psycopg.connect(DB_URL, autocommit=True) as conn:
        conn.execute(
            "delete from live_admin_audit "
            "where action = 'pin-map-viewed' and school_id = %s",
            (school_id,),
        )


def make_school(client, headers, marker: str, suffix: str = "") -> dict:
    created = client.post(
        "/api/fleet/schools",
        json={"name": f"IT PinMap School {suffix}{marker}", "lat": -1.30, "lng": 36.80},
        headers=headers,
    )
    assert created.status_code == 200, created.text
    return created.json()


def make_student(client, headers, marker: str, i: int, school_id: str, **overrides) -> dict:
    """One student satisfying the two-parent invariant, stamped to the school.
    Pass home_lat/home_lng (+ provenance) for a placed pin; omit them AND the
    address for an unresolved student (an address would be geocoded)."""
    payload = {
        "name": f"IT PinMap Kid {marker} {i}",
        "grade": "G4",
        "parent_name": f"IT PinMap Parent {marker}",
        "parent_phone": "+254711000001",
        "parent_email": f"it-pinmap-{marker}-{i}@test.local",
        "school_id": school_id,
    }
    payload.update(overrides)
    created = client.post("/api/students", json=payload, headers=headers)
    assert created.status_code == 200, created.text
    return created.json()


def test_pin_map_returns_school_pins_and_audits_every_call(client, admin_headers):
    """R19 core: the school's pins come back split placed/unresolved with
    identity + provenance, and each call writes exactly one audit row carrying
    the actor's denormalized identity, the school, and the pin counts —
    two calls, two rows."""
    marker = uuid.uuid4().hex[:6]
    school = make_school(client, admin_headers, marker)
    students: list[dict] = []
    try:
        students.append(make_student(
            client, admin_headers, marker, 0, school["id"],
            home_address="IT Pin Lane", home_lat=-1.2921, home_lng=36.8219,
            provenance="picked",
        ))
        students.append(make_student(
            client, admin_headers, marker, 1, school["id"],
            home_address="IT Import Row", home_lat=-1.3050, home_lng=36.7900,
            provenance="imported",
        ))
        # No coordinates and no address (an address would be geocoded): the
        # unresolved tier the side panel names for hand-placement.
        students.append(make_student(client, admin_headers, marker, 2, school["id"]))

        response = client.get(
            "/api/students/pin-map", params={"school_id": school["id"]},
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert str(body["school_id"]) == str(school["id"])

        placed = {p["id"]: p for p in body["placed"]}
        assert set(placed) == {str(students[0]["id"]), str(students[1]["id"])}
        picked = placed[str(students[0]["id"])]
        assert picked["name"] == students[0]["name"]
        assert picked["lat"] == pytest.approx(-1.2921)
        assert picked["lng"] == pytest.approx(36.8219)
        assert picked["provenance"] == "picked"
        assert picked["state"] == "placed"
        assert placed[str(students[1]["id"])]["provenance"] == "imported"

        # The coordless student is named separately, not silently dropped.
        assert [u["id"] for u in body["unresolved"]] == [str(students[2]["id"])]
        unresolved = body["unresolved"][0]
        assert unresolved["name"] == students[2]["name"]
        assert unresolved["lat"] is None and unresolved["lng"] is None
        assert unresolved["state"] == "unresolved"

        # Exactly ONE audit row for the call, with actor identity and detail.
        rows = pin_map_audit_rows(school["id"])
        assert len(rows) == 1, rows
        row = rows[0]
        assert row["actor_id"] is not None
        assert row["actor_email"] == ADMIN["email"]
        assert row["actor_name"]  # denormalized, never blank
        assert str(row["school_id"]) == str(school["id"])
        assert row["detail"] == {"pin_count": 2, "unresolved_count": 1}

        # A second view is a second audited access: two calls -> two rows.
        again = client.get(
            "/api/students/pin-map", params={"school_id": school["id"]},
            headers=admin_headers,
        )
        assert again.status_code == 200, again.text
        rows = pin_map_audit_rows(school["id"])
        assert len(rows) == 2, rows
        assert all(r["detail"] == {"pin_count": 2, "unresolved_count": 1} for r in rows)
    finally:
        for s in students:
            client.delete(f"/api/students/{s['id']}", headers=admin_headers)
        purge_pin_map_audit(school["id"])
        client.delete(f"/api/fleet/schools/{school['id']}", headers=admin_headers)


def test_pin_map_never_shows_another_schools_students(client, admin_headers):
    """The aggregate is school-scoped: school B's child appears in neither
    tier of school A's pin map, and vice versa."""
    marker = uuid.uuid4().hex[:6]
    school_a = make_school(client, admin_headers, marker, suffix="A ")
    school_b = make_school(client, admin_headers, marker, suffix="B ")
    students: list[dict] = []
    try:
        kid_a = make_student(
            client, admin_headers, marker, 0, school_a["id"],
            home_lat=-1.2900, home_lng=36.8100,
        )
        kid_b = make_student(
            client, admin_headers, marker, 1, school_b["id"],
            home_lat=-1.3100, home_lng=36.7800,
        )
        students.extend([kid_a, kid_b])

        response = client.get(
            "/api/students/pin-map", params={"school_id": school_a["id"]},
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        listed = {p["id"] for p in body["placed"]} | {u["id"] for u in body["unresolved"]}
        assert listed == {str(kid_a["id"])}
        assert str(kid_b["id"]) not in listed
    finally:
        for s in students:
            client.delete(f"/api/students/{s['id']}", headers=admin_headers)
        for school in (school_a, school_b):
            purge_pin_map_audit(school["id"])
            client.delete(f"/api/fleet/schools/{school['id']}", headers=admin_headers)


def test_pin_map_non_admin_is_403_and_writes_no_audit_row(client, admin_headers):
    """A refused access is not an access: a parent gets 403 (and an anonymous
    caller 401) with ZERO audit rows written — the trail records views of the
    aggregate, not attempts bounced at the door."""
    marker = uuid.uuid4().hex[:6]
    school = make_school(client, admin_headers, marker)
    parent_email = f"it-pinmap-parent-{marker}@test.local"
    signup = client.post(
        "/api/auth/signup",
        json={"email": parent_email, "password": "ParentPass1!",
              "full_name": f"IT PinMap NonAdmin {marker}", "role": "parent"},
    )
    assert signup.status_code == 200, signup.text
    parent_id = signup.json()["user"]["id"]
    parent_headers = {"Authorization": f"Bearer {signup.json()['token']}"}
    try:
        refused = client.get(
            "/api/students/pin-map", params={"school_id": school["id"]},
            headers=parent_headers,
        )
        assert refused.status_code == 403, refused.text

        anonymous = client.get(
            "/api/students/pin-map", params={"school_id": school["id"]}
        )
        assert anonymous.status_code == 401, anonymous.text

        assert pin_map_audit_rows(school["id"]) == []
    finally:
        client.delete(f"/api/accounts/parents/{parent_id}", headers=admin_headers)
        purge_pin_map_audit(school["id"])
        client.delete(f"/api/fleet/schools/{school['id']}", headers=admin_headers)


def test_pin_map_unknown_school_is_a_clean_404(client, admin_headers):
    """A school id nothing matches is refused up front as a lookup miss — 404,
    the fleet-plan endpoints' convention — not answered with an empty audited
    view of nowhere."""
    response = client.get(
        "/api/students/pin-map", params={"school_id": str(uuid.uuid4())},
        headers=admin_headers,
    )
    assert response.status_code == 404, response.text
    assert "school" in response.json()["detail"].lower()
