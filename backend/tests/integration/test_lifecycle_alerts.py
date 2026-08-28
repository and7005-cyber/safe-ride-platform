"""Office run-lifecycle alerts (plan 2026-07-28-001, U16/R29-R30).

The office has no run lifecycle in its alert feed at all today: completion shows
only on the Runs page, which nobody watches during a route. In the morning the
school-gate arrival alert happens to double as a finished signal; in the
afternoon there is no equivalent, so a route ends and the office learns nothing.

These alerts share live_incidents with real incidents, so they carry a lifecycle
marker that keeps them out of three places:
- the parent alerts feed (which filtered only child-stamped rows, and so has
  been showing parents the 'arrival' rows all along)
- the parent push fan-out
- the incident counters behind the Dashboard tile and the acknowledgement badge

Run with the stack up: RUN_INTEGRATION=1 pytest tests/integration/test_lifecycle_alerts.py -q
"""

import os
import random
import uuid

import httpx
import psycopg
import pytest

from conftest import purge_accounts, purge_run, school_sandbox

# Cross-module helper reuse, as the other integration suites do — parent
# accounts are created through signup, not by naming an email on a student.
from test_students_parents import complete_run, signup_parent

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="needs the local stack; set RUN_INTEGRATION=1",
)

BASE = os.environ.get("INTEGRATION_API_URL", "http://localhost:9001")
ADMIN = {"email": "admin@test.com", "password": "test1234."}
DSN = os.environ.get("DATABASE_URL", "postgresql://saferide:saferide@localhost:5432/saferide")


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE, timeout=20) as c:
        yield c


@pytest.fixture(scope="module")
def sandbox():
    # Post-U6 world provisioning: school creation left the staff API, so the
    # throwaway school (plus its own single-membership admin, whose header
    # fallback lands there) is provisioned by the conftest sandbox instead.
    with school_sandbox("IT LC School", lat=-1.29, lng=36.82) as sb:
        yield sb


