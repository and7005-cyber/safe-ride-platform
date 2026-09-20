"""Stop-bypassed exceptions and the per-stop outcome predicate (GPS plan U2).

Run with the stack up (scripts/start-local.sh, then scripts/sync-api.sh):

    RUN_INTEGRATION=1 ../.venv/bin/python -m pytest tests/integration/test_run_exceptions.py -q

Covers R13, R15, R21, R22, R23 (server half), F3, AE5 and AE10 of
docs/brainstorms/2026-09-18-gps-bus-tracking-requirements.md:

- the per-stop predicate is the closure gate's own "no outcome" test keyed on
  one stop order — participation rows, covering absences and cross-bus riders
  — and the union over a run's stops always equals the gate's set (DAO-direct,
  the way the gate itself is only ever exercised through the product);
- an Arrive that moves progress past a stop whose children have no outcome
  raises exactly one exception for (run, stop order), one pending prompt event
  and one office-only lifecycle incident; catch-up Arrives and reopens never
  re-alert; the school gate, an emptied stop and the last stop raise nothing;
- open/resolved is derived at read time from current outcomes, so outcomes
  close it and an undo reopens it without a second write path;
- the staff list/review routes follow school scoping (404 across schools, 403
  for drivers and parents, the provider stepped in passes as director) and the
  review is idempotent with one audit row carrying the exception id only;
- parents' track, children and alerts readers never carry exception rows or
  the office incident.

Isolation: the module builds its own throwaway school (school_sandbox), two
drivers with known PINs, two buses, routes and five students — two of whom
share one stop so a passed stop can list several children (AE5). Every run a
test starts is purged in a finally block (a completed run would block its
route for the day), and every absence a test marks is cleared afterwards.
"""

import json
import os
import random
import time
import uuid

import httpx
import psycopg
import pytest
from psycopg.rows import dict_row

from conftest import (
    COORDINATOR_A,
    DIRECTOR_A,
    DSN,
    PROVIDER,
    SCHOOL_A_ID,
    login as seeded_login,
    purge_accounts,
    purge_run,
    school_headers,
    school_sandbox,
)
from test_students_parents import signup_parent

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="needs the local stack; set RUN_INTEGRATION=1",
)

BASE = os.environ.get("INTEGRATION_API_URL", "http://localhost:9001")
STOP_BYPASSED = "stop-bypassed"


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE, timeout=20) as c:
        yield c


def db():
    return psycopg.connect(DSN, autocommit=True, row_factory=dict_row)


def login(client: httpx.Client, email: str, password: str) -> dict:
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def pin_login(client: httpx.Client, pin: str) -> dict:
    response = client.post("/api/auth/pin-login", json={"pin": pin})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


@pytest.fixture(scope="module")
def sandbox():
    with school_sandbox("IT RX School", lat=-1.30, lng=36.80) as sb:
        yield sb


@pytest.fixture(scope="module")
def admin_headers(client, sandbox):
    return login(client, sandbox["email"], sandbox["password"])


def _create_driver(client, admin_headers, marker: str, n: int) -> dict:
    """A throwaway driver with a known PIN (retry rare PIN collisions)."""
    for _ in range(5):
        pin = str(random.randint(100000, 999999))
        response = client.post(
            "/api/accounts/drivers",
            json={"full_name": f"IT RX Driver{n} {marker}",
                  "email": f"it-rx-driver{n}-{marker}@test.local",
                  "password": "test1234.", "phone": f"+25471100{n}{random.randint(100, 999)}",
                  "pin": pin},
            headers=admin_headers,
        )
        if response.status_code == 200:
            return {**response.json(), "pin": pin}
    pytest.fail(f"could not create throwaway driver: {response.text}")


def accept_pending(client, parent_headers):
    """A staff-side link to a registered account is OFFERED (U11); the parent
    accepts the school's pending card to gain access."""
    cards = client.get("/api/parent-portal/pending", headers=parent_headers).json()
    for card in cards:
        r = client.post(
            f"/api/parent-portal/pending/{card['schoolId']}/accept", headers=parent_headers
        )
        assert r.status_code == 200, r.text


