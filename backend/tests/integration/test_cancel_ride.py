"""Cancel-a-Ride API (ops-refinement U5: R14, R16–R19; AE4).

Run with the stack up (scripts/start-local.sh):

    RUN_INTEGRATION=1 ../.venv/bin/python -m pytest tests/integration/test_cancel_ride.py -q

Covers the parent-portal cancel/withdraw verbs end to end: ownership-first
404s with the fixed message (no guard leakage for harvested student UUIDs),
per-scope completion guards with the day→afternoon narrowing, the on-bus
rejection, the staff-sourced 409, side effects gated on real transitions —
exactly one student-stamped 'cancellation' incident carrying the covered
route's bus and NULL driver, plus one 'ride-cancelled' confirmation per
linked parent and never a bus-wide fan-out — the mid-run run_absences
append, withdrawal guards keyed on run-row EXISTENCE, the list_children
``cancellation`` key, and the shared per-account limiter.

Isolation: a throwaway fleet (driver with a known PIN + bus + school +
morning/afternoon routes) and throwaway parent accounts with fresh emails
per run — the in-process limiter budgets key on account ids, so fresh
accounts keep this module immune to budget bleed across runs and suites
(the limiter test additionally creates its own dedicated account). Student
s1 links two parents (both email slots); s2 belongs to a different
household on the same bus. Every run created is deleted in a finally block
(a completed run left behind would gate that route for the rest of the
day), absences are cleared, and accounts and fleet are torn down at module
end. Direct SQL (the stack's published Postgres) is used only to read
``marked_by`` — no API exposes it.
"""

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

ADMIN = {"email": "admin@test.com", "password": "test1234."}
CHILD_NOT_FOUND = "Child not found for this parent"
CANCELLED_BY_PARENT = "Cancelled by parent"


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


