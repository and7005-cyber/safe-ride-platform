"""Phase 2 interval pings (GPS plan U14: R24, R26, R27, R28; F6; AE15).

Run with the stack up (scripts/start-local.sh, then scripts/sync-api.sh):

    RUN_INTEGRATION=1 ../.venv/bin/python -m pytest tests/integration/test_pings.py -q

`POST /api/runs/driver/pings` is one trail insert per accepted fix and the
forward-only served position — nothing else:

- a batch of six inserts six ping rows (source, session, device, reason,
  flags) and serves the newest by capture time as source `ping`; a late
  batch older than the served position is stored and moves nothing;
  duplicates — a re-sent batch, two fixes with one capture time — insert
  nothing and are counted;
- AE15: a ping for a completed run is 409 `run-not-active` and nothing
  changes; an unknown run is 404; another driver's run is 403;
- a second auth session for the same driver is refused 409
  `session-mismatch` and the run is flagged for the office once — one
  `unverified` / `session-mismatch` row with one silent ledger event, however
  many refusals follow — while the bound session keeps pinging; a tapped
  action from the second session re-binds the run, after which its pings
  are accepted and the first session's are refused (still one row, one event);
- a batch over the cap is 422 before any SQL; a batch inside half the
  school's ping interval is 429 `ping-too-soon` with `retry_after_s` and a
  Retry-After header and inserts nothing; one after the interval is accepted;
- coarse, clock-skewed and plausibility-flagged pings are stored and never
  served; the flagged ones attach to the run's `implausible-movement` review
  row; a capture time outside the run's window and a malformed fix are
  dropped and counted, never a 422;
- the endpoint schedules no background task: a call-now row stamped due
  stays unsent across a ping and is drained by the next context poll;
- no log line carries a coordinate; the manifest lists the route.

The trail-driven nudges (GPS plan U15: R29, R30, R31; F7; AE14) ride the
same route and are tested at the bottom of this module: the arrival offer
on entering a not-yet-arrived stop (two plain pings inside the enter
radius, named in the response and the context), the Arrive to a named stop
it answers with (`target_stop_order`), the bypassed-stop exception raised
by the first exterior batch that completes a departure with children
unrecorded — row, pending prompt and office alert in the ping's own
transaction, and the next Arrive quiet about it — the hysteresis (one fix
inside does not enter, a fix between the radii does not toggle, coarse and
flagged pings change nothing), the locked-screen delay, the fallback to the
next Arrive when pings stop, the gate never raised, and a vicinity fault
flagging nothing.

Isolation: an own throwaway school (school_sandbox) with two drivers, two
buses, a morning route with two students on bus 1, and the school's ping
interval set to the smallest value that keeps the pacing waits short. Every
run a test starts is purged in a finally block.
"""

import os
import random
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import psycopg
import pytest
from psycopg.rows import dict_row

from app.core.config import GPS_PING_RUN_START_GRACE_S
from conftest import DSN, METRE, haversine_m, purge_run, school_sandbox
from test_gps_actions import (
    IMPLAUSIBLE_MOVEMENT,
    UNVERIFIED,
    _create_driver,
    _wait_for,
    api_log_tail,
    arrive,
    bus_position,
    clear_injected_failure,
    context,
    coordinate_needles,
    end_run,
    exceptions,
    fix,
    incidents,
    inject_exception_insert_failure,
    latest_session_id,
    layout,
    ledger,
    login,
    pin_login,
    post,
    run_row,
    start,
    trail,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="needs the local stack; set RUN_INTEGRATION=1",
)

BASE = os.environ.get("INTEGRATION_API_URL", "http://localhost:9001")
SCHOOL_LAT, SCHOOL_LNG = -1.30, 36.80
# The phone's home (conftest.Phone mints the taps' fixes from here); the ping
# stream starts here too, so the first ping is a plausible step from the
# Start Run fix. Distinctive digits for the log search.
ORIGIN = (-1.234567, 36.876543)
# The smallest per-school ping interval (KNOB_BOUNDS): half of it is the pace.
PING_INTERVAL_S = 6
PACE_S = PING_INTERVAL_S / 2
PINGS_PATH = "/api/runs/driver/pings"
DEVICE_ID = str(uuid.uuid4())


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE, timeout=30) as c:
        yield c


def db():
    return psycopg.connect(DSN, autocommit=True, row_factory=dict_row)


@pytest.fixture(scope="module")
def sandbox():
    with school_sandbox("IT Pings School", lat=SCHOOL_LAT, lng=SCHOOL_LNG) as sb:
        yield sb


@pytest.fixture(scope="module")
def admin_headers(client, sandbox):
    return login(client, sandbox["email"], sandbox["password"])


@pytest.fixture(scope="module")
def fleet(client, admin_headers, sandbox):
    marker = uuid.uuid4().hex[:6]
    driver1 = _create_driver(client, admin_headers, marker, 1)
    driver2 = _create_driver(client, admin_headers, marker, 2)
    created: dict = {"buses": [], "routes": [], "students": []}
    try:
        bus1 = client.post(
            "/api/fleet/buses",
            json={"name": f"IT Pings Bus1 {marker}", "driver_id": driver1["id"]},
            headers=admin_headers,
        ).json()
        bus2 = client.post(
            "/api/fleet/buses",
            json={"name": f"IT Pings Bus2 {marker}", "driver_id": driver2["id"]},
            headers=admin_headers,
        ).json()
        created["buses"] = [bus1["id"], bus2["id"]]
        morning = client.post(
            "/api/fleet/routes",
            json={"name": f"IT Pings Morning {marker}", "type": "morning",
                  "bus_id": bus1["id"], "school_id": sandbox["id"]},
            headers=admin_headers,
        )
        assert morning.status_code == 200, morning.text
        morning = morning.json()
        created["routes"] = [morning["id"]]
        students = []
        for tag, lat, pickup in (("A", -1.26, "06:20"), ("B", -1.27, "06:30")):
            response = client.post(
                "/api/students",
                json={
                    "name": f"IT Pings Kid{tag} {marker}", "parent_name": f"IT Pings Parent{tag}",
                    "parent_phone": f"+2547120004{random.randint(10, 99)}",
                    "parent_email": f"it-pings-p{tag.lower()}-{marker}@test.local",
                    "home_lat": lat, "home_lng": 36.79, "pickup_time": pickup,
                    "route_ids": [morning["id"]],
                },
                headers=admin_headers,
            )
            assert response.status_code == 200, response.text
            students.append(response.json())
            created["students"].append(response.json()["id"])
        # The school's own ping interval (U11): the smallest allowed, so the
        # pacing waits below stay short. The base fields travel as stored.
        school = client.get("/api/fleet/school", headers=admin_headers).json()
        base = {k: school.get(k) for k in
                ("name", "address", "phone", "lat", "lng", "morning_bell", "afternoon_bell")}
        updated = client.put(
            f"/api/fleet/schools/{sandbox['id']}",
            json={**base, "ping_interval_s": PING_INTERVAL_S}, headers=admin_headers,
        )
        assert updated.status_code == 200, updated.text

        driver_headers = pin_login(client, driver1["pin"])
        yield {
            "marker": marker, "school_id": sandbox["id"],
            "driver1": driver1, "driver2": driver2,
            "driver_headers": driver_headers,
            "session1": latest_session_id(driver1["id"]),
            "driver2_headers": pin_login(client, driver2["pin"]),
            "bus1": bus1, "bus2": bus2, "morning": morning,
            "a": students[0], "b": students[1],
        }
    finally:
        for run in client.get("/api/runs", headers=admin_headers).json():
            if run.get("bus_id") in created["buses"]:
                purge_run(run["id"])
        for sid in created["students"]:
            client.delete(f"/api/students/{sid}", headers=admin_headers)
        for rid in created["routes"]:
            client.delete(f"/api/fleet/routes/{rid}", headers=admin_headers)
        for bid in created["buses"]:
            client.delete(f"/api/fleet/buses/{bid}", headers=admin_headers)
        for driver in (driver1, driver2):
            client.delete(f"/api/accounts/drivers/{driver['id']}", headers=admin_headers)


