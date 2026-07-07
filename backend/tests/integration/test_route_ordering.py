"""Geometry recalculation in auto mode (ops-refinement U6: R9, R10, R12; AE3
auto half, AE6).

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


def _make_route(client, admin_headers, marker: str, route_type: str, school_id, bus_id=None) -> dict:
    response = client.post(
        "/api/fleet/routes",
        json={"name": f"IT RO {route_type} {marker}", "type": route_type,
              "school_id": school_id, "bus_id": bus_id},
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
    """Covers AE3 (auto half): the optimizer order lands with per-group times
    from cumulative leg ETAs anchored on the earliest assigned pickup_time;
    the gate row carries the computed school arrival; the flag clears. A
    second recalculation keeps the same anchor (read from the students before
    the stop delete — never from the deleted stops or the default), and a
    pickup-time edit re-anchors the morning departure."""
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
        assert _get_route(client, admin_headers, route["id"])["last_recalc_degraded"] is True

        patch_google(monkeypatch, geo, order=[2, 0, 1])  # pickup order A,B,C -> C,A,B
        assert fleet_dao.regenerate_route_stops(db, route["id"]) is True
        db.commit()

        listed = _get_route(client, admin_headers, route["id"])
        assert listed["last_recalc_degraded"] is False  # google success clears (R10)
        stops = _stops(listed)
        assert [s["stop_order"] for s in stops] == [1, 2, 3, 4]
        assert [s["name"] for s in stops] == [
            f"IT RO C Lane {marker}", f"IT RO A Lane {marker}",
            f"IT RO B Lane {marker}", f"IT RO School {marker}",
        ]
        # Anchor = earliest assigned pickup (06:30), +5 min per fake leg; the
        # gate gets the computed school arrival.
        assert [s["scheduled_time"] for s in stops] == ["06:30", "06:35", "06:40", "06:45"]
        assert stops[-1]["is_school_gate"] is True

        # Second recalc: same anchor, same times — not the 07:00 default.
        assert fleet_dao.regenerate_route_stops(db, route["id"]) is True
        db.commit()
        stops = _stops(_get_route(client, admin_headers, route["id"]))
        assert [s["scheduled_time"] for s in stops] == ["06:30", "06:35", "06:40", "06:45"]

        # Pickup-time edit re-anchors the morning departure (its only effect
        # while geometry owns the times).
        db.execute(
            "update live_students set pickup_time = '06:00' where id = %s", (kids[0]["id"],)
        )
        assert fleet_dao.regenerate_route_stops(db, route["id"]) is True
        db.commit()
        stops = _stops(_get_route(client, admin_headers, route["id"]))
        assert [s["scheduled_time"] for s in stops] == ["06:00", "06:05", "06:10", "06:15"]
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
        assert [s["scheduled_time"] for s in stops[:3]] == ["06:30", "06:35", "06:40"]
        assert stops[3]["student_id"] == kid_d["id"]
        assert stops[3]["scheduled_time"] == "06:35"
        assert stops[3]["lat"] is None  # coordinate-less, still a named stop
        assert stops[4]["is_school_gate"] is True
        assert stops[4]["scheduled_time"] == "06:45"

        # Removing the new student through the container preserves again.
        removed = client.delete(
            f"/api/fleet/routes/{route['id']}/stops/{kid_d['id']}", headers=admin_headers
        )
        assert removed.status_code == 200, removed.text
        assert removed.json() == {"ok": True, "stops_recalculated": False}
        stops = _stops(_get_route(client, admin_headers, route["id"]))
        assert [s["scheduled_time"] for s in stops] == ["06:30", "06:35", "06:40", "06:45"]
        assert [s["name"] for s in stops[:3]] == [
            f"IT RO C Lane {marker}", f"IT RO A Lane {marker}", f"IT RO B Lane {marker}",
        ]

        # The NEXT google success clears the durable flag (R10).
        assert fleet_dao.regenerate_route_stops(db, route["id"]) is True
        db.commit()
        assert _get_route(client, admin_headers, route["id"])["last_recalc_degraded"] is False
    finally:
        _cleanup(client, admin_headers, students=[*kids, kid_d], routes=(route,), schools=(school,))


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
