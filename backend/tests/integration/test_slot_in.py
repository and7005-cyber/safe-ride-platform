"""Slot-in proposal engine (U12; origin R12/R13/R15; AE2, AE7).

A student who becomes plannable while lacking a route for a leg their pattern
requires — at a school with applied plan routes — gets a one-line placement
proposal by cheapest insertion, stated as position + effect ("Bus 2, between
stop 3 and 4, +4 minutes for two children"). Accept applies JUST that
insertion (stop materialized at the stated position along the fixed order,
times recomputed, student linked) and notifies only the affected families
through the shipped U13 diff/baseline pipeline; dismiss removes the proposal
with the student still visibly unassigned; proposals age out (default 14
days) marked but never auto-applied. No feasible insertion surfaces the
student unplaceable naming the binding constraint.

Run with the stack up (scripts/start-local.sh):

    RUN_INTEGRATION=1 ../.venv/bin/python -m pytest tests/integration/test_slot_in.py -q

World geometry (keyless stack — the deterministic offline matrix: haversine
x 1.4 circuity / 30 km/h): the school sits west, three collinear homes run
east (near/mid/far, the far stop carrying two siblings), and the NEW home
sits south between the far and mid stops — close enough to the far→mid
segment that inserting there wins the lexicographic comparison (a detour
smaller than prepending a new first stop), far enough off it that the detour
delays the upstream children by >= 5 minutes (the R15 threshold), so the
delayed families genuinely notify on accept.

Conventions: 'IT SI '-prefixed entities, finally-cleanup, parents linked via
parent_email at student creation so feed rows are observable through the
parent notifications API, sanctioned psycopg only for what the API
deliberately does not expose (staging a proposal's age inside the applied
plan row's JSONB, staging a split pattern — pattern edits are draft-scoped
in the API — and peeking stop rows / links / plan documents). Proposal
generation runs in BackgroundTasks after the enrolment's response, so
positive assertions poll; silence assertions ride the causality of a row
written in the same fan-out transaction (the MEN-suite precedent).
"""

import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import psycopg
import pytest
from psycopg.rows import dict_row

from conftest import DSN

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="needs the local stack; set RUN_INTEGRATION=1",
)

BASE = os.environ.get("INTEGRATION_API_URL", "http://localhost:9001")

ADMIN = {"email": "admin@test.com", "password": "test1234."}
PARENT = {"email": "and7005@gmail.com", "password": "Test1234"}

MORNING, AFTERNOON = "morning", "afternoon"
PLAN_TYPES = ("route-updated", "route-unassigned")

SCHOOL_PT = (-1.3000, 36.8000)
H_NEAR = (-1.3000, 36.8200)
H_MID = (-1.3000, 36.8400)
H_FAR = (-1.3000, 36.8800)
# South of the far→mid segment: cheapest insertion lands between them with a
# >= 5-minute detour for the (upstream) far stop's two children.
NEW_HOME = (-1.3270, 36.8600)

WAIT_TIMEOUT = 15.0
WAIT_INTERVAL = 0.25
SILENCE_GRACE = 2.0


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE, timeout=30) as c:
        yield c


def login(client: httpx.Client, email: str, password: str) -> dict:
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


@pytest.fixture(scope="module")
def admin_headers(client):
    return login(client, ADMIN["email"], ADMIN["password"])


@pytest.fixture(scope="module")
def parent_headers(client):
    return login(client, PARENT["email"], PARENT["password"])


# --- builders -----------------------------------------------------------------

def signup_parent(client, marker: str, tag: str) -> dict:
    email = f"it-si-{tag}-{marker}@test.local"
    response = client.post(
        "/api/auth/signup",
        json={"email": email, "password": "ParentPass1!",
              "full_name": f"IT SI {tag} {marker}", "role": "parent"},
    )
    assert response.status_code == 200, response.text
    return {
        "id": response.json()["user"]["id"],
        "email": email,
        "headers": login(client, email, "ParentPass1!"),
    }


