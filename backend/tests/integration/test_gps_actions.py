"""The server action envelope: trail, served position, idempotency and the
bounded purge (GPS plan U7).

Run with the stack up (scripts/start-local.sh, then scripts/sync-api.sh):

    RUN_INTEGRATION=1 ../.venv/bin/python -m pytest tests/integration/test_gps_actions.py -q

Covers R1–R3, R5, R7, R8, R11, R12, R33, R36 and AE2 of the origin document:

- every one of the seven driver actions appends exactly one trail row —
  with the fix when there is one, with the reason when there is not — and
  every writer of the served position writes all five columns together;
  Start Run stamps the session and leaves the position at the school;
- a fix-less action keeps the checkpoint behaviour (AE2); two Boards inside
  the client's cache window share a capture time and both commit;
- the idempotency ledger: replay, mismatch, in-flight, another driver's
  namespace, no key; a duplicate Arrive with a passed expected stop order is
  a recorded no-op while catch-up Arrives still advance;
- clock skew is stored and flagged, never served; a malformed or naive fix is
  `invalid` and the tap completes; an old capture time is kept as captured;
- End Run nulls the five columns (pending prompts closing as unanswered at
  End Run is U3's coverage in test_run_exceptions);
- the purge after Start Run: one bounded batch, that school only, by receipt
  time, exception and event coordinates nulled with kind and distance kept,
  week-old keys gone, skipped without error while another pass holds the
  lock; the migrate Lambda's on-demand action for a school with no Start Run;
- no log line, incident or audit detail carries a coordinate;
- the retention and "today" predicates are instant-based (no staged-clock
  fixture exists in the suites; the boundary is covered with pure SQL);
- the `gps` verify set reports the trail and fix coverage of a run started
  here.

Isolation: an own throwaway school (school_sandbox) with two drivers, two
buses, a morning and an afternoon route on bus 1, a morning route on bus 2
and three students at their own stops. Every run a test starts is purged in
a finally block; the second sandbox the purge test opens sweeps itself.
"""

import os
import random
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import psycopg
import pytest
from psycopg.rows import dict_row

from conftest import DSN, purge_run, school_sandbox

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="needs the local stack; set RUN_INTEGRATION=1",
)

BASE = os.environ.get("INTEGRATION_API_URL", "http://localhost:9001")
SCHOOL_LAT, SCHOOL_LNG = -1.30, 36.80
# Distinctive digits so a substring search over logs and incidents means
# something: nothing else in the stack carries these.
FIX_LAT, FIX_LNG = -1.234567, 36.876543
POSITION_COLUMNS = (
    "current_lat", "current_lng", "position_source", "position_at", "position_accuracy_m",
)


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE, timeout=30) as c:
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
    with school_sandbox("IT GPS School", lat=SCHOOL_LAT, lng=SCHOOL_LNG) as sb:
        yield sb


@pytest.fixture(scope="module")
def admin_headers(client, sandbox):
    return login(client, sandbox["email"], sandbox["password"])


def _create_driver(client, admin_headers, marker: str, n: int) -> dict:
    for _ in range(5):
        pin = str(random.randint(100000, 999999))
        response = client.post(
            "/api/accounts/drivers",
            json={"full_name": f"IT GPS Driver{n} {marker}",
                  "email": f"it-gps-driver{n}-{marker}@test.local",
                  "password": "test1234.", "phone": f"+25471200{n}{random.randint(100, 999)}",
                  "pin": pin},
            headers=admin_headers,
        )
        if response.status_code == 200:
            return {**response.json(), "pin": pin}
    pytest.fail(f"could not create throwaway driver: {response.text}")


@pytest.fixture(scope="module")
def fleet(client, admin_headers, sandbox):
    marker = uuid.uuid4().hex[:6]
    driver1 = _create_driver(client, admin_headers, marker, 1)
    driver2 = _create_driver(client, admin_headers, marker, 2)
    created: dict = {"buses": [], "routes": [], "students": []}
    try:
        bus1 = client.post(
            "/api/fleet/buses",
            json={"name": f"IT GPS Bus1 {marker}", "driver_id": driver1["id"]},
            headers=admin_headers,
        ).json()
        bus2 = client.post(
            "/api/fleet/buses",
            json={"name": f"IT GPS Bus2 {marker}", "driver_id": driver2["id"]},
            headers=admin_headers,
        ).json()
        created["buses"] = [bus1["id"], bus2["id"]]

        def make_route(name: str, kind: str, bus_id: str) -> dict:
            response = client.post(
                "/api/fleet/routes",
                json={"name": f"IT GPS {name} {marker}", "type": kind,
                      "bus_id": bus_id, "school_id": sandbox["id"]},
                headers=admin_headers,
            )
            assert response.status_code == 200, response.text
            return response.json()

        morning = make_route("Morning", "morning", bus1["id"])
        afternoon = make_route("Afternoon", "afternoon", bus1["id"])
        morning2 = make_route("Morning2", "morning", bus2["id"])
        created["routes"] = [morning["id"], afternoon["id"], morning2["id"]]

        def make_student(tag: str, lat: float, pickup: str, routes: list[str]) -> dict:
            response = client.post(
                "/api/students",
                json={
                    "name": f"IT GPS Kid{tag} {marker}", "parent_name": f"IT GPS Parent{tag}",
                    "parent_phone": f"+2547120003{random.randint(10, 99)}",
                    "parent_email": f"it-gps-p{tag.lower()}-{marker}@test.local",
                    "home_lat": lat, "home_lng": 36.79, "pickup_time": pickup,
                    "route_ids": routes,
                },
                headers=admin_headers,
            )
            assert response.status_code == 200, response.text
            student = response.json()
            created["students"].append(student["id"])
            return student

        a = make_student("A", -1.26, "06:20", [morning["id"], afternoon["id"]])
        b = make_student("B", -1.27, "06:30", [morning["id"], afternoon["id"]])
        c = make_student("C", -1.28, "06:40", [morning["id"], afternoon["id"]])
        z = make_student("Z", -1.25, "06:10", [morning2["id"]])

        yield {
            "marker": marker, "school_id": sandbox["id"],
            "driver1": driver1, "driver2": driver2,
            "driver_headers": pin_login(client, driver1["pin"]),
            "driver2_headers": pin_login(client, driver2["pin"]),
            "bus1": bus1, "bus2": bus2,
            "morning": morning, "afternoon": afternoon, "morning2": morning2,
            "a": a, "b": b, "c": c, "z": z,
        }
    finally:
        for run in client.get("/api/runs", headers=admin_headers).json():
            if run.get("bus_id") in created["buses"]:
                purge_run(run["id"])
        for sid in created["students"]:
            for absence in client.get("/api/students/absences", headers=admin_headers).json():
                if absence["student_id"] == sid:
                    client.delete(f"/api/students/absences/{absence['id']}", headers=admin_headers)
            client.delete(f"/api/students/{sid}", headers=admin_headers)
        for rid in created["routes"]:
            client.delete(f"/api/fleet/routes/{rid}", headers=admin_headers)
        for bid in created["buses"]:
            client.delete(f"/api/fleet/buses/{bid}", headers=admin_headers)
        for driver in (driver1, driver2):
            client.delete(f"/api/accounts/drivers/{driver['id']}", headers=admin_headers)


