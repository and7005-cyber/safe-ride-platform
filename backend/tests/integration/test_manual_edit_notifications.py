"""Manual live-route edit notifications (fleet-plan U13, origin R15).

R15 requires parent notification for ANY live-route change — "an applied plan
or a manual edit". U6/U7 shipped the apply/restore half; U13 wires the manual
edit paths (stop-order, stop time edit, stop cancel, recalculate, route
update/delete) through the same baseline machinery: post-commit, each affected
(student, leg)'s LIVE stop is diffed against live_communicated_stops with the
plan diff's thresholds — >= 5-minute time move, place change, bus change,
removal, NULL baseline = first communication — feed rows land with
plan_audit_id NULL (the 011 partial unique indexes NULL as distinct, so
repeats are suppressed by the baseline, never the index), and the baseline
moves only on send (the anti-accumulation rule).

Roster paths (student create/update/delete, bulk) seed missing baselines
SILENTLY — the U13 deviation these tests also pin: creating a routed child
writes their baseline without a feed row.

Run with the stack up (scripts/start-local.sh):

    RUN_INTEGRATION=1 ../.venv/bin/python -m pytest tests/integration/test_manual_edit_notifications.py -q

The container has no Google key, so every mutation takes the degraded path:
a never-computed route's stop times equal the students' pickup_times, manual
reorders keep each stop's own time, and drift vs the baseline is staged via
the sanctioned psycopg tamper (the test_fleet_plan_apply precedent — the
baseline table is deliberately not API-exposed).

Conventions: 'IT MEN '-prefixed entities, finally-cleanup, parents linked via
parent_email at student creation so feed rows are observable through the
parent notifications API. The fan-out runs in BackgroundTasks after the
mutation's response, so positive assertions poll; zero-row assertions either
ride the causality of a row written in the same fan-out transaction or wait a
settle period.
"""

import os
import time
import uuid

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

MORNING = "morning"
PLAN_TYPES = ("route-updated", "route-unassigned")

HOME_A = (-1.2800, 36.7900)
HOME_B = (-1.2900, 36.8000)

# BackgroundTasks latency budget: generous for a cold container, cheap when
# the row is already there.
WAIT_TIMEOUT = 10.0
WAIT_INTERVAL = 0.25
# Settle period for must-stay-silent edits with no same-act positive row to
# ride on: the task runs right after the response, so 2 s is ample.
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


# --- builders -----------------------------------------------------------------

def signup_parent(client, marker: str, tag: str) -> dict:
    email = f"it-men-{tag}-{marker}@test.local"
    response = client.post(
        "/api/auth/signup",
        json={"email": email, "password": "ParentPass1!",
              "full_name": f"IT MEN {tag} {marker}", "role": "parent"},
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
        json={"name": f"IT MEN School {marker}", "lat": -1.3000, "lng": 36.8200},
        headers=headers,
    )
    assert created.status_code == 200, created.text
    return created.json()


def _make_route(client, headers, marker: str, school_id: str) -> dict:
    created = client.post(
        "/api/fleet/routes",
        json={"name": f"IT MEN Route {marker}", "type": MORNING, "school_id": school_id},
        headers=headers,
    )
    assert created.status_code == 200, created.text
    return created.json()


def _make_student(client, headers, marker: str, letter: str, pickup: str,
                  home: tuple[float, float], parent: dict,
                  route_ids: list[str]) -> dict:
    created = client.post(
        "/api/students",
        json={
            "name": f"IT MEN Kid {letter} {marker}",
            "parent_name": f"IT MEN Parent {letter}",
            "parent_phone": "+254711000051",
            "parent_email": parent["email"],
            "home_address": f"IT MEN {letter} Lane {marker}",
            "home_lat": home[0], "home_lng": home[1],
            "pickup_time": pickup,
            "route_ids": route_ids,
        },
        headers=headers,
    )
    assert created.status_code == 200, created.text
    return created.json()