def _make_school(client, headers, marker: str) -> dict:
    created = client.post(
        "/api/fleet/schools",
        json={"name": f"IT SI School {marker}", "lat": SCHOOL_PT[0], "lng": SCHOOL_PT[1],
              "morning_bell": "07:30", "afternoon_bell": "15:30"},
        headers=headers,
    )
    assert created.status_code == 200, created.text
    return created.json()


def _make_bus(client, headers, marker: str, *, capacity: int) -> dict:
    created = client.post(
        "/api/fleet/buses",
        json={"name": f"IT SI Bus {marker}", "capacity": capacity},
        headers=headers,
    )
    assert created.status_code == 200, created.text
    return created.json()


def _make_student(client, headers, marker: str, i: int, school_id: str,
                  home: tuple[float, float], email: str | None = None) -> dict:
    created = client.post(
        "/api/students",
        json={
            "name": f"IT SI Kid {marker} {i}",
            "parent_name": f"IT SI Parent {marker} {i}",
            "parent_phone": f"+2547140002{i:02d}",
            "parent_email": email or f"it-si-{marker}-{i}@test.local",
            "home_address": f"IT SI Home {marker} {i}",
            "home_lat": home[0], "home_lng": home[1],
            "school_id": school_id,
            "route_ids": [],
        },
        headers=headers,
    )
    assert created.status_code == 200, created.text
    return created.json()


def _apply_fresh_plan(client, headers, school_id: str, bus_ids: list[str],
                      *, seed: int = 1) -> dict:
    confirmed = client.post(
        "/api/fleet-plans/confirm-fleet",
        json={"school_id": school_id, "bus_ids": bus_ids},
        headers=headers,
    )
    assert confirmed.status_code == 200, confirmed.text
    drafted = client.post(
        "/api/fleet-plans/draft", json={"school_id": school_id, "seed": seed},
        headers=headers,
    )
    assert drafted.status_code == 200, drafted.text
    review = client.get(
        "/api/fleet-plans/review", params={"school_id": school_id}, headers=headers
    )
    assert review.status_code == 200, review.text
    acks = [
        {"student_id": u["student_id"], "leg": leg}
        for leg in (MORNING, AFTERNOON)
        for u in review.json()["unplaceable"][leg]
    ]
    applied = client.post(
        f"/api/fleet-plans/{drafted.json()['id']}/apply",
        json={"confirmations": [], "acknowledgments": acks},
        headers=headers,
    )
    assert applied.status_code == 200, applied.text
    return applied.json()


def _teardown(client, headers, *, students=(), buses=(), schools=(), parents=()):
    for s in students:
        if s:
            client.delete(f"/api/students/{s['id']}", headers=headers)
    # Applied routes belong to the school; deleting the school cascades them
    # and the plan rows, but routes are removed first so bus deletes are clean.
    for sc in schools:
        if sc:
            routes = client.get("/api/fleet/routes", headers=headers).json()
            for r in routes:
                if r["school_id"] == sc["id"]:
                    client.delete(f"/api/fleet/routes/{r['id']}", headers=headers)
    for b in buses:
        if b:
            client.delete(f"/api/fleet/buses/{b['id']}", headers=headers)
    for sc in schools:
        if sc:
            client.delete(f"/api/fleet/schools/{sc['id']}", headers=headers)
    for p in parents:
        if p:
            client.delete(f"/api/accounts/parents/{p['id']}", headers=headers)


# --- readers / sanctioned psycopg ----------------------------------------------

def _pg_all(sql: str, params=()) -> list[dict]:
    with psycopg.connect(DSN, autocommit=True, row_factory=dict_row) as pg:
        return pg.execute(sql, params).fetchall()


def _pg_exec(sql: str, params=()) -> None:
    with psycopg.connect(DSN, autocommit=True) as pg:
        pg.execute(sql, params)


def _slot_ins(client, headers, school_id: str) -> dict:
    got = client.get(
        "/api/fleet-plans/slot-ins", params={"school_id": school_id}, headers=headers
    )
    assert got.status_code == 200, got.text
    return got.json()


def _wait_until(predicate, timeout: float = WAIT_TIMEOUT) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(WAIT_INTERVAL)
    return bool(predicate())