def signup_parent(client, marker: str, tag: str) -> dict:
    """A fresh parent account; fresh email per run = fresh limiter budget."""
    email = f"it-cr-{tag}-{marker}@test.local"
    response = client.post(
        "/api/auth/signup",
        json={"email": email, "password": "ParentPass1!",
              "full_name": f"IT CR {tag} {marker}", "role": "parent"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    return {
        "id": body["user"]["id"],
        "email": email,
        "name": f"IT CR {tag} {marker}",
        "headers": {"Authorization": f"Bearer {body['token']}"},
    }


def _create_driver(client, admin_headers, marker: str) -> dict:
    """A throwaway driver with a known PIN (retry rare PIN collisions)."""
    for _ in range(5):
        pin = str(random.randint(100000, 999999))
        response = client.post(
            "/api/accounts/drivers",
            json={"full_name": f"IT CR Driver {marker}",
                  "email": f"it-cr-driver-{marker}@test.local",
                  "password": "test1234.", "phone": "+254711000080", "pin": pin},
            headers=admin_headers,
        )
        if response.status_code == 200:
            return {**response.json(), "pin": pin}
    pytest.fail(f"could not create throwaway driver: {response.text}")


@pytest.fixture(scope="module")
def fleet(client, admin_headers):
    """Throwaway driver + bus + school + morning/afternoon routes, student s1
    linked to parents p1 AND p2 (both email slots), student s2 linked to p3
    (another household on the same bus), plus p4 with no children at all.
    Torn down completely afterwards."""
    marker = uuid.uuid4().hex[:6]
    p1 = signup_parent(client, marker, "p1")
    p2 = signup_parent(client, marker, "p2")
    p3 = signup_parent(client, marker, "p3")
    p4 = signup_parent(client, marker, "p4")
    driver = _create_driver(client, admin_headers, marker)
    bus = school = morning = afternoon = s1 = s2 = None
    try:
        bus = client.post(
            "/api/fleet/buses",
            json={"name": f"IT CR Bus {marker}", "driver_id": driver["id"]},
            headers=admin_headers,
        ).json()
        school = client.post(
            "/api/fleet/schools",
            json={"name": f"IT CR School {marker}", "lat": -1.30, "lng": 36.80},
            headers=admin_headers,
        ).json()
        morning = client.post(
            "/api/fleet/routes",
            json={"name": f"IT CR Morning {marker}", "type": "morning",
                  "bus_id": bus["id"], "school_id": school["id"]},
            headers=admin_headers,
        ).json()
        afternoon = client.post(
            "/api/fleet/routes",
            json={"name": f"IT CR Afternoon {marker}", "type": "afternoon",
                  "bus_id": bus["id"], "school_id": school["id"]},
            headers=admin_headers,
        ).json()

        def make_student(n: int, lat: float, pickup: str, email: str, email2=None) -> dict:
            payload = {
                "name": f"IT CR Kid{n} {marker}", "parent_name": f"IT CR Parent{n}",
                "parent_phone": f"+25471100008{n}", "parent_email": email,
                "home_lat": lat, "home_lng": 36.79, "pickup_time": pickup,
                "route_ids": [morning["id"], afternoon["id"]],
            }
            if email2:
                payload["parent2_name"] = "IT CR CoParent"
                payload["parent2_email"] = email2
            response = client.post("/api/students", json=payload, headers=admin_headers)
            assert response.status_code == 200, response.text
            return response.json()

        s1 = make_student(1, -1.28, "06:30", p1["email"], email2=p2["email"])
        s2 = make_student(2, -1.29, "06:45", p3["email"])

        yield {
            "marker": marker,
            "driver": driver, "driver_headers": pin_login(client, driver["pin"]),
            "bus": bus, "school": school,
            "morning": morning, "afternoon": afternoon,
            "s1": s1, "s2": s2,
            "p1": p1, "p2": p2, "p3": p3, "p4": p4,
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
        for parent in (p1, p2, p3, p4):
            client.delete(f"/api/accounts/parents/{parent['id']}", headers=admin_headers)


# Helpers ----------------------------------------------------------------------

def cancel(client, parent, student_id: str, scope: str) -> httpx.Response:
    return client.post(
        "/api/parent-portal/cancel-ride",
        json={"student_id": student_id, "scope": scope},
        headers=parent["headers"],
    )


def withdraw(client, parent, student_id: str, scope: str) -> httpx.Response:
    # httpx has no json kwarg on .delete; the endpoint takes a JSON body.
    return client.request(
        "DELETE", "/api/parent-portal/cancel-ride",
        json={"student_id": student_id, "scope": scope},
        headers=parent["headers"],
    )


def _clear_absences_for(client, admin_headers, student_id: str) -> None:
    for a in client.get("/api/students/absences", headers=admin_headers).json():
        if a["student_id"] == student_id:
            client.delete(f"/api/students/absences/{a['id']}", headers=admin_headers)


def absence_row(client, admin_headers, student_id: str) -> dict | None:
    rows = [
        a for a in client.get("/api/students/absences", headers=admin_headers).json()
        if a["student_id"] == student_id
    ]
    assert len(rows) <= 1  # unique (student_id, absence_date)
    return rows[0] if rows else None


def child_row(client, parent, student_id: str) -> dict:
    children = client.get("/api/parent-portal/children", headers=parent["headers"]).json()
    return next(c for c in children if str(c["id"]) == str(student_id))


def notifications(client, parent) -> list[dict]:
    return client.get(
        "/api/push/notifications", params={"limit": 200}, headers=parent["headers"]
    ).json()


def ride_cancelled_ids(client, parent, student_id: str) -> set:
    return {
        n["id"] for n in notifications(client, parent)
        if n["type"] == "ride-cancelled" and str(n["student_id"]) == str(student_id)
    }


def cancellation_incidents(client, admin_headers, student_id: str) -> list[dict]:
    return [
        i for i in client.get("/api/incidents", headers=admin_headers).json()
        if i["type"] == "cancellation" and str(i.get("student_id")) == str(student_id)
    ]


def _purge_cancellation_incidents(client, admin_headers, student_id: str) -> None:
    """Teardown hygiene: cancellation alerts are never auto-deleted (the admin
    board is an audit surface), so tests that made one remove it."""
    for incident in cancellation_incidents(client, admin_headers, student_id):
        client.delete(f"/api/incidents/{incident['id']}", headers=admin_headers)


def marked_by_of(student_id: str) -> str | None:
    """No API exposes marked_by; read it off the stack's published Postgres."""
    with psycopg.connect(DB_URL) as conn:
        row = conn.execute(
            """
            select marked_by::text as marked_by from live_student_absences
            where student_id = %s
              and absence_date = (now() at time zone 'Africa/Nairobi')::date
            """,
            (student_id,),
        ).fetchone()
    return row[0] if row else None


def _start_run(client, driver_headers, route_id: str) -> dict:
    response = client.post(
        "/api/runs/driver/start", json={"route_id": route_id}, headers=driver_headers
    )
    assert response.status_code == 200, response.text
    return response.json()


def _end_and_delete(client, admin_headers, driver_headers, run_id: str | None) -> None:
    """Suite hygiene: end the run, then DELETE it so the completed-today gate
    never blocks later starts of the same route within this module."""
    if not run_id:
        return
    client.post("/api/runs/driver/end", json={"run_id": run_id}, headers=driver_headers)
    client.delete(f"/api/runs/{run_id}", headers=admin_headers)


def _driver_context(client, driver_headers) -> dict:
    return client.get("/api/runs/driver/context", headers=driver_headers).json()


def _complete_run_row(client, admin_headers, fleet, run_type: str) -> str:
    """An admin-created COMPLETED run row (no run_stops — the legacy shape):
    exactly what the per-scope completion and withdrawal guards key on."""
    route = fleet[run_type]
    response = client.post(
        "/api/runs",
        json={"route_id": route["id"], "bus_id": fleet["bus"]["id"],
              "type": run_type, "status": "completed"},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _wait_for(predicate, timeout: float = 10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.5)
    return None


# Cancellation roster effect (R14, R15, R19; AE4 first half) ---------------------

def test_afternoon_cancel_after_completed_morning_excludes_from_auto_board(
    client, admin_headers, fleet
):
    """AE4: morning completed, a parent cancels the afternoon — accepted, the
    absence row carries scope/source/reason and the acting parent, the
    afternoon auto-board excludes the child, the driver list flags them, and
    the run report snapshot names the cancellation."""
    driver_headers = fleet["driver_headers"]
    p1, s1, s2 = fleet["p1"], fleet["s1"], fleet["s2"]
    morning_run_id = afternoon_run_id = None
    try:
        morning_run_id = _start_run(client, driver_headers, fleet["morning"]["id"])["id"]
        ended = client.post(
            "/api/runs/driver/end", json={"run_id": morning_run_id}, headers=driver_headers
        )
        assert ended.status_code == 200, ended.text

        response = cancel(client, p1, s1["id"], "afternoon")
        assert response.status_code == 200, response.text
        assert response.json() == {"ok": True, "scope": "afternoon", "changed": True}

        row = absence_row(client, admin_headers, s1["id"])
        assert (row["scope"], row["source"]) == ("afternoon", "parent")
        assert row["reason"] == CANCELLED_BY_PARENT
        assert marked_by_of(s1["id"]) == str(p1["id"])

        child = child_row(client, p1, s1["id"])
        assert child["cancellation"] == {"scope": "afternoon", "withdrawable": True}
        assert child["display_status"] == "at-school"  # partial never writes status

        afternoon_run_id = _start_run(client, driver_headers, fleet["afternoon"]["id"])["id"]
        context = _driver_context(client, driver_headers)
        roster = {str(s["student_id"]) for s in context["run_stops"] if s["student_id"]}
        assert str(s1["id"]) not in roster
        assert str(s2["id"]) in roster
        # With an active run the driver list IS the run's roster: the pre-run
        # cancellation removed s1's stop entirely (R19's before-start shape;
        # the mid-run flag is covered by the not-boarded test below).
        flags = {s["id"]: s["absent"] for s in context["students"]}
        assert s1["id"] not in flags
        assert flags.get(s2["id"]) is False
        assert child_row(client, p1, s1["id"])["status"] == "at-school"  # never boarded

        report = client.get(
            f"/api/runs/{afternoon_run_id}/report", headers=admin_headers
        ).json()
        listed = {a["student_id"]: a["reason"] for a in report["absent_students"]}
        assert listed == {s1["id"]: CANCELLED_BY_PARENT}
    finally:
        _end_and_delete(client, admin_headers, driver_headers, afternoon_run_id)
        client.delete(f"/api/runs/{morning_run_id}", headers=admin_headers)
        _clear_absences_for(client, admin_headers, s1["id"])
        _purge_cancellation_incidents(client, admin_headers, s1["id"])


# On-bus guard + ownership boundary (R16; AE4 second half) -----------------------

def test_on_bus_cancel_rejected_and_ownership_evaluates_first(
    client, admin_headers, fleet
):
    """With the child on the bus: the owner's cancel gets the friendly 409 —
    but a non-linked parent gets the fixed 404 on BOTH verbs, for real and
    non-existent students alike. The guard must never fire first: its 409
    would tell a stranger holding a harvested UUID that the child is on the
    bus right now."""
    driver_headers = fleet["driver_headers"]
    p1, p4, s1 = fleet["p1"], fleet["p4"], fleet["s1"]
    incidents_before = {i["id"] for i in cancellation_incidents(client, admin_headers, s1["id"])}
    run_id = None
    try:
        run_id = _start_run(client, driver_headers, fleet["morning"]["id"])["id"]
        assert client.post(
            "/api/runs/driver/arrive", json={"run_id": run_id}, headers=driver_headers
        ).status_code == 200
        boarded = client.post(
            "/api/runs/driver/boarding",
            json={"student_id": s1["id"], "on_bus": True},
            headers=driver_headers,
        )
        assert boarded.status_code == 200, boarded.text

        for scope in ("morning", "day"):  # 'day' covers the active morning too
            rejected = cancel(client, p1, s1["id"], scope)
            assert rejected.status_code == 409, rejected.text
            assert "on the bus right now" in rejected.json()["detail"]
        assert absence_row(client, admin_headers, s1["id"]) is None
        assert {
            i["id"] for i in cancellation_incidents(client, admin_headers, s1["id"])
        } == incidents_before  # a refusal alerts nobody

        # Non-linked parent, same student, same moment: the fixed 404 — never
        # the on-bus 409 the owner just saw.
        for verb in (cancel, withdraw):
            response = verb(client, p4, s1["id"], "morning")
            assert response.status_code == 404, response.text
            assert response.json()["detail"] == CHILD_NOT_FOUND
        ghost = cancel(client, p4, str(uuid.uuid4()), "morning")
        assert ghost.status_code == 404
        assert ghost.json()["detail"] == CHILD_NOT_FOUND
    finally:
        _end_and_delete(client, admin_headers, driver_headers, run_id)
        _clear_absences_for(client, admin_headers, s1["id"])


# Side effects: once per real transition (R17) -----------------------------------

def test_duplicate_cancel_single_incident_and_confirmation_household_fanout(
    client, admin_headers, fleet
):
    """A duplicate submit is a 200 no-op: across two identical cancels there
    is exactly ONE 'cancellation' incident (student-stamped, covered route's
    bus, NULL driver, acting parent in the description) and exactly ONE
    'ride-cancelled' confirmation per linked parent (scope-mapped run_type,
    run_id NULL). The other household on the same bus sees nothing — no
    incident-typed rows, no confirmation."""
    p1, p2, p3, s1 = fleet["p1"], fleet["p2"], fleet["p3"], fleet["s1"]
    before_p1 = ride_cancelled_ids(client, p1, s1["id"])
    before_p2 = ride_cancelled_ids(client, p2, s1["id"])
    before_incidents = {i["id"] for i in cancellation_incidents(client, admin_headers, s1["id"])}
    try:
        first = cancel(client, p1, s1["id"], "afternoon")
        assert first.status_code == 200, first.text
        assert first.json()["changed"] is True
        row_before = absence_row(client, admin_headers, s1["id"])

        incident = _wait_for(
            lambda: next(
                (i for i in cancellation_incidents(client, admin_headers, s1["id"])
                 if i["id"] not in before_incidents),
                None,
            )
        )
        assert incident is not None, "cancellation incident never appeared"
        assert str(incident["bus_id"]) == str(fleet["bus"]["id"])  # covered route's bus
        assert incident["bus_name"] == fleet["bus"]["name"]
        assert incident["driver_id"] is None and incident["driver_name"] is None
        assert incident["run_type"] == "afternoon"
        assert incident["run_id"] is None
        assert incident["acknowledged"] is False
        assert s1["name"] in incident["description"]
        assert p1["name"] in incident["description"]  # acting parent named
        assert p1["email"] in incident["description"]

        assert _wait_for(
            lambda: len(ride_cancelled_ids(client, p1, s1["id"]) - before_p1) == 1
            and len(ride_cancelled_ids(client, p2, s1["id"]) - before_p2) == 1
        ), "confirmations never reached both linked parents"
        for parent, before in ((p1, before_p1), (p2, before_p2)):
            new_id = (ride_cancelled_ids(client, parent, s1["id"]) - before).pop()
            note = next(n for n in notifications(client, parent) if n["id"] == new_id)
            assert note["run_type"] == "afternoon"  # scope-mapped period
            assert note["run_id"] is None
            assert s1["name"] in note["body"]

        # Duplicate submit: 200, unchanged row, and NO second side effect.
        again = cancel(client, p1, s1["id"], "afternoon")
        assert again.status_code == 200, again.text
        assert again.json()["changed"] is False
        row_after = absence_row(client, admin_headers, s1["id"])
        assert (row_after["id"], row_after["scope"], row_after["source"]) == (
            row_before["id"], "afternoon", "parent",
        )
        time.sleep(2)  # give any (wrongly) scheduled background task time to land
        assert len(ride_cancelled_ids(client, p1, s1["id"]) - before_p1) == 1
        assert len(ride_cancelled_ids(client, p2, s1["id"]) - before_p2) == 1
        assert len({
            i["id"] for i in cancellation_incidents(client, admin_headers, s1["id"])
        } - before_incidents) == 1

        # The other household on the same bus: zero incident-typed rows, zero
        # confirmations for this child.
        p3_feed = notifications(client, p3)
        assert [n for n in p3_feed if n["type"] == "incident"] == []
        assert ride_cancelled_ids(client, p3, s1["id"]) == set()

        # Both linked parents see the pending cancellation; profile rides along.
        for parent in (p1, p2):
            assert child_row(client, parent, s1["id"])["cancellation"] == {
                "scope": "afternoon", "withdrawable": True,
            }
        profile = client.get("/api/parent-portal/profile", headers=p1["headers"]).json()
        assert all("cancellation" in c for c in profile["children"])

        withdrawn = withdraw(client, p1, s1["id"], "afternoon")
        assert withdrawn.status_code == 200, withdrawn.text
        assert withdrawn.json() == {"ok": True, "deleted": True, "scope": None}
        assert absence_row(client, admin_headers, s1["id"]) is None
        assert child_row(client, p1, s1["id"])["cancellation"] is None
    finally:
        _clear_absences_for(client, admin_headers, s1["id"])
        _purge_cancellation_incidents(client, admin_headers, s1["id"])


# Per-scope completion + withdrawal guards (R16, R18) ----------------------------

def test_day_cancel_after_completed_morning_records_afternoon_and_withdraw_guards(
    client, admin_headers, fleet
):
    """'day' after a completed morning is ACCEPTED and records 'afternoon'
    (the parent means "not riding the rest of today"); the confirmation
    carries the narrowed run_type. Per-scope completion 409s follow, then
    the withdrawal guard: a covered-type run ROW existing today blocks the
    withdrawal — completion does not reopen it, only deleting the run does
    (accepted admin-destructive behavior)."""
    p1, p2, s1 = fleet["p1"], fleet["p2"], fleet["s1"]
    morning_run = afternoon_run = None
    before_p1 = ride_cancelled_ids(client, p1, s1["id"])
    try:
        morning_run = _complete_run_row(client, admin_headers, fleet, "morning")

        response = cancel(client, p2, s1["id"], "day")
        assert response.status_code == 200, response.text
        assert response.json() == {"ok": True, "scope": "afternoon", "changed": True}
        row = absence_row(client, admin_headers, s1["id"])
        assert (row["scope"], row["source"]) == ("afternoon", "parent")

        # The narrowed scope reaches the co-parent's confirmation as the
        # afternoon period — the parent will look under the Afternoon chip.
        assert _wait_for(
            lambda: len(ride_cancelled_ids(client, p1, s1["id"]) - before_p1) == 1
        )
        new_id = (ride_cancelled_ids(client, p1, s1["id"]) - before_p1).pop()
        note = next(n for n in notifications(client, p1) if n["id"] == new_id)
        assert note["run_type"] == "afternoon"

        # Per-scope completion: the completed morning blocks a morning cancel.
        blocked = cancel(client, p2, s1["id"], "morning")
        assert blocked.status_code == 409, blocked.text
        assert "already been completed" in blocked.json()["detail"]

        afternoon_run = _complete_run_row(client, admin_headers, fleet, "afternoon")

        # Both halves completed: 'day' and 'afternoon' have nothing to cancel.
        both = cancel(client, p2, s1["id"], "day")
        assert both.status_code == 409, both.text
        assert "nothing left to cancel" in both.json()["detail"]
        assert cancel(client, p2, s1["id"], "afternoon").status_code == 409

        # Withdrawal keys on run-row EXISTENCE for the withdrawn half.
        held = withdraw(client, p2, s1["id"], "afternoon")
        assert held.status_code == 409, held.text
        assert "no longer be withdrawn" in held.json()["detail"]
        assert child_row(client, p2, s1["id"])["cancellation"] == {
            "scope": "afternoon", "withdrawable": False,
        }

        # Deleting the completed run reopens withdrawal — run deletion is
        # already a destructive admin action; the absence row stays truth.
        assert client.delete(
            f"/api/runs/{afternoon_run}", headers=admin_headers
        ).status_code == 200
        afternoon_run = None
        reopened = withdraw(client, p2, s1["id"], "afternoon")
        assert reopened.status_code == 200, reopened.text
        assert reopened.json()["deleted"] is True
    finally:
        for run_id in (morning_run, afternoon_run):
            if run_id:
                client.delete(f"/api/runs/{run_id}", headers=admin_headers)
        _clear_absences_for(client, admin_headers, s1["id"])
        _purge_cancellation_incidents(client, admin_headers, s1["id"])


# Staff-sourced rows stay staff-owned (R19 boundary) -----------------------------

def test_staff_marked_absence_blocks_cancel_and_withdraw(client, admin_headers, fleet):
    """An office sick-day mark refuses both parent verbs with friendly 409s
    (the atomic ratchet maps to the cancel message; the pre-read maps to the
    withdraw one) and produces no confirmation, no incident, and no change
    to the staff row."""
    p3, s2 = fleet["p3"], fleet["s2"]
    try:
        marked = client.post(
            "/api/students/absences",
            json={"student_id": s2["id"], "reason": "IT CR sick day"},
            headers=admin_headers,
        )
        assert marked.status_code == 200, marked.text

        rejected = cancel(client, p3, s2["id"], "afternoon")
        assert rejected.status_code == 409, rejected.text
        assert "school has already marked" in rejected.json()["detail"]

        held = withdraw(client, p3, s2["id"], "day")
        assert held.status_code == 409, held.text
        assert "recorded by the school" in held.json()["detail"]

        row = absence_row(client, admin_headers, s2["id"])
        assert (row["scope"], row["source"], row["reason"]) == (
            "day", "admin", "IT CR sick day",
        )
        assert child_row(client, p3, s2["id"])["cancellation"] is None  # not a cancellation
        time.sleep(2)  # refusals schedule nothing; prove it
        assert ride_cancelled_ids(client, p3, s2["id"]) == set()
        assert cancellation_incidents(client, admin_headers, s2["id"]) == []
    finally:
        _clear_absences_for(client, admin_headers, s2["id"])


# Mid-run cancellation (R16, R19; System-Wide Impact) ----------------------------

def test_mid_run_not_boarded_cancel_appends_run_absences(client, admin_headers, fleet):
    """A not-yet-boarded child on an in-progress covered run CAN be cancelled
    (R16): the driver flag flips mid-run and the run_absences append makes
    the completed report list the child who never boarded — without it the
    report would omit them (the snapshot was taken before the cancel)."""
    driver_headers = fleet["driver_headers"]
    p1, s1, s2 = fleet["p1"], fleet["s1"], fleet["s2"]
    run_id = None
    try:
        run_id = _start_run(client, driver_headers, fleet["morning"]["id"])["id"]

        response = cancel(client, p1, s1["id"], "morning")
        assert response.status_code == 200, response.text

        context = _driver_context(client, driver_headers)
        flags = {s["id"]: s["absent"] for s in context["students"]}
        assert flags.get(s1["id"]) is True  # mid-run flag on the boarding list
        assert flags.get(s2["id"]) is False

        # The run row now exists for the covered half: withdrawal is closed.
        held = withdraw(client, p1, s1["id"], "morning")
        assert held.status_code == 409, held.text
        assert "already started" in held.json()["detail"]

        ended = client.post(
            "/api/runs/driver/end", json={"run_id": run_id}, headers=driver_headers
        )
        assert ended.status_code == 200, ended.text
        report = client.get(f"/api/runs/{run_id}/report", headers=admin_headers).json()
        listed = {a["student_id"]: a["reason"] for a in report["absent_students"]}
        assert listed == {s1["id"]: CANCELLED_BY_PARENT}
    finally:
        _end_and_delete(client, admin_headers, driver_headers, run_id)
        _clear_absences_for(client, admin_headers, s1["id"])
        _purge_cancellation_incidents(client, admin_headers, s1["id"])


# Merge + household withdrawal halves (R14, R18) ---------------------------------

def test_merge_to_day_and_household_half_withdrawal(client, admin_headers, fleet):
    """Morning + afternoon cancels merge to a 'day' row (only then does the
    displayed status flip to absent); the merging transition's confirmation
    carries the REQUESTED half's period. Either linked parent may withdraw
    (R18 is per household): withdrawing one half downgrades to the other and
    resets the status, withdrawing the rest deletes the row."""
    p1, p2, s1 = fleet["p1"], fleet["p2"], fleet["s1"]
    before_p2 = ride_cancelled_ids(client, p2, s1["id"])
    try:
        first = cancel(client, p1, s1["id"], "morning")
        assert first.status_code == 200, first.text
        assert first.json()["scope"] == "morning"
        assert child_row(client, p1, s1["id"])["display_status"] == "at-school"

        merged = cancel(client, p1, s1["id"], "afternoon")
        assert merged.status_code == 200, merged.text
        assert merged.json() == {"ok": True, "scope": "day", "changed": True}
        child = child_row(client, p1, s1["id"])
        assert child["cancellation"] == {"scope": "day", "withdrawable": True}
        assert child["display_status"] == "absent"  # whole-day is a real absence

        # Two transitions → two confirmations; the merge one says 'afternoon'
        # (the half this request actually cancelled), never NULL-for-day.
        assert _wait_for(
            lambda: len(ride_cancelled_ids(client, p2, s1["id"]) - before_p2) == 2
        )
        new_notes = [
            n for n in notifications(client, p2)
            if n["id"] in (ride_cancelled_ids(client, p2, s1["id"]) - before_p2)
        ]
        assert sorted(n["run_type"] for n in new_notes) == ["afternoon", "morning"]

        # The co-parent withdraws the morning half: downgrade, status reset.
        downgraded = withdraw(client, p2, s1["id"], "morning")
        assert downgraded.status_code == 200, downgraded.text
        assert downgraded.json() == {"ok": True, "deleted": False, "scope": "afternoon"}
        child = child_row(client, p2, s1["id"])
        assert child["cancellation"] == {"scope": "afternoon", "withdrawable": True}
        assert child["display_status"] == "at-school"
        assert absence_row(client, admin_headers, s1["id"])["scope"] == "afternoon"

        removed = withdraw(client, p2, s1["id"], "afternoon")
        assert removed.status_code == 200, removed.text
        assert removed.json()["deleted"] is True
        assert child_row(client, p1, s1["id"])["cancellation"] is None
        assert absence_row(client, admin_headers, s1["id"]) is None
    finally:
        _clear_absences_for(client, admin_headers, s1["id"])
        _purge_cancellation_incidents(client, admin_headers, s1["id"])


# Withdrawal friendliness (R18) --------------------------------------------------

def test_withdraw_guard_messages_no_row_and_scope_mismatch(client, admin_headers, fleet):
    """Withdrawing nothing, or the wrong half, gets a parent-readable 409
    naming what actually stands — never a silent no-op 200."""
    p3, s2 = fleet["p3"], fleet["s2"]
    try:
        nothing = withdraw(client, p3, s2["id"], "afternoon")
        assert nothing.status_code == 409, nothing.text
        assert "no cancellation" in nothing.json()["detail"]

        assert cancel(client, p3, s2["id"], "afternoon").status_code == 200
        for wrong in ("morning", "day"):
            mismatch = withdraw(client, p3, s2["id"], wrong)
            assert mismatch.status_code == 409, mismatch.text
            assert "Only the afternoon ride is cancelled" in mismatch.json()["detail"]

        assert withdraw(client, p3, s2["id"], "afternoon").status_code == 200
    finally:
        _clear_absences_for(client, admin_headers, s2["id"])
        _purge_cancellation_incidents(client, admin_headers, s2["id"])


def test_invalid_scope_rejected_with_400(client, admin_headers, fleet):
    """An unknown scope fails loudly on both verbs before any write — the
    merge expression would otherwise fold it into a whole-day absence."""
    p3, s2 = fleet["p3"], fleet["s2"]
    for verb in (cancel, withdraw):
        response = verb(client, p3, s2["id"], "evening")
        assert response.status_code == 400, response.text
        assert "Scope must be one of" in response.json()["detail"]
    assert absence_row(client, admin_headers, s2["id"]) is None


# Shared per-account limiter (POST + DELETE combined) ----------------------------

def test_rate_limited_after_20_calls_in_the_hour(client, admin_headers, fleet):
    """The 21st cancel/withdraw call inside the hour 429s. A dedicated fresh
    account keeps this deterministic: the in-process budget keys on the
    account id, and this account makes exactly these calls (404s count —
    the limiter runs first). The final DELETE proves the budget is shared
    across both verbs."""
    limited = signup_parent(client, uuid.uuid4().hex[:6], "rl")
    try:
        ghost = str(uuid.uuid4())
        for i in range(20):
            response = cancel(client, limited, ghost, "afternoon")
            assert response.status_code == 404, f"call {i + 1}: {response.text}"

        over = cancel(client, limited, ghost, "afternoon")
        assert over.status_code == 429, over.text
        assert "Too many cancellation changes" in over.json()["detail"]

        shared = withdraw(client, limited, ghost, "afternoon")
        assert shared.status_code == 429, shared.text
    finally:
        client.delete(f"/api/accounts/parents/{limited['id']}", headers=admin_headers)