def _cleanup(client, headers, *, students=(), routes=(), schools=(), parents=()):
    for s in students:
        if s:
            client.delete(f"/api/students/{s['id']}", headers=headers)
    for r in routes:
        if r:
            client.delete(f"/api/fleet/routes/{r['id']}", headers=headers)
    for sc in schools:
        if sc:
            client.delete(f"/api/fleet/schools/{sc['id']}", headers=headers)
    for p in parents:
        if p:
            client.delete(f"/api/accounts/parents/{p['id']}", headers=headers)


# --- readers / sanctioned psycopg -----------------------------------------------
# The baseline table is deliberately not API-exposed; reads and drift staging
# go through psycopg (the test_fleet_plan_apply precedent).

def _pg_all(sql: str, params=()) -> list[dict]:
    with psycopg.connect(DSN, autocommit=True, row_factory=dict_row) as pg:
        return pg.execute(sql, params).fetchall()


def _pg_exec(sql: str, params=()) -> None:
    with psycopg.connect(DSN, autocommit=True) as pg:
        pg.execute(sql, params)


def _baseline(student_id: str, leg: str = MORNING) -> dict | None:
    rows = _pg_all(
        "select stop_name, stop_lat, stop_lng, scheduled_time, bus_id "
        "from live_communicated_stops where student_id = %s and route_type = %s",
        (student_id, leg),
    )
    return rows[0] if rows else None


def _set_baseline_time(student_id: str, hhmm: str, leg: str = MORNING) -> None:
    _pg_exec(
        "update live_communicated_stops set scheduled_time = %s "
        "where student_id = %s and route_type = %s",
        (hhmm, student_id, leg),
    )


def _delete_baseline(student_id: str, leg: str = MORNING) -> None:
    _pg_exec(
        "delete from live_communicated_stops where student_id = %s and route_type = %s",
        (student_id, leg),
    )


def _plan_feed(client, headers) -> list[dict]:
    rows = client.get("/api/push/notifications", headers=headers).json()
    return [r for r in rows if r["type"] in PLAN_TYPES]


def _wait_until(predicate, timeout: float = WAIT_TIMEOUT) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(WAIT_INTERVAL)
    return bool(predicate())


def _wait_feed(client, headers, n: int) -> list[dict]:
    assert _wait_until(lambda: len(_plan_feed(client, headers)) >= n), (
        f"expected {n} plan feed row(s), got {_plan_feed(client, headers)}"
    )
    rows = _plan_feed(client, headers)
    assert len(rows) == n, rows
    return rows


def _route_payload(client, headers, route_id: str) -> dict:
    routes = client.get("/api/fleet/routes", headers=headers).json()
    return next(r for r in routes if r["id"] == route_id)


def _group_keys(route_payload: dict) -> list[str]:
    keys: list[str] = []
    for stop in sorted(route_payload["route_stops"], key=lambda s: s["stop_order"]):
        if stop["is_school_gate"] or stop["group_key"] is None:
            continue
        if stop["group_key"] not in keys:
            keys.append(stop["group_key"])
    return keys


def _reorder(client, headers, route_id: str, order: list[str]) -> None:
    response = client.put(
        f"/api/fleet/routes/{route_id}/stop-order", json={"order": order}, headers=headers
    )
    assert response.status_code == 200, response.text


def _minutes(hhmm: str) -> int:
    h, m = (int(x) for x in hhmm.split(":"))
    return h * 60 + m


def _shift(hhmm: str, minutes: int) -> str:
    total = (_minutes(hhmm) + minutes) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


# --- world -----------------------------------------------------------------------