def _wait_for_records(client, headers, school_id: str, student_id: str,
                      n: int = 1) -> dict:
    def records() -> list[dict]:
        body = _slot_ins(client, headers, school_id)
        return [r for r in body["proposals"] + body["unplaceable"]
                if r["student_id"] == student_id]

    assert _wait_until(lambda: len(records()) >= n), (
        f"expected {n} slot-in record(s) for {student_id}, got {records()}"
    )
    return _slot_ins(client, headers, school_id)

def _plan_feed(client, headers) -> list[dict]:
    rows = client.get("/api/push/notifications", headers=headers).json()
    return [r for r in rows if r["type"] in PLAN_TYPES]


def _school_routes(client, headers, school_id: str) -> list[dict]:
    routes = client.get("/api/fleet/routes", headers=headers).json()
    return [r for r in routes if r["school_id"] == school_id]


def _stop_rows(route_id: str) -> list[dict]:
    return _pg_all(
        "select stop_order, lat, lng, student_id, is_school_gate, scheduled_time "
        "from live_route_stops where route_id = %s "
        "order by stop_order asc, name asc",
        (route_id,),
    )


def _links(student_id: str) -> dict[str, str]:
    return {
        r["route_type"]: str(r["route_id"])
        for r in _pg_all(
            "select route_type, route_id from live_student_routes where student_id = %s",
            (student_id,),
        )
    }


def _minutes(hhmm: str) -> int:
    h, m = (int(x) for x in hhmm.split(":"))
    return h * 60 + m


# --- AE2: proposal text, accept, exact notification set --------------------------