@pytest.fixture(scope="module")
def fleet(client, admin_headers, sandbox):
    """Two drivers (known PINs), two buses, a morning + afternoon route on bus 1,
    a morning route on bus 2, and five students on bus 1's routes: A, B, C at
    their own stops and D + E sharing one stop (identical home coordinates).
    D's parent is a real signed-up account with an accepted link."""
    marker = uuid.uuid4().hex[:6]
    driver1 = _create_driver(client, admin_headers, marker, 1)
    driver2 = _create_driver(client, admin_headers, marker, 2)
    created: dict = {"buses": [], "routes": [], "students": [], "parent_id": None}
    try:
        bus1 = client.post(
            "/api/fleet/buses",
            json={"name": f"IT RX Bus1 {marker}", "driver_id": driver1["id"]},
            headers=admin_headers,
        ).json()
        bus2 = client.post(
            "/api/fleet/buses",
            json={"name": f"IT RX Bus2 {marker}", "driver_id": driver2["id"]},
            headers=admin_headers,
        ).json()
        created["buses"] = [bus1["id"], bus2["id"]]
        school_id = sandbox["id"]

        def make_route(name: str, kind: str, bus_id: str) -> dict:
            response = client.post(
                "/api/fleet/routes",
                json={"name": f"IT RX {name} {marker}", "type": kind,
                      "bus_id": bus_id, "school_id": school_id},
                headers=admin_headers,
            )
            assert response.status_code == 200, response.text
            return response.json()

        morning = make_route("Morning", "morning", bus1["id"])
        afternoon = make_route("Afternoon", "afternoon", bus1["id"])
        morning2 = make_route("Morning2", "morning", bus2["id"])
        created["routes"] = [morning["id"], afternoon["id"], morning2["id"]]

        parent_id, parent_email, parent_headers = signup_parent(client, marker, "rx-parent")
        created["parent_id"] = parent_id

        def make_student(tag: str, lat: float, pickup: str, **extra) -> dict:
            payload = {
                "name": f"IT RX Kid{tag} {marker}", "parent_name": f"IT RX Parent{tag}",
                "parent_phone": f"+2547110003{random.randint(10, 99)}",
                "parent_email": f"it-rx-p{tag.lower()}-{marker}@test.local",
                "home_lat": lat, "home_lng": 36.79, "pickup_time": pickup,
                "route_ids": [morning["id"], afternoon["id"]],
            }
            payload.update(extra)
            response = client.post("/api/students", json=payload, headers=admin_headers)
            assert response.status_code == 200, response.text
            student = response.json()
            created["students"].append(student["id"])
            return student

        a = make_student("A", -1.26, "06:20")
        b = make_student("B", -1.27, "06:30")
        c = make_student("C", -1.28, "06:40")
        d = make_student("D", -1.29, "06:50", parent_email=parent_email)
        e = make_student("E", -1.29, "06:50")
        accept_pending(client, parent_headers)

        yield {
            "marker": marker,
            "school_id": school_id,
            "driver1": driver1, "driver2": driver2,
            "driver_headers": pin_login(client, driver1["pin"]),
            "driver2_headers": pin_login(client, driver2["pin"]),
            "bus1": bus1, "bus2": bus2,
            "morning": morning, "afternoon": afternoon, "morning2": morning2,
            "a": a, "b": b, "c": c, "d": d, "e": e,
            "shared": [d, e],
            "parent_headers": parent_headers,
        }
    finally:
        for run in client.get("/api/runs", headers=admin_headers).json():
            if run.get("bus_id") in created["buses"]:
                purge_run(run["id"])
        for sid in created["students"]:
            _clear_absences_for(client, admin_headers, sid)
            client.delete(f"/api/students/{sid}", headers=admin_headers)
        for rid in created["routes"]:
            client.delete(f"/api/fleet/routes/{rid}", headers=admin_headers)
        for bid in created["buses"]:
            client.delete(f"/api/fleet/buses/{bid}", headers=admin_headers)
        for driver in (driver1, driver2):
            client.delete(f"/api/accounts/drivers/{driver['id']}", headers=admin_headers)
        purge_accounts(created["parent_id"])


# Helpers ----------------------------------------------------------------------

def _clear_absences_for(client, admin_headers, student_id: str) -> None:
    for a in client.get("/api/students/absences", headers=admin_headers).json():
        if a["student_id"] == student_id:
            client.delete(f"/api/students/absences/{a['id']}", headers=admin_headers)


def _start(client, driver_headers, route_id: str) -> dict:
    response = client.post(
        "/api/runs/driver/start", json={"route_id": route_id}, headers=driver_headers
    )
    assert response.status_code == 200, response.text
    return response.json()


def _arrive(client, driver_headers, run_id: str) -> dict:
    """The full Arrive response: {run, arrival_incident, prompts}."""
    response = client.post(
        "/api/runs/driver/arrive", json={"run_id": run_id}, headers=driver_headers
    )
    assert response.status_code == 200, response.text
    return response.json()


def _progress(client, driver_headers) -> int:
    context = client.get("/api/runs/driver/context", headers=driver_headers).json()
    return int((context.get("active_run") or {}).get("stops_completed") or 0)


def _arrive_until(client, driver_headers, run_id: str, stops_completed: int) -> list[dict]:
    """Arrive one stop at a time until progress reaches ``stops_completed`` —
    no tap at all when the bus is already there (two children share a stop).
    Returns every response so a test can see which tap carried a prompt."""
    responses: list[dict] = []
    current = _progress(client, driver_headers)
    while current < stops_completed and len(responses) < stops_completed + 2:
        body = _arrive(client, driver_headers, run_id)
        responses.append(body)
        current = body["run"]["stops_completed"]
    assert current >= stops_completed, (current, stops_completed)
    return responses


def _students_by_order(fleet, layout: dict) -> dict[int, list[dict]]:
    """The fixture's children grouped by their stop order on this run."""
    out: dict[int, list[dict]] = {}
    for kid in (fleet["a"], fleet["b"], fleet["c"], fleet["d"], fleet["e"]):
        order = layout["by_student"].get(kid["id"])
        if order is not None:
            out.setdefault(order, []).append(kid)
    return out


def _board(client, driver_headers, student_id: str) -> httpx.Response:
    return client.post(
        "/api/runs/driver/boarding", json={"student_id": student_id, "on_bus": True},
        headers=driver_headers,
    )


def _dropoff(client, driver_headers, student_id: str) -> httpx.Response:
    return client.post(
        "/api/runs/driver/dropoff", json={"student_id": student_id}, headers=driver_headers
    )


def _absent(client, driver_headers, student_id: str) -> httpx.Response:
    return client.post(
        "/api/runs/driver/absent", json={"student_id": student_id}, headers=driver_headers
    )


def _reverse(client, driver_headers, student_id: str) -> httpx.Response:
    return client.post(
        "/api/runs/driver/reverse", json={"student_id": student_id}, headers=driver_headers
    )


def _layout(run_id: str) -> dict:
    """The run's own stop snapshot: student id -> stop order, plus the gate's
    order and the last order. Read from run_stops (the roster the product
    evaluates), never from the route."""
    with db() as conn:
        rows = conn.execute(
            "select stop_order, student_id, is_school_gate, name from run_stops "
            "where run_id = %s order by stop_order",
            (run_id,),
        ).fetchall()
    by_student = {str(r["student_id"]): r["stop_order"] for r in rows if r["student_id"]}
    gate = next((r["stop_order"] for r in rows if r["is_school_gate"]), None)
    names = {r["stop_order"]: r["name"] for r in rows}
    return {
        "by_student": by_student, "gate": gate,
        "last": max(r["stop_order"] for r in rows), "orders": sorted({r["stop_order"] for r in rows}),
        "names": names,
    }


def _exceptions(client, headers, run_id: str) -> httpx.Response:
    return client.get(f"/api/runs/{run_id}/exceptions", headers=headers)


def _review(client, headers, run_id: str, exception_id: str) -> httpx.Response:
    return client.post(f"/api/runs/{run_id}/exceptions/{exception_id}/review", headers=headers)


