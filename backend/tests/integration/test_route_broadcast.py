"""Route broadcast API (ops-refinement U8: R20, R21, R23; AE5).

Run with the stack up (scripts/start-local.sh):

    RUN_INTEGRATION=1 ../.venv/bin/python -m pytest tests/integration/test_route_broadcast.py -q

Covers POST /api/fleet/routes/{route_id}/broadcast end to end: assignment-
derived recipients distinct per parent (a two-children parent gets exactly
one 'admin-notice' row and the response reports the distinct count; a parent
on another route gets nothing), body validation (whitespace-only and over-cap
400s, C0 control chars stripped with the newline kept, stored as raw text),
no run-scoped dedup (two identical sends = two rows; run_id / run_type /
student_id all NULL), the loud-failure 409s — no assigned students, and the
DISTINCT students-but-zero-linked-parents message with nothing inserted —
the admin_only boundary (parent AND driver tokens 403), and the per-admin
limiter (13th call in the hour 429s).

Isolation: a throwaway fleet (driver + bus + school + routes) and throwaway
parent accounts with fresh emails per run. Broadcasts are sent by throwaway
ADMIN accounts: signup only mints parent/driver roles, so admin rows are
inserted directly into the stack's Postgres (the suite's accepted direct-SQL
escape hatch) and then logged in through the real API — the in-process
limiter budget keys on the admin's user id, so a fresh admin per run keeps
this module immune to budget bleed across runs and suites (the limiter test
additionally creates its own dedicated account, mirroring test_cancel_ride).
Everything created is deleted in finally blocks; broadcast notification rows
die with their parent accounts (user_id ON DELETE CASCADE).
"""

import base64
import hashlib
import os
import random
import time
import uuid

import httpx
import psycopg
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="needs the local stack; set RUN_INTEGRATION=1",
)

BASE = os.environ.get("INTEGRATION_API_URL", "http://localhost:9001")
DB_URL = os.environ.get(
    "INTEGRATION_DB_URL", "postgresql://saferide:saferide@localhost:5432/saferide"
)

ADMIN = {"email": "admin@test.com", "password": "test1234."}  # fleet CRUD only
ADMIN_PASSWORD = "AdminPass1!"  # throwaway broadcast senders


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


# Throwaway admin accounts (direct SQL) ------------------------------------------

def _password_hash(password: str) -> str:
    """Mirror app.core.security.hash_password's stored format (pbkdf2_sha256,
    200k iterations) so a direct-SQL account can log in through the real API."""
    salt = uuid.uuid4().hex
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000
    )
    return f"pbkdf2_sha256$200000${salt}${base64.b64encode(digest).decode('ascii')}"


def create_throwaway_admin(marker: str, tag: str) -> dict:
    """A fresh ADMIN account (fresh in-process limiter budget: the broadcast
    budget keys on the admin's user id). Signup cannot mint the admin role,
    so the row goes straight into Postgres."""
    email = f"it-rb-{tag}-{marker}@test.local"
    with psycopg.connect(DB_URL) as conn:
        row = conn.execute(
            "insert into app_users (email, password_hash, full_name) "
            "values (%s, %s, %s) returning id",
            (email, _password_hash(ADMIN_PASSWORD), f"IT RB Admin {tag} {marker}"),
        ).fetchone()
        conn.execute(
            "insert into app_user_roles (user_id, role) values (%s, 'admin')",
            (row[0],),
        )
    return {"id": str(row[0]), "email": email}


def delete_throwaway_admin(admin_id: str) -> None:
    with psycopg.connect(DB_URL) as conn:
        # Roles and sessions cascade with the user row.
        conn.execute("delete from app_users where id = %s", (admin_id,))