# Helpers ----------------------------------------------------------------------

DEVICE_ID = str(uuid.uuid4())


def iso(moment: datetime) -> str:
    """The client's shape: ISO 8601 with the device's offset (Nairobi)."""
    return moment.astimezone(timezone(timedelta(hours=3))).isoformat(timespec="milliseconds")


def fix(lat=FIX_LAT, lng=FIX_LNG, accuracy=12.0, captured_at=None, **extra) -> dict:
    captured = captured_at or iso(datetime.now(timezone.utc) - timedelta(seconds=2))
    return {"lat": lat, "lng": lng, "accuracy_m": accuracy, "captured_at": captured, **extra}


def post(client, headers, path, body, *, key="auto", raw_header=None) -> httpx.Response:
    """POST one action. ``key`` is a uuid string, "auto" (a fresh one) or None
    (no header); ``raw_header`` sends an arbitrary header value instead."""
    sent = dict(headers)
    if raw_header is not None:
        sent["Idempotency-Key"] = raw_header
    elif key == "auto":
        sent["Idempotency-Key"] = str(uuid.uuid4())
    elif key:
        sent["Idempotency-Key"] = key
    return client.post(path, json=body, headers=sent)


def key_of(response: httpx.Response) -> str | None:
    return response.request.headers.get("Idempotency-Key")


def start(client, headers, route_id, *, fix_body=None, key="auto", device=DEVICE_ID):
    body = {"route_id": route_id, "device_id": device}
    if fix_body is not None:
        body["fix"] = fix_body
    response = post(client, headers, "/api/runs/driver/start", body, key=key)
    assert response.status_code == 200, response.text
    return response


def arrive(client, headers, run_id, *, expected=None, fix_body=None, key="auto",
           include_fix=True, raw_header=None):
    body = {"run_id": run_id, "device_id": DEVICE_ID}
    if expected is not None:
        body["expected_stop_order"] = expected
    if include_fix:
        body["fix"] = fix_body if fix_body is not None else fix()
    return post(client, headers, "/api/runs/driver/arrive", body, key=key, raw_header=raw_header)


def action(client, headers, path, body, *, fix_body="default", key="auto"):
    body = {**body, "device_id": DEVICE_ID}
    if fix_body == "default":
        body["fix"] = fix()
    elif fix_body is not None:
        body["fix"] = fix_body
    return post(client, headers, path, body, key=key)


def trail(run_id: str) -> list[dict]:
    with db() as conn:
        return conn.execute(
            "select * from run_positions where run_id = %s order by received_at, id", (run_id,)
        ).fetchall()


def bus_position(bus_id: str) -> dict:
    with db() as conn:
        row = conn.execute(
            f"select {', '.join(POSITION_COLUMNS)} from live_buses where id = %s", (bus_id,)
        ).fetchone()
    return dict(row)


def run_row(run_id: str) -> dict:
    with db() as conn:
        return conn.execute("select * from live_runs where id = %s", (run_id,)).fetchone()


def key_row(school_id: str, driver_id: str, key: str) -> dict | None:
    with db() as conn:
        return conn.execute(
            "select * from driver_action_keys where school_id = %s and driver_id = %s and key = %s",
            (school_id, driver_id, key),
        ).fetchone()


def latest_session_id(user_id: str) -> str:
    with db() as conn:
        row = conn.execute(
            "select id from auth_sessions where user_id = %s and revoked_at is null "
            "order by created_at desc limit 1",
            (user_id,),
        ).fetchone()
    return str(row["id"])


def layout(run_id: str) -> dict:
    with db() as conn:
        rows = conn.execute(
            "select stop_order, student_id, is_school_gate, lat, lng from run_stops "
            "where run_id = %s order by stop_order",
            (run_id,),
        ).fetchall()
    return {
        "by_student": {str(r["student_id"]): r["stop_order"] for r in rows if r["student_id"]},
        "coords": {r["stop_order"]: (r["lat"], r["lng"]) for r in rows},
        "last": max(r["stop_order"] for r in rows),
        "gate": next((r["stop_order"] for r in rows if r["is_school_gate"]), None),
    }


def progress(run_id: str) -> int:
    return int(run_row(run_id)["stops_completed"])


def arrive_until(client, headers, run_id, stop_order: int) -> None:
    """Arrive with the client's expected_stop_order until the stop is reached."""
    while progress(run_id) < stop_order:
        response = arrive(client, headers, run_id, expected=progress(run_id) + 1)
        assert response.status_code == 200, response.text


def end_run(client, headers, run_id: str, *, fix_body=None) -> httpx.Response:
    """Account for everyone the way a driver must, then end with the envelope."""
    run = run_row(run_id)
    arrive_until(client, headers, run_id, run["total_stops"])
    context = client.get("/api/runs/driver/context", headers=headers).json()
    for student in context.get("students", []):
        if student.get("absent"):
            continue
        if run["type"] == "afternoon":
            # A child already dropped off or handed over answers 409; ignored,
            # exactly as test_students_parents.complete_run does.
            action(client, headers, "/api/runs/driver/dropoff", {"student_id": student["id"]})
        else:
            action(client, headers, "/api/runs/driver/boarding",
                   {"student_id": student["id"], "on_bus": True})
    body = {"run_id": run_id, "device_id": DEVICE_ID}
    if fix_body is not None:
        body["fix"] = fix_body
    return post(client, headers, "/api/runs/driver/end", body)


def assert_action_position(position: dict, sent: dict) -> None:
    assert (position["current_lat"], position["current_lng"]) == (sent["lat"], sent["lng"])
    assert position["position_source"] == "action"
    assert position["position_accuracy_m"] == sent["accuracy_m"]
    assert position["position_at"] == datetime.fromisoformat(sent["captured_at"])