# Helpers ----------------------------------------------------------------------

NAIROBI = timezone(timedelta(hours=3))
_minted = {"steps": 0}


def iso(moment: datetime) -> str:
    return moment.astimezone(NAIROBI).isoformat(timespec="milliseconds")


def stream(n: int, *, base: datetime | None = None, step_s: float = 0.2,
           drift_m: float = 5.0, accuracy: float | None = None) -> list[dict]:
    """``n`` plausible consecutive ping fixes: capture times ``step_s`` apart
    ending at ``base`` (default: a moment ago), coordinates that keep
    walking north a few metres per fix across the whole module (25 m/s at
    the default step — a bus, not a teleport; never a planned stop's pin),
    a non-integer accuracy that varies. A run's pings are captured after
    its Start Run, so they are later than the checkpoint the start served."""
    end = base if base is not None else datetime.now(timezone.utc) - timedelta(milliseconds=50)
    out = []
    for i in range(n):
        _minted["steps"] += 1
        k = _minted["steps"]
        moment = end - timedelta(seconds=step_s * (n - 1 - i))
        out.append({
            "lat": ORIGIN[0] + k * drift_m * METRE,
            "lng": ORIGIN[1],
            "accuracy_m": accuracy if accuracy is not None else 12.25 + 0.5 * (k % 40),
            "captured_at": iso(moment),
        })
    return out


def ping(client, headers, run_id: str, fixes, *, device=DEVICE_ID) -> httpx.Response:
    body = {"run_id": run_id, "fixes": fixes}
    if device is not None:
        body["device_id"] = device
    return client.post(PINGS_PATH, json=body, headers=headers)


def ping_rows(run_id: str) -> list[dict]:
    return [r for r in trail(run_id) if r["source"] == "ping"]


def newest_captured(fixes: list[dict]) -> datetime:
    return max(datetime.fromisoformat(f["captured_at"]) for f in fixes)


def by_captured(fixes: list[dict], moment: datetime) -> dict:
    return next(f for f in fixes if datetime.fromisoformat(f["captured_at"]) == moment)


def assert_served_ping(bus_id: str, sent: dict) -> None:
    position = bus_position(bus_id)
    assert (position["current_lat"], position["current_lng"]) == (sent["lat"], sent["lng"])
    assert position["position_source"] == "ping"
    assert position["position_accuracy_m"] == sent["accuracy_m"]
    assert position["position_at"] == datetime.fromisoformat(sent["captured_at"])


def start_run(client, fleet) -> str:
    run_id = start(client, fleet["driver_headers"], fleet["morning"]["id"], fix_body=fix()).json()["id"]
    # The start served the school checkpoint stamped now(); the stream's
    # captures must come after it to move the position (forward only).
    time.sleep(0.3)
    return run_id


def wait_pace() -> None:
    time.sleep(PACE_S + 0.3)


# The manifest ------------------------------------------------------------------

def test_the_pings_route_is_classified_driver_scoped_in_the_manifest():
    from tests.scope_manifest import MANIFEST

    assert MANIFEST[("POST", PINGS_PATH)] == "driver-scoped"


# Six in, six stored, the newest served (R7, R26, R28) ------------------------------

def test_a_batch_of_six_inserts_six_ping_rows_and_serves_the_newest_by_capture_time(
    client, fleet, in_process_db, caplog,
):
    import logging

    from app.core.scope import SchoolScope
    from app.dao.position_dao import PositionDao

    h = fleet["driver_headers"]
    run_id = start_run(client, fleet)
    try:
        fixes = stream(6)
        response = ping(client, h, run_id, fixes)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body == {
            "accepted": 6,
            "dropped": {"invalid": 0, "outside_window": 0, "duplicate": 0},
            "flagged": 0,
            "served_at": body["served_at"],
            "prompts": [],
            "arrival_offer": None,
        }
        assert datetime.fromisoformat(body["served_at"]) == newest_captured(fixes)

        rows = ping_rows(run_id)
        assert len(rows) == 6
        for row, sent in zip(sorted(rows, key=lambda r: r["captured_at"]), fixes):
            assert row["source"] == "ping"
            assert row["action_kind"] is None and row["action_key"] is None
            assert row["session_id"] == fleet["session1"]
            assert row["device_id"] == DEVICE_ID
            assert (row["lat"], row["lng"], row["accuracy_m"]) == (
                sent["lat"], sent["lng"], sent["accuracy_m"],
            )
            assert row["captured_at"] == datetime.fromisoformat(sent["captured_at"])
            assert row["fix_reason"] == "none" and tuple(row["flags"]) == ()
        # The Start Run tap's own row is untouched beside them.
        assert [r["action_kind"] for r in trail(run_id) if r["source"] == "action"] == ["start"]
        assert_served_ping(fleet["bus1"]["id"], by_captured(fixes, newest_captured(fixes)))

        # No coordinate reaches the log (Risks: driver location is personal
        # data): the container logs access lines only, so the DAO's own line
        # is read in-process, as the same session the run is bound to.
        wait_pace()
        scope = SchoolScope(
            user_id=fleet["driver1"]["id"], school_id=fleet["school_id"], role="driver",
            actor_kind="driver", session_id=fleet["session1"],
        )
        more = stream(2)
        with caplog.at_level(logging.INFO, logger="saferide.positions"):
            outcome = PositionDao().record_pings(scope, run_id, more, device_id=DEVICE_ID)
        assert outcome["accepted"] == 2
        line = next(r.getMessage() for r in caplog.records if "ping batch recorded" in r.getMessage())
        assert f"run={run_id}" in line and "accepted=2" in line
        logged = "\n".join(r.getMessage() for r in caplog.records) + api_log_tail()
        for needle in coordinate_needles(*fixes, *more):
            assert needle not in logged, needle
        assert len(ping_rows(run_id)) == 8
    finally:
        purge_run(run_id)