def _build_world(client, admin_headers, marker: str) -> dict:
    """School + one morning route + kids A (06:30) and B (06:40), each with a
    linked parent. On the keyless stack the route is never-computed, so the
    live stop times ARE the pickup times. Creation is a roster path: the U13
    seed_only wiring writes both baselines silently — the world is complete
    only once they exist, and the seeding itself is asserted (values match
    live truth, zero feed rows)."""
    pa = signup_parent(client, marker, "pa")
    pb = signup_parent(client, marker, "pb")
    school = _make_school(client, admin_headers, marker)
    route = _make_route(client, admin_headers, marker, school["id"])
    kid_a = _make_student(client, admin_headers, marker, "A", "06:30", HOME_A, pa, [route["id"]])
    kid_b = _make_student(client, admin_headers, marker, "B", "06:40", HOME_B, pb, [route["id"]])

    assert _wait_until(
        lambda: _baseline(kid_a["id"]) is not None and _baseline(kid_b["id"]) is not None
    ), "roster paths should silently seed the communicated baselines"
    # Silent seeding pinned: baseline equals live truth, no feed rows.
    assert _baseline(kid_a["id"])["scheduled_time"] == "06:30"
    assert _baseline(kid_b["id"])["scheduled_time"] == "06:40"
    assert _plan_feed(client, pa["headers"]) == []
    assert _plan_feed(client, pb["headers"]) == []
    return {
        "school": school, "route": route,
        "kid_a": kid_a, "kid_b": kid_b, "pa": pa, "pb": pb,
    }


def _teardown_world(client, admin_headers, w: dict) -> None:
    _cleanup(
        client, admin_headers,
        students=(w.get("kid_a"), w.get("kid_b")),
        routes=(w.get("route"),),
        schools=(w.get("school"),),
        parents=(w.get("pa"), w.get("pb")),
    )


# --- tests -----------------------------------------------------------------------

def test_reorder_notifies_only_over_threshold_and_moves_baseline_on_send(
    client, admin_headers
):
    """One reorder against staged drift: the >= 6-minute child's family gets
    exactly one route-updated row and their baseline snaps to the live time;
    the 4-minute child stays silent and their baseline keeps the communicated
    value (the anti-accumulation rule). A second identical edit adds nothing:
    nothing moved vs the freshly written baseline — plan_audit_id NULL means
    the dedup index never suppresses manual rows, the baseline does."""
    marker = uuid.uuid4().hex[:6]
    w = _build_world(client, admin_headers, marker)
    try:
        kid_a, kid_b, pa, pb = w["kid_a"], w["kid_b"], w["pa"], w["pb"]
        # Stage drift in the last-communicated values: A was told 6 minutes
        # earlier than the live stop now reads, B 4 minutes.
        _set_baseline_time(kid_a["id"], _shift("06:30", -6))
        _set_baseline_time(kid_b["id"], _shift("06:40", -4))

        keys = _group_keys(_route_payload(client, admin_headers, w["route"]["id"]))
        assert len(keys) == 2
        new_order = [keys[1], keys[0]]
        _reorder(client, admin_headers, w["route"]["id"], new_order)

        rows = _wait_feed(client, pa["headers"], 1)
        assert rows[0]["type"] == "route-updated"
        assert rows[0]["student_id"] == kid_a["id"]
        # Same fan-out transaction wrote (or didn't write) both families'
        # rows: pa's arrival proves pb's silence is final for this edit.
        assert _plan_feed(client, pb["headers"]) == []
        # Baseline moved ONLY on send.
        assert _baseline(kid_a["id"])["scheduled_time"] == "06:30"
        assert _baseline(kid_b["id"])["scheduled_time"] == _shift("06:40", -4)

        # Second identical edit: no duplicate for anyone.
        _reorder(client, admin_headers, w["route"]["id"], new_order)
        time.sleep(SILENCE_GRACE)
        assert len(_plan_feed(client, pa["headers"])) == 1
        assert _plan_feed(client, pb["headers"]) == []
        assert _baseline(kid_a["id"])["scheduled_time"] == "06:30"
        assert _baseline(kid_b["id"])["scheduled_time"] == _shift("06:40", -4)
    finally:
        _teardown_world(client, admin_headers, w)


