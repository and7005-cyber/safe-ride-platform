"""Route stop ordering (ops-refinement U6 + U7): geometry recalculation in
auto mode (U6: R9, R10, R12; AE3 auto half, AE6) and admin manual ordering
with explicit recalculate (U7: R11–R13; AE3 manual half).

Run with the stack up (scripts/start-local.sh):

    RUN_INTEGRATION=1 ../.venv/bin/python -m pytest tests/integration/test_route_ordering.py -q

Two test planes share this module:

- **API-driven** tests hit the containerized API on :9001. The container has
  no GOOGLE_MAPS_API_KEY, so every roster mutation deterministically takes
  the DEGRADED path end-to-end: `stops_recalculated: false` in the mutation
  responses, `last_recalc_degraded` persisted on the route, preservation /
  pickup-time fallback ordering.
- **In-process** tests drive `regenerate_route_stops` (and `_sync_routes`)
  directly against the stack's published Postgres with monkeypatched fake
  geo providers — the GOOGLE path (optimizer order, cumulative leg ETAs,
  anchors, mixed signals, the route-row lock) cannot be exercised through
  the container and must never make live Google calls (the host venv DOES
  read a real key from backend/.env, so every in-process regenerate runs
  under patched providers).

Isolation: entities are 'IT RO '-prefixed and deleted in finally blocks; the
run-snapshot test builds its own throwaway driver+bus (the payload accepts a
known PIN) so no seeded entity is ever touched. In-process transactions that
only assert locking roll back instead of committing.
"""

import logging
import os
import uuid

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="needs the local stack; set RUN_INTEGRATION=1",
)

BASE = os.environ.get("INTEGRATION_API_URL", "http://localhost:9001")
# The stack's Postgres, published by docker-compose.local.yml — the same
# database the containerized API serves (test_driver_lifecycle precedent).
DB_URL = os.environ.get(
    "INTEGRATION_DB_URL", "postgresql://saferide:saferide@localhost:5432/saferide"
)

ADMIN = {"email": "admin@test.com", "password": "test1234."}

LEG_SECONDS = 300  # every fake leg is 5 minutes: ETAs are arithmetic


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


# In-process plumbing ------------------------------------------------------------

@pytest.fixture(scope="module")
def fleet_dao():
    from app.dao import fleet_dao as module

    return module


@pytest.fixture(scope="module")
def geo():
    from app.services import geo_service

    return geo_service


@pytest.fixture()
def db():
    import psycopg
    from psycopg.rows import dict_row

    conn = psycopg.connect(DB_URL, row_factory=dict_row)
    try:
        yield conn
        conn.rollback()  # anything a test left uncommitted is discarded
    finally:
        conn.close()


def patch_google(monkeypatch, geo, *, order: list[int] | None = None):
    """Both provider signals report Google: the optimiser returns ``order``
    (indices into the pickup-time-ordered group list) and the geometry a
    fixed LEG_SECONDS per leg."""

    def fake_order(points, school):
        idx = order if order is not None else list(range(len(points)))
        return {"ordered": [points[i] for i in idx], "provider": "google"}

    def fake_geometry(seq, departure=None):
        legs = [
            {"distance_m": 1000, "duration_s": LEG_SECONDS}
            for _ in range(max(len(seq) - 1, 0))
        ]
        return {
            "polyline": "itfake",
            "total_distance_m": 1000 * len(legs),
            "total_duration_s": LEG_SECONDS * len(legs),
            "legs": legs,
            "provider": "google-routes",
        }

    monkeypatch.setattr(geo, "optimized_order_with_provider", fake_order)
    monkeypatch.setattr(geo, "route_geometry", fake_geometry)


def patch_offline(monkeypatch, geo):
    """Neither signal is Google — the shape a key-less environment produces."""
    monkeypatch.setattr(
        geo,
        "optimized_order_with_provider",
        lambda points, school: {"ordered": list(points), "provider": "nearest-neighbour"},
    )
    monkeypatch.setattr(
        geo,
        "route_geometry",
        lambda seq, departure=None: {
            "polyline": None, "total_distance_m": 0, "total_duration_s": 0,
            "legs": [], "provider": "offline",
        },
    )


# API fixture helpers -------------------------------------------------------------