def test_out_of_order_pings_never_move_the_served_position_backwards_and_duplicates_insert_nothing(
    client, fleet,
):
    h = fleet["driver_headers"]
    run_id = start_run(client, fleet)
    try:
        first = stream(3)
        assert ping(client, h, run_id, first).status_code == 200
        served = by_captured(first, newest_captured(first))
        assert_served_ping(fleet["bus1"]["id"], served)

        # A late batch: captured before the served ping (the phone's queue
        # drained out of order). Stored — the trail keeps every fix — but
        # the served position does not go backwards.
        wait_pace()
        late = stream(2, base=newest_captured(first) - timedelta(seconds=5))
        response = ping(client, h, run_id, late)
        assert response.status_code == 200, response.text
        assert response.json()["accepted"] == 2
        assert response.json()["served_at"] is None
        assert len(ping_rows(run_id)) == 5
        assert_served_ping(fleet["bus1"]["id"], served)

        # The same batch again (a retry the client is not supposed to make):
        # every capture time already held, nothing inserted, all counted.
        wait_pace()
        response = ping(client, h, run_id, first)
        assert response.status_code == 200, response.text
        assert response.json() == {
            "accepted": 0, "dropped": {"invalid": 0, "outside_window": 0, "duplicate": 3},
            "flagged": 0, "served_at": None, "prompts": [], "arrival_offer": None,
        }
        assert len(ping_rows(run_id)) == 5

        # Two fixes sharing one capture time inside a batch: one row.
        wait_pace()
        twins = stream(2)
        twins[0]["captured_at"] = twins[1]["captured_at"]
        response = ping(client, h, run_id, twins)
        assert response.status_code == 200, response.text
        assert response.json()["accepted"] == 1
        assert response.json()["dropped"]["duplicate"] == 1
        assert len(ping_rows(run_id)) == 6
        # Capture order is stable: the first of the two is the row that landed.
        assert_served_ping(fleet["bus1"]["id"], twins[0])
    finally:
        purge_run(run_id)


# AE15 and the other refusals (R28) ---------------------------------------------------

def test_ae15_a_ping_for_an_ended_run_is_rejected_and_nothing_changes(client, fleet):
    h = fleet["driver_headers"]
    run_id = start_run(client, fleet)
    try:
        assert ping(client, h, run_id, stream(1)).status_code == 200
        ended = end_run(client, h, run_id, fix_body=fix())
        assert ended.status_code == 200, ended.text
        rows_before = ping_rows(run_id)
        cleared = bus_position(fleet["bus1"]["id"])
        assert all(value is None for value in cleared.values()), cleared

        wait_pace()
        response = ping(client, h, run_id, stream(2))
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "run-not-active"
        assert response.json()["detail"]["message"]
        assert ping_rows(run_id) == rows_before
        assert bus_position(fleet["bus1"]["id"]) == cleared

        # An unknown run is not found; another driver's run is not theirs.
        assert ping(client, h, str(uuid.uuid4()), stream(1)).status_code == 404
    finally:
        purge_run(run_id)


def test_another_drivers_run_is_403_and_inserts_nothing(client, fleet):
    run_id = start_run(client, fleet)
    try:
        response = ping(client, fleet["driver2_headers"], run_id, stream(2))
        assert response.status_code == 403, response.text
        assert ping_rows(run_id) == []
    finally:
        purge_run(run_id)


# Session binding (R28) --------------------------------------------------------------

def test_a_second_session_is_refused_and_flagged_once_and_a_tap_from_it_rebinds_the_run(
    client, admin_headers, fleet,
):
    """One PIN on two phones: the run is bound to the session that started
    it; the other phone's pings are refused with one flag for the office,
    however many arrive; the first tapped action from the other phone
    re-binds the run, so its pings resume and the first phone's are refused
    — and the flag stays one row, one event."""
    h1 = fleet["driver_headers"]
    driver_id = fleet["driver1"]["id"]
    run_id = start_run(client, fleet)
    h2 = pin_login(client, fleet["driver1"]["pin"])
    session2 = latest_session_id(driver_id)
    assert session2 != fleet["session1"]
    try:
        assert str(run_row(run_id)["started_session_id"]) == fleet["session1"]
        refused = ping(client, h2, run_id, stream(2))
        assert refused.status_code == 409, refused.text
        assert refused.json()["detail"]["code"] == "session-mismatch"
        assert ping_rows(run_id) == []
        rows = exceptions(client, admin_headers, run_id)
        assert [(x["kind"], x["reason"], x["stop_order"]) for x in rows] == [
            (UNVERIFIED, "session-mismatch", None),
        ]
        flag = rows[0]
        assert flag["fix_lat"] is None and flag["distance_m"] is None and flag["students"] == []
        events = ledger(flag["id"])
        assert len(events) == 1
        assert events[0]["prompt_state"] is None and events[0]["student_id"] is None
        assert events[0]["call_now_due_at"] is None

        # Once per run: a second refusal adds no event and no row.
        assert ping(client, h2, run_id, stream(2)).status_code == 409
        assert len(exceptions(client, admin_headers, run_id)) == 1
        assert len(ledger(flag["id"])) == 1
        # The bound session is unaffected.
        accepted = ping(client, h1, run_id, stream(2))
        assert accepted.status_code == 200, accepted.text
        assert len(ping_rows(run_id)) == 2
        assert {r["session_id"] for r in ping_rows(run_id)} == {fleet["session1"]}

        # A tapped action from the second session re-binds the run (U7).
        arrived = arrive(client, h2, run_id, expected=1)
        assert arrived.status_code == 200, arrived.text
        assert str(run_row(run_id)["started_session_id"]) == session2
        wait_pace()
        resumed = ping(client, h2, run_id, stream(2))
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["accepted"] == 2
        assert [r["session_id"] for r in ping_rows(run_id)][-2:] == [session2, session2]
        # ... and the first session is the stranger now — same single flag.
        wait_pace()
        refused = ping(client, h1, run_id, stream(2))
        assert refused.status_code == 409 and refused.json()["detail"]["code"] == "session-mismatch"
        assert len(exceptions(client, admin_headers, run_id)) == 1
        assert len(ledger(flag["id"])) == 1
        assert len(ping_rows(run_id)) == 4
    finally:
        purge_run(run_id)