def _bypassed(client, headers, run_id: str) -> list[dict]:
    response = _exceptions(client, headers, run_id)
    assert response.status_code == 200, response.text
    return [x for x in response.json() if x["kind"] == STOP_BYPASSED]


def _incidents(client, admin_headers, run_id: str, kind: str) -> list[dict]:
    rows = client.get("/api/incidents", headers=admin_headers).json()
    return [i for i in rows if i.get("run_id") == run_id and i.get("type") == kind]


def _wait_for(predicate, timeout: float = 10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.25)
    return predicate()


def _pending_events(exception_id: str) -> list[dict]:
    with db() as conn:
        return conn.execute(
            "select * from run_exception_events where exception_id = %s "
            "and prompt_state = 'pending' order by created_at",
            (exception_id,),
        ).fetchall()


def _exception_rows(run_id: str) -> list[dict]:
    with db() as conn:
        return conn.execute(
            "select * from run_exceptions where run_id = %s order by stop_order", (run_id,)
        ).fetchall()


def _audit_rows(action: str, resource_id: str) -> list[dict]:
    with db() as conn:
        return conn.execute(
            "select * from live_admin_audit where action = %s and resource_id = %s "
            "order by created_at",
            (action, resource_id),
        ).fetchall()


def _names(rows: list[dict]) -> list[str]:
    return sorted(r["name"] for r in rows)


def _ids(rows: list[dict]) -> set[str]:
    return {str(r["id"]) for r in rows}


# The predicate, DAO-direct (the gate is only ever reachable through the product).

def _at_stop(run_id: str, run_type: str, stop_order: int) -> list[dict]:
    from app.dao import participation_dao

    with db() as conn:
        return participation_dao.unaccounted_at_stop(conn, run_id, run_type, stop_order)


def _gate_set(run_id: str, run_type: str) -> list[dict]:
    from app.dao import participation_dao

    with db() as conn:
        return participation_dao.unaccounted_on_run(conn, run_id, run_type)


def _assert_agrees_with_gate(run_id: str, run_type: str, layout: dict) -> None:
    """The invariant the plan pins: the per-stop factoring and the gate can
    never disagree — the union over every stop order IS the gate's set."""
    union: set[str] = set()
    for order in layout["orders"]:
        union |= _ids(_at_stop(run_id, run_type, order))
    assert union == _ids(_gate_set(run_id, run_type))


# Per-stop predicate (test-first against the gate's arms) -----------------------

def test_per_stop_predicate_names_a_stops_unrecorded_children_and_agrees_with_the_gate(
    client, fleet,
):
    """The gate's set, factored by stop order: a stop with two children and no
    outcome lists both; a single-child stop lists that child; the gate stop and
    an unknown order list nobody; a boarding removes exactly one child from
    exactly one stop. At every step the union over stops equals the gate."""
    run = _start(client, fleet["driver_headers"], fleet["morning"]["id"])
    run_id = run["id"]
    try:
        layout = _layout(run_id)
        shared = layout["by_student"][fleet["d"]["id"]]
        assert layout["by_student"][fleet["e"]["id"]] == shared, "D and E must share a stop"
        a_stop = layout["by_student"][fleet["a"]["id"]]

        assert _names(_at_stop(run_id, "morning", shared)) == _names(fleet["shared"])
        assert _names(_at_stop(run_id, "morning", a_stop)) == [fleet["a"]["name"]]
        assert _at_stop(run_id, "morning", layout["gate"]) == []
        assert _at_stop(run_id, "morning", 99) == []
        _assert_agrees_with_gate(run_id, "morning", layout)

        _arrive_until(client, fleet["driver_headers"], run_id, a_stop)
        assert _board(client, fleet["driver_headers"], fleet["a"]["id"]).status_code == 200
        assert _at_stop(run_id, "morning", a_stop) == []
        assert _names(_at_stop(run_id, "morning", shared)) == _names(fleet["shared"])
        _assert_agrees_with_gate(run_id, "morning", layout)
    finally:
        purge_run(run_id)


def test_per_stop_predicate_honours_covering_absences_like_the_gate(client, admin_headers, fleet):
    """The absence arm: a child covered by an absence for this run's period is
    accounted for at their stop, exactly as the gate treats them."""
    run = _start(client, fleet["driver_headers"], fleet["morning"]["id"])
    run_id = run["id"]
    try:
        layout = _layout(run_id)
        shared = layout["by_student"][fleet["d"]["id"]]
        assert _absent(client, fleet["driver_headers"], fleet["d"]["id"]).status_code == 200
        assert _names(_at_stop(run_id, "morning", shared)) == [fleet["e"]["name"]]
        _assert_agrees_with_gate(run_id, "morning", layout)
    finally:
        purge_run(run_id)
        _clear_absences_for(client, admin_headers, fleet["d"]["id"])