@pytest.fixture(scope="module")
def admin_headers(client, sandbox):
    response = client.post(
        "/api/auth/login",
        json={"email": sandbox["email"], "password": sandbox["password"]},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


@pytest.fixture(scope="module")
def fleet(client, admin_headers, sandbox):
    """Throwaway bus + driver + school + morning route with one linked parent."""
    marker = uuid.uuid4().hex[:6]
    created: dict = {"marker": marker}
    pin = str(random.randint(100000, 999999))

    response = client.post(
        "/api/accounts/drivers",
        json={"full_name": f"IT LC Driver {marker}",
              "email": f"it-lc-driver-{marker}@test.local", "password": "test1234.",
              "phone": f"+2547{random.randint(10000000, 99999999)}", "pin": pin},
        headers=admin_headers,
    )
    assert response.status_code in (200, 201), response.text
    created["driver"] = response.json()
    created["pin"] = pin

    response = client.post(
        "/api/fleet/buses",
        json={"name": f"IT LC Bus {marker}", "capacity": 20,
              "driver_id": created["driver"]["id"]},
        headers=admin_headers,
    )
    assert response.status_code in (200, 201), response.text
    created["bus"] = response.json()

    created["school"] = {"id": sandbox["id"], "name": sandbox["name"]}

    response = client.post(
        "/api/fleet/routes",
        json={"name": f"IT LC Route {marker}", "type": "morning",
              "bus_id": created["bus"]["id"], "school_id": created["school"]["id"]},
        headers=admin_headers,
    )
    assert response.status_code in (200, 201), response.text
    created["route"] = response.json()

    parent_id, parent_email, parent_headers = signup_parent(client, marker, "lc-parent")
    created["parent_id"] = parent_id
    created["parent_headers"] = parent_headers

    response = client.post(
        "/api/students",
        json={"name": f"IT LC Kid {marker}", "parent_name": "IT LC Parent",
              "parent_phone": f"+2547{random.randint(10000000, 99999999)}",
              "parent_email": parent_email, "school_id": created["school"]["id"],
              "home_lat": -1.30, "home_lng": 36.79, "pickup_time": "06:30",
              "route_ids": [created["route"]["id"]]},
        headers=admin_headers,
    )
    assert response.status_code in (200, 201), response.text
    created["student"] = response.json()

    try:
        yield created
    finally:
        for run in client.get("/api/runs", headers=admin_headers).json():
            if run.get("bus_id") == created["bus"]["id"]:
                purge_run(run['id'])
        client.delete(f"/api/students/{created['student']['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/routes/{created['route']['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/buses/{created['bus']['id']}", headers=admin_headers)
        client.delete(f"/api/accounts/drivers/{created['driver']['id']}", headers=admin_headers)
        purge_accounts(created['parent_id'])


def _driver_headers(client, fleet):
    token = client.post("/api/auth/pin-login", json={"pin": fleet["pin"]}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _alerts_for_bus(client, admin_headers, bus_id: str) -> list[dict]:
    rows = client.get("/api/incidents", headers=admin_headers).json()
    return [a for a in rows if a.get("bus_id") == bus_id]


def _real_alerts(client, admin_headers, bus_id: str) -> list[dict]:
    """Incidents that should move the office's counters — everything the backend
    does not mark as run lifecycle."""
    return [a for a in _alerts_for_bus(client, admin_headers, bus_id) if not a["lifecycle"]]


def _wait_for(predicate, timeout: float = 10.0):
    """Background tasks run inside the request lifecycle but the feed row lands
    asynchronously enough to race a fast assertion."""
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.25)
    return predicate()


def test_run_start_and_completion_each_raise_one_office_alert(client, admin_headers, fleet):
    """R29: the office finally sees a route start and end, naming route, period
    and bus — the defect being that a route ends and the office learns nothing."""
    driver_headers = _driver_headers(client, fleet)
    started = client.post("/api/runs/driver/start",
                          json={"route_id": fleet["route"]["id"]}, headers=driver_headers)
    assert started.status_code == 200, started.text
    run_id = started.json()["id"]
    try:
        alerts = _wait_for(lambda: [a for a in _alerts_for_bus(client, admin_headers, fleet["bus"]["id"])
                                    if a["type"] == "run-started"])
        assert len(alerts) == 1, f"expected one run-started alert, got {len(alerts)}"
        assert fleet["route"]["name"] in (alerts[0]["description"] or "")
        assert fleet["bus"]["name"] in (alerts[0]["description"] or "")
        assert "morning" in (alerts[0]["description"] or "")

        complete_run(client, driver_headers, run_id)
        ended = _wait_for(lambda: [a for a in _alerts_for_bus(client, admin_headers, fleet["bus"]["id"])
                                   if a["type"] == "run-completed"])
        assert len(ended) == 1, f"expected one run-completed alert, got {len(ended)}"
        assert ended[0]["type"] != alerts[0]["type"], "start and end are indistinguishable"
    finally:
        purge_run(run_id)


def test_lifecycle_alerts_never_reach_the_parent(client, admin_headers, fleet):
    """R30: office-only. Parents already get their own messages for anything
    that concerns them; these would arrive as duplicate, vaguer news."""
    driver_headers = _driver_headers(client, fleet)
    started = client.post("/api/runs/driver/start",
                          json={"route_id": fleet["route"]["id"]}, headers=driver_headers)
    assert started.status_code == 200, started.text
    run_id = started.json()["id"]
    try:
        _wait_for(lambda: [a for a in _alerts_for_bus(client, admin_headers, fleet["bus"]["id"])
                           if a["type"] == "run-started"])
        complete_run(client, driver_headers, run_id)
        _wait_for(lambda: [a for a in _alerts_for_bus(client, admin_headers, fleet["bus"]["id"])
                           if a["type"] == "run-completed"])

        feed = client.get("/api/parent-portal/alerts", headers=fleet["parent_headers"])
        assert feed.status_code == 200, feed.text
        types = {row.get("type") for row in feed.json()}
        assert "run-started" not in types
        assert "run-completed" not in types
        # The pre-existing 'arrival' rows were reaching parents all along,
        # because the only feed filter was on child-stamped rows.
        assert "arrival" not in types
    finally:
        purge_run(run_id)


def test_lifecycle_alerts_do_not_move_the_incident_counters(client, admin_headers, fleet):
    """R30: roughly four of these per bus per day. Counted, they would turn the
    Dashboard's incidents tile red on an ordinary day and fill the
    acknowledgement badge with items nobody needs to acknowledge."""
    driver_headers = _driver_headers(client, fleet)
    before_today = client.get("/api/incidents/today-count", headers=admin_headers).json()["count"]
    before_unread = client.get("/api/incidents/unread-count", headers=admin_headers).json()["count"]
    # Filtered on the row's own lifecycle marker, not a list of type names: U11
    # added four more lifecycle types, and a hardcoded list silently counted
    # them as real incidents here while the backend correctly excluded them —
    # so this assertion started failing about the very thing it was protecting.
    before_real = len(_real_alerts(client, admin_headers, fleet["bus"]["id"]))

    started = client.post("/api/runs/driver/start",
                          json={"route_id": fleet["route"]["id"]}, headers=driver_headers)
    assert started.status_code == 200, started.text
    run_id = started.json()["id"]
    try:
        _wait_for(lambda: [a for a in _alerts_for_bus(client, admin_headers, fleet["bus"]["id"])
                           if a["type"] == "run-started"])
        complete_run(client, driver_headers, run_id)
        _wait_for(lambda: [a for a in _alerts_for_bus(client, admin_headers, fleet["bus"]["id"])
                           if a["type"] == "run-completed"])

        # Deltas, not absolutes: the fixture is module-scoped, so earlier tests
        # in this file have already raised lifecycle alerts on the same bus.
        rows = _alerts_for_bus(client, admin_headers, fleet["bus"]["id"])
        lifecycle = [a for a in rows if a["lifecycle"]]
        real = [a for a in rows if not a["lifecycle"]]
        assert len(lifecycle) >= 2, "the lifecycle alerts were not raised"

        # Driving the run to the school gate raises a genuine 'arrival' incident,
        # which the office has always counted and still should. The point is that
        # the two lifecycle rows contribute nothing on top of it.
        after_today = client.get("/api/incidents/today-count", headers=admin_headers).json()["count"]
        after_unread = client.get("/api/incidents/unread-count", headers=admin_headers).json()["count"]
        assert after_today - before_today == len(real) - before_real, (
            "lifecycle alerts moved the incidents-today tile"
        )
        assert after_unread - before_unread == len(real) - before_real, (
            "lifecycle alerts landed in the acknowledgement queue"
        )
    finally:
        purge_run(run_id)


# U11 — the closure events (R29-R30) ---------------------------------------------
#
# The gate can now refuse, and three driver actions can release it. Each one
# changes what a completed run means, and none of them was visible to the
# office: a finished run could mean every drop-off confirmed, or a driver
# self-attesting a hand-over, and nothing in the feed told them apart.

LIFECYCLE_TYPES = (
    "run-started", "run-completed",
    "closure-refused", "force-closed", "handover-recorded", "action-reversed",
)


@pytest.fixture(scope="module")
def afternoon(client, admin_headers, fleet):
    """An afternoon route on the same bus with two children of its own.

    Its own children rather than the morning fixture's: the afternoon roster is
    auto-boarded, so every one of them blocks closure until released, which is
    exactly the state these tests need.
    """
    marker = fleet["marker"]
    route = client.post(
        "/api/fleet/routes",
        json={"name": f"IT LC PM {marker}", "type": "afternoon",
              "bus_id": fleet["bus"]["id"], "school_id": fleet["school"]["id"]},
        headers=admin_headers,
    ).json()
    students = []
    for n, (lat, lng) in enumerate([(-1.32, 36.77), (-1.33, 36.76)], start=1):
        students.append(client.post(
            "/api/students",
            json={"name": f"IT LC PMKid{n} {marker}", "parent_name": f"IT LC PMParent{n}",
                  "parent_phone": f"+2547{random.randint(10000000, 99999999)}",
                  "parent_email": f"it-lc-pm{n}-{marker}@test.local",
                  "school_id": fleet["school"]["id"], "home_lat": lat, "home_lng": lng,
                  "pickup_time": "06:30", "route_ids": [route["id"]]},
            headers=admin_headers,
        ).json())
    try:
        yield {"route": route, "students": students}
    finally:
        for s in students:
            client.delete(f"/api/students/{s['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/routes/{route['id']}", headers=admin_headers)


@pytest.fixture
def pm_run(client, admin_headers, fleet, afternoon):
    """An afternoon run with every stop arrived, so drop-offs are confirmable."""
    driver_headers = _driver_headers(client, fleet)
    started = client.post("/api/runs/driver/start",
                          json={"route_id": afternoon["route"]["id"]}, headers=driver_headers)
    assert started.status_code == 200, started.text
    run_id = started.json()["id"]
    context = client.get("/api/runs/driver/context", headers=driver_headers).json()
    for _ in range(len(context["run_stops"])):
        client.post("/api/runs/driver/arrive", json={"run_id": run_id}, headers=driver_headers)
    yield run_id
    purge_run(run_id)


def _alerts_of_type(client, admin_headers, bus_id: str, type_: str) -> list[dict]:
    """Newest first — /api/incidents orders by created_at desc, so index 0 is
    the alert the event under test just raised."""
    return [a for a in _alerts_for_bus(client, admin_headers, bus_id) if a["type"] == type_]


def test_repeated_refusals_against_the_same_children_raise_one_alert(
    client, admin_headers, fleet, afternoon, pm_run
):
    """R29: a driver who taps End four times against the same unresolved
    children is one situation. Four identical alerts would train the office to
    scroll past the type."""
    driver_headers = _driver_headers(client, fleet)
    before = len(_alerts_of_type(client, admin_headers, fleet["bus"]["id"], "closure-refused"))

    for _ in range(3):
        refused = client.post("/api/runs/driver/end", json={"run_id": pm_run},
                              headers=driver_headers)
        assert refused.status_code == 409, refused.text

    raised = _wait_for(
        lambda: _alerts_of_type(client, admin_headers, fleet["bus"]["id"], "closure-refused")
        if len(_alerts_of_type(client, admin_headers, fleet["bus"]["id"], "closure-refused")) > before
        else None
    )
    assert len(raised) - before == 1, "one alert per tap instead of one per situation"
    assert afternoon["students"][0]["name"] in raised[0]["description"]


def test_a_refusal_after_partial_progress_raises_a_fresh_alert(
    client, admin_headers, fleet, afternoon, pm_run
):
    """R29: the blocking set changed, so this is news. Keying the alert on the
    run alone would tell the office once and then stay quiet while the run sat
    stuck on a different child."""
    driver_headers = _driver_headers(client, fleet)
    first, second = afternoon["students"]
    before = len(_alerts_of_type(client, admin_headers, fleet["bus"]["id"], "closure-refused"))

    assert client.post("/api/runs/driver/end", json={"run_id": pm_run},
                       headers=driver_headers).status_code == 409
    client.post("/api/runs/driver/dropoff", json={"student_id": first["id"]},
                headers=driver_headers)
    assert client.post("/api/runs/driver/end", json={"run_id": pm_run},
                       headers=driver_headers).status_code == 409

    raised = _wait_for(
        lambda: _alerts_of_type(client, admin_headers, fleet["bus"]["id"], "closure-refused")
        if len(_alerts_of_type(client, admin_headers, fleet["bus"]["id"], "closure-refused")) >= before + 2
        else None
    )
    assert len(raised) - before == 2, "the second, different blocking set was not reported"
    latest = raised[0]["description"]
    assert second["name"] in latest, latest
    assert first["name"] not in latest, "the resolved child is still named as blocking"


def test_a_handover_alert_carries_the_child_and_the_note(
    client, admin_headers, fleet, afternoon, pm_run
):
    """R29: a hand-over is a driver self-attesting an outcome away from the
    route. Without the note in the feed, a completed run reads identically
    whether every drop-off was confirmed at its own stop or not."""
    driver_headers = _driver_headers(client, fleet)
    kid = afternoon["students"][0]
    note = "Collected by grandmother at the junction"

    recorded = client.post("/api/runs/driver/handover",
                           json={"student_id": kid["id"], "note": note},
                           headers=driver_headers)
    assert recorded.status_code == 200, recorded.text

    alerts = _wait_for(
        lambda: _alerts_of_type(client, admin_headers, fleet["bus"]["id"], "handover-recorded")
    )
    assert alerts, "the office was not told"
    assert kid["name"] in alerts[0]["description"]
    assert note in alerts[0]["description"]


def test_a_reversal_alert_names_what_was_retracted(
    client, admin_headers, fleet, afternoon, pm_run
):
    """R29: the driver retracted a statement already sent to a family. The
    office needs to know a correction happened, and to which claim."""
    driver_headers = _driver_headers(client, fleet)
    kid = afternoon["students"][1]
    client.post("/api/runs/driver/dropoff", json={"student_id": kid["id"]},
                headers=driver_headers)
    reversed_ = client.post("/api/runs/driver/reverse", json={"student_id": kid["id"]},
                            headers=driver_headers)
    assert reversed_.status_code == 200, reversed_.text

    alerts = _wait_for(
        lambda: _alerts_of_type(client, admin_headers, fleet["bus"]["id"], "action-reversed")
    )
    assert alerts, "the office was not told about the correction"
    assert kid["name"] in alerts[0]["description"]
    assert "drop-off" in alerts[0]["description"]


def test_a_force_close_is_distinguishable_from_a_driver_completed_run(
    client, admin_headers, fleet, afternoon, pm_run
):
    """R29: both leave a completed run. Only one of them means children were
    recorded as unaccounted, and the office cannot act on the difference it
    cannot see."""
    closed = client.post(f"/api/runs/{pm_run}/force-close", headers=admin_headers)
    assert closed.status_code == 200, closed.text

    alerts = _wait_for(
        lambda: _alerts_of_type(client, admin_headers, fleet["bus"]["id"], "force-closed")
    )
    assert alerts, "a force-closed run raised no alert"
    description = alerts[0]["description"]
    for kid in afternoon["students"]:
        assert kid["name"] in description, description
    completed = _alerts_of_type(client, admin_headers, fleet["bus"]["id"], "run-completed")
    assert not [a for a in completed if a["run_id"] == pm_run], (
        "a force-close also reported itself as a normal completion"
    )


def test_the_closure_alerts_stay_out_of_parent_feeds_and_counters(
    client, admin_headers, fleet, afternoon, pm_run
):
    """R30: same guarantee as the start/end alerts. These name other people's
    children, so a leak here is worse than a duplicate — it is a disclosure."""
    driver_headers = _driver_headers(client, fleet)
    before_today = client.get("/api/incidents/today-count", headers=admin_headers).json()["count"]
    before_unread = client.get("/api/incidents/unread-count", headers=admin_headers).json()["count"]

    assert client.post("/api/runs/driver/end", json={"run_id": pm_run},
                       headers=driver_headers).status_code == 409
    client.post("/api/runs/driver/handover",
                json={"student_id": afternoon["students"][0]["id"], "note": "IT note"},
                headers=driver_headers)
    _wait_for(
        lambda: _alerts_of_type(client, admin_headers, fleet["bus"]["id"], "handover-recorded")
    )

    feed = client.get("/api/parent-portal/alerts", headers=fleet["parent_headers"]).json()
    types = {row.get("type") for row in feed}
    for type_ in ("closure-refused", "force-closed", "handover-recorded", "action-reversed"):
        assert type_ not in types, f"{type_} reached a parent's feed"
    blob = " ".join(str(row.get("description") or "") for row in feed)
    for kid in afternoon["students"]:
        assert kid["name"] not in blob, "another family's child was named in a parent's feed"

    after_today = client.get("/api/incidents/today-count", headers=admin_headers).json()["count"]
    after_unread = client.get("/api/incidents/unread-count", headers=admin_headers).json()["count"]
    assert after_today == before_today, "closure alerts moved the incidents-today tile"
    assert after_unread == before_unread, "closure alerts landed in the acknowledgement queue"


def test_the_today_counter_uses_the_nairobi_day(client, admin_headers, fleet):
    """The office tile counts today in Africa/Nairobi, not in UTC.

    This only ever manifested between 00:00 and 03:00 Nairobi, so it hid for
    twenty-one hours a day: the predicate compared a timestamptz against
    `(now() at time zone 'Africa/Nairobi')::date`, which Postgres resolves at the
    server's UTC midnight. An incident raised at 00:30 Nairobi is 21:30 UTC the
    day before, so it fell outside "today" and the tile silently undercounted
    every incident from the start of the service day until dawn.

    Staged by SQL because it is a clock-dependent bug and the test must not be
    one: the row is placed inside the window rather than waiting for it.
    """
    before = client.get("/api/incidents/today-count", headers=admin_headers).json()["count"]
    with psycopg.connect(DSN, autocommit=True) as pg:
        pg.execute(
            """
            insert into live_incidents (bus_id, bus_name, type, description,
                                        school_id, created_at)
            values (
                %s, %s, 'other', 'IT nairobi-boundary probe',
                -- U7: the tile is per school now, so the staged row must carry
                -- the bus's school or it is invisible to the scoped count.
                (select school_id from live_buses where id = %s),
                -- 00:30 on today's Nairobi date, expressed as the instant it is.
                ((now() at time zone 'Africa/Nairobi')::date + time '00:30')
                    at time zone 'Africa/Nairobi'
            )
            """,
            (fleet["bus"]["id"], fleet["bus"]["name"], fleet["bus"]["id"]),
        )
    try:
        after = client.get("/api/incidents/today-count", headers=admin_headers).json()["count"]
        assert after == before + 1, (
            "an incident from the small hours of the service day was not counted as today"
        )
    finally:
        with psycopg.connect(DSN, autocommit=True) as pg:
            pg.execute(
                "delete from live_incidents where description = 'IT nairobi-boundary probe'"
            )