def test_unassign_sends_route_unassigned_and_deletes_the_baseline(
    client, admin_headers
):
    """Removing a child from a route (the stop-cancel path) sends exactly one
    route-unassigned row to that family and deletes their leg baseline — a
    later re-assignment is a first communication again. The co-rider's stop
    is untouched by the degraded rebuild: silent, baseline intact."""
    marker = uuid.uuid4().hex[:6]
    w = _build_world(client, admin_headers, marker)
    try:
        kid_a, kid_b, pa, pb = w["kid_a"], w["kid_b"], w["pa"], w["pb"]
        removed = client.delete(
            f"/api/fleet/routes/{w['route']['id']}/stops/{kid_a['id']}",
            headers=admin_headers,
        )
        assert removed.status_code == 200, removed.text

        rows = _wait_feed(client, pa["headers"], 1)
        assert rows[0]["type"] == "route-unassigned"
        assert rows[0]["student_id"] == kid_a["id"]
        assert _baseline(kid_a["id"]) is None  # deleted with the send
        assert _plan_feed(client, pb["headers"]) == []
        assert _baseline(kid_b["id"])["scheduled_time"] == "06:40"
    finally:
        _teardown_world(client, admin_headers, w)


def test_first_communication_on_an_edit_notifies_once_and_seeds(
    client, admin_headers
):
    """A child with NO baseline touched by an edit (here: their pickup-time
    edit, which genuinely moves the live stop time) is notified exactly once
    — first communication — and the baseline seeds with the live truth.
    Repeating the identical edit adds nothing: the seeded baseline now
    matches. The co-rider's time never moved: silent throughout."""
    marker = uuid.uuid4().hex[:6]
    w = _build_world(client, admin_headers, marker)
    try:
        kid_a, kid_b, pa, pb = w["kid_a"], w["kid_b"], w["pa"], w["pb"]
        # No baseline for A: the world builder proved seeding, so un-seed.
        _delete_baseline(kid_a["id"])

        def retime(hhmm: str) -> None:
            edited = client.put(
                f"/api/fleet/routes/{w['route']['id']}/stops/{kid_a['id']}",
                json={"pickup_time": hhmm}, headers=admin_headers,
            )
            assert edited.status_code == 200, edited.text

        retime("06:50")  # live stop time on the never-computed route follows
        rows = _wait_feed(client, pa["headers"], 1)
        assert rows[0]["type"] == "route-updated"
        assert rows[0]["student_id"] == kid_a["id"]
        seeded = _baseline(kid_a["id"])
        assert seeded is not None and seeded["scheduled_time"] == "06:50"
        assert _plan_feed(client, pb["headers"]) == []
        assert _baseline(kid_b["id"])["scheduled_time"] == "06:40"

        retime("06:50")  # identical second edit: nothing moved vs the seed
        time.sleep(SILENCE_GRACE)
        assert len(_plan_feed(client, pa["headers"])) == 1
        assert _plan_feed(client, pb["headers"]) == []
        assert _baseline(kid_a["id"])["scheduled_time"] == "06:50"
    finally:
        _teardown_world(client, admin_headers, w)


def test_recalculate_notifies_only_the_over_threshold_movers(
    client, admin_headers
):
    """Recalculate rebuilds every stop time; families are notified only where
    the recomputed time sits >= 5 minutes from what was last communicated —
    staged here as a 7-minute drift for A and 3 for B against the rebuild's
    (unchanged, keyless-degraded) times. A's baseline snaps to the live time,
    B keeps the communicated value."""
    marker = uuid.uuid4().hex[:6]
    w = _build_world(client, admin_headers, marker)
    try:
        kid_a, kid_b, pa, pb = w["kid_a"], w["kid_b"], w["pa"], w["pb"]
        _set_baseline_time(kid_a["id"], _shift("06:30", -7))
        _set_baseline_time(kid_b["id"], _shift("06:40", -3))

        recalculated = client.post(
            f"/api/fleet/routes/{w['route']['id']}/recalculate", headers=admin_headers
        )
        assert recalculated.status_code == 200, recalculated.text

        rows = _wait_feed(client, pa["headers"], 1)
        assert rows[0]["type"] == "route-updated"
        assert rows[0]["student_id"] == kid_a["id"]
        assert _plan_feed(client, pb["headers"]) == []
        assert _baseline(kid_a["id"])["scheduled_time"] == "06:30"
        assert _baseline(kid_b["id"])["scheduled_time"] == _shift("06:40", -3)
    finally:
        _teardown_world(client, admin_headers, w)