def assert_checkpoint_position(position: dict, coords: tuple) -> None:
    assert (position["current_lat"], position["current_lng"]) == tuple(coords)
    assert position["position_source"] == "checkpoint"
    assert position["position_accuracy_m"] is None
    assert position["position_at"] is not None
    assert abs((datetime.now(timezone.utc) - position["position_at"]).total_seconds()) < 120


def assert_trail_row(row: dict, *, kind: str, key: str | None, sent: dict | None,
                     reason: str = "none", flags: tuple = ()) -> None:
    assert row["source"] == "action"
    assert row["action_kind"] == kind
    assert (str(row["action_key"]) if row["action_key"] else None) == key
    assert row["device_id"] == DEVICE_ID
    assert row["fix_reason"] == reason
    assert tuple(row["flags"]) == flags
    if sent is None:
        assert row["lat"] is None and row["lng"] is None
        assert row["accuracy_m"] is None and row["captured_at"] is None
    else:
        assert (row["lat"], row["lng"], row["accuracy_m"]) == (
            sent["lat"], sent["lng"], sent["accuracy_m"],
        )
        assert row["captured_at"] == datetime.fromisoformat(sent["captured_at"])


def _wait_for(predicate, timeout: float = 10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.2)
    return predicate()


def _clear_absences(client, admin_headers, *student_ids: str) -> None:
    for absence in client.get("/api/students/absences", headers=admin_headers).json():
        if absence["student_id"] in student_ids:
            client.delete(f"/api/students/absences/{absence['id']}", headers=admin_headers)


# Trail rows and the five columns (R1, R7, R8, R11, R36) --------------------------

def test_every_morning_action_with_a_fix_writes_one_trail_row_and_the_five_columns_together(
    client, admin_headers, fleet,
):
    h = fleet["driver_headers"]
    school = fleet["school_id"]
    driver_id = fleet["driver1"]["id"]
    bus_id = fleet["bus1"]["id"]
    f_start = fix(lat=-1.2000, lng=36.7000)  # the driver's home, not the school
    started = start(client, h, fleet["morning"]["id"], fix_body=f_start)
    run_id = started.json()["id"]
    try:
        # Start Run: trail row with the fix, session stamped, served position
        # at the school checkpoint — never the fix (R36).
        rows = trail(run_id)
        assert len(rows) == 1
        assert_trail_row(rows[0], kind="start", key=key_of(started), sent=f_start)
        assert rows[0]["session_id"] == latest_session_id(driver_id)
        assert str(run_row(run_id)["started_session_id"]) == latest_session_id(driver_id)
        assert_checkpoint_position(bus_position(bus_id), (SCHOOL_LAT, SCHOOL_LNG))
        stored = key_row(school, driver_id, key_of(started))
        assert stored is not None and stored["response"]["id"] == run_id
        assert str(stored["run_id"]) == run_id

        # Arrive with a fix: the fix, not the stop, is served (R8).
        plan = layout(run_id)
        f1 = fix(lat=-1.2611, lng=36.7911, accuracy=8.0)
        arrived = arrive(client, h, run_id, expected=1, fix_body=f1)
        assert arrived.status_code == 200, arrived.text
        assert arrived.json()["noop"] is False
        assert progress(run_id) == 1
        assert_action_position(bus_position(bus_id), f1)
        rows = trail(run_id)
        assert len(rows) == 2
        assert_trail_row(rows[1], kind="arrive", key=key_of(arrived), sent=f1)
        assert rows[1]["session_id"] == latest_session_id(driver_id)

        # Board at the reached stop, Absent for another child: each one row,
        # each moves the served position to its own fix.
        a_stop = plan["by_student"][fleet["a"]["id"]]
        arrive_until(client, h, run_id, a_stop)
        f2 = fix(lat=-1.2622, lng=36.7922, accuracy=15.0)
        boarded = action(client, h, "/api/runs/driver/boarding",
                         {"student_id": fleet["a"]["id"], "on_bus": True}, fix_body=f2)
        assert boarded.status_code == 200, boarded.text
        assert_action_position(bus_position(bus_id), f2)
        assert_trail_row(trail(run_id)[-1], kind="board", key=key_of(boarded), sent=f2)

        f3 = fix(lat=-1.2733, lng=36.7933, accuracy=20.0)
        absent = action(client, h, "/api/runs/driver/absent",
                        {"student_id": fleet["b"]["id"]}, fix_body=f3)
        assert absent.status_code == 200, absent.text
        assert_action_position(bus_position(bus_id), f3)
        assert_trail_row(trail(run_id)[-1], kind="absent", key=key_of(absent), sent=f3)

        # Every tap so far has exactly one row and one sealed key.
        rows = trail(run_id)
        assert [r["action_kind"] for r in rows].count("start") == 1
        for row in rows:
            assert row["action_key"] is not None
            sealed = key_row(school, driver_id, str(row["action_key"]))
            assert sealed is not None and sealed["response"] is not None
            assert sealed["action"] == row["action_kind"]

        # End Run: its own trail row with the fix, then nothing is current (R11).
        f_end = fix(lat=-1.3001, lng=36.8001, accuracy=9.0)
        before = len(trail(run_id))
        ended = end_run(client, h, run_id, fix_body=f_end)
        assert ended.status_code == 200, ended.text
        rows = trail(run_id)
        assert rows[-1]["action_kind"] == "end"
        assert_trail_row(rows[-1], kind="end", key=key_of(ended), sent=f_end)
        assert len(rows) > before
        assert bus_position(bus_id) == {column: None for column in POSITION_COLUMNS}
        assert run_row(run_id)["status"] == "completed"
    finally:
        purge_run(run_id)
        _clear_absences(client, admin_headers, fleet["b"]["id"])