def test_per_stop_predicate_excludes_a_child_confirmed_aboard_another_run_of_the_period(
    client, admin_headers, fleet,
):
    """The cross-bus arm: a child moved to another bus's route after this run
    snapshotted its roster, and confirmed aboard that other run, is not listed
    at their stop here — the gate's own release for cross-bus riders."""
    marker = fleet["marker"]
    x = client.post(
        "/api/students",
        json={"name": f"IT RX KidX {marker}", "parent_name": "IT RX ParentX",
              "parent_phone": "+254711000399", "parent_email": f"it-rx-px-{marker}@test.local",
              "home_lat": -1.29, "home_lng": 36.79, "pickup_time": "06:50",
              "route_ids": [fleet["morning"]["id"]]},
        headers=admin_headers,
    )
    assert x.status_code == 200, x.text
    x = x.json()
    run1_id = run2_id = None
    try:
        run1 = _start(client, fleet["driver_headers"], fleet["morning"]["id"])
        run1_id = run1["id"]
        layout = _layout(run1_id)
        shared = layout["by_student"][fleet["d"]["id"]]
        assert layout["by_student"][x["id"]] == shared, "X must share D and E's stop"
        assert _names(_at_stop(run1_id, "morning", shared)) == _names(fleet["shared"] + [x])

        # The office moves X to bus 2's morning route mid-run (one route per
        # period, so this is a move, not a second membership).
        moved = client.put(
            f"/api/students/{x['id']}",
            json={"name": x["name"], "parent_name": "IT RX ParentX",
                  "parent_phone": "+254711000399",
                  "parent_email": f"it-rx-px-{marker}@test.local",
                  "home_lat": -1.29, "home_lng": 36.79, "pickup_time": "06:50",
                  "route_ids": [fleet["morning2"]["id"]]},
            headers=admin_headers,
        )
        assert moved.status_code == 200, moved.text

        run2 = _start(client, fleet["driver2_headers"], fleet["morning2"]["id"])
        run2_id = run2["id"]
        x_stop = _layout(run2_id)["by_student"][x["id"]]
        _arrive_until(client, fleet["driver2_headers"], run2_id, x_stop)
        assert _board(client, fleet["driver2_headers"], x["id"]).status_code == 200

        # Run 1's snapshot still holds X, but X is confirmed aboard run 2.
        assert _names(_at_stop(run1_id, "morning", shared)) == _names(fleet["shared"])
        assert x["id"] not in _ids(_gate_set(run1_id, "morning"))
        _assert_agrees_with_gate(run1_id, "morning", layout)
    finally:
        purge_run(run2_id)
        purge_run(run1_id)
        client.delete(f"/api/students/{x['id']}", headers=admin_headers)


def test_per_stop_predicate_afternoon_arms_follow_the_gate(client, fleet):
    """Afternoon: the presumed auto-board is no outcome; a confirmed drop-off is;
    an undo puts the child back — the gate's afternoon arm, per stop."""
    run = _start(client, fleet["driver_headers"], fleet["afternoon"]["id"])
    run_id = run["id"]
    try:
        layout = _layout(run_id)
        shared = layout["by_student"][fleet["d"]["id"]]
        assert _names(_at_stop(run_id, "afternoon", shared)) == _names(fleet["shared"])
        _assert_agrees_with_gate(run_id, "afternoon", layout)

        _arrive_until(client, fleet["driver_headers"], run_id, shared)
        assert _dropoff(client, fleet["driver_headers"], fleet["d"]["id"]).status_code == 200
        assert _names(_at_stop(run_id, "afternoon", shared)) == [fleet["e"]["name"]]
        _assert_agrees_with_gate(run_id, "afternoon", layout)

        assert _reverse(client, fleet["driver_headers"], fleet["d"]["id"]).status_code == 200
        assert _names(_at_stop(run_id, "afternoon", shared)) == _names(fleet["shared"])
        _assert_agrees_with_gate(run_id, "afternoon", layout)
    finally:
        purge_run(run_id)


# Arrive raises, outcomes resolve (AE5, F3) ---------------------------------------

def _drive_to_shared_stop(client, fleet, run_id: str) -> dict:
    """Morning: board A, B and C at their stops so only the shared stop is left
    unrecorded, then Arrive past it. Returns {layout, shared, responses}."""
    layout = _layout(run_id)
    shared = layout["by_student"][fleet["d"]["id"]]
    for kid in (fleet["a"], fleet["b"], fleet["c"]):
        _arrive_until(client, fleet["driver_headers"], run_id, layout["by_student"][kid["id"]])
        assert _board(client, fleet["driver_headers"], kid["id"]).status_code == 200
    responses = _arrive_until(client, fleet["driver_headers"], run_id, shared + 1)
    return {"layout": layout, "shared": shared, "responses": responses}


def test_ae5_arrive_past_an_unrecorded_stop_raises_one_exception_that_outcomes_resolve(
    client, admin_headers, fleet,
):
    """AE5: the shared stop has D and E with no outcome; Arrive at the next stop
    creates one exception listing both, one pending prompt, one office incident
    naming the stop and the children (never coordinates). Boarding one and
    marking the other absent resolves it on read; the run report agrees."""
    run = _start(client, fleet["driver_headers"], fleet["morning"]["id"])
    run_id = run["id"]
    try:
        drive = _drive_to_shared_stop(client, fleet, run_id)
        shared, layout = drive["shared"], drive["layout"]
        # No Arrive before the pass carried a prompt; the passing one did.
        for body in drive["responses"][:-1]:
            assert body["prompts"] == [], body
        prompts = drive["responses"][-1]["prompts"]
        assert len(prompts) == 1, prompts
        prompt = prompts[0]
        assert prompt["kind"] == STOP_BYPASSED
        assert prompt["stop_order"] == shared
        assert _names(prompt["students"]) == _names(fleet["shared"])
        assert prompt["event_id"] and prompt["exception_id"]

        rows = _bypassed(client, admin_headers, run_id)
        assert len(rows) == 1, rows
        row = rows[0]
        assert row["id"] == prompt["exception_id"]
        assert row["stop_order"] == shared
        assert row["student_id"] is None, "a bypassed stop is per stop, not per child"
        assert row["status"] == "open"
        assert _names(row["students"]) == _names(fleet["shared"])
        assert row["reviewed_at"] is None and row["reviewed_by"] is None
        assert row["stop_name"] == layout["names"][shared]
        pending = [e for e in row["events"] if e["prompt_state"] == "pending"]
        assert len(pending) == 1 and pending[0]["id"] == prompt["event_id"]

        incidents = _wait_for(lambda: _incidents(client, admin_headers, run_id, STOP_BYPASSED))
        assert len(incidents) == 1, incidents
        description = incidents[0]["description"]
        assert layout["names"][shared] in description
        for kid in fleet["shared"]:
            assert kid["name"] in description
        assert "-1.29" not in description and "36.79" not in description
        assert incidents[0]["lifecycle"] is True and incidents[0]["acknowledged"] is True

        # The outcomes AE5 names: one boarded, one absent. The exception reads
        # resolved without anyone writing to it.
        assert _board(client, fleet["driver_headers"], fleet["d"]["id"]).status_code == 200
        half = _bypassed(client, admin_headers, run_id)[0]
        assert half["status"] == "open" and _names(half["students"]) == [fleet["e"]["name"]]
        assert _absent(client, fleet["driver_headers"], fleet["e"]["id"]).status_code == 200
        done = _bypassed(client, admin_headers, run_id)[0]
        assert done["status"] == "resolved" and done["students"] == []

        report = client.get(f"/api/runs/{run_id}/report", headers=admin_headers)
        assert report.status_code == 200, report.text
        reported = [x for x in report.json()["exceptions"] if x["kind"] == STOP_BYPASSED]
        assert len(reported) == 1 and reported[0]["status"] == "resolved"
        assert reported[0]["id"] == prompt["exception_id"]
    finally:
        purge_run(run_id)
        _clear_absences_for(client, admin_headers, fleet["e"]["id"])