def _make_school(client, admin_headers, marker: str) -> dict:
    response = client.post(
        "/api/fleet/schools",
        json={"name": f"IT RO School {marker}", "lat": -1.3000, "lng": 36.8200},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _make_route(client, admin_headers, marker: str, route_type: str, school_id,
                bus_id=None, gate_anchor=None) -> dict:
    response = client.post(
        "/api/fleet/routes",
        json={"name": f"IT RO {route_type} {marker}", "type": route_type,
              "school_id": school_id, "bus_id": bus_id, "gate_anchor": gate_anchor},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _student_payload(marker: str, letter: str, pickup: str, lat=None, lng=None) -> dict:
    return {
        "name": f"IT RO Kid {letter} {marker}",
        "parent_name": f"IT RO Parent {letter}",
        "parent_phone": "+254711000041",
        "parent_email": f"it-ro-{letter.lower()}-{marker}@test.local",
        "home_address": f"IT RO {letter} Lane {marker}" if lat is not None else None,
        "home_lat": lat,
        "home_lng": lng,
        "pickup_time": pickup,
    }


def _make_student(client, admin_headers, payload: dict, route_ids: list[str]) -> dict:
    response = client.post(
        "/api/students", json={**payload, "route_ids": route_ids}, headers=admin_headers
    )
    assert response.status_code == 200, response.text
    return response.json()


def _get_route(client, admin_headers, route_id: str) -> dict:
    routes = client.get("/api/fleet/routes", headers=admin_headers).json()
    return next(r for r in routes if r["id"] == route_id)


def _stops(route_payload: dict) -> list[dict]:
    return sorted(route_payload["route_stops"], key=lambda s: s["stop_order"])


def _ordered_group_keys(stops: list[dict]) -> list[str]:
    """The manual-reorder payload (U7): every non-gate stop's server-issued
    group_key in display order, deduped — siblings at one location share a
    key and move as one group."""
    keys: list[str] = []
    for stop in stops:
        if stop["is_school_gate"] or stop["group_key"] is None:
            continue
        if stop["group_key"] not in keys:
            keys.append(stop["group_key"])
    return keys


def _reorder(client, admin_headers, route_id: str, order: list[str]) -> httpx.Response:
    return client.put(
        f"/api/fleet/routes/{route_id}/stop-order", json={"order": order}, headers=admin_headers
    )


def _cleanup(client, admin_headers, *, students=(), routes=(), schools=(), buses=(), drivers=()):
    for s in students:
        if s:
            client.delete(f"/api/students/{s['id']}", headers=admin_headers)
    for r in routes:
        if r:
            client.delete(f"/api/fleet/routes/{r['id']}", headers=admin_headers)
    for sc in schools:
        if sc:
            client.delete(f"/api/fleet/schools/{sc['id']}", headers=admin_headers)
    for b in buses:
        if b:
            client.delete(f"/api/fleet/buses/{b['id']}", headers=admin_headers)
    for d in drivers:
        if d:
            client.delete(f"/api/accounts/drivers/{d['id']}", headers=admin_headers)


# Payload surface (R10) ------------------------------------------------------------

def test_routes_payload_serializes_ordering_flags(client, admin_headers):
    """The routes list carries the two 008 ordering columns — RoutesPage
    renders the mode chip and the durable degradation badge from them."""
    routes = client.get("/api/fleet/routes", headers=admin_headers).json()
    assert routes, "seeded routes expected"
    for route in routes:
        assert isinstance(route["manual_stop_order"], bool), route
        assert isinstance(route["last_recalc_degraded"], bool), route


# Degraded path end-to-end (API plane; container has no Google key) ----------------

def test_degraded_mutations_signal_and_fall_back_to_pickup_order(client, admin_headers):
    """Covers AE3 (auto, degraded env): every roster mutation on a
    never-computed auto route answers stops_recalculated: false, persists
    last_recalc_degraded, and keeps the original pickup-time-then-name build
    (wholesale — creation order must not matter), so pickup-time edits keep
    applying. Removing the last student is not a degradation."""
    marker = uuid.uuid4().hex[:6]
    school = _make_school(client, admin_headers, marker)
    route = _make_route(client, admin_headers, marker, "morning", school["id"])
    kid_a = kid_b = None
    try:
        # A fresh, empty auto route is not degraded — nothing to compute.
        assert _get_route(client, admin_headers, route["id"])["last_recalc_degraded"] is False

        # Later pickup created FIRST: the rebuild must re-sort by pickup time,
        # not append by creation order.
        created_b = client.post(
            "/api/students",
            json={**_student_payload(marker, "B", "06:50", -1.2900, 36.8000),
                  "route_ids": [route["id"]]},
            headers=admin_headers,
        )
        assert created_b.status_code == 200, created_b.text
        kid_b = created_b.json()
        assert kid_b["stops_recalculated"] is False

        kid_a = _make_student(
            client, admin_headers,
            _student_payload(marker, "A", "06:30", -1.2800, 36.7900), [route["id"]],
        )
        assert kid_a["stops_recalculated"] is False

        listed = _get_route(client, admin_headers, route["id"])
        assert listed["last_recalc_degraded"] is True
        stops = _stops(listed)
        assert [s["name"] for s in stops] == [
            f"IT RO A Lane {marker}", f"IT RO B Lane {marker}", f"IT RO School {marker}",
        ]
        assert [s["scheduled_time"] for s in stops] == ["06:30", "06:50", None]
        assert stops[-1]["is_school_gate"] is True

        # Student update responses carry the signal too. Unchanged route
        # membership regenerates nothing — vacuously true; a real membership
        # change regenerates and reports the degraded fallback.
        unchanged = client.put(
            f"/api/students/{kid_a['id']}",
            json={**_student_payload(marker, "A", "06:30", -1.2800, 36.7900),
                  "route_ids": [route["id"]]},
            headers=admin_headers,
        )
        assert unchanged.status_code == 200, unchanged.text
        assert unchanged.json()["stops_recalculated"] is True

        for route_ids in ([], [route["id"]]):  # unassign, then re-assign
            updated = client.put(
                f"/api/students/{kid_a['id']}",
                json={**_student_payload(marker, "A", "06:30", -1.2800, 36.7900),
                      "route_ids": route_ids},
                headers=admin_headers,
            )
            assert updated.status_code == 200, updated.text
            assert updated.json()["stops_recalculated"] is False

        # Route metadata update: same signal, flag stays.
        route_update = client.put(
            f"/api/fleet/routes/{route['id']}",
            json={"name": route["name"], "type": "morning", "school_id": school["id"]},
            headers=admin_headers,
        )
        assert route_update.status_code == 200, route_update.text
        assert route_update.json()["stops_recalculated"] is False
        assert _get_route(client, admin_headers, route["id"])["last_recalc_degraded"] is True

        # Pickup-time edit still regenerates and re-sorts (R9 input path):
        # B moves to the front on a never-computed route.
        retimed = client.put(
            f"/api/fleet/routes/{route['id']}/stops/{kid_b['id']}",
            json={"pickup_time": "06:20"},
            headers=admin_headers,
        )
        assert retimed.status_code == 200, retimed.text
        # The time-edit endpoint threads the recalc signal too (B3): the
        # key-less container's rebuild is the degraded fallback.
        assert retimed.json() == {"ok": True, "stops_recalculated": False}
        stops = _stops(_get_route(client, admin_headers, route["id"]))
        assert [s["scheduled_time"] for s in stops] == ["06:20", "06:30", None]
        assert stops[0]["name"] == f"IT RO B Lane {marker}"

        # Removing a student keeps the signal shape; removing the last one is
        # a clean empty rebuild (flag clears — there is nothing to degrade).
        removed_b = client.delete(
            f"/api/fleet/routes/{route['id']}/stops/{kid_b['id']}", headers=admin_headers
        )
        assert removed_b.status_code == 200, removed_b.text
        assert removed_b.json() == {"ok": True, "stops_recalculated": False}

        removed_a = client.delete(
            f"/api/fleet/routes/{route['id']}/stops/{kid_a['id']}", headers=admin_headers
        )
        assert removed_a.status_code == 200, removed_a.text
        assert removed_a.json() == {"ok": True, "stops_recalculated": True}
        emptied = _get_route(client, admin_headers, route["id"])
        assert emptied["last_recalc_degraded"] is False
        assert [s["is_school_gate"] for s in _stops(emptied)] == [True]
    finally:
        _cleanup(client, admin_headers, students=(kid_a, kid_b), routes=(route,), schools=(school,))


# Google path (in-process, fake providers) -----------------------------------------

def test_google_morning_writes_optimizer_order_and_anchored_times(
    client, admin_headers, db, fleet_dao, geo, monkeypatch
):
    """Covers AE1/AE3 (auto half): the optimizer order lands with per-group
    times BACKWARD-SOLVED from the route's gate anchor (U4) — the gate row
    carries the anchor as the school arrival, each earlier stop one fake leg
    before; the flag clears. A second recalculation keeps the same gate; a
    pickup-time edit does NOT move a computed bell-anchored morning schedule."""
    marker = uuid.uuid4().hex[:6]
    school = _make_school(client, admin_headers, marker)
    # gate_anchor 07:45: with 3 stops (3 fake legs x 5 min) the departure solves
    # back to 07:30 and the gate arrival lands exactly on 07:45.
    route = _make_route(client, admin_headers, marker, "morning", school["id"], gate_anchor="07:45")
    kids = []
    try:
        for letter, pickup, lat, lng in (
            ("A", "06:30", -1.2800, 36.7900),
            ("B", "06:40", -1.2900, 36.8000),
            ("C", "06:50", -1.3100, 36.8300),
        ):
            kids.append(_make_student(
                client, admin_headers,
                _student_payload(marker, letter, pickup, lat, lng), [route["id"]],
            ))
        assert _get_route(client, admin_headers, route["id"])["last_recalc_degraded"] is True

        patch_google(monkeypatch, geo, order=[2, 0, 1])  # pickup order A,B,C -> C,A,B
        assert fleet_dao.regenerate_route_stops(db, route["id"]) is True
        db.commit()

        # U3: google success sets the explicit stops_computed marker and persists
        # the auto route's drive duration (3 legs x LEG_SECONDS) — auto routes now
        # write total_duration_s, not just planner-saved custom routes.
        marker_row = db.execute(
            "select stops_computed, total_duration_s from live_routes where id = %s",
            (route["id"],),
        ).fetchone()
        assert marker_row["stops_computed"] is True
        assert marker_row["total_duration_s"] == LEG_SECONDS * 3

        listed = _get_route(client, admin_headers, route["id"])
        assert listed["last_recalc_degraded"] is False  # google success clears (R10)
        stops = _stops(listed)
        assert [s["stop_order"] for s in stops] == [1, 2, 3, 4]
        assert [s["name"] for s in stops] == [
            f"IT RO C Lane {marker}", f"IT RO A Lane {marker}",
            f"IT RO B Lane {marker}", f"IT RO School {marker}",
        ]
        # Backward-solved from gate_anchor 07:45: departure 07:30, +5 min per
        # fake leg, gate arrival lands ON the anchor (U4).
        assert [s["scheduled_time"] for s in stops] == ["07:30", "07:35", "07:40", "07:45"]
        assert stops[-1]["is_school_gate"] is True

        # Second recalc: same gate anchor -> same times.
        assert fleet_dao.regenerate_route_stops(db, route["id"]) is True
        db.commit()
        stops = _stops(_get_route(client, admin_headers, route["id"]))
        assert [s["scheduled_time"] for s in stops] == ["07:30", "07:35", "07:40", "07:45"]

        # A pickup-time edit does NOT re-anchor a computed bell-anchored morning
        # schedule (U4 retired that): the gate stays 07:45 and every time holds.
        db.execute(
            "update live_students set pickup_time = '06:00' where id = %s", (kids[0]["id"],)
        )
        assert fleet_dao.regenerate_route_stops(db, route["id"]) is True
        db.commit()
        stops = _stops(_get_route(client, admin_headers, route["id"]))
        assert [s["scheduled_time"] for s in stops] == ["07:30", "07:35", "07:40", "07:45"]
    finally:
        _cleanup(client, admin_headers, students=kids, routes=(route,), schools=(school,))


def test_stops_computed_marker_false_true_then_preserved_on_degrade(
    client, admin_headers, db, fleet_dao, geo, monkeypatch
):
    """U3: the explicit stops_computed marker replaces the gate-row-time
    inference. A never-computed route reads false (not inferred from a gate
    time); a google recompute flips it true and persists total_duration_s; a
    subsequent degraded recompute preserves the marker instead of resetting it."""
    marker = uuid.uuid4().hex[:6]
    school = _make_school(client, admin_headers, marker)
    route = _make_route(client, admin_headers, marker, "morning", school["id"])
    kids = []
    try:
        kids.append(_make_student(
            client, admin_headers,
            _student_payload(marker, "A", "06:30", -1.2800, 36.7900), [route["id"]],
        ))
        # Never geometry-computed (key-less container -> degraded create): the
        # marker is an explicit false, not inferred from a written gate time.
        db.rollback()
        assert db.execute(
            "select stops_computed from live_routes where id = %s", (route["id"],)
        ).fetchone()["stops_computed"] is False

        # Google recompute -> marker true, drive duration persisted (1 stop -> 1
        # leg to the gate).
        patch_google(monkeypatch, geo, order=[0])
        assert fleet_dao.regenerate_route_stops(db, route["id"]) is True
        db.commit()
        row = db.execute(
            "select stops_computed, total_duration_s from live_routes where id = %s",
            (route["id"],),
        ).fetchone()
        assert row["stops_computed"] is True
        assert row["total_duration_s"] == LEG_SECONDS

        # Degraded recompute preserves the marker (stays true) — the preservation
        # path keys off the column, not a re-derived gate time.
        patch_offline(monkeypatch, geo)
        assert fleet_dao.regenerate_route_stops(db, route["id"]) is False
        db.commit()
        assert db.execute(
            "select stops_computed from live_routes where id = %s", (route["id"],)
        ).fetchone()["stops_computed"] is True
    finally:
        _cleanup(client, admin_headers, students=kids, routes=(route,), schools=(school,))


def test_google_afternoon_gate_first_reversed_and_anchored_at_1530(
    client, admin_headers, db, fleet_dao, geo, monkeypatch
):
    """Covers AE6: the afternoon sequence starts at the school gate and runs
    the computed order backwards, and every time is anchored on the 15:30
    type default — the students' morning-clock pickup times (06:40/06:48,
    the seeded shape) must never leak into afternoon drop times."""
    marker = uuid.uuid4().hex[:6]
    school = _make_school(client, admin_headers, marker)
    route = _make_route(client, admin_headers, marker, "afternoon", school["id"])
    kids = []
    try:
        for letter, pickup, lat, lng in (
            ("A", "06:40", -1.2800, 36.7900),
            ("B", "06:48", -1.2900, 36.8000),
        ):
            kids.append(_make_student(
                client, admin_headers,
                _student_payload(marker, letter, pickup, lat, lng), [route["id"]],
            ))

        patch_google(monkeypatch, geo, order=[0, 1])  # optimizer keeps A,B
        assert fleet_dao.regenerate_route_stops(db, route["id"]) is True
        db.commit()

        listed = _get_route(client, admin_headers, route["id"])
        assert listed["last_recalc_degraded"] is False
        stops = _stops(listed)
        # Gate first (departure), then the reversed drop sequence: B, A.
        assert stops[0]["is_school_gate"] is True
        assert stops[0]["scheduled_time"] == "15:30"
        assert [s["name"] for s in stops[1:]] == [
            f"IT RO B Lane {marker}", f"IT RO A Lane {marker}",
        ]
        assert [s["scheduled_time"] for s in stops] == ["15:30", "15:35", "15:40"]
    finally:
        _cleanup(client, admin_headers, students=kids, routes=(route,), schools=(school,))


def test_gate_anchor_override_backward_solves_to_that_gate(
    client, admin_headers, db, fleet_dao, geo, monkeypatch
):
    """U4: an explicit route gate_anchor is the arrival the morning schedule is
    solved back from — the gate lands on it regardless of the students' pickup
    times (which the pre-U4 model would have anchored on)."""
    marker = uuid.uuid4().hex[:6]
    school = _make_school(client, admin_headers, marker)
    route = _make_route(client, admin_headers, marker, "morning", school["id"], gate_anchor="08:10")
    kids = []
    try:
        for letter, pickup, lat, lng in (("A", "06:30", -1.28, 36.79), ("B", "06:40", -1.29, 36.80)):
            kids.append(_make_student(
                client, admin_headers, _student_payload(marker, letter, pickup, lat, lng), [route["id"]]
            ))
        patch_google(monkeypatch, geo, order=[0, 1])
        assert fleet_dao.regenerate_route_stops(db, route["id"]) is True
        db.commit()
        stops = _stops(_get_route(client, admin_headers, route["id"]))
        # 2 stops -> 2 fake legs -> departure 08:00, gate arrival ON 08:10.
        assert [s["scheduled_time"] for s in stops] == ["08:00", "08:05", "08:10"]
        assert stops[-1]["is_school_gate"] is True and stops[-1]["scheduled_time"] == "08:10"
    finally:
        _cleanup(client, admin_headers, students=kids, routes=(route,), schools=(school,))


def test_school_morning_bell_is_the_default_gate_anchor(
    client, admin_headers, db, fleet_dao, geo, monkeypatch
):
    """U4: with no route override, the school's morning_bell is the anchor (one
    authority: route override -> school bell -> system default)."""
    marker = uuid.uuid4().hex[:6]
    resp = client.post(
        "/api/fleet/schools",
        json={"name": f"IT RO Bell {marker}", "lat": -1.30, "lng": 36.82, "morning_bell": "07:20"},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    school = resp.json()
    route = _make_route(client, admin_headers, marker, "morning", school["id"])  # no override
    kids = []
    try:
        kids.append(_make_student(
            client, admin_headers, _student_payload(marker, "A", "06:30", -1.28, 36.79), [route["id"]]
        ))
        patch_google(monkeypatch, geo, order=[0])
        assert fleet_dao.regenerate_route_stops(db, route["id"]) is True
        db.commit()
        stops = _stops(_get_route(client, admin_headers, route["id"]))
        # 1 stop -> 1 leg -> departure 07:15, gate 07:20 (the school bell).
        assert [s["scheduled_time"] for s in stops] == ["07:15", "07:20"]
    finally:
        _cleanup(client, admin_headers, students=kids, routes=(route,), schools=(school,))


def test_non_convergent_solve_writes_best_iterate_and_flags_degraded(
    client, admin_headers, db, fleet_dao, geo, monkeypatch
):
    """U4: when the backward solve oscillates across a (simulated) traffic
    discontinuity and never lands within tolerance, the best-error iterate is
    still written but last_recalc_degraded is set — never silent."""
    marker = uuid.uuid4().hex[:6]
    school = _make_school(client, admin_headers, marker)
    route = _make_route(client, admin_headers, marker, "morning", school["id"], gate_anchor="07:30")
    kids = []
    try:
        kids.append(_make_student(
            client, admin_headers, _student_payload(marker, "A", "06:30", -1.28, 36.79), [route["id"]]
        ))

        # Fake optimiser = google; geometry drive OSCILLATES per call (10 vs 40
        # min) so the fixed point never settles inside the 60s tolerance.
        monkeypatch.setattr(
            geo, "optimized_order_with_provider",
            lambda points, s: {"ordered": list(points), "provider": "google"},
        )
        calls = {"n": 0}

        def oscillating_geometry(seq, departure=None):
            calls["n"] += 1
            dur = 600 if calls["n"] % 2 else 2400  # alternate 10 / 40 min
            legs = [{"distance_m": 1000, "duration_s": dur} for _ in range(max(len(seq) - 1, 0))]
            return {"polyline": "x", "total_distance_m": 1000 * len(legs),
                    "total_duration_s": dur * len(legs), "legs": legs, "provider": "google-routes"}

        monkeypatch.setattr(geo, "route_geometry", oscillating_geometry)

        # Degraded return (False): the solve did not converge.
        assert fleet_dao.regenerate_route_stops(db, route["id"]) is False
        db.commit()
        row = db.execute(
            "select last_recalc_degraded, stops_computed from live_routes where id = %s",
            (route["id"],),
        ).fetchone()
        assert row["last_recalc_degraded"] is True   # flagged, never silent
        assert row["stops_computed"] is True          # best iterate WAS written
        stops = _stops(_get_route(client, admin_headers, route["id"]))
        assert stops[-1]["is_school_gate"] is True     # a full computed set exists
        assert len([s for s in stops if not s["is_school_gate"]]) == 1
    finally:
        _cleanup(client, admin_headers, students=kids, routes=(route,), schools=(school,))


def test_single_location_route_computes_with_trivial_order(
    client, admin_headers, db, fleet_dao, geo, monkeypatch
):
    """A one-group auto route has no ordering problem: the real optimiser
    reports provider 'trivial' for a single located point (mirrored by the
    fake here), and with google geometry that is a FULL computed write —
    anchored times land on both stops and last_recalc_degraded is never
    raised."""
    marker = uuid.uuid4().hex[:6]
    school = _make_school(client, admin_headers, marker)
    route = _make_route(client, admin_headers, marker, "morning", school["id"])
    kid = None
    try:
        kid = _make_student(
            client, admin_headers,
            _student_payload(marker, "A", "06:30", -1.2800, 36.7900), [route["id"]],
        )
        patch_google(monkeypatch, geo)  # geometry: google-routes, 5-min legs
        monkeypatch.setattr(  # the real single-point order signal
            geo, "optimized_order_with_provider",
            lambda points, school_point: {"ordered": list(points), "provider": "trivial"},
        )
        assert fleet_dao.regenerate_route_stops(db, route["id"]) is True
        db.commit()

        listed = _get_route(client, admin_headers, route["id"])
        assert listed["last_recalc_degraded"] is False
        stops = _stops(listed)
        assert [s["name"] for s in stops] == [
            f"IT RO A Lane {marker}", f"IT RO School {marker}",
        ]
        # Backward-solved from the 07:00 default gate anchor (U4): one group,
        # one fake leg -> departure 06:55, gate arrival lands on 07:00.
        assert [s["scheduled_time"] for s in stops] == ["06:55", "07:00"]
        assert stops[-1]["is_school_gate"] is True
    finally:
        _cleanup(client, admin_headers, students=(kid,), routes=(route,), schools=(school,))


# Degraded fallback preserves computed state (API + in-process) ---------------------

def test_degraded_recalc_preserves_computed_order_times_and_gate(
    client, admin_headers, db, fleet_dao, geo, monkeypatch
):
    """Covers AE3/R10 fallback: after a google-computed write, a roster change
    in the degraded container keeps the surviving groups' previous relative
    order AND scheduled_time (never a re-sort that would contradict the
    computed times), appends the genuinely new group with the student's own
    pickup_time, and leaves the gate as-is. A student without coordinates is
    exactly such a degraded trigger."""
    marker = uuid.uuid4().hex[:6]
    school = _make_school(client, admin_headers, marker)
    route = _make_route(client, admin_headers, marker, "morning", school["id"])
    kids = []
    kid_d = None
    try:
        for letter, pickup, lat, lng in (
            ("A", "06:30", -1.2800, 36.7900),
            ("B", "06:40", -1.2900, 36.8000),
            ("C", "06:50", -1.3100, 36.8300),
        ):
            kids.append(_make_student(
                client, admin_headers,
                _student_payload(marker, letter, pickup, lat, lng), [route["id"]],
            ))
        patch_google(monkeypatch, geo, order=[2, 0, 1])  # computed order: C,A,B
        assert fleet_dao.regenerate_route_stops(db, route["id"]) is True
        db.commit()

        # Roster change through the container (no key there): coordinate-less
        # student D — no address either, so no silent geocoding can locate it.
        created = client.post(
            "/api/students",
            json={**_student_payload(marker, "D", "06:35"), "route_ids": [route["id"]]},
            headers=admin_headers,
        )
        assert created.status_code == 200, created.text
        kid_d = created.json()
        assert kid_d["stops_recalculated"] is False

        listed = _get_route(client, admin_headers, route["id"])
        assert listed["last_recalc_degraded"] is True
        stops = _stops(listed)
        # Survivors keep the computed order AND times; D appends before the
        # gate with its own pickup time; the gate time survives untouched.
        assert [s["name"] for s in stops[:3]] == [
            f"IT RO C Lane {marker}", f"IT RO A Lane {marker}", f"IT RO B Lane {marker}",
        ]
        # Computed backward from the 07:00 default anchor (3 legs): C 06:45,
        # A 06:50, B 06:55, gate 07:00.
        assert [s["scheduled_time"] for s in stops[:3]] == ["06:45", "06:50", "06:55"]
        assert stops[3]["student_id"] == kid_d["id"]
        assert stops[3]["scheduled_time"] == "06:35"
        assert stops[3]["lat"] is None  # coordinate-less, still a named stop
        assert stops[4]["is_school_gate"] is True
        assert stops[4]["scheduled_time"] == "07:00"

        # Removing the new student through the container preserves again.
        removed = client.delete(
            f"/api/fleet/routes/{route['id']}/stops/{kid_d['id']}", headers=admin_headers
        )
        assert removed.status_code == 200, removed.text
        assert removed.json() == {"ok": True, "stops_recalculated": False}
        stops = _stops(_get_route(client, admin_headers, route["id"]))
        assert [s["scheduled_time"] for s in stops] == ["06:45", "06:50", "06:55", "07:00"]
        assert [s["name"] for s in stops[:3]] == [
            f"IT RO C Lane {marker}", f"IT RO A Lane {marker}", f"IT RO B Lane {marker}",
        ]

        # The NEXT google success clears the durable flag (R10).
        assert fleet_dao.regenerate_route_stops(db, route["id"]) is True
        db.commit()
        assert _get_route(client, admin_headers, route["id"])["last_recalc_degraded"] is False
    finally:
        _cleanup(client, admin_headers, students=[*kids, kid_d], routes=(route,), schools=(school,))


def test_coordinate_edit_keeps_preserved_order_via_student_alias(
    client, admin_headers, db, fleet_dao, geo, monkeypatch
):
    """A coordinate-only student edit re-keys their location group while the
    stale stop rows still carry the old coords (the stale-stop state — e.g. a
    direct DAO/SQL write with no regeneration). The next degraded rebuild
    must resolve the group through the per-student alias: preserved order and
    time survive instead of the student being appended as new."""
    marker = uuid.uuid4().hex[:6]
    school = _make_school(client, admin_headers, marker)
    route = _make_route(client, admin_headers, marker, "morning", school["id"])
    kids = []
    try:
        for letter, pickup, lat, lng in (
            ("A", "06:30", -1.2800, 36.7900),
            ("B", "06:40", -1.2900, 36.8000),
            ("C", "06:50", -1.3100, 36.8300),
        ):
            kids.append(_make_student(
                client, admin_headers,
                _student_payload(marker, letter, pickup, lat, lng), [route["id"]],
            ))
        patch_google(monkeypatch, geo, order=[2, 0, 1])  # computed: C,A,B
        assert fleet_dao.regenerate_route_stops(db, route["id"]) is True
        db.commit()
        computed = [
            (s["name"], s["stop_order"], s["scheduled_time"])
            for s in _stops(_get_route(client, admin_headers, route["id"]))
        ]

        # Coordinate-only edit, no regeneration: the stop rows go stale.
        db.execute(
            "update live_students set home_lat = -1.3500, home_lng = 36.8500 where id = %s",
            (kids[0]["id"],),  # kid A — the group computed at order 2
        )
        db.commit()

        patch_offline(monkeypatch, geo)
        assert fleet_dao.regenerate_route_stops(db, route["id"]) is False
        db.commit()

        listed = _get_route(client, admin_headers, route["id"])
        assert listed["last_recalc_degraded"] is True
        after = [
            (s["name"], s["stop_order"], s["scheduled_time"]) for s in _stops(listed)
        ]
        assert after == computed  # alias-resolved: same order, same times
        # The stop row itself carries the fresh coordinates.
        moved = next(
            s for s in listed["route_stops"] if s["student_id"] == kids[0]["id"]
        )
        assert (moved["lat"], moved["lng"]) == (-1.35, 36.85)
    finally:
        _cleanup(client, admin_headers, students=kids, routes=(route,), schools=(school,))


def test_sibling_split_inherits_the_shared_record_exactly_once(
    client, admin_headers, db, fleet_dao, geo, monkeypatch
):
    """Two siblings shared one location group (one record, two student
    aliases). After one sibling's coordinates move, the degraded rebuild must
    hand the record to exactly ONE group — the unmoved sibling wins by exact
    location key — and treat the moved sibling as new (appended before the
    gate with their own pickup time). Never two groups with the inherited
    stop_order."""
    marker = uuid.uuid4().hex[:6]
    school = _make_school(client, admin_headers, marker)
    route = _make_route(client, admin_headers, marker, "morning", school["id"])
    kids = []
    try:
        for letter, pickup, lat, lng in (
            ("A", "06:30", -1.2800, 36.7900),
            ("A2", "06:35", -1.2800, 36.7900),  # sibling location: same group
            ("B", "06:40", -1.2900, 36.8000),
        ):
            kids.append(_make_student(
                client, admin_headers,
                _student_payload(marker, letter, pickup, lat, lng), [route["id"]],
            ))
        patch_google(monkeypatch, geo, order=[0, 1])  # groups: (A,A2), B
        assert fleet_dao.regenerate_route_stops(db, route["id"]) is True
        db.commit()

        # Split the siblings: A2 moves, stop rows stay stale.
        db.execute(
            "update live_students set home_lat = -1.3500, home_lng = 36.8500 where id = %s",
            (kids[1]["id"],),
        )
        db.commit()

        patch_offline(monkeypatch, geo)
        assert fleet_dao.regenerate_route_stops(db, route["id"]) is False
        db.commit()

        listed = _get_route(client, admin_headers, route["id"])
        by_name = {
            s["name"]: (s["stop_order"], s["scheduled_time"]) for s in listed["route_stops"]
        }
        # Computed backward from the 07:00 default anchor (2 legs): the shared
        # (A,A2) group 06:50, B 06:55, gate 07:00. The split preserves those.
        assert by_name == {
            f"IT RO A Lane {marker}": (1, "06:50"),   # kept the shared record
            f"IT RO B Lane {marker}": (2, "06:55"),   # preserved
            f"IT RO A2 Lane {marker}": (3, "06:35"),  # new group: own pickup time
            f"IT RO School {marker}": (4, "07:00"),   # gate as-is
        }
        # Exactly one inheritance: every group got a distinct order.
        orders = [s["stop_order"] for s in listed["route_stops"]]
        assert len(orders) == len(set(orders))
    finally:
        _cleanup(client, admin_headers, students=kids, routes=(route,), schools=(school,))


def test_mixed_provider_signals_take_the_full_fallback(
    client, admin_headers, db, fleet_dao, geo, monkeypatch, caplog
):
    """Order-without-geometry and geometry-without-order both fall back
    wholesale — never a partial write (U6/R10): the previously computed order
    and times survive byte-identical, the flag raises, and a WARNING names
    the failing signal. A key-less provider ('none') degrades the same way
    through the real geo_service code."""
    marker = uuid.uuid4().hex[:6]
    school = _make_school(client, admin_headers, marker)
    route = _make_route(client, admin_headers, marker, "morning", school["id"])
    kids = []
    try:
        for letter, pickup, lat, lng in (
            ("A", "06:30", -1.2800, 36.7900),
            ("B", "06:40", -1.2900, 36.8000),
        ):
            kids.append(_make_student(
                client, admin_headers,
                _student_payload(marker, letter, pickup, lat, lng), [route["id"]],
            ))
        patch_google(monkeypatch, geo, order=[0, 1])
        assert fleet_dao.regenerate_route_stops(db, route["id"]) is True
        db.commit()
        computed = [
            (s["name"], s["stop_order"], s["scheduled_time"])
            for s in _stops(_get_route(client, admin_headers, route["id"]))
        ]

        def assert_full_fallback():
            listed = _get_route(client, admin_headers, route["id"])
            assert listed["last_recalc_degraded"] is True
            after = [
                (s["name"], s["stop_order"], s["scheduled_time"]) for s in _stops(listed)
            ]
            assert after == computed  # preserved, not partially rewritten

        # Geometry fails, order succeeds — with a REVERSED order that must
        # never reach the table.
        patch_google(monkeypatch, geo, order=[1, 0])
        monkeypatch.setattr(
            geo, "route_geometry",
            lambda seq, departure=None: {
                "polyline": None, "total_distance_m": 0, "total_duration_s": 0,
                "legs": [], "provider": "offline",
            },
        )
        with caplog.at_level(logging.WARNING, logger="saferide.fleet"):
            assert fleet_dao.regenerate_route_stops(db, route["id"]) is False
        db.commit()
        assert any("degraded" in r.message for r in caplog.records)
        assert_full_fallback()

        # Order falls back, geometry would succeed.
        patch_google(monkeypatch, geo)
        monkeypatch.setattr(
            geo, "optimized_order_with_provider",
            lambda points, school: {"ordered": list(points), "provider": "nearest-neighbour"},
        )
        assert fleet_dao.regenerate_route_stops(db, route["id"]) is False
        db.commit()
        assert_full_fallback()

        # Provider 'none' (no keys at all) through the REAL geo functions:
        # no network is touched — the key check short-circuits first.
        class _NoKeys:
            google_maps_api_key = ""
            mapbox_token = ""

        monkeypatch.undo()
        monkeypatch.setattr(geo, "get_settings", lambda: _NoKeys())
        assert fleet_dao.regenerate_route_stops(db, route["id"]) is False
        db.commit()
        assert_full_fallback()
    finally:
        _cleanup(client, admin_headers, students=kids, routes=(route,), schools=(school,))


# Route-row lock (U6 -> U7 contention seam) -----------------------------------------

def test_regeneration_locks_the_route_row(
    client, admin_headers, db, fleet_dao, geo, monkeypatch
):
    """Every stop rewrite opens with `select … for update` on the route row —
    the same lock U7's manual reorder takes, so the two serialize instead of
    interleaving. Proven twice: the SQL itself (spy), and a real second
    connection timing out while the row is held, then succeeding once it is
    released. Nothing here commits — the lock assertions leave no state."""
    import psycopg
    from psycopg.rows import dict_row

    marker = uuid.uuid4().hex[:6]
    school = _make_school(client, admin_headers, marker)
    route = _make_route(client, admin_headers, marker, "morning", school["id"])
    kid = None
    try:
        kid = _make_student(
            client, admin_headers,
            _student_payload(marker, "A", "06:30", -1.2800, 36.7900), [route["id"]],
        )
        patch_offline(monkeypatch, geo)

        class _Spy:
            def __init__(self, conn):
                self._conn = conn
                self.sqls: list[str] = []

            def execute(self, sql, params=None):
                self.sqls.append(sql)
                return self._conn.execute(sql, params)

        spy = _Spy(db)
        fleet_dao.regenerate_route_stops(spy, route["id"])
        assert any("for update" in sql.lower() for sql in spy.sqls), spy.sqls
        db.rollback()

        with psycopg.connect(DB_URL, row_factory=dict_row) as holder:
            holder.execute(
                "select id from live_routes where id = %s for update", (route["id"],)
            )
            db.execute("set lock_timeout = '400ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                fleet_dao.regenerate_route_stops(db, route["id"])
            db.rollback()  # also rolls the transactional lock_timeout back
            holder.rollback()  # release the route row

            assert fleet_dao.regenerate_route_stops(db, route["id"]) is False
            db.rollback()
    finally:
        _cleanup(client, admin_headers, students=(kid,), routes=(route,), schools=(school,))


def test_geometry_drift_between_phases_discards_and_degrades(
    client, admin_headers, db, fleet_dao, geo, monkeypatch, caplog
):
    """The provider calls run BEFORE the route-row lock (phase 1); when the
    students drift before the locked write (phase 2), the stale computed
    geometry is DISCARDED: the write is the observable degraded fallback
    (preserved order and times, flag raised, WARNING names the drift) —
    never a partial or stale mix. The drift lands via a second committed
    connection inside the faked order call, i.e. exactly between phases."""
    import psycopg

    marker = uuid.uuid4().hex[:6]
    school = _make_school(client, admin_headers, marker)
    route = _make_route(client, admin_headers, marker, "morning", school["id"])
    kids = []
    try:
        for letter, pickup, lat, lng in (
            ("A", "06:30", -1.2800, 36.7900),
            ("B", "06:40", -1.2900, 36.8000),
        ):
            kids.append(_make_student(
                client, admin_headers,
                _student_payload(marker, letter, pickup, lat, lng), [route["id"]],
            ))
        # Honest computed pass first — the preservation baseline.
        patch_google(monkeypatch, geo, order=[0, 1])
        assert fleet_dao.regenerate_route_stops(db, route["id"]) is True
        db.commit()
        computed = [
            (s["name"], s["stop_order"], s["scheduled_time"])
            for s in _stops(_get_route(client, admin_headers, route["id"]))
        ]

        # Phase-1 provider whose order is REVERSED (it must never land) and
        # which mutates a student's coordinates through a second committed
        # connection before returning.
        patch_google(monkeypatch, geo, order=[0, 1])

        def order_and_mutate(points, school_point):
            with psycopg.connect(DB_URL) as side:
                side.execute(
                    "update live_students set home_lat = -1.5000 where id = %s",
                    (kids[0]["id"],),
                )
                side.commit()
            return {"ordered": list(reversed(points)), "provider": "google"}

        monkeypatch.setattr(geo, "optimized_order_with_provider", order_and_mutate)

        with caplog.at_level(logging.WARNING, logger="saferide.fleet"):
            assert fleet_dao.regenerate_route_stops(db, route["id"]) is False
        db.commit()
        assert any("drifted" in r.getMessage() for r in caplog.records), (
            [r.getMessage() for r in caplog.records]
        )

        listed = _get_route(client, admin_headers, route["id"])
        assert listed["last_recalc_degraded"] is True
        after = [
            (s["name"], s["stop_order"], s["scheduled_time"]) for s in _stops(listed)
        ]
        # The stale reversed order was discarded wholesale: the previously
        # computed order and times survive (A's group via its student alias).
        assert after == computed
    finally:
        _cleanup(client, admin_headers, students=kids, routes=(route,), schools=(school,))


# _sync_routes threads the aggregate signal (assignment plumbing) --------------------

def test_sync_routes_returns_the_aggregate_recalc_signal(
    client, admin_headers, db, geo, monkeypatch
):
    """The student-mutation responses' stops_recalculated comes from
    _sync_routes: True when every affected route computed geometry, False as
    soon as one fell back. Runs in-process (the True case is unreachable
    through the key-less container) and rolls back — no state leaks."""
    from app.dao.student_live_dao import _sync_routes

    marker = uuid.uuid4().hex[:6]
    school = _make_school(client, admin_headers, marker)
    route = _make_route(client, admin_headers, marker, "morning", school["id"])
    kid = loose = None
    try:
        kid = _make_student(
            client, admin_headers,
            _student_payload(marker, "A", "06:30", -1.2800, 36.7900), [route["id"]],
        )
        loose = _make_student(
            client, admin_headers,
            _student_payload(marker, "B", "06:40", -1.2900, 36.8000), [],
        )

        patch_google(monkeypatch, geo)
        assert _sync_routes(db, loose["id"], [route["id"]]) is True
        db.rollback()

        patch_offline(monkeypatch, geo)
        assert _sync_routes(db, loose["id"], [route["id"]]) is False
        db.rollback()

        # No membership change -> nothing regenerated -> vacuously true.
        patch_offline(monkeypatch, geo)
        assert _sync_routes(db, kid["id"], [route["id"]]) is True
        db.rollback()
    finally:
        _cleanup(client, admin_headers, students=(kid, loose), routes=(route,), schools=(school,))


# Started runs keep their snapshot (R12) --------------------------------------------

def test_started_run_keeps_its_snapshot_through_a_recalc(
    client, admin_headers, db, fleet_dao, geo, monkeypatch
):
    """R12: a run started before a reassignment operates on its own run_stops
    snapshot — a geometry recalculation that reorders the live route stops
    must not touch it. Throwaway driver+bus (known PIN), everything deleted."""
    import random

    marker = uuid.uuid4().hex[:6]
    driver = None
    for _ in range(5):
        pin = str(random.randint(100000, 999999))
        response = client.post(
            "/api/accounts/drivers",
            json={"full_name": f"IT RO Driver {marker}",
                  "email": f"it-ro-driver-{marker}@test.local",
                  "password": "test1234.", "phone": "+254711000042", "pin": pin},
            headers=admin_headers,
        )
        if response.status_code == 200:
            driver = {**response.json(), "pin": pin}
            break
    assert driver, "could not create throwaway driver"

    school = _make_school(client, admin_headers, marker)
    bus = client.post(
        "/api/fleet/buses",
        json={"name": f"IT RO Bus {marker}", "driver_id": driver["id"]},
        headers=admin_headers,
    ).json()
    route = _make_route(client, admin_headers, marker, "morning", school["id"], bus_id=bus["id"])
    kids = []
    run_id = None
    driver_headers = None
    try:
        for letter, pickup, lat, lng in (
            ("A", "06:30", -1.2800, 36.7900),
            ("B", "06:40", -1.2900, 36.8000),
        ):
            kids.append(_make_student(
                client, admin_headers,
                _student_payload(marker, letter, pickup, lat, lng), [route["id"]],
            ))

        pin_login = client.post("/api/auth/pin-login", json={"pin": driver["pin"]})
        assert pin_login.status_code == 200, pin_login.text
        driver_headers = {"Authorization": f"Bearer {pin_login.json()['token']}"}

        started = client.post(
            "/api/runs/driver/start", json={"route_id": route["id"]}, headers=driver_headers
        )
        assert started.status_code == 200, started.text
        run_id = started.json()["id"]

        def run_snapshot() -> list[tuple]:
            context = client.get("/api/runs/driver/context", headers=driver_headers).json()
            return [
                (s["stop_order"], s["name"], s["scheduled_time"])
                for s in context["run_stops"]
            ]

        before = run_snapshot()
        assert before, "started run must carry a run_stops snapshot"

        # Reorder the LIVE stops via a google recalc (B before A now).
        patch_google(monkeypatch, geo, order=[1, 0])
        assert fleet_dao.regenerate_route_stops(db, route["id"]) is True
        db.commit()

        live = _stops(_get_route(client, admin_headers, route["id"]))
        assert live[0]["name"] == f"IT RO B Lane {marker}"  # live order changed
        assert run_snapshot() == before  # the run's snapshot did not (R12)
    finally:
        if run_id and driver_headers:
            client.post("/api/runs/driver/end", json={"run_id": run_id}, headers=driver_headers)
            client.delete(f"/api/runs/{run_id}", headers=admin_headers)
        _cleanup(
            client, admin_headers,
            students=kids, routes=(route,), schools=(school,), buses=(bus,), drivers=(driver,),
        )


# Manual ordering (U7) ---------------------------------------------------------

def test_manual_reorder_persists_flips_flag_and_moves_sibling_groups_together(
    client, admin_headers
):
    """Covers AE3 (manual half): the admin's order is written positionally,
    manual_stop_order flips on, every stop keeps its own scheduled_time (times
    travel WITH their stop), the gate row is untouched, and siblings sharing a
    location move as one group under their shared server-issued group_key."""
    marker = uuid.uuid4().hex[:6]
    school = _make_school(client, admin_headers, marker)
    route = _make_route(client, admin_headers, marker, "morning", school["id"])
    kids = []
    try:
        for letter, pickup, lat, lng in (
            ("A", "06:30", -1.2800, 36.7900),
            ("A2", "06:35", -1.2800, 36.7900),  # sibling location: same group
            ("B", "06:40", -1.2900, 36.8000),
            ("C", "06:50", -1.3100, 36.8300),
        ):
            kids.append(_make_student(
                client, admin_headers,
                _student_payload(marker, letter, pickup, lat, lng), [route["id"]],
            ))

        listed = _get_route(client, admin_headers, route["id"])
        assert listed["manual_stop_order"] is False
        stops = _stops(listed)
        # Server-issued keys: coordinate-format for located groups, NULL on
        # the gate row; the sibling rows share one key.
        keys = _ordered_group_keys(stops)
        assert keys[0] == "-1.280000,36.790000"
        assert len(keys) == 3
        assert stops[-1]["is_school_gate"] is True and stops[-1]["group_key"] is None

        key_a, key_b, key_c = keys
        response = _reorder(client, admin_headers, route["id"], [key_c, key_a, key_b])
        assert response.status_code == 200, response.text
        assert response.json() == {"ok": True}

        listed = _get_route(client, admin_headers, route["id"])
        assert listed["manual_stop_order"] is True
        # Reordering is not a recalculation: the degraded badge from the
        # container's key-less builds stays until an explicit Recalculate.
        assert listed["last_recalc_degraded"] is True
        # Assert per-stop order and time (display order within a shared
        # stop_order is a collation detail): the siblings moved together and
        # every stop kept its own time; the gate row is untouched.
        assert {s["name"]: (s["stop_order"], s["scheduled_time"])
                for s in listed["route_stops"]} == {
            f"IT RO C Lane {marker}": (1, "06:50"),
            f"IT RO A Lane {marker}": (2, "06:30"),
            f"IT RO A2 Lane {marker}": (2, "06:35"),
            f"IT RO B Lane {marker}": (3, "06:40"),
            f"IT RO School {marker}": (4, None),
        }

        # Reordering again while already manual just rewrites the order.
        response = _reorder(client, admin_headers, route["id"], [key_a, key_b, key_c])
        assert response.status_code == 200, response.text
        listed = _get_route(client, admin_headers, route["id"])
        assert {s["name"]: s["stop_order"] for s in listed["route_stops"]} == {
            f"IT RO A Lane {marker}": 1,
            f"IT RO A2 Lane {marker}": 1,
            f"IT RO B Lane {marker}": 2,
            f"IT RO C Lane {marker}": 3,
            f"IT RO School {marker}": 4,
        }
    finally:
        _cleanup(client, admin_headers, students=kids, routes=(route,), schools=(school,))


def test_reorder_validation_rejects_bad_key_sets(client, admin_headers):
    """Missing, extra, duplicate, empty and foreign key lists are all 400s —
    including a real student:<uuid> key harvested from ANOTHER route — an
    unknown route is 404, and no failed attempt leaves any trace: the order
    stays put and the route never flips to manual."""
    marker = uuid.uuid4().hex[:6]
    school = _make_school(client, admin_headers, marker)
    route = _make_route(client, admin_headers, marker, "morning", school["id"])
    other_route = _make_route(client, admin_headers, marker, "afternoon", school["id"])
    kids = []
    try:
        for letter, pickup, lat, lng in (
            ("A", "06:30", -1.2800, 36.7900),
            ("B", "06:40", -1.2900, 36.8000),
        ):
            kids.append(_make_student(
                client, admin_headers,
                _student_payload(marker, letter, pickup, lat, lng), [route["id"]],
            ))
        # Coordinate-less, address-less student on the OTHER route: its group
        # key is the per-student fallback — a harvestable foreign key.
        kids.append(_make_student(
            client, admin_headers, _student_payload(marker, "N", "06:45"), [other_route["id"]],
        ))
        foreign_key = _ordered_group_keys(
            _stops(_get_route(client, admin_headers, other_route["id"]))
        )[0]
        assert foreign_key == f"student:{kids[-1]['id']}"

        key_a, key_b = _ordered_group_keys(_stops(_get_route(client, admin_headers, route["id"])))
        for bad in (
            [key_a],                                   # missing
            [],                                        # missing everything
            [key_a, key_b, "addr:it ro nowhere"],      # extra/unknown
            [key_a, key_a],                            # duplicate (same length)
            [key_a, key_b, key_b],                     # duplicate (extra copy)
            [key_a, foreign_key],                      # another route's student key
            [key_a, key_b, f"student:{uuid.uuid4()}"],  # nonexistent student key
        ):
            response = _reorder(client, admin_headers, route["id"], bad)
            assert response.status_code == 400, (bad, response.text)

        response = _reorder(client, admin_headers, str(uuid.uuid4()), [key_a, key_b])
        assert response.status_code == 404, response.text

        listed = _get_route(client, admin_headers, route["id"])
        assert listed["manual_stop_order"] is False  # nothing flipped
        assert [s["name"] for s in _stops(listed)] == [
            f"IT RO A Lane {marker}", f"IT RO B Lane {marker}", f"IT RO School {marker}",
        ]
    finally:
        _cleanup(client, admin_headers, students=kids, routes=(route, other_route),
                 schools=(school,))


def test_reorder_and_recalculate_conflict_on_custom_routes(client, admin_headers):
    """Planner-saved routes have one ordering authority — their saved stops:
    both verbs answer a friendly 409 (recalculate must never 200 while the
    regeneration silently early-returns) and the saved stops survive."""
    marker = uuid.uuid4().hex[:6]
    created = client.post(
        "/api/fleet/routes",
        json={
            "name": f"IT RO Planned {marker}",
            "type": "morning",
            "stops": [
                {"label": f"IT RO Corner A {marker}", "lat": -1.30, "lng": 36.80,
                 "pickup_time": "07:05"},
                {"label": f"IT RO Corner B {marker}", "lat": -1.31, "lng": 36.81,
                 "pickup_time": "07:20"},
                {"label": f"IT RO Gate {marker}", "lat": -1.32, "lng": 36.82,
                 "is_school": True},
            ],
            "polyline": "itfakepoly",
            "total_distance_m": 12345,
            "total_duration_s": 1800,
        },
        headers=admin_headers,
    )
    assert created.status_code == 200, created.text
    route = created.json()
    try:
        assert route["custom_stops"] is True

        response = _reorder(client, admin_headers, route["id"], ["addr:anywhere"])
        assert response.status_code == 409, response.text
        assert "planner" in response.json()["detail"].lower()

        response = client.post(
            f"/api/fleet/routes/{route['id']}/recalculate", headers=admin_headers
        )
        assert response.status_code == 409, response.text
        assert "planner" in response.json()["detail"].lower()

        response = client.post(
            f"/api/fleet/routes/{uuid.uuid4()}/recalculate", headers=admin_headers
        )
        assert response.status_code == 404, response.text

        listed = _get_route(client, admin_headers, route["id"])
        assert listed["custom_stops"] is True
        assert listed["manual_stop_order"] is False
        assert [s["name"] for s in _stops(listed)] == [
            f"IT RO Corner A {marker}", f"IT RO Corner B {marker}", f"IT RO Gate {marker}",
        ]
    finally:
        _cleanup(client, admin_headers, routes=(route,))


def test_manual_order_survives_assignment_and_unassignment(
    client, admin_headers, db, fleet_dao, geo, monkeypatch
):
    """Covers AE3 (manual half) + R13: with google-computed times on the
    stops, a manual reorder carries each time with its stop; a later
    assignment through the key-less container appends the new group before
    the gate with the student's own pickup_time and disturbs neither the
    manual order nor the surviving times nor the gate; unassignment preserves
    the same way. Manual preservation is a choice, not a degradation:
    stops_recalculated stays true and the degraded flag is never raised."""
    marker = uuid.uuid4().hex[:6]
    school = _make_school(client, admin_headers, marker)
    route = _make_route(client, admin_headers, marker, "morning", school["id"])
    kids = []
    kid_d = None
    try:
        for letter, pickup, lat, lng in (
            ("A", "06:30", -1.2800, 36.7900),
            ("B", "06:40", -1.2900, 36.8000),
            ("C", "06:50", -1.3100, 36.8300),
        ):
            kids.append(_make_student(
                client, admin_headers,
                _student_payload(marker, letter, pickup, lat, lng), [route["id"]],
            ))
        patch_google(monkeypatch, geo, order=[2, 0, 1])  # computed: C,A,B
        assert fleet_dao.regenerate_route_stops(db, route["id"]) is True
        db.commit()

        # Computed state (backward from the 07:00 default anchor, 3 legs):
        # C 06:45, A 06:50, B 06:55, gate 07:00.
        stops = _stops(_get_route(client, admin_headers, route["id"]))
        key_c, key_a, key_b = _ordered_group_keys(stops)

        response = _reorder(client, admin_headers, route["id"], [key_b, key_c, key_a])
        assert response.status_code == 200, response.text
        listed = _get_route(client, admin_headers, route["id"])
        assert listed["manual_stop_order"] is True
        assert listed["last_recalc_degraded"] is False
        # Times travelled with their stops — deliberately non-monotonic now.
        assert [(s["name"], s["scheduled_time"]) for s in _stops(listed)] == [
            (f"IT RO B Lane {marker}", "06:55"),
            (f"IT RO C Lane {marker}", "06:45"),
            (f"IT RO A Lane {marker}", "06:50"),
            (f"IT RO School {marker}", "07:00"),
        ]

        # Assignment through the degraded container: appends before the gate,
        # own pickup time, nothing else moves, no degradation signalled.
        created = client.post(
            "/api/students",
            json={**_student_payload(marker, "D", "06:35", -1.3200, 36.8400),
                  "route_ids": [route["id"]]},
            headers=admin_headers,
        )
        assert created.status_code == 200, created.text
        kid_d = created.json()
        assert kid_d["stops_recalculated"] is True  # manual preserve ≠ degraded

        listed = _get_route(client, admin_headers, route["id"])
        assert listed["manual_stop_order"] is True
        assert listed["last_recalc_degraded"] is False
        assert [(s["name"], s["scheduled_time"]) for s in _stops(listed)] == [
            (f"IT RO B Lane {marker}", "06:55"),
            (f"IT RO C Lane {marker}", "06:45"),
            (f"IT RO A Lane {marker}", "06:50"),
            (f"IT RO D Lane {marker}", "06:35"),  # appended, own pickup time
            (f"IT RO School {marker}", "07:00"),  # gate untouched
        ]

        # Unassignment preserves the manual order and surviving times too.
        removed = client.delete(
            f"/api/fleet/routes/{route['id']}/stops/{kid_d['id']}", headers=admin_headers
        )
        assert removed.status_code == 200, removed.text
        assert removed.json() == {"ok": True, "stops_recalculated": True}
        listed = _get_route(client, admin_headers, route["id"])
        assert listed["manual_stop_order"] is True
        assert listed["last_recalc_degraded"] is False
        assert [(s["name"], s["scheduled_time"]) for s in _stops(listed)] == [
            (f"IT RO B Lane {marker}", "06:55"),
            (f"IT RO C Lane {marker}", "06:45"),
            (f"IT RO A Lane {marker}", "06:50"),
            (f"IT RO School {marker}", "07:00"),
        ]
    finally:
        _cleanup(client, admin_headers, students=[*kids, kid_d], routes=(route,),
                 schools=(school,))


def test_recalculate_clears_manual_and_recomputes(
    client, admin_headers, db, fleet_dao, geo, monkeypatch
):
    """Recalculate is the explicit exit from manual mode: it clears the flag
    and reruns U6's path immediately — through the key-less container that is
    the observable degraded fallback ({ok, stops_recalculated: false}); at
    the DAO level with a fake google provider it recomputes order and times
    and clears the degraded flag."""
    marker = uuid.uuid4().hex[:6]
    school = _make_school(client, admin_headers, marker)
    route = _make_route(client, admin_headers, marker, "morning", school["id"])
    kids = []
    try:
        for letter, pickup, lat, lng in (
            ("A", "06:30", -1.2800, 36.7900),
            ("B", "06:40", -1.2900, 36.8000),
            ("C", "06:50", -1.3100, 36.8300),
        ):
            kids.append(_make_student(
                client, admin_headers,
                _student_payload(marker, letter, pickup, lat, lng), [route["id"]],
            ))
        key_a, key_b, key_c = _ordered_group_keys(
            _stops(_get_route(client, admin_headers, route["id"]))
        )
        assert _reorder(
            client, admin_headers, route["id"], [key_c, key_a, key_b]
        ).status_code == 200

        # Container recalculate (no key there): manual clears, the rebuild
        # falls back observably — never-computed route, so pickup order.
        response = client.post(
            f"/api/fleet/routes/{route['id']}/recalculate", headers=admin_headers
        )
        assert response.status_code == 200, response.text
        assert response.json() == {"ok": True, "stops_recalculated": False}
        listed = _get_route(client, admin_headers, route["id"])
        assert listed["manual_stop_order"] is False
        assert listed["last_recalc_degraded"] is True
        assert [s["name"] for s in _stops(listed)] == [
            f"IT RO A Lane {marker}", f"IT RO B Lane {marker}",
            f"IT RO C Lane {marker}", f"IT RO School {marker}",
        ]

        # Back to manual, then recalculate at the DAO level with google up:
        # optimizer order + anchored times land, both flags come back clean.
        assert _reorder(
            client, admin_headers, route["id"], [key_c, key_a, key_b]
        ).status_code == 200
        patch_google(monkeypatch, geo, order=[1, 0, 2])  # A,B,C -> B,A,C
        assert fleet_dao.recalculate_route_stops(db, route["id"]) is True
        db.commit()

        listed = _get_route(client, admin_headers, route["id"])
        assert listed["manual_stop_order"] is False
        assert listed["last_recalc_degraded"] is False
        assert [(s["name"], s["scheduled_time"]) for s in _stops(listed)] == [
            (f"IT RO B Lane {marker}", "06:45"),
            (f"IT RO A Lane {marker}", "06:50"),
            (f"IT RO C Lane {marker}", "06:55"),
            (f"IT RO School {marker}", "07:00"),
        ]
    finally:
        _cleanup(client, admin_headers, students=kids, routes=(route,), schools=(school,))


def test_manual_pickup_time_edit_writes_through_to_that_stop_only(
    client, admin_headers, db, fleet_dao, geo, monkeypatch
):
    """R13: in manual mode a pickup-time edit updates that student's own stop
    time in place — no re-sort (the new time would move it first under auto),
    no regeneration, every other stop and the gate untouched — while the
    student attribute itself still updates."""
    marker = uuid.uuid4().hex[:6]
    school = _make_school(client, admin_headers, marker)
    route = _make_route(client, admin_headers, marker, "morning", school["id"])
    kids = []
    try:
        for letter, pickup, lat, lng in (
            ("A", "06:30", -1.2800, 36.7900),
            ("B", "06:40", -1.2900, 36.8000),
            ("C", "06:50", -1.3100, 36.8300),
        ):
            kids.append(_make_student(
                client, admin_headers,
                _student_payload(marker, letter, pickup, lat, lng), [route["id"]],
            ))
        patch_google(monkeypatch, geo)  # computed (07:00 anchor, 3 legs): A 06:45, B 06:50, C 06:55, gate 07:00
        assert fleet_dao.regenerate_route_stops(db, route["id"]) is True
        db.commit()

        key_a, key_b, key_c = _ordered_group_keys(
            _stops(_get_route(client, admin_headers, route["id"]))
        )
        assert _reorder(
            client, admin_headers, route["id"], [key_c, key_a, key_b]
        ).status_code == 200

        # Edit A's pickup time through the container: 05:50 would sort first
        # under any re-sort — the manual order must not move.
        response = client.put(
            f"/api/fleet/routes/{route['id']}/stops/{kids[0]['id']}",
            json={"pickup_time": "05:50"},
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text
        # Manual write-through touches no auto rebuild: the threaded signal
        # stays true (set_student_pickup_time only reports a degraded pass).
        assert response.json() == {"ok": True, "stops_recalculated": True}

        listed = _get_route(client, admin_headers, route["id"])
        assert listed["manual_stop_order"] is True
        assert listed["last_recalc_degraded"] is False  # nothing regenerated
        assert [(s["name"], s["scheduled_time"]) for s in _stops(listed)] == [
            (f"IT RO C Lane {marker}", "06:55"),
            (f"IT RO A Lane {marker}", "05:50"),  # written through in place
            (f"IT RO B Lane {marker}", "06:50"),
            (f"IT RO School {marker}", "07:00"),  # gate untouched
        ]

        students = client.get("/api/students", headers=admin_headers).json()
        me = next(s for s in students if s["id"] == kids[0]["id"])
        assert me["pickup_time"] == "05:50"  # the student attribute updated
    finally:
        _cleanup(client, admin_headers, students=kids, routes=(route,), schools=(school,))


def test_concurrent_reorder_and_assignment_serialize_on_the_route_lock(
    client, admin_headers, db, fleet_dao, geo, monkeypatch
):
    """The reorder transaction and the assignment's regeneration take the same
    route-row lock, both ways: an uncommitted reorder blocks the assignment,
    and an in-flight assignment blocks the reorder — no interleaving, no lost
    update. Nothing here commits; the lock assertions leave no state."""
    import psycopg
    from psycopg.rows import dict_row

    from app.dao.student_live_dao import _sync_routes

    marker = uuid.uuid4().hex[:6]
    school = _make_school(client, admin_headers, marker)
    route = _make_route(client, admin_headers, marker, "morning", school["id"])
    kid = loose = None
    try:
        kid = _make_student(
            client, admin_headers,
            _student_payload(marker, "A", "06:30", -1.2800, 36.7900), [route["id"]],
        )
        loose = _make_student(
            client, admin_headers,
            _student_payload(marker, "B", "06:40", -1.2900, 36.8000), [],
        )
        patch_offline(monkeypatch, geo)
        keys = _ordered_group_keys(_stops(_get_route(client, admin_headers, route["id"])))

        # Reorder holds the route row: the assignment path cannot interleave.
        fleet_dao.reorder_route_stops(db, route["id"], keys)  # uncommitted
        with psycopg.connect(DB_URL, row_factory=dict_row) as other:
            other.execute("set lock_timeout = '400ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                _sync_routes(other, loose["id"], [route["id"]])
            other.rollback()
        db.rollback()

        # And the reverse: an in-flight assignment blocks the reorder.
        with psycopg.connect(DB_URL, row_factory=dict_row) as other:
            assert _sync_routes(other, loose["id"], [route["id"]]) is False  # holds lock
            db.execute("set lock_timeout = '400ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                fleet_dao.reorder_route_stops(db, route["id"], keys)
            db.rollback()
            other.rollback()

        # Released: the reorder goes straight through (and is discarded).
        fleet_dao.reorder_route_stops(db, route["id"], keys)
        db.rollback()
        assert _get_route(client, admin_headers, route["id"])["manual_stop_order"] is False
    finally:
        _cleanup(client, admin_headers, students=(kid, loose), routes=(route,),
                 schools=(school,))


def test_custom_to_auto_handover_lands_in_auto_and_recalculates_immediately(
    client, admin_headers, db, geo, monkeypatch
):
    """R18 x U7: assigning a student to a planner-saved route hands ordering
    back to auto — custom_stops clears, manual_stop_order is (and stays)
    false — and the regeneration runs immediately in the same transaction:
    with google up the rebuilt stops carry computed times, no degradation."""
    from app.dao.student_live_dao import _sync_routes

    marker = uuid.uuid4().hex[:6]
    school = _make_school(client, admin_headers, marker)
    created = client.post(
        "/api/fleet/routes",
        json={
            "name": f"IT RO Handover {marker}",
            "type": "morning",
            "school_id": school["id"],
            "stops": [
                {"label": f"IT RO Corner {marker}", "lat": -1.3050, "lng": 36.8100,
                 "pickup_time": "07:05"},
                {"label": f"IT RO Gate {marker}", "lat": -1.3000, "lng": 36.8200,
                 "is_school": True},
            ],
            "polyline": "itfakepoly",
            "total_distance_m": 5000,
            "total_duration_s": 900,
        },
        headers=admin_headers,
    )
    assert created.status_code == 200, created.text
    route = created.json()
    kid = None
    try:
        assert route["custom_stops"] is True
        kid = _make_student(
            client, admin_headers,
            _student_payload(marker, "A", "06:30", -1.2800, 36.7900), [],
        )
        patch_google(monkeypatch, geo)
        assert _sync_routes(db, kid["id"], [route["id"]]) is True
        db.commit()

        listed = _get_route(client, admin_headers, route["id"])
        assert listed["custom_stops"] is False
        assert listed["manual_stop_order"] is False  # auto, not manual
        assert listed["last_recalc_degraded"] is False
        assert listed["polyline"] is None
        stops = _stops(listed)
        assert [s["is_school_gate"] for s in stops] == [False, True]
        assert stops[0]["student_id"] == kid["id"]  # student stops, not planner rows
        # Computed backward from the 07:00 default anchor (one group, one leg):
        # departure 06:55, gate arrival 07:00.
        assert [s["scheduled_time"] for s in stops] == ["06:55", "07:00"]  # computed now
    finally:
        _cleanup(client, admin_headers, students=(kid,), routes=(route,), schools=(school,))


def test_started_run_keeps_its_snapshot_through_a_manual_reorder(client, admin_headers):
    """R12 (manual half): a run started before the admin reorders operates on
    its own run_stops snapshot — the reorder rewrites the live stops only."""
    import random

    marker = uuid.uuid4().hex[:6]
    driver = None
    for _ in range(5):
        pin = str(random.randint(100000, 999999))
        response = client.post(
            "/api/accounts/drivers",
            json={"full_name": f"IT RO Driver M {marker}",
                  "email": f"it-ro-driver-m-{marker}@test.local",
                  "password": "test1234.", "phone": "+254711000043", "pin": pin},
            headers=admin_headers,
        )
        if response.status_code == 200:
            driver = {**response.json(), "pin": pin}
            break
    assert driver, "could not create throwaway driver"

    school = _make_school(client, admin_headers, marker)
    bus = client.post(
        "/api/fleet/buses",
        json={"name": f"IT RO Bus M {marker}", "driver_id": driver["id"]},
        headers=admin_headers,
    ).json()
    route = _make_route(client, admin_headers, marker, "morning", school["id"], bus_id=bus["id"])
    kids = []
    run_id = None
    driver_headers = None
    try:
        for letter, pickup, lat, lng in (
            ("A", "06:30", -1.2800, 36.7900),
            ("B", "06:40", -1.2900, 36.8000),
        ):
            kids.append(_make_student(
                client, admin_headers,
                _student_payload(marker, letter, pickup, lat, lng), [route["id"]],
            ))

        pin_login = client.post("/api/auth/pin-login", json={"pin": driver["pin"]})
        assert pin_login.status_code == 200, pin_login.text
        driver_headers = {"Authorization": f"Bearer {pin_login.json()['token']}"}

        started = client.post(
            "/api/runs/driver/start", json={"route_id": route["id"]}, headers=driver_headers
        )
        assert started.status_code == 200, started.text
        run_id = started.json()["id"]

        def run_snapshot() -> list[tuple]:
            context = client.get("/api/runs/driver/context", headers=driver_headers).json()
            return [
                (s["stop_order"], s["name"], s["scheduled_time"])
                for s in context["run_stops"]
            ]

        before = run_snapshot()
        assert before, "started run must carry a run_stops snapshot"

        key_a, key_b = _ordered_group_keys(
            _stops(_get_route(client, admin_headers, route["id"]))
        )
        assert _reorder(client, admin_headers, route["id"], [key_b, key_a]).status_code == 200

        live = _stops(_get_route(client, admin_headers, route["id"]))
        assert live[0]["name"] == f"IT RO B Lane {marker}"  # live order changed
        assert run_snapshot() == before  # the run's snapshot did not (R12)
    finally:
        if run_id and driver_headers:
            client.post("/api/runs/driver/end", json={"run_id": run_id}, headers=driver_headers)
            client.delete(f"/api/runs/{run_id}", headers=admin_headers)
        _cleanup(
            client, admin_headers,
            students=kids, routes=(route,), schools=(school,), buses=(bus,), drivers=(driver,),
        )


def test_ordering_endpoints_require_admin(client):
    """Both U7 verbs sit under the admin-only dependency: a parent token is
    rejected before any route lookup runs."""
    parent_headers = login(client, "and7005@gmail.com", "Test1234")
    bogus = uuid.uuid4()
    response = client.put(
        f"/api/fleet/routes/{bogus}/stop-order", json={"order": []}, headers=parent_headers
    )
    assert response.status_code == 403, response.text
    response = client.post(f"/api/fleet/routes/{bogus}/recalculate", headers=parent_headers)
    assert response.status_code == 403, response.text