def test_afternoon_dropoff_and_handover_with_fixes_move_the_served_position(
    client, admin_headers, fleet,
):
    h = fleet["driver_headers"]
    bus_id = fleet["bus1"]["id"]
    run_id = start(client, h, fleet["afternoon"]["id"], fix_body=fix()).json()["id"]
    try:
        plan = layout(run_id)
        a_stop = plan["by_student"][fleet["a"]["id"]]
        arrive_until(client, h, run_id, a_stop)
        f_drop = fix(lat=-1.2644, lng=36.7944, accuracy=11.0)
        dropped = action(client, h, "/api/runs/driver/dropoff",
                         {"student_id": fleet["a"]["id"]}, fix_body=f_drop)
        assert dropped.status_code == 200, dropped.text
        assert_action_position(bus_position(bus_id), f_drop)
        assert_trail_row(trail(run_id)[-1], kind="dropoff", key=key_of(dropped), sent=f_drop)

        f_hand = fix(lat=-1.2955, lng=36.8055, accuracy=30.0)
        handed = action(client, h, "/api/runs/driver/handover",
                        {"student_id": fleet["b"]["id"], "note": "Roadside, to her aunt"},
                        fix_body=f_hand)
        assert handed.status_code == 200, handed.text
        assert_action_position(bus_position(bus_id), f_hand)
        assert_trail_row(trail(run_id)[-1], kind="handover", key=key_of(handed), sent=f_hand)

        f_abs = fix(lat=-1.2866, lng=36.7966, accuracy=14.0)
        absent = action(client, h, "/api/runs/driver/absent",
                        {"student_id": fleet["c"]["id"]}, fix_body=f_abs)
        assert absent.status_code == 200, absent.text
        assert_action_position(bus_position(bus_id), f_abs)

        kinds = [r["action_kind"] for r in trail(run_id)]
        assert kinds[0] == "start"
        assert kinds.count("dropoff") == 1 and kinds.count("handover") == 1
        assert kinds.count("absent") == 1
        ended = end_run(client, h, run_id, fix_body=fix())
        assert ended.status_code == 200, ended.text
        assert bus_position(bus_id) == {column: None for column in POSITION_COLUMNS}
    finally:
        purge_run(run_id)
        _clear_absences(client, admin_headers, fleet["c"]["id"])


def test_fix_less_actions_keep_the_checkpoint_behaviour_and_record_the_reason(
    client, fleet,
):
    """AE2: a denied, timed-out or absent fix never blocks the tap; the trail
    row carries the reason and the served position is the planned checkpoint,
    five columns consistent."""
    h = fleet["driver_headers"]
    bus_id = fleet["bus1"]["id"]
    started = start(client, h, fleet["morning"]["id"], fix_body={"reason": "denied"})
    run_id = started.json()["id"]
    try:
        assert_trail_row(trail(run_id)[0], kind="start", key=key_of(started), sent=None,
                         reason="denied")
        assert_checkpoint_position(bus_position(bus_id), (SCHOOL_LAT, SCHOOL_LNG))

        plan = layout(run_id)
        # An older client: no `fix` key in the body at all.
        older = arrive(client, h, run_id, expected=1, include_fix=False)
        assert older.status_code == 200, older.text
        assert_trail_row(trail(run_id)[-1], kind="arrive", key=key_of(older), sent=None,
                         reason="none")
        assert_checkpoint_position(bus_position(bus_id), plan["coords"][1])

        timed_out = arrive(client, h, run_id, expected=2, fix_body={"reason": "timeout"})
        assert timed_out.status_code == 200, timed_out.text
        assert_trail_row(trail(run_id)[-1], kind="arrive", key=key_of(timed_out), sent=None,
                         reason="timeout")
        assert_checkpoint_position(bus_position(bus_id), plan["coords"][2])
        checkpoint = bus_position(bus_id)

        # A Board without a fix writes its row and leaves the checkpoint alone.
        first = min(plan["by_student"], key=plan["by_student"].get)
        boarded = action(client, h, "/api/runs/driver/boarding",
                         {"student_id": first, "on_bus": True}, fix_body={"reason": "unavailable"})
        assert boarded.status_code == 200, boarded.text
        assert_trail_row(trail(run_id)[-1], kind="board", key=key_of(boarded), sent=None,
                         reason="unavailable")
        assert bus_position(bus_id) == checkpoint
    finally:
        purge_run(run_id)


def test_two_boards_inside_the_cache_window_share_a_capture_time_and_both_commit(
    client, fleet,
):
    h = fleet["driver_headers"]
    run_id = start(client, h, fleet["morning"]["id"], fix_body=fix()).json()["id"]
    try:
        plan = layout(run_id)
        arrive_until(client, h, run_id, max(plan["by_student"][fleet["a"]["id"]],
                                            plan["by_student"][fleet["b"]["id"]]))
        shared = iso(datetime.now(timezone.utc) - timedelta(seconds=3))
        one = action(client, h, "/api/runs/driver/boarding",
                     {"student_id": fleet["a"]["id"], "on_bus": True},
                     fix_body=fix(captured_at=shared))
        two = action(client, h, "/api/runs/driver/boarding",
                     {"student_id": fleet["b"]["id"], "on_bus": True},
                     fix_body=fix(captured_at=shared))
        assert one.status_code == 200, one.text
        assert two.status_code == 200, two.text
        boards = [r for r in trail(run_id) if r["action_kind"] == "board"]
        assert len(boards) == 2
        assert boards[0]["captured_at"] == boards[1]["captured_at"]
        assert boards[0]["action_key"] != boards[1]["action_key"]
    finally:
        purge_run(run_id)


# Idempotency (R33) ----------------------------------------------------------------

def test_replay_mismatch_other_driver_and_keyless_requests(client, fleet):
    h = fleet["driver_headers"]
    school = fleet["school_id"]
    driver_id = fleet["driver1"]["id"]
    run_id = start(client, h, fleet["morning"]["id"], fix_body=fix()).json()["id"]
    run2_id = None
    try:
        key = str(uuid.uuid4())
        f1 = fix(lat=-1.2611, lng=36.7911)
        first = arrive(client, h, run_id, expected=1, fix_body=f1, key=key)
        assert first.status_code == 200, first.text
        rows_after_first = len(trail(run_id))

        # Same key, same envelope: the stored response, nothing written.
        again = arrive(client, h, run_id, expected=1, fix_body=f1, key=key)
        assert again.status_code == 200, again.text
        assert again.json() == first.json()
        assert progress(run_id) == 1
        assert len(trail(run_id)) == rows_after_first
        stored = key_row(school, driver_id, key)
        assert stored is not None and stored["action"] == "arrive"
        assert stored["response"] == first.json()

        # Same key, a different payload: 409 idempotency-mismatch, no body
        # of the stored response and no execution.
        for body_change in (
            dict(expected=2, fix_body=f1),
            dict(expected=1, fix_body=fix(lat=-1.2999, lng=36.7999)),
        ):
            clash = arrive(client, h, run_id, key=key, **body_change)
            assert clash.status_code == 409, clash.text
            detail = clash.json()["detail"]
            assert detail["code"] == "idempotency-mismatch"
            assert set(detail) == {"code", "message"}
        assert progress(run_id) == 1
        assert len(trail(run_id)) == rows_after_first

        # Another driver at the same school with the same key: their own
        # namespace, executes normally.
        other = start(client, fleet["driver2_headers"], fleet["morning2"]["id"],
                      fix_body=fix(), key=key)
        run2_id = other.json()["id"]
        assert other.json()["id"] != run_id
        assert key_row(school, fleet["driver2"]["id"], key)["action"] == "start"

        # No header, and a header that is not a UUID: normal execution, no
        # key row, action_key null on the trail row.
        keyless = arrive(client, h, run_id, expected=2, key=None)
        assert keyless.status_code == 200, keyless.text
        assert progress(run_id) == 2
        assert trail(run_id)[-1]["action_key"] is None
        malformed = arrive(client, h, run_id, expected=3, raw_header="not-a-uuid")
        assert malformed.status_code == 200, malformed.text
        assert progress(run_id) == 3
        assert trail(run_id)[-1]["action_key"] is None
        with db() as conn:
            keys_for_run = conn.execute(
                "select count(*) as n from driver_action_keys where run_id = %s", (run_id,)
            ).fetchone()["n"]
        assert keys_for_run == 2  # the start and the keyed arrive; nothing for the other two
    finally:
        purge_run(run_id)
        purge_run(run2_id)