def test_catch_up_arrives_evaluate_a_passed_stop_once(client, admin_headers, fleet):
    """Two rapid Arrives at the end of the run re-evaluate the same passed stop:
    one exception row, one pending event, one incident, and only the first
    evaluation returns a prompt."""
    run = _start(client, fleet["driver_headers"], fleet["morning"]["id"])
    run_id = run["id"]
    try:
        drive = _drive_to_shared_stop(client, fleet, run_id)
        shared, layout = drive["shared"], drive["layout"]
        assert shared + 1 == layout["last"], "the shared stop precedes the last stop"
        first = drive["responses"][-1]["prompts"]
        assert len(first) == 1

        # Catch-up taps at the last stop: progress is capped, the passed stop is
        # evaluated again and nothing new is written.
        again = _arrive(client, fleet["driver_headers"], run_id)
        once_more = _arrive(client, fleet["driver_headers"], run_id)
        assert again["run"]["stops_completed"] == layout["last"]
        assert again["prompts"] == [] and once_more["prompts"] == []

        rows = _exception_rows(run_id)
        assert len(rows) == 1 and rows[0]["stop_order"] == shared
        assert len(_pending_events(str(rows[0]["id"]))) == 1
        _wait_for(lambda: _incidents(client, admin_headers, run_id, STOP_BYPASSED))
        time.sleep(0.5)  # give a wrongly duplicated alert time to land
        assert len(_incidents(client, admin_headers, run_id, STOP_BYPASSED)) == 1
    finally:
        purge_run(run_id)


def _inject_exception_insert_failure() -> None:
    """Fault injection: every insert into run_exceptions raises, so the
    savepoint tier fails exactly where a real write error would."""
    with db() as conn:
        conn.execute(
            "create or replace function it_rx_inject_failure() returns trigger "
            "language plpgsql as $$ begin "
            "raise exception 'injected: run_exceptions insert refused'; end $$"
        )
        conn.execute("drop trigger if exists it_rx_inject_failure on run_exceptions")
        conn.execute(
            "create trigger it_rx_inject_failure before insert on run_exceptions "
            "for each row execute function it_rx_inject_failure()"
        )


def _clear_injected_failure() -> None:
    with db() as conn:
        conn.execute("drop trigger if exists it_rx_inject_failure on run_exceptions")
        conn.execute("drop function if exists it_rx_inject_failure()")


def test_a_failing_evaluation_never_blocks_the_arrive(client, admin_headers, fleet):
    """R23/R40: the check, the upsert and the prompt event run in a savepoint.
    With the exception insert made to fail, the Arrive still commits — progress
    moves and the stop is stamped — with no prompt, no exception row and no
    incident. With the fault gone, a catch-up Arrive at the last stop
    re-evaluates the passed stop and raises it."""
    _inject_exception_insert_failure()
    injected = True
    run = _start(client, fleet["driver_headers"], fleet["morning"]["id"])
    run_id = run["id"]
    try:
        drive = _drive_to_shared_stop(client, fleet, run_id)
        passing = drive["responses"][-1]
        assert passing["prompts"] == [], passing
        assert passing["run"]["stops_completed"] == drive["shared"] + 1
        with db() as conn:
            stamped = conn.execute(
                "select arrived_at from run_stops where run_id = %s and stop_order = %s",
                (run_id, drive["shared"] + 1),
            ).fetchall()
        assert stamped and all(r["arrived_at"] is not None for r in stamped), (
            "the Arrive did not commit"
        )
        assert _exception_rows(run_id) == []
        time.sleep(0.5)
        assert _incidents(client, admin_headers, run_id, STOP_BYPASSED) == []

        _clear_injected_failure()
        injected = False
        again = _arrive(client, fleet["driver_headers"], run_id)
        assert again["run"]["stops_completed"] == drive["layout"]["last"]
        assert len(again["prompts"]) == 1 and again["prompts"][0]["stop_order"] == drive["shared"]
        assert len(_exception_rows(run_id)) == 1
    finally:
        if injected:
            _clear_injected_failure()
        purge_run(run_id)