def test_ae2_enrolment_proposal_text_accept_and_notifications(client, admin_headers):
    """AE2: a mid-year enrolment near an applied route yields ONE proposal
    whose text states the position and the minutes-delta exactly ("Bus …,
    between stop 1 and 2, +N minutes for two children" — the two siblings at
    the upstream stop are the delayed children). Accepting links the student
    on both legs, materializes the stop at the stated position on each
    (surviving stops untouched), and notifies exactly the new child's family
    plus the delayed children's families — the co-riders whose times did not
    move stay silent, and no audit row is written (the U13 manual-edit
    contract)."""
    marker = uuid.uuid4().hex[:6]
    parents = {tag: signup_parent(client, marker, tag)
               for tag in ("far1", "far2", "mid", "near", "new")}
    school = bus = None
    students: list[dict] = []
    new_kid = None
    try:
        school = _make_school(client, admin_headers, marker)
        bus = _make_bus(client, admin_headers, marker, capacity=8)
        students = [
            _make_student(client, admin_headers, marker, 0, school["id"], H_FAR,
                          email=parents["far1"]["email"]),
            _make_student(client, admin_headers, marker, 1, school["id"], H_FAR,
                          email=parents["far2"]["email"]),
            _make_student(client, admin_headers, marker, 2, school["id"], H_MID,
                          email=parents["mid"]["email"]),
            _make_student(client, admin_headers, marker, 3, school["id"], H_NEAR,
                          email=parents["near"]["email"]),
        ]
        _apply_fresh_plan(client, admin_headers, school["id"], [bus["id"]])
        for tag in ("far1", "far2", "mid", "near"):
            assert len(_plan_feed(client, parents[tag]["headers"])) == 1  # first apply

        # The trigger: a plannable enrolment lacking both legs.
        new_kid = _make_student(client, admin_headers, marker, 9, school["id"],
                                NEW_HOME, email=parents["new"]["email"])
        body = _wait_for_records(client, admin_headers, school["id"], new_kid["id"])
        assert body["unplaceable"] == []
        assert len(body["proposals"]) == 1
        p = body["proposals"][0]
        assert p["kind"] == "proposal"
        assert p["expired"] is False
        assert p["bus_id"] == bus["id"]
        assert set(p["legs"]) == {MORNING, AFTERNOON}

        # Cheapest insertion: between the far stop (two siblings, upstream —
        # delayed by the detour) and the mid stop; the PM mirror inserts at
        # the corresponding position of the reversed order.
        am, pm = p["legs"][MORNING], p["legs"][AFTERNOON]
        far_ids = {students[0]["id"], students[1]["id"]}
        assert am["position"] == 1 and am["join"] is False
        assert am["delayed_children"] == 2
        assert set(am["delayed_student_ids"]) == far_ids
        assert am["delay_minutes"] >= 5
        assert pm["position"] == 2
        assert set(pm["delayed_student_ids"]) == far_ids

        # AE2's exact statement shape, composed from the morning leg.
        assert p["text"] == (
            f"{bus['name']}, between stop 1 and 2, "
            f"+{am['delay_minutes']} minutes for two children"
        )

        accepted = client.post(
            "/api/fleet-plans/slot-ins/accept",
            json={"school_id": school["id"], "proposal_id": p["id"]},
            headers=admin_headers,
        )
        assert accepted.status_code == 200, accepted.text
        result = accepted.json()
        assert result["student_id"] == new_kid["id"]
        assert result["legs"][MORNING]["position"] == 1
        assert result["legs"][AFTERNOON]["position"] == 2

        # Materialized at the stated position, surviving stops untouched.
        routes = {r["type"]: r for r in _school_routes(client, admin_headers, school["id"])}
        am_rows = [r for r in _stop_rows(routes[MORNING]["id"]) if not r["is_school_gate"]]
        by_order: dict[int, set] = {}
        for r in am_rows:
            by_order.setdefault(r["stop_order"], set()).add(str(r["student_id"]))
        assert by_order[1] == far_ids                        # order 1: the far siblings
        assert by_order[2] == {new_kid["id"]}                # inserted between 1 and 2
        assert by_order[3] == {students[2]["id"]}            # mid shifted 2 -> 3
        assert by_order[4] == {students[3]["id"]}            # near shifted 3 -> 4
        pm_rows = [r for r in _stop_rows(routes[AFTERNOON]["id"]) if not r["is_school_gate"]]
        pm_orders = {str(r["student_id"]): r["stop_order"] for r in pm_rows}
        assert pm_orders[new_kid["id"]] == 4                 # base 2 + position 2
        assert pm_orders[students[3]["id"]] == 2             # near untouched
        assert pm_orders[students[2]["id"]] == 3             # mid untouched
        assert pm_orders[students[0]["id"]] == 5             # far shifted 4 -> 5

        # Links + denormalized attributes (morning-clock rule).
        assert _links(new_kid["id"]) == {
            MORNING: routes[MORNING]["id"], AFTERNOON: routes[AFTERNOON]["id"],
        }
        live = {s["id"]: s for s in client.get("/api/students", headers=admin_headers).json()}
        assert live[new_kid["id"]]["bus_id"] == bus["id"]
        new_am_time = result["legs"][MORNING]["scheduled_time"]
        assert live[new_kid["id"]]["pickup_time"] == new_am_time

        # The new child's pickup precedes the gate anchor (backward solve).
        assert _minutes(new_am_time) < _minutes("07:30")

        # Exactly the new child's and the delayed children's families notify:
        # the new child is a first communication; the far siblings moved >= 5
        # minutes; mid and near did not move at all. All feed rows land in ONE
        # fan-out transaction, so far1's second row proves mid/near's silence.
        assert _wait_until(lambda: len(_plan_feed(client, parents["new"]["headers"])) == 1)
        new_rows = _plan_feed(client, parents["new"]["headers"])
        assert [r["type"] for r in new_rows] == ["route-updated"]
        assert new_rows[0]["student_id"] == new_kid["id"]
        assert _wait_until(lambda: len(_plan_feed(client, parents["far1"]["headers"])) == 2)
        assert len(_plan_feed(client, parents["far2"]["headers"])) == 2
        assert len(_plan_feed(client, parents["mid"]["headers"])) == 1
        assert len(_plan_feed(client, parents["near"]["headers"])) == 1

        # No audit row: accept rides the manual-edit notification path.
        assert _pg_all(
            "select 1 from live_admin_audit where school_id = %s "
            "and action not in ('plan-applied')",
            (school["id"],),
        ) == []

        # The accepted proposal is gone from the store.
        after = _slot_ins(client, admin_headers, school["id"])
        assert after["proposals"] == [] and after["unplaceable"] == []
    finally:
        _teardown(
            client, admin_headers,
            students=students + ([new_kid] if new_kid else []),
            buses=(bus,), schools=(school,),
            parents=parents.values(),
        )


