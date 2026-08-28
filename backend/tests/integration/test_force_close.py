"""Office force-close (plan 2026-07-28-001, U6/R12-R15).

The closure gate means a run can now refuse to close, which is the point — but
it also means a run can get stuck in ways no driver can resolve: a dead phone, a
shift that ended, a child who left with a parent nobody told the driver about.
A stuck run is not cosmetic. The per-date uniqueness index blocks that bus from
its next route, and past midnight the run falls out of every date-scoped driver
lookup, so the driver loses even their own release.

The force-close is the exit, and what makes it safe is what it refuses to do.
It is not the old end-of-run sweep under a new name: children without a recorded
outcome are written **unaccounted** — the app saying plainly that nobody knows —
never given a terminal status nobody observed. The office was not on the bus, so
it gets no authority to assert arrival either: the notification depends on the
gate arrival having been recorded, and on the driver having confirmed the child
aboard.

Run with the stack up: RUN_INTEGRATION=1 pytest tests/integration/test_force_close.py -q
"""

import os
import random
import uuid

import httpx
import psycopg
import pytest

from conftest import purge_accounts, purge_run, school_sandbox

# Parent accounts come from signup; naming an email on a student only links a
# row, and without a linked account no notification is ever produced.
from test_students_parents import signup_parent

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="needs the local stack; set RUN_INTEGRATION=1",
)

BASE = os.environ.get("INTEGRATION_API_URL", "http://localhost:9001")
DSN = os.environ.get("DATABASE_URL", "postgresql://saferide:saferide@localhost:5432/saferide")
ADMIN = {"email": "admin@test.com", "password": "test1234."}


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE, timeout=30) as c:
        yield c


@pytest.fixture(scope="module")
def sandbox():
    # Post-U6 world provisioning: school creation left the staff API, so the
    # throwaway school (plus its own single-membership admin, whose header
    # fallback lands there) is provisioned by the conftest sandbox instead.
    with school_sandbox("IT FC School", lat=-1.29, lng=36.82) as sb:
        yield sb