def test_the_school_gate_and_an_emptied_stop_raise_nothing(client, admin_headers, fleet):
    """Afternoon: the gate is stop 1 — passing it raises nothing. A stop whose
    only child left the school mid-run (run_stops keeps the row, student null)
    has no students and raises nothing either."""
    marker = fleet["marker"]
    f = client.post(
        "/api/students",
        json={"name": f"IT RX KidF {marker}", "parent_name": "IT RX ParentF",
              "parent_phone": "+254711000398", "parent_email": f"it-rx-pf-{marker}@test.local",
              "home_lat": -1.25, "home_lng": 36.79, "pickup_time": "06:10",
              "route_ids": [fleet["afternoon"]["id"]]},
        headers=admin_headers,
    )
    assert f.status_code == 200, f.text
    f = f.json()
    run_id = None
    try:
        run = _start(client, fleet["driver_headers"], fleet["afternoon"]["id"])
        run_id = run["id"]
        layout = _layout(run_id)
        assert layout["gate"] == 1, layout
        f_stop = layout["by_student"][f["id"]]
        assert f_stop == layout["last"], "the earliest pickup drops last in the afternoon"

        # Past the gate: nothing.
        second = _arrive_until(client, fleet["driver_headers"], run_id, 2)
        assert all(body["prompts"] == [] for body in second)
        assert _exception_rows(run_id) == []

        # F leaves the school; F's stop row survives with no student.
        assert client.delete(f"/api/students/{f['id']}", headers=admin_headers).status_code == 200
        with db() as conn:
            emptied = conn.execute(
                "select student_id from run_stops where run_id = %s and stop_order = %s",
                (run_id, f_stop),
            ).fetchall()
        assert emptied and all(r["student_id"] is None for r in emptied)

        # Walk the remaining stops upward, resolving each as it is reached, so
        # the only stop ever reached with nobody to record is the emptied last
        # one. No Arrive carries a prompt, no exception is ever written, and
        # End Run's gate has nothing to say.
        by_order = _students_by_order(fleet, layout)
        taps: list[dict] = []
        for order in layout["orders"]:
            if order == layout["gate"]:
                continue
            taps += _arrive_until(client, fleet["driver_headers"], run_id, order)
            for kid in by_order.get(order, []):
                assert _dropoff(client, fleet["driver_headers"], kid["id"]).status_code == 200
        assert all(body["prompts"] == [] for body in taps), taps
        assert _exception_rows(run_id) == []
        ended = client.post(
            "/api/runs/driver/end", json={"run_id": run_id}, headers=fleet["driver_headers"]
        )
        assert ended.status_code == 200, ended.text
    finally:
        purge_run(run_id)
        # Already deleted mid-test on the happy path; a repeat is a harmless 404.
        client.delete(f"/api/students/{f['id']}", headers=admin_headers)


def test_the_last_stop_belongs_to_end_runs_gate_not_to_an_exception(client, admin_headers, fleet):
    """Afternoon: every stop but the last is resolved as it is passed; the last
    stop's child stays unrecorded. Repeated Arrives at the last stop raise
    nothing, and End Run refuses naming that child — the gate owns the last
    stop, the exception never does."""
    run = _start(client, fleet["driver_headers"], fleet["afternoon"]["id"])
    run_id = run["id"]
    try:
        layout = _layout(run_id)
        by_order = _students_by_order(fleet, layout)
        last_kids = by_order[layout["last"]]
        taps: list[dict] = []
        for order in layout["orders"]:
            if order == layout["gate"]:
                continue
            taps += _arrive_until(client, fleet["driver_headers"], run_id, order)
            if order == layout["last"]:
                continue  # reached, deliberately left unrecorded
            for kid in by_order.get(order, []):
                assert _dropoff(client, fleet["driver_headers"], kid["id"]).status_code == 200
        assert all(body["prompts"] == [] for body in taps), taps
        again = _arrive(client, fleet["driver_headers"], run_id)
        assert again["prompts"] == [] and again["run"]["stops_completed"] == layout["last"]
        assert _exception_rows(run_id) == []

        refused = client.post(
            "/api/runs/driver/end", json={"run_id": run_id}, headers=fleet["driver_headers"]
        )
        assert refused.status_code == 409, refused.text
        for kid in last_kids:
            assert kid["name"] in refused.json()["detail"]
        assert _exception_rows(run_id) == []
        assert _incidents(client, admin_headers, run_id, STOP_BYPASSED) == []
    finally:
        purge_run(run_id)


def test_undoing_a_resolving_outcome_reopens_the_exception_on_read(client, admin_headers, fleet):
    """Afternoon: D and E's stop is passed, both are dropped off (resolved), then
    D's drop-off is undone. The same exception reads open again with D listed;
    no second row, event or incident is written."""
    run = _start(client, fleet["driver_headers"], fleet["afternoon"]["id"])
    run_id = run["id"]
    try:
        layout = _layout(run_id)
        shared = layout["by_student"][fleet["d"]["id"]]
        assert shared < layout["last"]
        passing = _arrive_until(client, fleet["driver_headers"], run_id, shared + 1)
        assert len(passing[-1]["prompts"]) == 1
        exception_id = passing[-1]["prompts"][0]["exception_id"]
        _wait_for(lambda: _incidents(client, admin_headers, run_id, STOP_BYPASSED))

        for kid in fleet["shared"]:
            assert _dropoff(client, fleet["driver_headers"], kid["id"]).status_code == 200
        assert _bypassed(client, admin_headers, run_id)[0]["status"] == "resolved"

        assert _reverse(client, fleet["driver_headers"], fleet["d"]["id"]).status_code == 200
        rows = _bypassed(client, admin_headers, run_id)
        assert len(rows) == 1 and rows[0]["id"] == exception_id
        assert rows[0]["status"] == "open"
        assert _names(rows[0]["students"]) == [fleet["d"]["name"]]

        # A reopen is a read-time fact, never a second exception: the run
        # carries on past the next (resolved) stop and writes nothing new —
        # one row, one pending event, one incident.
        by_order = _students_by_order(fleet, layout)
        for kid in by_order.get(shared + 1, []):
            assert _dropoff(client, fleet["driver_headers"], kid["id"]).status_code == 200
        onward = _arrive(client, fleet["driver_headers"], run_id)
        assert onward["run"]["stops_completed"] == shared + 2
        assert onward["prompts"] == []
        assert len(_exception_rows(run_id)) == 1
        assert len(_pending_events(exception_id)) == 1
        time.sleep(0.5)
        assert len(_incidents(client, admin_headers, run_id, STOP_BYPASSED)) == 1
    finally:
        purge_run(run_id)


# Staff routes: scoping, roles, review (AE10, R21) ------------------------------

def _raise_every_stop(client, fleet, run_id: str) -> list[dict]:
    """Morning, nobody recorded: Arrive to the end raises one exception per
    child stop. Returns the bypassed rows, oldest first."""
    layout = _layout(run_id)
    _arrive_until(client, fleet["driver_headers"], run_id, layout["last"])
    rows = _exception_rows(run_id)
    assert len(rows) == len(layout["orders"]) - 1, rows  # every stop but the gate
    return rows