# --- AE7: frozen route, explicit position, no reflow ------------------------------

def test_ae7_frozen_route_insertion_keeps_manual_order(client, admin_headers):
    """AE7: after an admin manually reorders an applied route (freeze), a
    slot-in proposal targets the FROZEN order and accepting inserts at the
    explicit position without reflowing the surviving stops — the manual
    order stands, the freeze flags stand, nothing re-optimises."""
    marker = uuid.uuid4().hex[:6]
    school = bus = None
    students: list[dict] = []
    new_kid = None
    try:
        school = _make_school(client, admin_headers, marker)
        bus = _make_bus(client, admin_headers, marker, capacity=8)
        students = [
            _make_student(client, admin_headers, marker, 0, school["id"], H_FAR),
            _make_student(client, admin_headers, marker, 1, school["id"], H_MID),
        ]
        _apply_fresh_plan(client, admin_headers, school["id"], [bus["id"]])

        routes = {r["type"]: r for r in _school_routes(client, admin_headers, school["id"])}
        am_route = routes[MORNING]
        keys = [s["group_key"] for s in sorted(am_route["route_stops"],
                                               key=lambda s: s["stop_order"])
                if s["group_key"] and not s["is_school_gate"]]
        assert len(keys) == 2
        frozen_order = list(reversed(keys))
        frozen = client.put(
            f"/api/fleet/routes/{am_route['id']}/stop-order",
            json={"order": frozen_order}, headers=admin_headers,
        )
        assert frozen.status_code == 200, frozen.text

        new_kid = _make_student(client, admin_headers, marker, 9, school["id"], NEW_HOME)
        body = _wait_for_records(client, admin_headers, school["id"], new_kid["id"])
        assert len(body["proposals"]) == 1
        p = body["proposals"][0]
        am = p["legs"][MORNING]
        assert am["frozen"] is True

        # The frozen (manually reversed) student order, by location key.
        def am_sequence() -> list[str]:
            rows = [r for r in _stop_rows(am_route["id"]) if not r["is_school_gate"]]
            seq: list[str] = []
            for r in rows:
                key = f"{r['lat']:.6f},{r['lng']:.6f}"
                if key not in seq:
                    seq.append(key)
            return seq

        before = am_sequence()
        assert before == frozen_order  # sanity: the freeze is in force

        accepted = client.post(
            "/api/fleet-plans/slot-ins/accept",
            json={"school_id": school["id"], "proposal_id": p["id"]},
            headers=admin_headers,
        )
        assert accepted.status_code == 200, accepted.text

        # Inserted at exactly the stated position; the surviving stops keep
        # the manual order; the freeze flags survive the insertion.
        new_key = f"{NEW_HOME[0]:.6f},{NEW_HOME[1]:.6f}"
        expected = list(before)
        expected.insert(am["position"], new_key)
        assert am_sequence() == expected
        after = {r["type"]: r for r in _school_routes(client, admin_headers, school["id"])}
        assert after[MORNING]["manual_stop_order"] is True
        assert after[MORNING]["plan_ordered"] is True
    finally:
        _teardown(client, admin_headers,
                  students=students + ([new_kid] if new_kid else []),
                  buses=(bus,), schools=(school,))


# --- no feasible insertion --------------------------------------------------------