@pytest.fixture(scope="module")
def admin_headers(client, sandbox):
    response = client.post(
        "/api/auth/login",
        json={"email": sandbox["email"], "password": sandbox["password"]},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def participation(run_id: str, name: str) -> dict | None:
    with psycopg.connect(DSN, autocommit=True, row_factory=psycopg.rows.dict_row) as pg:
        return pg.execute(
            "select * from run_participation where run_id = %s and student_name = %s",
            (run_id, name),
        ).fetchone()


def notifications_for(student_id: str, run_id: str) -> list[str]:
    with psycopg.connect(DSN, autocommit=True, row_factory=psycopg.rows.dict_row) as pg:
        rows = pg.execute(
            "select type from live_notifications where student_id = %s and run_id = %s",
            (student_id, run_id),
        ).fetchall()
    return [r["type"] for r in rows]


def display_status(client, admin_headers, student_id: str) -> str:
    """The derived status as the office surface reads it — through the API, not
    the status column, because the column is no longer the answer."""
    students = client.get("/api/students", headers=admin_headers).json()
    match = [s for s in students if s["id"] == student_id]
    assert match, "student vanished from the office roster"
    return match[0]["display_status"]


def backdate_run(run_id: str, days: int = 1) -> None:
    """Move a run into a past service day.

    Done in SQL rather than by waiting for midnight, and deliberately not
    exposed as an endpoint — nothing in the product should be able to change a
    run's service day.
    """
    with psycopg.connect(DSN, autocommit=True) as pg:
        pg.execute(
            "update live_runs set date = date - make_interval(days => %s) where id = %s",
            (days, run_id),
        )


@pytest.fixture(scope="module")
def fleet(client, admin_headers, sandbox):
    marker = uuid.uuid4().hex[:6]
    created: dict = {"marker": marker}
    pin = str(random.randint(100000, 999999))

    created["driver"] = client.post(
        "/api/accounts/drivers",
        json={"full_name": f"IT FC Driver {marker}", "email": f"it-fc-drv-{marker}@test.local",
              "password": "test1234.", "phone": f"+2547{random.randint(10000000, 99999999)}",
              "pin": pin},
        headers=admin_headers,
    ).json()
    created["pin"] = pin

    created["bus"] = client.post(
        "/api/fleet/buses",
        json={"name": f"IT FC Bus {marker}", "capacity": 20,
              "driver_id": created["driver"]["id"]},
        headers=admin_headers,
    ).json()

    created["school"] = {"id": sandbox["id"], "name": sandbox["name"]}

    for period in ("morning", "afternoon"):
        created[period] = client.post(
            "/api/fleet/routes",
            json={"name": f"IT FC {period} {marker}", "type": period,
                  "bus_id": created["bus"]["id"], "school_id": created["school"]["id"]},
            headers=admin_headers,
        ).json()

    created["students"] = []
    created["parent_ids"] = []
    for n, (lat, lng) in enumerate([(-1.30, 36.79), (-1.31, 36.78)], start=1):
        parent_id, parent_email, _ = signup_parent(client, marker, f"fc-p{n}")
        created["parent_ids"].append(parent_id)
        created["students"].append(client.post(
            "/api/students",
            json={"name": f"IT FC Kid{n} {marker}", "parent_name": f"IT FC Parent{n}",
                  "parent_phone": f"+2547{random.randint(10000000, 99999999)}",
                  "parent_email": parent_email,
                  "school_id": created["school"]["id"], "home_lat": lat, "home_lng": lng,
                  "pickup_time": "06:30",
                  "route_ids": [created["morning"]["id"], created["afternoon"]["id"]]},
            headers=admin_headers,
        ).json())

    try:
        yield created
    finally:
        for run in client.get("/api/runs", headers=admin_headers).json():
            if run.get("bus_id") == created["bus"]["id"]:
                purge_run(run['id'])
        for s in created["students"]:
            client.delete(f"/api/students/{s['id']}", headers=admin_headers)
        for period in ("morning", "afternoon"):
            client.delete(f"/api/fleet/routes/{created[period]['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/buses/{created['bus']['id']}", headers=admin_headers)
        client.delete(f"/api/accounts/drivers/{created['driver']['id']}", headers=admin_headers)
        for parent_id in created["parent_ids"]:
            purge_accounts(parent_id)


@pytest.fixture
def driver_headers(client, fleet):
    token = client.post("/api/auth/pin-login", json={"pin": fleet["pin"]}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def start_and_arrive(client, driver_headers, fleet, period: str, stops: int | None = None) -> str:
    """Start a run and arrive `stops` of its stops (all of them by default)."""
    started = client.post("/api/runs/driver/start",
                          json={"route_id": fleet[period]["id"]}, headers=driver_headers)
    assert started.status_code == 200, started.text
    run_id = started.json()["id"]
    context = client.get("/api/runs/driver/context", headers=driver_headers).json()
    total = len(context["run_stops"]) if stops is None else stops
    for _ in range(total):
        client.post("/api/runs/driver/arrive", json={"run_id": run_id}, headers=driver_headers)
    return run_id


@pytest.fixture
def afternoon_run(client, admin_headers, fleet, driver_headers):
    run_id = start_and_arrive(client, driver_headers, fleet, "afternoon")
    yield run_id
    purge_run(run_id)


def test_force_close_keeps_confirmed_dropoffs_and_records_the_rest_unaccounted(
    client, admin_headers, fleet, driver_headers, afternoon_run
):
    """R12: the distinction the whole unit exists for.

    The old sweep wrote 'dropped off' for every roster child at run end, so a
    child nobody confirmed off the bus was recorded as safely home. Here the
    confirmed child keeps their drop-off and the unconfirmed one is recorded as
    unaccounted — not as an outcome, as the absence of one.
    """
    confirmed, unknown = fleet["students"]
    client.post("/api/runs/driver/dropoff", json={"student_id": confirmed["id"]},
                headers=driver_headers)

    closed = client.post(f"/api/runs/{afternoon_run}/force-close", headers=admin_headers)
    assert closed.status_code == 200, closed.text

    kept = participation(afternoon_run, confirmed["name"])
    assert kept["dropped_off_at"] is not None, "the force-close erased a real drop-off"
    assert kept["unaccounted_at"] is None, "a confirmed child was marked unaccounted"

    stuck = participation(afternoon_run, unknown["name"])
    assert stuck["unaccounted_at"] is not None, "the unconfirmed child was not recorded"
    assert stuck["dropped_off_at"] is None, "the force-close invented a drop-off"
    assert stuck["handover_at"] is None, "the force-close invented a hand-over"


def test_no_automated_message_reaches_an_unaccounted_childs_parents(
    client, admin_headers, fleet, driver_headers, afternoon_run
):
    """R14: nobody knows where this child is. A push saying anything at all
    would be a claim the office cannot make, so the obligation is a phone call
    and the app tracks whether it happened."""
    unknown = fleet["students"][1]
    before = notifications_for(unknown["id"], afternoon_run)

    client.post(f"/api/runs/{afternoon_run}/force-close", headers=admin_headers)

    after = notifications_for(unknown["id"], afternoon_run)
    assert after == before, f"the force-close sent {set(after) - set(before)}"
    assert "dropped-off" not in after, "the parents were told their child was dropped off"


def test_the_office_gets_the_children_by_name_and_records_each_call(
    client, admin_headers, fleet, driver_headers, afternoon_run
):
    """R14: a generic 'some children unaccounted' is not actionable at 4pm.
    The office needs the names and a way to close each one out."""
    confirmed, unknown = fleet["students"]
    client.post("/api/runs/driver/dropoff", json={"student_id": confirmed["id"]},
                headers=driver_headers)

    closed = client.post(f"/api/runs/{afternoon_run}/force-close", headers=admin_headers)
    outstanding = closed.json()["unaccounted"]
    assert [c["student_name"] for c in outstanding] == [unknown["name"]], (
        "the obligation list should name only the children with no outcome"
    )
    assert outstanding[0]["contacted_at"] is None

    recorded = client.post(f"/api/runs/{afternoon_run}/contacted",
                           json={"student_id": unknown["id"]}, headers=admin_headers)
    assert recorded.status_code == 200, recorded.text
    assert recorded.json()["unaccounted"][0]["contacted_at"] is not None
    assert participation(afternoon_run, unknown["name"])["contacted_by"] is not None


def test_an_unaccounted_child_does_not_read_as_at_home(
    client, admin_headers, fleet, driver_headers, afternoon_run
):
    """R12: the surface-level failure. Once the run completed, the old
    derivation had nothing left to key on and quietly decayed every child to
    'at home' — the most reassuring possible reading of the one case where
    nobody knows anything."""
    unknown = fleet["students"][1]
    client.post(f"/api/runs/{afternoon_run}/force-close", headers=admin_headers)

    assert display_status(client, admin_headers, unknown["id"]) == "unaccounted"


def test_a_morning_force_close_after_a_recorded_gate_arrival_notifies_boarded_children(
    client, admin_headers, fleet, driver_headers
):
    """R13: with the arrival recorded, the children the driver confirmed aboard
    genuinely did reach school, and their families should hear so even though
    the driver never closed the run."""
    run_id = start_and_arrive(client, driver_headers, fleet, "morning")
    try:
        aboard, missed = fleet["students"]
        client.post("/api/runs/driver/boarding",
                    json={"student_id": aboard["id"], "on_bus": True}, headers=driver_headers)

        closed = client.post(f"/api/runs/{run_id}/force-close", headers=admin_headers)
        assert closed.status_code == 200, closed.text
        assert closed.json()["gate_arrival_recorded"] is True

        assert "reached-school" in notifications_for(aboard["id"], run_id)
        assert "reached-school" not in notifications_for(missed["id"], run_id), (
            "a child who never boarded was told they reached school"
        )
    finally:
        purge_run(run_id)


def test_a_morning_force_close_with_no_gate_arrival_notifies_nobody(
    client, admin_headers, fleet, driver_headers
):
    """R13: the office was not on the bus. Without a recorded arrival there is
    no evidence anyone reached school, and a boarding confirmation alone is
    evidence of departure, not of arrival."""
    run_id = start_and_arrive(client, driver_headers, fleet, "morning", stops=0)
    try:
        aboard = fleet["students"][0]
        client.post("/api/runs/driver/boarding",
                    json={"student_id": aboard["id"], "on_bus": True}, headers=driver_headers)

        closed = client.post(f"/api/runs/{run_id}/force-close", headers=admin_headers)
        assert closed.status_code == 200, closed.text
        assert closed.json()["gate_arrival_recorded"] is False

        assert "reached-school" not in notifications_for(aboard["id"], run_id), (
            "arrival was asserted with no record of the bus reaching the gate"
        )
    finally:
        purge_run(run_id)


def test_the_bus_can_start_its_next_route_immediately(
    client, admin_headers, fleet, driver_headers
):
    """R12: the operational reason force-close exists. The partial unique index
    on (bus, date) means a stuck run blocks the bus from its next route — the
    afternoon school run does not wait for an admin to untangle the morning."""
    morning = start_and_arrive(client, driver_headers, fleet, "morning", stops=0)
    afternoon = None
    try:
        blocked = client.post("/api/runs/driver/start",
                              json={"route_id": fleet["afternoon"]["id"]},
                              headers=driver_headers)
        assert blocked.status_code == 409, blocked.text

        client.post(f"/api/runs/{morning}/force-close", headers=admin_headers)

        freed = client.post("/api/runs/driver/start",
                            json={"route_id": fleet["afternoon"]["id"]},
                            headers=driver_headers)
        assert freed.status_code == 200, freed.text
        afternoon = freed.json()["id"]
    finally:
        if afternoon:
            purge_run(afternoon)
        purge_run(morning)


def test_a_run_from_a_past_service_day_rejects_driver_actions(
    client, admin_headers, fleet, driver_headers
):
    """R15: the midnight case. A day-late close would stamp today's end_time and
    today's counts onto yesterday's run, and arriving stops a day later records
    a journey that did not happen. The office force-close is the only exit, and
    it is the one that records the unresolved children honestly."""
    run_id = start_and_arrive(client, driver_headers, fleet, "morning", stops=0)
    try:
        backdate_run(run_id)

        arrived = client.post("/api/runs/driver/arrive", json={"run_id": run_id},
                              headers=driver_headers)
        assert arrived.status_code == 409, arrived.text
        assert "previous day" in arrived.json()["detail"]

        ended = client.post("/api/runs/driver/end", json={"run_id": run_id},
                            headers=driver_headers)
        assert ended.status_code == 409, ended.text

        closed = client.post(f"/api/runs/{run_id}/force-close", headers=admin_headers)
        assert closed.status_code == 200, closed.text
    finally:
        purge_run(run_id)


def test_a_stale_run_surfaces_to_the_office_as_active(
    client, admin_headers, fleet, driver_headers
):
    """R15: before this, the active-runs card was pinned to today, so a run open
    at midnight fell out of the office's view as well as the driver's and sat in
    progress forever with nobody told."""
    run_id = start_and_arrive(client, driver_headers, fleet, "morning", stops=0)
    try:
        backdate_run(run_id)

        active = client.get("/api/runs", params={"active": "true"},
                            headers=admin_headers).json()
        mine = [r for r in active if r["id"] == run_id]
        assert mine, "the stale run is invisible to the office"
        assert mine[0]["stale"] is True, "the stale run is not flagged as such"
    finally:
        purge_run(run_id)


def test_force_close_is_refused_on_a_completed_run(
    client, admin_headers, fleet, driver_headers
):
    """R12: force-close writes unaccounted rows. Re-running it over a properly
    closed run would manufacture doubt about children the driver did account
    for."""
    run_id = start_and_arrive(client, driver_headers, fleet, "afternoon")
    try:
        for kid in fleet["students"]:
            client.post("/api/runs/driver/dropoff", json={"student_id": kid["id"]},
                        headers=driver_headers)
        ended = client.post("/api/runs/driver/end", json={"run_id": run_id},
                            headers=driver_headers)
        assert ended.status_code == 200, ended.text

        again = client.post(f"/api/runs/{run_id}/force-close", headers=admin_headers)
        assert again.status_code == 409, again.text
    finally:
        purge_run(run_id)


def test_a_driver_cannot_force_close(client, fleet, driver_headers, afternoon_run):
    """R12: the force-close is the office's judgement that a run cannot be
    resolved. Handing it to the driver would make it the easy way out of the
    closure gate, which is the guarantee it is meant to protect."""
    response = client.post(f"/api/runs/{afternoon_run}/force-close", headers=driver_headers)
    assert response.status_code == 403, response.text