# Cap and pace (R26, R28) ----------------------------------------------------------

def test_a_batch_over_the_cap_is_refused_and_a_batch_inside_half_the_interval_is_paced(
    client, fleet,
):
    h = fleet["driver_headers"]
    run_id = start_run(client, fleet)
    try:
        seven = stream(7)
        assert ping(client, h, run_id, seven).status_code == 422
        assert ping(client, h, run_id, []).status_code == 422
        assert ping_rows(run_id) == []

        six = stream(6)
        first = ping(client, h, run_id, six)
        assert first.status_code == 200, first.text
        assert len(ping_rows(run_id)) == 6

        # Straight after: inside half the school's interval — paced, nothing
        # inserted, told how long to wait.
        paced = ping(client, h, run_id, stream(2))
        assert paced.status_code == 429, paced.text
        detail = paced.json()["detail"]
        assert detail["code"] == "ping-too-soon"
        assert 0 < detail["retry_after_s"] <= PACE_S
        assert int(paced.headers["Retry-After"]) >= 1
        assert len(ping_rows(run_id)) == 6
        assert_served_ping(fleet["bus1"]["id"], by_captured(six, newest_captured(six)))

        # The pace measures the ping stream, not the taps: an Arrive in
        # between changes nothing about it.
        assert arrive(client, h, run_id, expected=1).status_code == 200
        assert ping(client, h, run_id, stream(1)).status_code == 429
        assert len(ping_rows(run_id)) == 6

        wait_pace()
        after = ping(client, h, run_id, stream(2))
        assert after.status_code == 200, after.text
        assert after.json()["accepted"] == 2
        assert len(ping_rows(run_id)) == 8
    finally:
        purge_run(run_id)


# Stored, never served (R27, R32) ---------------------------------------------------

def test_coarse_skewed_and_flagged_pings_are_stored_but_never_served_and_the_flagged_attach_to_review(
    client, admin_headers, fleet,
):
    h = fleet["driver_headers"]
    run_id = start_run(client, fleet)
    try:
        created_at = run_row(run_id)["created_at"]
        now = datetime.now(timezone.utc)
        plain = stream(1)[0]
        coarse = stream(1, base=now + timedelta(seconds=1), accuracy=900)[0]
        # A 5 km teleport one second after the coarse fix: speed and jump.
        jump = {
            "lat": coarse["lat"] + 5000 * METRE, "lng": coarse["lng"], "accuracy_m": 14.75,
            "captured_at": iso(now + timedelta(seconds=2)),
        }
        # Ahead of receipt by more than the skew tolerance but inside the
        # window: stored, flagged clock-skew, never served.
        skewed = stream(1, base=now + timedelta(seconds=90))[0]
        # Outside the window either way: dropped and counted.
        far_future = stream(1, base=now + timedelta(seconds=1000))[0]
        before_run = stream(1, base=created_at - timedelta(seconds=120))[0]
        invalid = {"lat": "x", "lng": 36.8, "accuracy_m": 10, "captured_at": iso(now)}
        # Seven would be a 422; the batch cap is on the body, so two batches.
        response = ping(client, h, run_id, [plain, coarse, jump, skewed, far_future, before_run])
        assert response.status_code == 200, response.text
        assert response.json() == {
            "accepted": 4, "dropped": {"invalid": 0, "outside_window": 2, "duplicate": 0},
            "flagged": 2, "served_at": response.json()["served_at"],
            "prompts": [], "arrival_offer": None,
        }
        assert datetime.fromisoformat(response.json()["served_at"]) == datetime.fromisoformat(
            plain["captured_at"]
        )
        assert_served_ping(fleet["bus1"]["id"], plain)

        rows = {row["captured_at"]: row for row in ping_rows(run_id)}
        assert len(rows) == 4
        assert rows[datetime.fromisoformat(plain["captured_at"])]["fix_reason"] == "none"
        coarse_row = rows[datetime.fromisoformat(coarse["captured_at"])]
        assert coarse_row["fix_reason"] == "coarse" and tuple(coarse_row["flags"]) == ()
        jump_row = rows[datetime.fromisoformat(jump["captured_at"])]
        assert {"jump", "speed"} <= set(jump_row["flags"])
        skew_row = rows[datetime.fromisoformat(skewed["captured_at"])]
        assert "clock-skew" in skew_row["flags"]

        review = exceptions(client, admin_headers, run_id, IMPLAUSIBLE_MOVEMENT)
        assert len(review) == 1
        listed = sorted(e["fix_captured_at"] for e in ledger(review[0]["id"]))
        assert listed == sorted([jump_row["captured_at"], skew_row["captured_at"]])
        assert all(e["prompt_state"] is None and e["student_id"] is None
                   for e in ledger(review[0]["id"]))
        assert "clock-skew" in review[0]["reason"] and "jump" in review[0]["reason"]

        wait_pace()
        response = ping(client, h, run_id, [invalid, {"reason": "denied"}, 7])
        assert response.status_code == 200, response.text
        assert response.json() == {
            "accepted": 0, "dropped": {"invalid": 3, "outside_window": 0, "duplicate": 0},
            "flagged": 0, "served_at": None, "prompts": [], "arrival_offer": None,
        }
        assert len(ping_rows(run_id)) == 4
        assert_served_ping(fleet["bus1"]["id"], plain)
    finally:
        purge_run(run_id)


# No background task (R24) ----------------------------------------------------------