def signup_parent(client, marker: str, tag: str) -> dict:
    """A fresh parent account; fresh email per run keeps links deterministic."""
    email = f"it-rb-{tag}-{marker}@test.local"
    response = client.post(
        "/api/auth/signup",
        json={"email": email, "password": "ParentPass1!",
              "full_name": f"IT RB {tag} {marker}", "role": "parent"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    return {
        "id": body["user"]["id"],
        "email": email,
        "headers": {"Authorization": f"Bearer {body['token']}"},
    }


def _create_driver(client, admin_headers, marker: str) -> dict:
    """A throwaway driver with a known PIN (retry rare PIN collisions)."""
    for _ in range(5):
        pin = str(random.randint(100000, 999999))
        response = client.post(
            "/api/accounts/drivers",
            json={"full_name": f"IT RB Driver {marker}",
                  "email": f"it-rb-driver-{marker}@test.local",
                  "password": "test1234.", "phone": "+254711000090", "pin": pin},
            headers=admin_headers,
        )
        if response.status_code == 200:
            return {**response.json(), "pin": pin}
    pytest.fail(f"could not create throwaway driver: {response.text}")


@pytest.fixture(scope="module")
def fleet(client, admin_headers):
    """Throwaway sender admin + driver + bus + school and four routes:

    - route_a (morning, on the bus): s1 linked to p1 AND p2, s2 linked to p1
      only — the sibling-dedup/distinct-count target (recipients = {p1, p2}).
    - route_b (afternoon, same bus): s3 linked to p3 — the OTHER route whose
      parent a route_a broadcast must never reach (assignment truth, not
      bus_id: p3's child rides the same bus).
    - route_c (bus-less): s4 whose parent email matches NO account — students
      assigned, zero linked parents.
    - route_d (bus-less): no students at all.

    Budget arithmetic: the sender admin makes 10 limiter-counted broadcast
    calls across this module (limit 12/hour). New tests that send as this
    admin must fit that budget or mint their own like the limiter test.
    """
    marker = uuid.uuid4().hex[:6]
    p1 = signup_parent(client, marker, "p1")
    p2 = signup_parent(client, marker, "p2")
    p3 = signup_parent(client, marker, "p3")
    sender = create_throwaway_admin(marker, "sender")
    driver = _create_driver(client, admin_headers, marker)
    bus = school = route_a = route_b = route_c = route_d = None
    s1 = s2 = s3 = s4 = None
    try:
        sender_headers = login(client, sender["email"], ADMIN_PASSWORD)
        bus = client.post(
            "/api/fleet/buses",
            json={"name": f"IT RB Bus {marker}", "driver_id": driver["id"]},
            headers=admin_headers,
        ).json()
        school = client.post(
            "/api/fleet/schools",
            json={"name": f"IT RB School {marker}", "lat": -1.30, "lng": 36.80},
            headers=admin_headers,
        ).json()

        def make_route(name: str, type_: str, bus_id: str | None) -> dict:
            response = client.post(
                "/api/fleet/routes",
                json={"name": name, "type": type_, "bus_id": bus_id,
                      "school_id": school["id"]},
                headers=admin_headers,
            )
            assert response.status_code == 200, response.text
            return response.json()

        route_a = make_route(f"IT RB Morning {marker}", "morning", bus["id"])
        route_b = make_route(f"IT RB Afternoon {marker}", "afternoon", bus["id"])
        route_c = make_route(f"IT RB Unlinked {marker}", "morning", None)
        route_d = make_route(f"IT RB Empty {marker}", "afternoon", None)

        def make_student(n: int, route_id: str, email: str, email2=None) -> dict:
            payload = {
                "name": f"IT RB Kid{n} {marker}", "parent_name": f"IT RB Parent{n}",
                "parent_phone": f"+25471100009{n}", "parent_email": email,
                "home_lat": -1.28, "home_lng": 36.79, "pickup_time": "06:30",
                "route_ids": [route_id],
            }
            if email2:
                payload["parent2_name"] = "IT RB CoParent"
                payload["parent2_email"] = email2
            response = client.post("/api/students", json=payload, headers=admin_headers)
            assert response.status_code == 200, response.text
            return response.json()

        s1 = make_student(1, route_a["id"], p1["email"], email2=p2["email"])
        s2 = make_student(2, route_a["id"], p1["email"])
        s3 = make_student(3, route_b["id"], p3["email"])
        # No account ever signs up with this email: assigned but unlinked.
        s4 = make_student(4, route_c["id"], f"it-rb-unlinked-{marker}@test.local")

        yield {
            "marker": marker,
            "sender_headers": sender_headers,
            "driver_headers": pin_login(client, driver["pin"]),
            "bus": bus,
            "route_a": route_a, "route_b": route_b,
            "route_c": route_c, "route_d": route_d,
            "p1": p1, "p2": p2, "p3": p3,
        }
    finally:
        for student in (s1, s2, s3, s4):
            if student:
                client.delete(f"/api/students/{student['id']}", headers=admin_headers)
        for route in (route_a, route_b, route_c, route_d):
            if route:
                client.delete(f"/api/fleet/routes/{route['id']}", headers=admin_headers)
        if school:
            client.delete(f"/api/fleet/schools/{school['id']}", headers=admin_headers)
        if bus:
            client.delete(f"/api/fleet/buses/{bus['id']}", headers=admin_headers)
        client.delete(f"/api/accounts/drivers/{driver['id']}", headers=admin_headers)
        for parent in (p1, p2, p3):
            client.delete(f"/api/accounts/parents/{parent['id']}", headers=admin_headers)
        delete_throwaway_admin(sender["id"])


# Helpers ----------------------------------------------------------------------

def broadcast(client, headers: dict, route_id: str, body: str) -> httpx.Response:
    return client.post(
        f"/api/fleet/routes/{route_id}/broadcast", json={"body": body}, headers=headers
    )


def notifications(client, parent) -> list[dict]:
    return client.get(
        "/api/push/notifications", params={"limit": 200}, headers=parent["headers"]
    ).json()


def admin_notices_with_body(client, parent, body: str) -> list[dict]:
    return [
        n for n in notifications(client, parent)
        if n["type"] == "admin-notice" and n["body"] == body
    ]


def _admin_notice_count(body: str) -> int:
    """Row count across ALL users — the refusal tests must prove nothing was
    inserted for anybody, and no parent feed exists to ask when the students
    have no linked accounts."""
    with psycopg.connect(DB_URL) as conn:
        row = conn.execute(
            "select count(*) from live_notifications "
            "where type = 'admin-notice' and body = %s",
            (body,),
        ).fetchone()
    return int(row[0])


def _wait_for(predicate, timeout: float = 10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.5)
    return None


# Sibling dedup, distinct count, route scoping (R20, R21; AE5) --------------------

def test_sibling_dedup_distinct_count_and_route_scoping(client, fleet):
    """AE5: p1 (two children on the route) gets exactly ONE 'admin-notice'
    row; the response reports the DISTINCT parent count (p1 + s1's co-parent
    p2 = 2). The row carries run_type NULL, run_id NULL, student_id NULL and
    the route's bus. p3 — whose child rides the SAME BUS but the other route
    — gets nothing: recipients come from assignments, never bus_id."""
    body = f"IT RB delay notice {fleet['marker']}"
    response = broadcast(client, fleet["sender_headers"], fleet["route_a"]["id"], body)
    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True, "recipients": 2}

    assert _wait_for(
        lambda: len(admin_notices_with_body(client, fleet["p1"], body)) == 1
        and len(admin_notices_with_body(client, fleet["p2"], body)) == 1
    ), "broadcast rows never reached both linked parents"
    row = admin_notices_with_body(client, fleet["p1"], body)[0]
    assert row["type"] == "admin-notice"
    assert row["title"] == f"School notice — {fleet['route_a']['name']}"
    assert row["run_id"] is None
    assert row["run_type"] is None
    assert row["student_id"] is None
    assert str(row["bus_id"]) == str(fleet["bus"]["id"])
    # The fan-out task finished (both rows above are in): p3 has nothing.
    assert admin_notices_with_body(client, fleet["p3"], body) == []
    assert _admin_notice_count(body) == 2  # and nobody else, anywhere


# No dedup across sends (R23) -----------------------------------------------------

def test_two_identical_sends_are_two_rows(client, fleet):
    """R23: each send is a new notification — resending the same text is a
    second real row per parent (run_id NULL exempts broadcasts from the
    run-scoped dedup index)."""
    body = f"IT RB resend {fleet['marker']}"
    for _ in range(2):
        response = broadcast(client, fleet["sender_headers"], fleet["route_a"]["id"], body)
        assert response.status_code == 200, response.text
        assert response.json()["recipients"] == 2
    assert _wait_for(
        lambda: len(admin_notices_with_body(client, fleet["p1"], body)) == 2
        and len(admin_notices_with_body(client, fleet["p2"], body)) == 2
    ), "the second identical send never produced a second row"


# Loud zero-recipient failures (the banned silent 200) ----------------------------

def test_students_without_linked_parents_distinct_409_nothing_inserted(client, fleet):
    """Students ARE assigned but no parent account is linked: a 409 with its
    own message (NOT the no-students one) and no row inserted anywhere — the
    silent-zero-recipients 200 is the failure the plan bans."""
    body = f"IT RB unlinked {fleet['marker']}"
    response = broadcast(client, fleet["sender_headers"], fleet["route_c"]["id"], body)
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert "linked parent account" in detail
    assert "No students are assigned" not in detail  # distinct from the other 409
    time.sleep(1)  # a refusal schedules no background fan-out; prove it
    assert _admin_notice_count(body) == 0


def test_route_with_no_students_409(client, fleet):
    body = f"IT RB empty route {fleet['marker']}"
    response = broadcast(client, fleet["sender_headers"], fleet["route_d"]["id"], body)
    assert response.status_code == 409, response.text
    assert "No students are assigned" in response.json()["detail"]
    assert _admin_notice_count(body) == 0


def test_unknown_route_404(client, fleet):
    response = broadcast(
        client, fleet["sender_headers"], str(uuid.uuid4()), "IT RB ghost route"
    )
    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Route not found"


# Body validation (R23) ------------------------------------------------------------

def test_body_cap_and_whitespace_only_400(client, fleet):
    """501 characters → 400; whitespace-only → 400; exactly 500 → accepted
    (the cap is a boundary, not an off-by-one)."""
    over = broadcast(client, fleet["sender_headers"], fleet["route_a"]["id"], "x" * 501)
    assert over.status_code == 400, over.text
    assert "500 characters" in over.json()["detail"]
    assert _admin_notice_count("x" * 501) == 0

    blank = broadcast(client, fleet["sender_headers"], fleet["route_a"]["id"], " \t \n  ")
    assert blank.status_code == 400, blank.text
    assert "must not be empty" in blank.json()["detail"]

    at_cap = broadcast(client, fleet["sender_headers"], fleet["route_a"]["id"], "x" * 500)
    assert at_cap.status_code == 200, at_cap.text
    assert at_cap.json()["recipients"] == 2


def test_control_chars_stripped_newline_kept(client, fleet):
    """C0 controls (NUL, BEL, CR, TAB, ESC) are stripped and the result
    trimmed, but the newline survives: the stored body is exactly the
    cleaned text — raw otherwise (no HTML stripping; the text-only renderer
    is the inertness boundary)."""
    marker = fleet["marker"]
    raw = f"IT RB ctrl {marker}\x00: bus\x07 delayed.\r\nWait\tat the stop.\x1b  "
    expected = f"IT RB ctrl {marker}: bus delayed.\nWaitat the stop."
    response = broadcast(client, fleet["sender_headers"], fleet["route_a"]["id"], raw)
    assert response.status_code == 200, response.text
    assert _wait_for(
        lambda: len(admin_notices_with_body(client, fleet["p2"], expected)) == 1
    ), "stripped body never appeared in the feed"


# Role boundary --------------------------------------------------------------------

def test_parent_and_driver_tokens_403(client, fleet):
    """Sending is admin-only (R23): parent AND driver tokens bounce with 403
    and nothing is inserted."""
    body = f"IT RB forbidden {fleet['marker']}"
    for headers in (fleet["p1"]["headers"], fleet["driver_headers"]):
        response = broadcast(client, headers, fleet["route_a"]["id"], body)
        assert response.status_code == 403, response.text
    time.sleep(1)
    assert _admin_notice_count(body) == 0


# Per-admin limiter ------------------------------------------------------------------

def test_rate_limited_after_12_sends_in_the_hour(client):
    """The 13th broadcast call inside the hour 429s. A dedicated fresh admin
    keeps this deterministic: the in-process budget keys on the admin's user
    id and this account makes exactly these calls (404s count — the limiter
    runs before any lookup, mirroring the cancel-ride order)."""
    limited = create_throwaway_admin(uuid.uuid4().hex[:6], "rl")
    try:
        headers = login(client, limited["email"], ADMIN_PASSWORD)
        ghost = str(uuid.uuid4())
        for i in range(12):
            response = broadcast(client, headers, ghost, "IT RB budget probe")
            assert response.status_code == 404, f"call {i + 1}: {response.text}"
        over = broadcast(client, headers, ghost, "IT RB budget probe")
        assert over.status_code == 429, over.text
        assert "Too many broadcasts" in over.json()["detail"]
    finally:
        delete_throwaway_admin(limited["id"])