def test_ae10_scoping_and_roles_on_the_staff_routes(client, admin_headers, sandbox, fleet):
    """A coordinator at another school cannot list or review this school's
    exceptions (404, nothing changes); a coordinator here can; a director of
    another school without a membership here gets 404; drivers and parents get
    403. The provider stepped into this school reviews as director with a
    provider-kind audit row and a masked display."""
    run = _start(client, fleet["driver_headers"], fleet["morning"]["id"])
    run_id = run["id"]
    coordinator = seeded_login(COORDINATOR_A)
    director_a = seeded_login(DIRECTOR_A)
    support_ids: list[str] = []
    with db() as conn:
        coordinator_id = str(conn.execute(
            "select id from app_users where email = %s", (COORDINATOR_A,)
        ).fetchone()["id"])
    try:
        rows = _raise_every_stop(client, fleet, run_id)
        first, second, third = (str(r["id"]) for r in rows[:3])

        # The director here (the sandbox admin) lists everything.
        listed = _exceptions(client, admin_headers, run_id)
        assert listed.status_code == 200, listed.text
        assert {x["id"] for x in listed.json()} == {str(r["id"]) for r in rows}

        # Coordinator of school A, acting in school A: this run is not there.
        foreign = school_headers(coordinator, SCHOOL_A_ID)
        assert _exceptions(client, foreign, run_id).status_code == 404
        assert _review(client, foreign, run_id, first).status_code == 404
        # …and naming this school without a membership here: not found either.
        assert _exceptions(client, school_headers(coordinator, sandbox["id"]), run_id).status_code == 404
        assert _exceptions(client, school_headers(director_a, sandbox["id"]), run_id).status_code == 404
        assert _review(client, school_headers(director_a, sandbox["id"]), run_id, first).status_code == 404
        assert all(r["reviewed_at"] is None for r in _exception_rows(run_id))
        assert _audit_rows("exception-reviewed", first) == []

        # Drivers and parents are refused outright.
        assert _exceptions(client, fleet["driver_headers"], run_id).status_code == 403
        assert _review(client, fleet["driver_headers"], run_id, first).status_code == 403
        assert _exceptions(client, fleet["parent_headers"], run_id).status_code == 403
        assert _review(client, fleet["parent_headers"], run_id, first).status_code == 403

        # Give the seeded coordinator a coordinator membership HERE: passes.
        with db() as conn:
            conn.execute(
                "insert into school_memberships (user_id, school_id, role, state, accepted_at) "
                "values (%s, %s, 'coordinator', 'active', now())",
                (coordinator_id, sandbox["id"]),
            )
        here = school_headers(coordinator, sandbox["id"])
        assert _exceptions(client, here, run_id).status_code == 200
        reviewed = _review(client, here, run_id, first)
        assert reviewed.status_code == 200, reviewed.text
        body = reviewed.json()
        assert body["id"] == first and body["reviewed_at"] is not None
        assert body["reviewed_by"] == coordinator_id
        assert body["status"] == "open", "review is independent of open/resolved"
        audit = _audit_rows("exception-reviewed", first)
        assert len(audit) == 1
        assert audit[0]["actor_kind"] == "staff"
        assert str(audit[0]["school_id"]) == sandbox["id"]
        assert audit[0]["resource_type"] == "exception"
        assert audit[0]["detail"] == {"exception_id": first}

        # The provider stepped into this school reviews as director.
        token = _provider_session(client)
        stepped = client.post(
            "/api/provider/step-in",
            json={"schoolId": sandbox["id"], "reason": "IT RX exception review"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert stepped.status_code == 200, stepped.text
        support_ids.append(stepped.json()["supportSessionId"])
        provider_here = {"Authorization": f"Bearer {token}", "X-School-Id": sandbox["id"]}
        assert _exceptions(client, provider_here, run_id).status_code == 200
        by_provider = _review(client, provider_here, run_id, second)
        assert by_provider.status_code == 200, by_provider.text
        assert by_provider.json()["reviewed_by_display"] == "SafeRide"
        audit = _audit_rows("exception-reviewed", second)
        assert len(audit) == 1 and audit[0]["actor_kind"] == "provider"
        assert str(audit[0]["support_session_id"]) == support_ids[0]
        assert client.post(
            "/api/provider/step-out", headers={"Authorization": f"Bearer {token}"}
        ).status_code == 200
        # Stepped out, the provider is a stranger to this school again.
        assert _exceptions(client, provider_here, run_id).status_code in (403, 404)

        # The third exception is still untouched by any of the above.
        assert next(r for r in _exception_rows(run_id) if str(r["id"]) == third)["reviewed_at"] is None
    finally:
        _purge_support_sessions(support_ids)
        with db() as conn:
            conn.execute(
                "delete from school_memberships where user_id = %s and school_id = %s",
                (coordinator_id, sandbox["id"]),
            )
        purge_run(run_id)


def test_reviewing_twice_is_idempotent_and_writes_one_audit_row(client, admin_headers, sandbox, fleet):
    run = _start(client, fleet["driver_headers"], fleet["morning"]["id"])
    run_id = run["id"]
    try:
        rows = _raise_every_stop(client, fleet, run_id)
        target = str(rows[0]["id"])
        first = _review(client, admin_headers, run_id, target)
        assert first.status_code == 200, first.text
        stamped = first.json()["reviewed_at"]
        assert stamped and first.json()["reviewed_by"] == sandbox["admin_id"]
        second = _review(client, admin_headers, run_id, target)
        assert second.status_code == 200, second.text
        assert second.json()["reviewed_at"] == stamped, "a repeat review moved the stamp"
        audit = _audit_rows("exception-reviewed", target)
        assert len(audit) == 1
        assert audit[0]["detail"] == {"exception_id": target}, "detail carries the id only"
        assert audit[0]["resource_id"] == target
        # The list carries the stored review state unchanged.
        listed = next(x for x in _bypassed(client, admin_headers, run_id) if x["id"] == target)
        assert listed["reviewed_at"] == stamped
        assert listed["reviewed_by_display"]
    finally:
        purge_run(run_id)


def test_error_paths_and_empty_runs(client, admin_headers, fleet):
    """An unknown run: 404. A wrong exception id, or a real one under the wrong
    run: 404. A run with no stops, and a run of another day: an empty list."""
    unknown = str(uuid.uuid4())
    assert _exceptions(client, admin_headers, unknown).status_code == 404
    assert _review(client, admin_headers, unknown, str(uuid.uuid4())).status_code == 404

    run = _start(client, fleet["driver_headers"], fleet["morning"]["id"])
    run_id = run["id"]
    other_ids: list[str] = []
    try:
        rows = _raise_every_stop(client, fleet, run_id)
        assert _review(client, admin_headers, run_id, str(uuid.uuid4())).status_code == 404

        empty = client.post(
            "/api/runs",
            json={"route_id": fleet["morning2"]["id"], "bus_id": fleet["bus2"]["id"],
                  "type": "morning", "date": "2020-01-06", "status": "completed"},
            headers=admin_headers,
        )
        assert empty.status_code == 200, empty.text
        other_ids.append(empty.json()["id"])
        listed = _exceptions(client, admin_headers, empty.json()["id"])
        assert listed.status_code == 200 and listed.json() == []
        # A real exception id under a run it does not belong to: not found.
        assert _review(client, admin_headers, empty.json()["id"], str(rows[0]["id"])).status_code == 404
        assert all(r["reviewed_at"] is None for r in _exception_rows(run_id))
    finally:
        purge_run(run_id)
        for oid in other_ids:
            purge_run(oid)


def test_staff_routes_are_classified_school_scoped_in_the_manifest():
    from tests.scope_manifest import MANIFEST

    assert MANIFEST[("GET", "/api/runs/{run_id}/exceptions")] == "school-scoped"
    assert MANIFEST[("POST", "/api/runs/{run_id}/exceptions/{exception_id}/review")] == "school-scoped"


# Parents never see exceptions (R22) ----------------------------------------------

def test_parents_track_children_and_alerts_never_carry_exceptions_or_the_office_incident(
    client, admin_headers, fleet,
):
    """R22: D's parent reads Track, the children list and the alerts feed after
    D's stop was bypassed. No exception row, no exception id, no stop-bypassed
    incident and no description naming D reaches any of them — while the office
    feed carries the incident, lifecycle-marked."""
    run = _start(client, fleet["driver_headers"], fleet["morning"]["id"])
    run_id = run["id"]
    try:
        drive = _drive_to_shared_stop(client, fleet, run_id)
        exception_id = drive["responses"][-1]["prompts"][0]["exception_id"]
        office = _wait_for(lambda: _incidents(client, admin_headers, run_id, STOP_BYPASSED))
        assert len(office) == 1 and office[0]["lifecycle"] is True

        parent = fleet["parent_headers"]
        track = client.get(
            "/api/parent-portal/track", params={"student_id": fleet["d"]["id"]}, headers=parent
        )
        assert track.status_code == 200, track.text
        children = client.get("/api/parent-portal/children", headers=parent)
        assert children.status_code == 200, children.text
        alerts = client.get("/api/parent-portal/alerts", headers=parent)
        assert alerts.status_code == 200, alerts.text

        for body in (track.json(), children.json()):
            dumped = json.dumps(body)
            assert exception_id not in dumped
            assert STOP_BYPASSED not in dumped
            assert "exception" not in dumped.lower()
        feed = alerts.json()
        assert all(row.get("type") != STOP_BYPASSED for row in feed)
        assert all(row.get("lifecycle") is not True for row in feed)
        assert exception_id not in json.dumps(feed)
        # The office description names D; nothing on the parent feed may.
        assert all(fleet["d"]["name"] not in (row.get("description") or "") for row in feed)
    finally:
        purge_run(run_id)


# Provider helpers (the seeded provider signs in with password + TOTP) -----------

@pytest.fixture(scope="module", autouse=True)
def _seeded_provider_enrolled():
    """Snapshot the seeded provider row, arrange it ENROLLED for this module
    (the API enrolment flow has its own suite), and restore it exactly."""
    from app.core.config import get_settings
    from app.core.totp import pepper_key

    with db() as conn:
        provider_id = str(conn.execute(
            "select id from app_users where email = %s", (PROVIDER,)
        ).fetchone()["id"])
        snapshot = conn.execute(
            "select totp_salt, totp_pepper_key, totp_enrolled_at, totp_last_step "
            "from provider_accounts where user_id = %s",
            (provider_id,),
        ).fetchone()
        conn.execute(
            "update provider_accounts set totp_enrolled_at = now(), totp_pepper_key = %s, "
            "totp_last_step = null where user_id = %s",
            (pepper_key(get_settings().totp_pepper), provider_id),
        )
    yield
    with db() as conn:
        conn.execute(
            "update provider_accounts set totp_salt = %s, totp_pepper_key = %s, "
            "totp_enrolled_at = %s, totp_last_step = %s where user_id = %s",
            (snapshot["totp_salt"], snapshot["totp_pepper_key"],
             snapshot["totp_enrolled_at"], snapshot["totp_last_step"], provider_id),
        )


def _provider_session(client) -> str:
    """The seeded provider's two-step sign-in against the running API. The
    container and this process share backend/.env, so the code computed here
    is the code the API expects."""
    from app.core.config import get_settings
    from app.core.totp import current_step, derive_secret, totp_code

    with db() as conn:
        row = conn.execute(
            "update provider_accounts set totp_last_step = null "
            "where user_id = (select id from app_users where email = %s) returning totp_salt",
            (PROVIDER,),
        ).fetchone()
    first = client.post("/api/auth/login", json={"email": PROVIDER, "password": "Test1234"})
    assert first.status_code == 200, first.text
    body = first.json()
    assert body.get("totpRequired") is True, body
    code = totp_code(derive_secret(get_settings().totp_pepper, row["totp_salt"]), current_step())
    second = client.post("/api/auth/totp", json={"token": body["preauth"], "code": code})
    assert second.status_code == 200, second.text
    return second.json()["token"]


def _purge_support_sessions(support_ids: list[str]) -> None:
    if not support_ids:
        return
    with db() as conn:
        conn.execute(
            "update auth_sessions set support_session_id = null "
            "where support_session_id = any(%s::uuid[])",
            (support_ids,),
        )
        conn.execute(
            "delete from provider_support_sessions where id = any(%s::uuid[])", (support_ids,)
        )