def test_the_endpoint_schedules_no_background_task(client, admin_headers, fleet):
    """Every driver action and context poll drains the call-now outbox
    post-commit; a ping must not. A ledger row stamped due (by hand — the
    product stamps only remote-absent rows, and this needs no parent) stays
    unsent across a ping and goes on the next context poll."""
    h1 = fleet["driver_headers"]
    run_id = start_run(client, fleet)
    h2 = pin_login(client, fleet["driver1"]["pin"])
    try:
        assert ping(client, h2, run_id, stream(1)).status_code == 409
        flag = exceptions(client, admin_headers, run_id, UNVERIFIED)[0]
        event_id = ledger(flag["id"])[0]["id"]
        with db() as conn:
            conn.execute(
                "update run_exception_events set call_now_due_at = now() where id = %s",
                (event_id,),
            )
        wait_pace()
        assert ping(client, h1, run_id, stream(2)).status_code == 200
        time.sleep(1.5)
        with db() as conn:
            row = conn.execute(
                "select call_now_sent_at from run_exception_events where id = %s", (event_id,)
            ).fetchone()
        assert row["call_now_sent_at"] is None, "a ping drained the outbox"

        # The next context poll does.
        assert client.get("/api/runs/driver/context", headers=h1).status_code == 200
        deadline = time.time() + 10
        sent = None
        while time.time() < deadline and sent is None:
            with db() as conn:
                sent = conn.execute(
                    "select call_now_sent_at from run_exception_events where id = %s",
                    (event_id,),
                ).fetchone()["call_now_sent_at"]
            time.sleep(0.2)
        assert sent is not None
    finally:
        purge_run(run_id)


# =====================================================================================
# The trail-driven nudges (GPS plan U15: R29, R30, R31; F7; AE14)
# =====================================================================================
#
# The sandbox's morning route: stop 1 and stop 2 are the two children's homes
# (a kilometre apart along the same longitude), stop 3 the school gate. The
# defaults apply: enter at 100 m, leave at 150 m, accuracy cap 200 m.

STOP_BYPASSED = "stop-bypassed"
ENTER_M = 100.0
EXIT_M = 150.0
PROMPT_KEYS = {
    "event_id", "exception_id", "kind", "stop_order", "stop_name", "student_id",
    "students", "answers", "distance_m", "created_at", "delivered_at", "shown_at",
}


class Drive:
    """Mints ping fixes along an honest drive for one run: capture times
    advance by the travel time at a bus's pace from the last fix (under the
    plausibility cap, so nothing is flagged), starting inside the run's
    capture window and never ahead of the wall clock; accuracies vary like a
    phone's; every point is given as metres north and east of a stop's pin
    (never on it — a fix within two metres of a pin is flagged)."""

    SPEED_MPS = 25.0

    def __init__(self, run_id: str):
        created = run_row(run_id)["created_at"]
        self.clock = created - timedelta(seconds=GPS_PING_RUN_START_GRACE_S - 10)
        self.at: tuple[float, float] | None = None
        self.n = 0

    def near(self, pin: tuple, *, north_m: float, east_m: float = 0.0,
             accuracy: float | None = None) -> dict:
        lat = pin[0] + north_m * METRE
        lng = pin[1] + east_m * METRE
        travel = haversine_m(self.at, (lat, lng)) if self.at is not None else 0.0
        self.clock += timedelta(seconds=max(1.0, travel / self.SPEED_MPS))
        now = datetime.now(timezone.utc)
        if self.clock > now:
            time.sleep((self.clock - now).total_seconds())
        self.n += 1
        self.at = (lat, lng)
        return {
            "lat": lat, "lng": lng,
            "accuracy_m": accuracy if accuracy is not None else 14.25 + 0.5 * (self.n % 20),
            "captured_at": iso(self.clock),
        }


def send(client, headers, run_id: str, fixes: list[dict]) -> dict:
    """One paced batch: waits the pace first, posts, asserts 200, returns the
    body — every U15 batch is one of these, so the pacing can never fail a
    scenario by accident."""
    wait_pace()
    response = ping(client, headers, run_id, fixes)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["accepted"] == len(fixes), body
    assert body["flagged"] == 0, body
    return body


def arrive_to(client, headers, run_id: str, target: int, *, fix_body="default") -> httpx.Response:
    """The arrival offer's answer: an Arrive to the stop it named."""
    body = {"run_id": run_id, "device_id": DEVICE_ID, "target_stop_order": target}
    if fix_body == "default":
        body["fix"] = fix()
    elif fix_body is not None:
        body["fix"] = fix_body
    return post(client, headers, "/api/runs/driver/arrive", body)


def arrived_orders(run_id: str) -> set[int]:
    with db() as conn:
        rows = conn.execute(
            "select distinct stop_order from run_stops where run_id = %s and arrived_at is not null",
            (run_id,),
        ).fetchall()
    return {r["stop_order"] for r in rows}


def bypassed_rows(client, admin_headers, run_id: str) -> list[dict]:
    return exceptions(client, admin_headers, run_id, STOP_BYPASSED)


def unflagged(run_id: str) -> bool:
    return all(tuple(r["flags"]) == () for r in trail(run_id))


def route_layout(fleet, run_id: str) -> dict:
    """The run's own stop snapshot plus the child at each order."""
    shape = layout(run_id)
    child_at = {}
    for kid in (fleet["a"], fleet["b"]):
        child_at[shape["by_student"][kid["id"]]] = kid
    with db() as conn:
        names = {
            r["stop_order"]: r["name"] for r in conn.execute(
                "select distinct on (stop_order) stop_order, name from run_stops "
                "where run_id = %s order by stop_order, is_school_gate desc, name",
                (run_id,),
            ).fetchall()
        }
    return {**shape, "child_at": child_at, "names": names}


def test_the_sandbox_route_has_two_child_stops_before_the_gate(client, fleet):
    run_id = start_run(client, fleet)
    try:
        shape = route_layout(fleet, run_id)
        assert shape["gate"] == 3 and shape["last"] == 3
        assert set(shape["child_at"]) == {1, 2}
        assert all(shape["coords"][order][0] is not None for order in (1, 2, 3))
    finally:
        purge_run(run_id)


# AE14: leave a stop with a child unrecorded (R30) ---------------------------------------