def test_a_replayed_dropoff_answers_200_instead_of_already_confirmed(client, fleet):
    """The claim sits before the business guards: a retry of a committed
    drop-off whose response was lost replays, it does not meet the 409."""
    h = fleet["driver_headers"]
    run_id = start(client, h, fleet["afternoon"]["id"], fix_body=fix()).json()["id"]
    try:
        plan = layout(run_id)
        arrive_until(client, h, run_id, plan["by_student"][fleet["a"]["id"]])
        key = str(uuid.uuid4())
        f = fix()
        first = action(client, h, "/api/runs/driver/dropoff",
                       {"student_id": fleet["a"]["id"]}, fix_body=f, key=key)
        assert first.status_code == 200, first.text
        retry = action(client, h, "/api/runs/driver/dropoff",
                       {"student_id": fleet["a"]["id"]}, fix_body=f, key=key)
        assert retry.status_code == 200, retry.text
        assert retry.json() == first.json()
        # A NEW tap for the same child still meets the guard.
        fresh = action(client, h, "/api/runs/driver/dropoff", {"student_id": fleet["a"]["id"]})
        assert fresh.status_code == 409
        assert "already confirmed" in fresh.json()["detail"]
    finally:
        purge_run(run_id)


def test_two_simultaneous_requests_with_one_key_execute_once(client, fleet):
    """Threads against the live API: one advances, the other replays (or,
    when the first is still executing past the lock wait, answers in-flight
    and the same envelope is retried, as the client does)."""
    h = fleet["driver_headers"]
    run_id = start(client, h, fleet["morning"]["id"], fix_body=fix()).json()["id"]
    try:
        key = str(uuid.uuid4())
        body = {"run_id": run_id, "expected_stop_order": 1, "device_id": DEVICE_ID, "fix": fix()}
        headers = {**h, "Idempotency-Key": key}
        barrier = threading.Barrier(2)
        results: list[dict] = []

        def fire():
            with httpx.Client(base_url=BASE, timeout=30) as c:
                barrier.wait()
                response = c.post("/api/runs/driver/arrive", json=body, headers=headers)
                for _ in range(5):
                    if response.status_code != 409:
                        break
                    detail = response.json().get("detail") or {}
                    if detail.get("code") != "idempotency-in-flight":
                        break
                    time.sleep(0.5)
                    response = c.post("/api/runs/driver/arrive", json=body, headers=headers)
                results.append({"status": response.status_code, "body": response.json()})

        threads = [threading.Thread(target=fire) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        assert [r["status"] for r in results] == [200, 200], results
        assert results[0]["body"] == results[1]["body"]
        assert results[0]["body"]["noop"] is False
        assert progress(run_id) == 1
        arrives = [r for r in trail(run_id) if r["action_kind"] == "arrive"]
        assert len(arrives) == 1
        with db() as conn:
            assert conn.execute(
                "select count(*) as n from driver_action_keys where key = %s", (key,)
            ).fetchone()["n"] == 1
    finally:
        purge_run(run_id)


def test_a_stale_expected_stop_order_is_a_recorded_noop_and_catch_up_still_advances(
    client, admin_headers, fleet,
):
    h = fleet["driver_headers"]
    bus_id = fleet["bus1"]["id"]
    run_id = start(client, h, fleet["morning"]["id"], fix_body=fix()).json()["id"]
    try:
        f1 = fix(lat=-1.2611, lng=36.7911)
        first = arrive(client, h, run_id, expected=1, fix_body=f1)
        assert first.status_code == 200 and progress(run_id) == 1
        served = bus_position(bus_id)
        incidents_before = _incident_count(run_id)
        rows_before = len(trail(run_id))

        # The same intention again under a NEW key (a re-tap, not a retry):
        # recorded, nothing moves.
        f_dup = fix(lat=-1.2999, lng=36.7999)
        dup = arrive(client, h, run_id, expected=1, fix_body=f_dup)
        assert dup.status_code == 200, dup.text
        body = dup.json()
        assert body["noop"] is True
        assert body["arrival_incident"] is None and body["prompts"] == []
        assert body["run"]["id"] == run_id and body["run"]["stops_completed"] == 1
        assert set(body) == {"run", "arrival_incident", "prompts", "noop"}
        assert progress(run_id) == 1
        rows = trail(run_id)
        assert len(rows) == rows_before + 1
        assert_trail_row(rows[-1], kind="arrive", key=key_of(dup), sent=f_dup)
        assert bus_position(bus_id) == served  # the no-op never moves the served position
        assert _incident_count(run_id) == incidents_before

        # An expectation AHEAD of the run is a no-op too (a stale client in the
        # other direction); the client refreshes either way.
        ahead = arrive(client, h, run_id, expected=5)
        assert ahead.status_code == 200 and ahead.json()["noop"] is True
        assert progress(run_id) == 1

        # Without the field (older client): today's catch-up behaviour.
        catch_up = arrive(client, h, run_id)
        assert catch_up.status_code == 200 and catch_up.json()["noop"] is False
        assert progress(run_id) == 2
        assert arrive(client, h, run_id, expected=3).json()["noop"] is False
        assert progress(run_id) == 3
    finally:
        purge_run(run_id)


def _incident_count(run_id: str) -> int:
    with db() as conn:
        return conn.execute(
            "select count(*) as n from live_incidents where run_id = %s", (run_id,)
        ).fetchone()["n"]


# Capture time and validity (R2, R40) -------------------------------------------

def test_skewed_invalid_naive_and_old_capture_times(client, fleet):
    h = fleet["driver_headers"]
    bus_id = fleet["bus1"]["id"]
    run_id = start(client, h, fleet["morning"]["id"], fix_body=fix()).json()["id"]
    try:
        plan = layout(run_id)
        # Minutes before receipt: kept as captured and served with that time.
        earlier = fix(captured_at=iso(datetime.now(timezone.utc) - timedelta(minutes=6)))
        old = arrive(client, h, run_id, expected=1, fix_body=earlier)
        assert old.status_code == 200, old.text
        assert_action_position(bus_position(bus_id), earlier)
        assert trail(run_id)[-1]["flags"] == []
        served = bus_position(bus_id)

        # Ten minutes in the future: stored, flagged, never served.
        ahead = fix(lat=-1.2777, lng=36.7777,
                    captured_at=iso(datetime.now(timezone.utc) + timedelta(minutes=10)))
        by_order = sorted(plan["by_student"], key=plan["by_student"].get)
        first, second = by_order[0], by_order[1]
        skewed = action(client, h, "/api/runs/driver/boarding",
                        {"student_id": first, "on_bus": True}, fix_body=ahead)
        assert skewed.status_code == 200, skewed.text
        assert_trail_row(trail(run_id)[-1], kind="board", key=key_of(skewed), sent=ahead,
                         flags=("clock-skew",))
        assert bus_position(bus_id) == served

        # Malformed shapes: reason `invalid`, nothing stored, the tap completes
        # and the checkpoint is served as if there were no fix.
        for stop_order, bad in (
            (2, "garbage"),
            (3, {"lat": 200, "lng": 36.8, "accuracy_m": 5, "captured_at": iso(datetime.now(timezone.utc))}),
        ):
            if stop_order > plan["last"]:
                break
            response = arrive(client, h, run_id, expected=stop_order, fix_body=bad)
            assert response.status_code == 200, response.text
            assert_trail_row(trail(run_id)[-1], kind="arrive", key=key_of(response), sent=None,
                             reason="invalid")
            assert_checkpoint_position(bus_position(bus_id), plan["coords"][stop_order])

        # A naive capture time (no offset) is invalid too.
        naive = {"lat": FIX_LAT, "lng": FIX_LNG, "accuracy_m": 5,
                 "captured_at": datetime.now().replace(tzinfo=None).isoformat()}
        absent = action(client, h, "/api/runs/driver/absent", {"student_id": fleet["c"]["id"]},
                        fix_body=naive)
        assert absent.status_code == 200, absent.text
        assert_trail_row(trail(run_id)[-1], kind="absent", key=key_of(absent), sent=None,
                         reason="invalid")

        # Coarse: above the cap, stored with the reason and still served with
        # its accuracy beside it.
        arrive_until(client, h, run_id, plan["by_student"][second])
        coarse = fix(lat=-1.2888, lng=36.7888, accuracy=1500.0, reason="coarse")
        response = action(client, h, "/api/runs/driver/boarding",
                          {"student_id": second, "on_bus": True}, fix_body=coarse)
        assert response.status_code == 200, response.text
        assert trail(run_id)[-1]["fix_reason"] == "coarse"
        assert_action_position(bus_position(bus_id), coarse)
    finally:
        purge_run(run_id)


# Purge (R12) ----------------------------------------------------------------------

def _seed_completed_run(client, admin_headers, bus_id: str, days_ago: int) -> str:
    date = (datetime.now(timezone(timedelta(hours=3))) - timedelta(days=days_ago)).date()
    response = client.post(
        "/api/runs",
        json={"bus_id": bus_id, "date": date.isoformat(), "status": "completed",
              "total_stops": 3},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _seed_trail(school_id: str, run_id: str, bus_id: str, *, count: int, days_ago: int,
                lat: float = FIX_LAT, lng: float = FIX_LNG) -> None:
    with db() as conn:
        conn.execute(
            """
            insert into run_positions
                (school_id, run_id, bus_id, source, action_kind, action_key, lat, lng,
                 accuracy_m, captured_at, received_at, fix_reason)
            select %s, %s, %s, 'action', 'arrive', gen_random_uuid(), %s, %s, 10,
                   now() - make_interval(days => %s) - (g || ' seconds')::interval,
                   now() - make_interval(days => %s) - (g || ' seconds')::interval,
                   'none'
            from generate_series(1, %s) as g
            """,
            (school_id, run_id, bus_id, lat, lng, days_ago, days_ago, count),
        )


def _seed_exception(
    school_id: str, run_id: str, *, days_ago: int, stop_order: int
) -> tuple[str, str]:
    with db() as conn:
        exception_id = conn.execute(
            """
            insert into run_exceptions
                (school_id, run_id, stop_order, kind, fix_lat, fix_lng, fix_accuracy_m,
                 fix_captured_at, distance_m, seen_at_stop, created_at)
            values (%s, %s, %s, 'custody-away', %s, %s, 12, now() - make_interval(days => %s),
                    1800, false, now() - make_interval(days => %s))
            returning id
            """,
            (school_id, run_id, stop_order, FIX_LAT, FIX_LNG, days_ago, days_ago),
        ).fetchone()["id"]
        event_id = conn.execute(
            """
            insert into run_exception_events
                (exception_id, school_id, run_id, fix_lat, fix_lng, fix_accuracy_m,
                 fix_captured_at, distance_m, prompt_state, response, created_at)
            values (%s, %s, %s, %s, %s, 12, now() - make_interval(days => %s), 1800,
                    'answered', 'confirmed', now() - make_interval(days => %s))
            returning id
            """,
            (exception_id, school_id, run_id, FIX_LAT, FIX_LNG, days_ago, days_ago),
        ).fetchone()["id"]
    return str(exception_id), str(event_id)


def _seed_key(school_id: str, driver_id: str, *, days_ago: int) -> str:
    key = str(uuid.uuid4())
    with db() as conn:
        conn.execute(
            """
            insert into driver_action_keys
                (school_id, driver_id, key, action, request_fingerprint, response, created_at)
            values (%s, %s, %s, 'board', 'fp', '{}'::jsonb, now() - make_interval(days => %s))
            """,
            (school_id, driver_id, key, days_ago),
        )
    return key


def _old_trail_count(school_id: str, days: int = 90) -> int:
    with db() as conn:
        return conn.execute(
            "select count(*) as n from run_positions where school_id = %s "
            "and received_at < now() - make_interval(days => %s)",
            (school_id, days),
        ).fetchone()["n"]


def _exception_coords(table: str, row_id: str) -> dict:
    with db() as conn:
        return conn.execute(
            f"select fix_lat, fix_lng, fix_accuracy_m, fix_captured_at, distance_m "  # noqa: S608
            f"from {table} where id = %s",
            (row_id,),
        ).fetchone()


def test_purge_after_start_run_is_one_batch_that_school_only_by_receipt_time_and_lock_aware(
    client, admin_headers, fleet, monkeypatch,
):
    school = fleet["school_id"]
    bus1 = fleet["bus1"]["id"]
    driver_id = fleet["driver1"]["id"]
    old_run = _seed_completed_run(client, admin_headers, bus1, days_ago=100)
    started: list[str] = []
    try:
        # 2,050 rows past retention (received 100 days ago), 5 inside it; an
        # old and a recent exception with coordinates; an 8-day-old key and a
        # fresh one. Another school's old rows must survive untouched.
        _seed_trail(school, old_run, bus1, count=2050, days_ago=100)
        _seed_trail(school, old_run, bus1, count=5, days_ago=1)
        old_x, old_e = _seed_exception(school, old_run, days_ago=100, stop_order=2)
        new_x, new_e = _seed_exception(school, old_run, days_ago=3, stop_order=3)
        old_key = _seed_key(school, driver_id, days_ago=8)
        fresh_key = _seed_key(school, driver_id, days_ago=2)
        assert _old_trail_count(school) == 2050

        with school_sandbox("IT GPS Other School", lat=-1.31, lng=36.81) as other:
            other_admin = login(client, other["email"], other["password"])
            other_bus = client.post(
                "/api/fleet/buses", json={"name": "IT GPS Other Bus"}, headers=other_admin,
            ).json()["id"]
            other_run = _seed_completed_run(client, other_admin, other_bus, days_ago=100)
            _seed_trail(other["id"], other_run, other_bus, count=30, days_ago=100)
            assert _old_trail_count(other["id"]) == 30

            # Start Run → one batch of 2,000 in the background.
            run_id = start(client, fleet["driver_headers"], fleet["morning"]["id"],
                           fix_body=fix()).json()["id"]
            started.append(run_id)
            assert _wait_for(lambda: _old_trail_count(school) <= 50)
            assert _old_trail_count(school) == 50
            assert _old_trail_count(other["id"]) == 30
            with db() as conn:
                recent = conn.execute(
                    "select count(*) as n from run_positions where run_id = %s "
                    "and received_at >= now() - interval '2 days'",
                    (old_run,),
                ).fetchone()["n"]
            assert recent == 5
            # Old exception and event: coordinates gone, kind/distance kept.
            for table, row_id in (("run_exceptions", old_x), ("run_exception_events", old_e)):
                row = _exception_coords(table, row_id)
                assert (row["fix_lat"], row["fix_lng"], row["fix_accuracy_m"],
                        row["fix_captured_at"]) == (None, None, None, None), (table, row)
                assert row["distance_m"] == 1800
            with db() as conn:
                kept = conn.execute(
                    "select kind, seen_at_stop from run_exceptions where id = %s", (old_x,)
                ).fetchone()
                assert kept["kind"] == "custody-away" and kept["seen_at_stop"] is False
                assert conn.execute(
                    "select response from run_exception_events where id = %s", (old_e,)
                ).fetchone()["response"] == "confirmed"
            for table, row_id in (("run_exceptions", new_x), ("run_exception_events", new_e)):
                row = _exception_coords(table, row_id)
                assert (row["fix_lat"], row["fix_lng"]) == (FIX_LAT, FIX_LNG), (table, row)
            assert key_row(school, driver_id, old_key) is None
            assert key_row(school, driver_id, fresh_key) is not None

            # A second Start Run while another pass holds the school's lock:
            # the run starts, the purge is skipped, nothing errors.
            with psycopg.connect(DSN) as holder:
                holder.execute(
                    "select pg_advisory_xact_lock(16, hashtext(%s))", (school,)
                )
                run2 = start(client, fleet["driver2_headers"], fleet["morning2"]["id"],
                             fix_body=fix()).json()["id"]
                started.append(run2)
                time.sleep(1.5)
                assert _old_trail_count(school) == 50
                holder.rollback()

            # The on-demand action (a school with no Start Run): loops to clean.
            monkeypatch.setenv("DATABASE_URL", DSN)
            from app import migrate_handler

            report = migrate_handler.handler({"action": "gps-purge", "school_id": school})
            assert report["status"] == "ok", report
            assert report["stopped"] == "clean"
            assert report["trail_rows_deleted"] == 50
            assert report["batches"] >= 1
            assert _old_trail_count(school) == 0
            assert _old_trail_count(other["id"]) == 30

            # The per-school retention (30 days) and the batch bound.
            with db() as conn:
                conn.execute(
                    "update live_schools set position_retention_days = 30 where id = %s",
                    (school,),
                )
            try:
                _seed_trail(school, old_run, bus1, count=4100, days_ago=40)
                _seed_trail(school, old_run, bus1, count=3, days_ago=20)
                bounded = migrate_handler.handler(
                    {"action": "gps-purge", "school_id": school, "max_batches": 1}
                )
                assert bounded["stopped"] == "batch-bound"
                assert bounded["trail_rows_deleted"] == 2000
                assert _old_trail_count(school, 30) == 2100
                rest = migrate_handler.handler({"action": "gps-purge", "school_id": school})
                assert rest["stopped"] == "clean" and rest["trail_rows_deleted"] == 2100
                assert _old_trail_count(school, 30) == 0
                assert _old_trail_count(school, 19) == 3  # inside 30 days: kept
            finally:
                with db() as conn:
                    conn.execute(
                        "update live_schools set position_retention_days = null where id = %s",
                        (school,),
                    )
            # Locked from elsewhere: the action reports it instead of waiting.
            with psycopg.connect(DSN) as holder:
                holder.execute("select pg_advisory_xact_lock(16, hashtext(%s))", (school,))
                locked = migrate_handler.handler({"action": "gps-purge", "school_id": school})
                assert locked["status"] == "ok" and locked["stopped"] == "locked"
                assert locked["batches"] == 0
                holder.rollback()
    finally:
        for run_id in started:
            purge_run(run_id)
        purge_run(old_run)


# Internal columns never leave the DAO layer --------------------------------------

def test_started_session_id_is_stamped_in_the_database_but_never_serialised(
    client, admin_headers, fleet,
):
    """The session binding is for Phase 2 pings and the trail: the column is
    written, and no run payload — Start Run, the driver context, the runs
    list for staff or driver, Arrive, the report, End Run — carries it."""
    h = fleet["driver_headers"]
    started = start(client, h, fleet["morning"]["id"], fix_body=fix())
    run_id = started.json()["id"]
    try:
        assert "started_session_id" not in started.json()
        assert str(run_row(run_id)["started_session_id"]) == latest_session_id(
            fleet["driver1"]["id"]
        )
        context = client.get("/api/runs/driver/context", headers=h).json()
        assert context["active_run"]["id"] == run_id
        assert "started_session_id" not in context["active_run"]
        for headers in (admin_headers, h):
            rows = client.get("/api/runs", headers=headers).json()
            row = next(r for r in rows if r["id"] == run_id)
            assert "started_session_id" not in row
        arrived = arrive(client, h, run_id, expected=1)
        assert arrived.status_code == 200, arrived.text
        assert "started_session_id" not in arrived.json()["run"]
        report = client.get(f"/api/runs/{run_id}/report", headers=admin_headers).json()
        assert report["id"] == run_id and "started_session_id" not in report
        ended = end_run(client, h, run_id, fix_body=fix())
        assert ended.status_code == 200, ended.text
        assert "started_session_id" not in ended.json()
        assert run_row(run_id)["started_session_id"] is not None
    finally:
        purge_run(run_id)


# Privacy: no coordinates in logs, incidents or audit (Risks) ----------------------

def test_no_log_line_incident_or_audit_detail_carries_a_coordinate(
    client, admin_headers, fleet, in_process_db, caplog, monkeypatch,
):
    import logging

    from app.api import runs_live
    from app.core.scope import SchoolScope
    from app.dao import position_dao

    school = fleet["school_id"]
    # The fix coordinates every tap in this module sends; nothing else in the
    # stack carries these digits.
    needles = ("1.234567", "36.876543")
    scope = SchoolScope(
        user_id=fleet["driver1"]["id"], school_id=school, role="driver", actor_kind="driver",
    )
    # A real pass and every failure branch of the wrapper, in-process, with
    # a run whose trail carries the distinctive coordinates.
    run_id = start(client, fleet["driver_headers"], fleet["morning"]["id"],
                   fix_body=fix()).json()["id"]
    try:
        arrive(client, fleet["driver_headers"], run_id, expected=1)
        with caplog.at_level(logging.INFO):
            runs_live._purge_after_start(scope)

            def cancelled(_scope):
                raise psycopg.errors.QueryCanceled("canceling statement due to statement timeout")

            monkeypatch.setattr(runs_live.position_dao, "purge_after_start", cancelled)
            runs_live._purge_after_start(scope)
            monkeypatch.setattr(runs_live.position_dao, "purge_after_start", lambda _s: None)
            runs_live._purge_after_start(scope)
        messages = [record.getMessage() for record in caplog.records]
        assert any("gps purge pass (school=" in m for m in messages), messages
        assert any("gps purge skipped (school=" in m and "QueryCanceled" in m for m in messages)
        assert any("another pass holds the lock" in m for m in messages)
        for message in messages:
            for needle in needles:
                assert needle not in message, message
        assert position_dao.PURGE_LOCK_CLASS == 16

        # Incidents and audit rows written around this run name stops and
        # children, never a fix.
        with db() as conn:
            texts = [
                r["description"] for r in conn.execute(
                    "select description from live_incidents where school_id = %s", (school,)
                ).fetchall()
            ] + [
                str(r["detail"]) for r in conn.execute(
                    "select detail from live_admin_audit where school_id = %s", (school,)
                ).fetchall()
            ]
        for text in texts:
            for needle in needles:
                assert needle not in (text or ""), text
    finally:
        purge_run(run_id)


# Nairobi clock: instant-based predicates --------------------------------------

def test_retention_and_today_predicates_are_instant_based_at_half_past_midnight_nairobi():
    """No staged-clock fixture exists in the suites (only the rate limiter
    fakes a monotonic clock), so the boundary is pinned with pure SQL: a row
    received at 21:30 UTC belongs to the NEXT Nairobi day for every "today"
    predicate the trail uses, and the retention cutoff — the purge's own
    expression, RETENTION_CUTOFF_SQL — is the same instant under any session
    time zone. The day-field form (`make_interval(days => 90)`) is NOT: under
    a DST zone it lands an hour off across a transition, which is why the
    purge and the verify set subtract seconds."""
    from app.dao.position_dao import RETENTION_CUTOFF_SQL

    cutoff = RETENTION_CUTOFF_SQL.replace("now()", "%s::timestamptz")
    received = "2026-09-20T21:30:00+00:00"   # 00:30 on 2026-09-21 in Nairobi
    with db() as conn:
        for zone in ("UTC", "Africa/Nairobi", "America/Los_Angeles"):
            conn.execute(f"set timezone = '{zone}'")  # noqa: S608 — fixed literals
            day = conn.execute(
                "select (%s::timestamptz at time zone 'Africa/Nairobi')::date as day",
                (received,),
            ).fetchone()["day"]
            assert day.isoformat() == "2026-09-21", zone
            # At that instant + 90 days, one second either side of the
            # boundary — identical verdicts under every zone.
            verdicts = conn.execute(
                f"select %s::timestamptz < {cutoff} as before, %s::timestamptz < {cutoff} as after",
                (received, "2026-12-19T21:29:59+00:00", 90,
                 received, "2026-12-19T21:30:01+00:00", 90),
            ).fetchone()
            assert (verdicts["before"], verdicts["after"]) == (False, True), zone
        conn.execute("set timezone = default")


# The gps verify set (Verification) -----------------------------------------------

def test_the_gps_verify_set_reports_this_runs_trail_and_fix_coverage(
    client, fleet, monkeypatch,
):
    monkeypatch.setenv("DATABASE_URL", DSN)
    from app import verify_handler

    h = fleet["driver_headers"]
    run_id = start(client, h, fleet["morning"]["id"], fix_body=fix()).json()["id"]
    try:
        assert arrive(client, h, run_id, expected=1).status_code == 200
        assert arrive(client, h, run_id, expected=2, include_fix=False).status_code == 200
        result = verify_handler.handler({"checks": "gps"})
        assert result["status"] == "ok", result
        by_label = {e["label"]: e for e in result["results"]}
        per_run = [
            r for r in by_label["trail-rows-per-run-today"]["rows"] if r["run_id"] == run_id
        ]
        assert per_run == [{"run_id": run_id, "source": "action", "rows": 3}]
        coverage = by_label["fix-coverage-ratio-today"]["rows"][0]
        assert coverage["action_rows"] >= 3
        assert coverage["with_fix"] >= 2
        assert coverage["action_rows"] >= coverage["with_fix"]
        assert by_label["trail-rows-classification-failed"]["rows"][0]["flagged"] >= 0
    finally:
        purge_run(run_id)