def test_full_bus_yields_unplaceable_named_seats_and_no_proposal(client, admin_headers):
    """No feasible insertion (every claimed bus full): NO proposal — the
    student surfaces on the slot-in list as unplaceable naming 'seats' (the
    solver's binding-constraint attribution) and stays visibly unassigned."""
    marker = uuid.uuid4().hex[:6]
    school = bus = None
    students: list[dict] = []
    new_kid = None
    try:
        school = _make_school(client, admin_headers, marker)
        bus = _make_bus(client, admin_headers, marker, capacity=2)
        students = [
            _make_student(client, admin_headers, marker, 0, school["id"], H_FAR),
            _make_student(client, admin_headers, marker, 1, school["id"], H_MID),
        ]
        _apply_fresh_plan(client, admin_headers, school["id"], [bus["id"]])

        new_kid = _make_student(client, admin_headers, marker, 9, school["id"], H_NEAR)
        body = _wait_for_records(client, admin_headers, school["id"], new_kid["id"])
        assert body["proposals"] == []
        assert len(body["unplaceable"]) == 1
        entry = body["unplaceable"][0]
        assert entry["constraint"] == "seats"
        assert entry["student_name"] == new_kid["name"]
        assert set(entry["legs"]) == {MORNING, AFTERNOON}

        # Unplaceable notices are not acceptable.
        refused = client.post(
            "/api/fleet-plans/slot-ins/accept",
            json={"school_id": school["id"], "proposal_id": entry["id"]},
            headers=admin_headers,
        )
        assert refused.status_code == 409, refused.text

        # Visibly unassigned: no links, display_status 'unassigned'.
        live = {s["id"]: s for s in client.get("/api/students", headers=admin_headers).json()}
        assert live[new_kid["id"]]["route_ids"] == []
        assert live[new_kid["id"]]["display_status"] == "unassigned"
    finally:
        _teardown(client, admin_headers,
                  students=students + ([new_kid] if new_kid else []),
                  buses=(bus,), schools=(school,))


# --- aging + dismiss --------------------------------------------------------------