def test_ae14_leaving_a_stop_with_a_child_unrecorded_raises_the_bypassed_stop_from_the_completing_batch(
    client, admin_headers, fleet,
):
    """The bus enters stop 1 (two plain pings inside 100 m less accuracy),
    the driver Arrives and boards nobody, and drives off. The first exterior
    ping raises nothing; the second — the batch that completes the exit —
    carries the bypassed-stop prompt in its response, and the exception row
    and the office alert exist before any Arrive at stop 2. Further exterior
    pings add nothing; the later Arrive at stop 2 finds the row and stays
    quiet: one row, one pending event, one incident."""
    h = fleet["driver_headers"]
    run_id = start_run(client, fleet)
    try:
        shape = route_layout(fleet, run_id)
        pin, child, name = shape["coords"][1], shape["child_at"][1], shape["names"][1]
        drive = Drive(run_id)

        # Approaching: one fix inside is not an entry.
        approach = send(client, h, run_id, [
            drive.near(pin, north_m=300), drive.near(pin, north_m=200), drive.near(pin, north_m=30),
        ])
        assert approach["prompts"] == [] and approach["arrival_offer"] is None
        assert context(client, h)["arrival_offer"] is None

        # The second plain fix inside completes the entry: the offer names the stop.
        entered = send(client, h, run_id, [drive.near(pin, north_m=25, east_m=12)])
        assert entered["arrival_offer"] == {"stop_order": 1, "stop_name": name}
        assert entered["prompts"] == []
        assert context(client, h)["arrival_offer"] == {"stop_order": 1, "stop_name": name}

        # The driver Arrives (the Run page's tap): the offer is gone; nobody boards.
        assert arrive(client, h, run_id, expected=1).status_code == 200
        assert context(client, h)["arrival_offer"] is None
        assert bypassed_rows(client, admin_headers, run_id) == []

        # First exterior ping (200 m − accuracy > 150): not yet a departure.
        first_out = send(client, h, run_id, [drive.near(pin, north_m=200)])
        assert first_out["prompts"] == [] and first_out["arrival_offer"] is None
        assert bypassed_rows(client, admin_headers, run_id) == []
        assert incidents(client, admin_headers, run_id, STOP_BYPASSED) == []

        # The batch that completes the exit raises it — response, row, alert.
        left = send(client, h, run_id, [drive.near(pin, north_m=260, east_m=-15)])
        assert len(left["prompts"]) == 1, left
        prompt = left["prompts"][0]
        assert set(prompt) == PROMPT_KEYS
        assert prompt["kind"] == STOP_BYPASSED and prompt["stop_order"] == 1
        assert prompt["stop_name"] == name and prompt["answers"] == ["dismissed"]
        assert [s["id"] for s in prompt["students"]] == [child["id"]]
        assert prompt["delivered_at"] is None and prompt["shown_at"] is None

        rows = bypassed_rows(client, admin_headers, run_id)
        assert len(rows) == 1 and rows[0]["id"] == prompt["exception_id"]
        assert rows[0]["status"] == "open" and rows[0]["stop_order"] == 1
        assert [s["id"] for s in rows[0]["students"]] == [child["id"]]
        events = ledger(rows[0]["id"])
        assert len(events) == 1 and events[0]["prompt_state"] == "pending"
        assert str(events[0]["id"]) == prompt["event_id"]
        # The office alert was written in the ping's own transaction: no
        # waiting on a background task, because there is none.
        alerts = incidents(client, admin_headers, run_id, STOP_BYPASSED)
        assert len(alerts) == 1, alerts
        assert alerts[0]["description"].endswith(f"Stop 1 ({name}): no record yet for {child['name']}.")
        assert alerts[0]["lifecycle"] is True and alerts[0]["acknowledged"] is True
        for needle in coordinate_needles(*[f for f in trail(run_id) if f["lat"] is not None]):
            assert needle not in alerts[0]["description"], needle
        # The context re-delivers the same prompt, stamping its delivery.
        delivered = context(client, h)["pending_prompts"]
        assert [p["event_id"] for p in delivered] == [prompt["event_id"]]
        assert delivered[0]["delivered_at"] is not None
        assert unflagged(run_id)

        # Further exterior pings: nothing new.
        further = send(client, h, run_id, [drive.near(pin, north_m=320), drive.near(pin, north_m=400)])
        assert further["prompts"] == []
        assert len(ledger(rows[0]["id"])) == 1

        # The Arrive at stop 2 evaluates stop 1 as N−1, finds the row and
        # says nothing: still one row, one pending event, one incident.
        arrived = arrive(client, h, run_id, expected=2)
        assert arrived.status_code == 200, arrived.text
        assert arrived.json()["prompts"] == []
        assert len(bypassed_rows(client, admin_headers, run_id)) == 1
        assert [e["prompt_state"] for e in ledger(rows[0]["id"])] == ["pending"]
        time.sleep(1.0)  # the Arrive's post-commit alert task, had it fired
        assert len(incidents(client, admin_headers, run_id, STOP_BYPASSED)) == 1
    finally:
        purge_run(run_id)


# Hysteresis (R31): one fix, two fixes, the boundary, coarse and flagged pings --------------

def test_one_fix_inside_does_not_enter_two_do_a_boundary_fix_does_not_toggle_and_coarse_or_flagged_pings_change_nothing(
    client, admin_headers, fleet,
):
    h = fleet["driver_headers"]
    run_id = start_run(client, fleet)
    try:
        shape = route_layout(fleet, run_id)
        pin, child, name = shape["coords"][1], shape["child_at"][1], shape["names"][1]
        offer = {"stop_order": 1, "stop_name": name}
        drive = Drive(run_id)

        # One plain fix inside: no entry.
        one = send(client, h, run_id, [drive.near(pin, north_m=400), drive.near(pin, north_m=20)])
        assert one["arrival_offer"] is None

        # Two coarse fixes right at the stop: coarse never enters.
        wait_pace()
        coarse = ping(client, h, run_id, [
            drive.near(pin, north_m=12, accuracy=900), drive.near(pin, north_m=16, east_m=5, accuracy=900),
        ])
        assert coarse.status_code == 200 and coarse.json()["arrival_offer"] is None
        assert {r["fix_reason"] for r in ping_rows(run_id)} == {"none", "coarse"}

        # Two flagged fixes at the stop (accuracy zero, U12): flagged never enters.
        wait_pace()
        flagged = ping(client, h, run_id, [
            drive.near(pin, north_m=24, accuracy=0), drive.near(pin, north_m=28, east_m=-6, accuracy=0),
        ])
        assert flagged.status_code == 200, flagged.text
        assert flagged.json()["flagged"] == 2 and flagged.json()["arrival_offer"] is None
        assert context(client, h)["arrival_offer"] is None

        # The next plain fix inside is the second consecutive *plain* fix
        # inside (the coarse and flagged ones changed nothing, neither way):
        # entered, and the offer names the stop.
        two = send(client, h, run_id, [drive.near(pin, north_m=30, east_m=10)])
        assert two["arrival_offer"] == offer
        assert context(client, h)["arrival_offer"] == offer

        # 120 m off (net above the enter radius, inside the exit radius): no toggle.
        boundary = send(client, h, run_id, [drive.near(pin, north_m=120, east_m=20)])
        assert boundary["arrival_offer"] == offer
        # One exterior fix, then back inside the exit radius: still inside.
        flap = send(client, h, run_id, [drive.near(pin, north_m=190), drive.near(pin, north_m=130)])
        assert flap["arrival_offer"] == offer and flap["prompts"] == []
        assert context(client, h)["arrival_offer"] == offer
        assert bypassed_rows(client, admin_headers, run_id) == []

        # Two exterior fixes: left — and the stop was passed without an
        # Arrive with its child unrecorded, so the departure raises it (R30).
        gone = send(client, h, run_id, [drive.near(pin, north_m=200), drive.near(pin, north_m=245, east_m=8)])
        assert gone["arrival_offer"] is None
        assert [p["stop_order"] for p in gone["prompts"]] == [1]
        assert [s["id"] for s in gone["prompts"][0]["students"]] == [child["id"]]
        rows = bypassed_rows(client, admin_headers, run_id)
        assert len(rows) == 1 and rows[0]["status"] == "open"
        assert len(incidents(client, admin_headers, run_id, STOP_BYPASSED)) == 1
        assert unflagged_plain(run_id)

        # The catch-up Arrives find the row: nothing new from either.
        for expected in (1, 2):
            arrived = arrive(client, h, run_id, expected=expected)
            assert arrived.status_code == 200 and arrived.json()["prompts"] == []
        assert len(bypassed_rows(client, admin_headers, run_id)) == 1
        assert [e["prompt_state"] for e in ledger(rows[0]["id"])] == ["pending"]
    finally:
        purge_run(run_id)


def unflagged_plain(run_id: str) -> bool:
    """No trail row carries `classification-failed` (a vicinity fault flags
    nothing); rows flagged on purpose by a test keep their own flags."""
    return all("classification-failed" not in r["flags"] for r in trail(run_id))


# The offer for a later stop and its answer (R29) --------------------------------------------

def test_entering_a_later_stop_offers_it_by_name_and_the_answer_advances_past_the_skipped_stop(
    client, admin_headers, fleet,
):
    """Stop 1 never visited. Entering stop 2 offers Arrive at stop 2 by name
    (response and context). Answering with ``target_stop_order`` 2 moves
    progress to 2 in one transaction, evaluates stop 1's bypass (prompt, row,
    office alert), stamps ``arrived_at`` on stop 2 only, and the offer leaves
    the context. A target at or below progress is a recorded no-op; one
    beyond the run is refused."""
    h = fleet["driver_headers"]
    run_id = start_run(client, fleet)
    try:
        shape = route_layout(fleet, run_id)
        pin2, name2 = shape["coords"][2], shape["names"][2]
        skipped_child = shape["child_at"][1]
        drive = Drive(run_id)

        entered = send(client, h, run_id, [
            drive.near(pin2, north_m=300), drive.near(pin2, north_m=40), drive.near(pin2, north_m=28, east_m=9),
        ])
        assert entered["arrival_offer"] == {"stop_order": 2, "stop_name": name2}
        assert context(client, h)["arrival_offer"] == {"stop_order": 2, "stop_name": name2}

        actions_before = len([r for r in trail(run_id) if r["source"] == "action"])
        answered = arrive_to(client, h, run_id, 2)
        assert answered.status_code == 200, answered.text
        body = answered.json()
        assert body["noop"] is False and body["run"]["stops_completed"] == 2
        assert [p["stop_order"] for p in body["prompts"]] == [1]
        assert [s["id"] for s in body["prompts"][0]["students"]] == [skipped_child["id"]]
        assert arrived_orders(run_id) == {2}, "only the stop reached is stamped"
        rows = bypassed_rows(client, admin_headers, run_id)
        assert [(r["stop_order"], r["status"]) for r in rows] == [(1, "open")]
        alerts = _wait_for(lambda: incidents(client, admin_headers, run_id, STOP_BYPASSED))
        assert len(alerts) == 1 and skipped_child["name"] in alerts[0]["description"]
        assert context(client, h)["arrival_offer"] is None
        assert [p["event_id"] for p in context(client, h)["pending_prompts"]] == [
            body["prompts"][0]["event_id"]
        ]

        # A stale target (the offer answered twice, or from a second device):
        # a recorded no-op — trail row, nothing else.
        for stale in (1, 2):
            again = arrive_to(client, h, run_id, stale)
            assert again.status_code == 200, again.text
            assert again.json()["noop"] is True and again.json()["prompts"] == []
            assert again.json()["run"]["stops_completed"] == 2
        assert len([r for r in trail(run_id) if r["source"] == "action"]) == actions_before + 3
        assert arrived_orders(run_id) == {2}
        assert len(bypassed_rows(client, admin_headers, run_id)) == 1
        # A target the run does not have.
        assert arrive_to(client, h, run_id, 9).status_code == 400
        assert arrive_to(client, h, run_id, 0).status_code == 400

        # The target that is exactly the next stop is a plain Arrive: to the
        # gate here, passing stop 2 with its child unrecorded.
        gate = arrive_to(client, h, run_id, 3)
        assert gate.status_code == 200, gate.text
        assert gate.json()["run"]["stops_completed"] == 3
        assert [p["stop_order"] for p in gate.json()["prompts"]] == [2]
        assert arrived_orders(run_id) == {2, 3}
        assert sorted(r["stop_order"] for r in bypassed_rows(client, admin_headers, run_id)) == [1, 2]
    finally:
        purge_run(run_id)


# A locked screen, and no pings at all (R30's fallback) ------------------------------------