def test_expired_proposal_marked_not_acceptable_then_dismiss(client, admin_headers):
    """A proposal past the aging window (staged via the sanctioned psycopg
    tamper inside the applied plan row's JSONB — the store is deliberately
    not API-writable) is MARKED expired and refuses accept with the student
    still visibly unassigned; dismiss removes it outright and a second accept
    404s. Expired or dismissed, the student never silently disappears."""
    marker = uuid.uuid4().hex[:6]
    school = bus = None
    students: list[dict] = []
    new_kid = None
    try:
        school = _make_school(client, admin_headers, marker)
        bus = _make_bus(client, admin_headers, marker, capacity=8)
        students = [_make_student(client, admin_headers, marker, 0, school["id"], H_FAR)]
        _apply_fresh_plan(client, admin_headers, school["id"], [bus["id"]])

        new_kid = _make_student(client, admin_headers, marker, 9, school["id"], H_MID)
        body = _wait_for_records(client, admin_headers, school["id"], new_kid["id"])
        assert len(body["proposals"]) == 1
        p = body["proposals"][0]
        assert p["expired"] is False

        # Stage the age: 40 days beats any plausible SLOT_IN_AGING_DAYS config.
        stale = (datetime.now(timezone.utc) - timedelta(days=40)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        _pg_exec(
            "update live_fleet_plans set document = "
            "jsonb_set(document, %s, to_jsonb(%s::text)) "
            "where school_id = %s and status = 'applied'",
            (["slot_in_proposals", "0", "created_at"], stale, school["id"]),
        )

        body = _slot_ins(client, admin_headers, school["id"])
        assert len(body["proposals"]) == 1
        assert body["proposals"][0]["expired"] is True

        refused = client.post(
            "/api/fleet-plans/slot-ins/accept",
            json={"school_id": school["id"], "proposal_id": p["id"]},
            headers=admin_headers,
        )
        assert refused.status_code == 409, refused.text
        assert "aged" in refused.json()["detail"]
        live = {s["id"]: s for s in client.get("/api/students", headers=admin_headers).json()}
        assert live[new_kid["id"]]["route_ids"] == []  # still unassigned, still visible

        dismissed = client.post(
            "/api/fleet-plans/slot-ins/dismiss",
            json={"school_id": school["id"], "proposal_id": p["id"]},
            headers=admin_headers,
        )
        assert dismissed.status_code == 200, dismissed.text
        assert dismissed.json()["removed"]["student_id"] == new_kid["id"]
        body = _slot_ins(client, admin_headers, school["id"])
        assert body["proposals"] == [] and body["unplaceable"] == []
        gone = client.post(
            "/api/fleet-plans/slot-ins/accept",
            json={"school_id": school["id"], "proposal_id": p["id"]},
            headers=admin_headers,
        )
        assert gone.status_code == 404, gone.text
        live = {s["id"]: s for s in client.get("/api/students", headers=admin_headers).json()}
        assert live[new_kid["id"]]["route_ids"] == []
        assert live[new_kid["id"]]["display_status"] == "unassigned"
    finally:
        _teardown(client, admin_headers,
                  students=students + ([new_kid] if new_kid else []),
                  buses=(bus,), schools=(school,))


# --- split rider: one proposal per leg, independently acceptable -------------------

def test_split_rider_gets_two_independent_proposals(client, admin_headers):
    """A split rider gets one proposal PER LEG; accepting one places that leg
    only and leaves the other proposal pending and acceptable. The split
    pattern is staged via psycopg (pattern edits are draft-scoped in the API;
    live_students.ridership_pattern is not client-writable) and regeneration
    re-triggered through the student-update hook."""
    marker = uuid.uuid4().hex[:6]
    school = bus = None
    students: list[dict] = []
    new_kid = None
    try:
        school = _make_school(client, admin_headers, marker)
        bus = _make_bus(client, admin_headers, marker, capacity=8)
        students = [_make_student(client, admin_headers, marker, 0, school["id"], H_FAR)]
        _apply_fresh_plan(client, admin_headers, school["id"], [bus["id"]])

        new_kid = _make_student(client, admin_headers, marker, 9, school["id"], H_MID)
        _wait_for_records(client, admin_headers, school["id"], new_kid["id"])

        _pg_exec(
            "update live_students set ridership_pattern = 'split' where id = %s",
            (new_kid["id"],),
        )
        retrigger = client.put(
            f"/api/students/{new_kid['id']}",
            json={
                "name": new_kid["name"],
                "parent_name": f"IT SI Parent {marker} 9",
                "parent_phone": "+254714000299",
                "parent_email": f"it-si-{marker}-9@test.local",
                "home_address": f"IT SI Home {marker} 9",
                "home_lat": H_MID[0], "home_lng": H_MID[1],
                "school_id": school["id"],
                "route_ids": [],
            },
            headers=admin_headers,
        )
        assert retrigger.status_code == 200, retrigger.text

        def split_records() -> list[dict]:
            body = _slot_ins(client, admin_headers, school["id"])
            return [r for r in body["proposals"]
                    if r["student_id"] == new_kid["id"] and len(r["legs"]) == 1]

        assert _wait_until(lambda: len(split_records()) == 2), split_records()
        records = split_records()
        assert {next(iter(r["legs"])) for r in records} == {MORNING, AFTERNOON}
        am_rec = next(r for r in records if MORNING in r["legs"])

        accepted = client.post(
            "/api/fleet-plans/slot-ins/accept",
            json={"school_id": school["id"], "proposal_id": am_rec["id"]},
            headers=admin_headers,
        )
        assert accepted.status_code == 200, accepted.text
        assert set(_links(new_kid["id"])) == {MORNING}

        remaining = split_records()
        assert len(remaining) == 1 and AFTERNOON in remaining[0]["legs"]
        accepted2 = client.post(
            "/api/fleet-plans/slot-ins/accept",
            json={"school_id": school["id"], "proposal_id": remaining[0]["id"]},
            headers=admin_headers,
        )
        assert accepted2.status_code == 200, accepted2.text
        assert set(_links(new_kid["id"])) == {MORNING, AFTERNOON}
    finally:
        _teardown(client, admin_headers,
                  students=students + ([new_kid] if new_kid else []),
                  buses=(bus,), schools=(school,))


# --- address-change stability rule -------------------------------------------------

def test_address_retriage_to_failed_keeps_the_existing_stop(client, admin_headers):
    """The U12 stability rule: an address edit that re-triages to failed (no
    geocode — an unresolvable string on the keyless stack) must NOT drop a
    placed child's stop. The student keeps the stale pin, their stop stays
    exactly where families were told it is, and no slot-in proposal appears
    (their legs are still routed)."""
    marker = uuid.uuid4().hex[:6]
    school = bus = None
    students: list[dict] = []
    try:
        school = _make_school(client, admin_headers, marker)
        bus = _make_bus(client, admin_headers, marker, capacity=8)
        students = [
            _make_student(client, admin_headers, marker, 0, school["id"], H_FAR),
            _make_student(client, admin_headers, marker, 1, school["id"], H_MID),
        ]
        _apply_fresh_plan(client, admin_headers, school["id"], [bus["id"]])
        kid = students[0]
        live = {s["id"]: s for s in client.get("/api/students", headers=admin_headers).json()}
        route_ids = live[kid["id"]]["route_ids"]
        assert len(route_ids) == 2

        # The edit: a new address, NO coordinates — the re-geocode fails
        # (gibberish no provider resolves) and the pin must survive.
        edited = client.put(
            f"/api/students/{kid['id']}",
            json={
                "name": kid["name"],
                "parent_name": f"IT SI Parent {marker} 0",
                "parent_phone": "+254714000200",
                "parent_email": f"it-si-{marker}-0@test.local",
                "home_address": f"Zzqx Unresolvable Lane 99999 Nowheretown {marker}",
                "home_lat": None, "home_lng": None,
                "school_id": school["id"],
                "route_ids": route_ids,
            },
            headers=admin_headers,
        )
        assert edited.status_code == 200, edited.text

        live = {s["id"]: s for s in client.get("/api/students", headers=admin_headers).json()}
        assert live[kid["id"]]["home_lat"] == pytest.approx(H_FAR[0], abs=1e-6)
        assert live[kid["id"]]["home_lng"] == pytest.approx(H_FAR[1], abs=1e-6)
        assert live[kid["id"]]["route_ids"] and set(live[kid["id"]]["route_ids"]) == set(route_ids)

        # The stop stayed at the stale pin on both legs.
        for r in _school_routes(client, admin_headers, school["id"]):
            row = next(
                (s for s in _stop_rows(r["id"]) if str(s["student_id"]) == kid["id"]),
                None,
            )
            assert row is not None, f"stop dropped from {r['type']} route"
            assert float(row["lat"]) == pytest.approx(H_FAR[0], abs=1e-6)
            assert float(row["lng"]) == pytest.approx(H_FAR[1], abs=1e-6)

        # Still fully routed: the update hook's regeneration pass leaves no
        # slot-in record behind (poll-free: the settle grace bounds the task).
        time.sleep(SILENCE_GRACE)
        body = _slot_ins(client, admin_headers, school["id"])
        assert [r for r in body["proposals"] + body["unplaceable"]
                if r["student_id"] == kid["id"]] == []
    finally:
        _teardown(client, admin_headers, students=students,
                  buses=(bus,), schools=(school,))


# --- authorization -----------------------------------------------------------------

def test_non_admin_refused_on_slot_in_surface(client, parent_headers):
    """The slot-in surface is admin-only: unauthenticated 401, parent 403 on
    list, accept and dismiss — phantom ids keep the checks side-effect-free."""
    phantom_school = str(uuid.uuid4())
    payload = {"school_id": phantom_school, "proposal_id": uuid.uuid4().hex}
    assert client.get(
        "/api/fleet-plans/slot-ins", params={"school_id": phantom_school}
    ).status_code == 401
    assert client.get(
        "/api/fleet-plans/slot-ins", params={"school_id": phantom_school},
        headers=parent_headers,
    ).status_code == 403
    for verb in ("accept", "dismiss"):
        assert client.post(
            f"/api/fleet-plans/slot-ins/{verb}", json=payload
        ).status_code == 401
        assert client.post(
            f"/api/fleet-plans/slot-ins/{verb}", json=payload, headers=parent_headers
        ).status_code == 403