def test_a_screen_locked_after_one_exterior_ping_raises_exactly_one_exception_on_the_next_batch(
    client, admin_headers, fleet,
):
    h = fleet["driver_headers"]
    run_id = start_run(client, fleet)
    try:
        shape = route_layout(fleet, run_id)
        pin = shape["coords"][1]
        drive = Drive(run_id)
        send(client, h, run_id, [drive.near(pin, north_m=300), drive.near(pin, north_m=30), drive.near(pin, north_m=20, east_m=10)])
        assert arrive(client, h, run_id, expected=1).status_code == 200
        first_out = send(client, h, run_id, [drive.near(pin, north_m=210)])
        assert first_out["prompts"] == []
        # The screen locks: no pings for a while. Nothing is raised meanwhile.
        time.sleep(PACE_S * 2)
        assert bypassed_rows(client, admin_headers, run_id) == []
        assert context(client, h)["pending_prompts"] == []
        # The screen unlocks a kilometre on: this batch completes the exit.
        resumed = send(client, h, run_id, [drive.near(pin, north_m=900), drive.near(pin, north_m=980)])
        assert [p["stop_order"] for p in resumed["prompts"]] == [1]
        rows = bypassed_rows(client, admin_headers, run_id)
        assert len(rows) == 1
        assert [e["prompt_state"] for e in ledger(rows[0]["id"])] == ["pending"]
        assert len(incidents(client, admin_headers, run_id, STOP_BYPASSED)) == 1
        # And only one, however the run goes on.
        more = send(client, h, run_id, [drive.near(pin, north_m=1050)])
        assert more["prompts"] == []
        assert arrive(client, h, run_id, expected=2).json()["prompts"] == []
        assert len(bypassed_rows(client, admin_headers, run_id)) == 1
        assert len(ledger(rows[0]["id"])) == 1
        time.sleep(1.0)
        assert len(incidents(client, admin_headers, run_id, STOP_BYPASSED)) == 1
    finally:
        purge_run(run_id)


def test_when_pings_stop_after_one_exterior_ping_the_next_arrive_raises_it_exactly_once(
    client, admin_headers, fleet,
):
    """Decision 5: nothing here runs without pings; U2's check on the next
    Arrive is the fallback, unchanged."""
    h = fleet["driver_headers"]
    run_id = start_run(client, fleet)
    try:
        shape = route_layout(fleet, run_id)
        pin, child = shape["coords"][1], shape["child_at"][1]
        drive = Drive(run_id)
        send(client, h, run_id, [drive.near(pin, north_m=300), drive.near(pin, north_m=30), drive.near(pin, north_m=22, east_m=8)])
        assert arrive(client, h, run_id, expected=1).status_code == 200
        assert send(client, h, run_id, [drive.near(pin, north_m=210)])["prompts"] == []
        assert bypassed_rows(client, admin_headers, run_id) == []

        arrived = arrive(client, h, run_id, expected=2)
        assert arrived.status_code == 200, arrived.text
        prompts = arrived.json()["prompts"]
        assert [p["stop_order"] for p in prompts] == [1]
        assert [s["id"] for s in prompts[0]["students"]] == [child["id"]]
        rows = bypassed_rows(client, admin_headers, run_id)
        assert len(rows) == 1 and [e["prompt_state"] for e in ledger(rows[0]["id"])] == ["pending"]
        alerts = _wait_for(lambda: incidents(client, admin_headers, run_id, STOP_BYPASSED))
        assert len(alerts) == 1
        # A ping after the Arrive that completes the old exit: the row is
        # there already, the prompt pending — nothing new.
        late = send(client, h, run_id, [drive.near(pin, north_m=300)])
        assert late["prompts"] == []
        assert len(ledger(rows[0]["id"])) == 1
        assert len(incidents(client, admin_headers, run_id, STOP_BYPASSED)) == 1
    finally:
        purge_run(run_id)


# The gate is offered but never raised; a vicinity fault flags nothing -----------------------

def test_the_school_gate_is_offered_on_entering_but_never_raised_on_leaving(client, admin_headers, fleet):
    h = fleet["driver_headers"]
    run_id = start_run(client, fleet)
    try:
        shape = route_layout(fleet, run_id)
        gate_pin, gate_name = shape["coords"][3], shape["names"][3]
        drive = Drive(run_id)
        entered = send(client, h, run_id, [
            drive.near(gate_pin, north_m=300), drive.near(gate_pin, north_m=35), drive.near(gate_pin, north_m=25, east_m=10),
        ])
        # The lowest not-yet-arrived stop the bus is inside is the gate; the
        # offer names it (an Arrive there is a real Arrive).
        assert entered["arrival_offer"] == {"stop_order": 3, "stop_name": gate_name}
        left = send(client, h, run_id, [drive.near(gate_pin, north_m=220), drive.near(gate_pin, north_m=280)])
        assert left["prompts"] == [] and left["arrival_offer"] is None
        assert bypassed_rows(client, admin_headers, run_id) == []
        assert incidents(client, admin_headers, run_id, STOP_BYPASSED) == []
    finally:
        purge_run(run_id)


def test_a_vicinity_fault_flags_nothing_on_the_trail_and_the_batch_is_still_accepted(
    client, admin_headers, fleet,
):
    """The exception insert is made to fail (a trigger): the batch that
    would have raised the departure is still 200 with its rows plain — no
    `classification-failed`, no row, no alert, no prompt — and once the
    fault is gone the next Arrive raises it as today."""
    h = fleet["driver_headers"]
    run_id = start_run(client, fleet)
    try:
        shape = route_layout(fleet, run_id)
        pin = shape["coords"][1]
        drive = Drive(run_id)
        send(client, h, run_id, [drive.near(pin, north_m=300), drive.near(pin, north_m=30), drive.near(pin, north_m=24, east_m=6)])
        assert arrive(client, h, run_id, expected=1).status_code == 200
        send(client, h, run_id, [drive.near(pin, north_m=210)])
        inject_exception_insert_failure()
        try:
            broken = send(client, h, run_id, [drive.near(pin, north_m=270, east_m=-10)])
        finally:
            clear_injected_failure()
        assert broken["prompts"] == [] and broken["arrival_offer"] is None
        assert len(ping_rows(run_id)) == 5
        assert unflagged(run_id)
        assert bypassed_rows(client, admin_headers, run_id) == []
        assert incidents(client, admin_headers, run_id, STOP_BYPASSED) == []
        # The departure was completed by that batch; later batches do not
        # re-raise it — the next Arrive's own check does.
        assert send(client, h, run_id, [drive.near(pin, north_m=330)])["prompts"] == []
        arrived = arrive(client, h, run_id, expected=2)
        assert arrived.status_code == 200, arrived.text
        assert [p["stop_order"] for p in arrived.json()["prompts"]] == [1]
        assert len(bypassed_rows(client, admin_headers, run_id)) == 1
        assert _wait_for(lambda: incidents(client, admin_headers, run_id, STOP_BYPASSED))
    finally:
        purge_run(run_id)
