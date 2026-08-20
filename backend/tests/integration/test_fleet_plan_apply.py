"""Fleet-plan apply pipeline (U6): one gated, provider-free transaction that
replaces the school's live routes from the draft document, preserves the
displaced live state as the one-level 'previous' plan, notifies affected
families through the shared diff, and refreshes geometry post-commit.

Run with the stack up (scripts/start-local.sh):

    RUN_INTEGRATION=1 ../.venv/bin/python -m pytest tests/integration/test_fleet_plan_apply.py -q

Covers R2/R4/R8/R11/R14/R15/R21/R22/R23/R24 + AE5: gate order (basis drift 409
until confirmed, fleet drift 409 naming the bus, unacknowledged unplaceable
422 naming them); apply mid-run leaves run_stops untouched and the run
closable; surplus routes retired with route-unassigned to their families;
multi-trip chains survive untouched with zero notifications; post-apply
invariants (links match the document, plan_ordered set with custom/manual
clear, freezes cleared, document times materialized and frozen, bus_id /
pickup_time re-derived with PM-only NULL, ridership_pattern written); status
lifecycle applied=1/previous=1/draft=0 with as-evolved capture and one-level
deletion; the notification matrix (first apply once per family, +6 min
notifies, +4 min silent, bus swap at identical times notifies,
still-unplaceable silent, narrow → leg-removed + baseline deleted, re-widen →
notifies again); atomicity; 403.

Conventions: 'IT '-prefixed entities, finally-cleanup, parents linked via
parent_email at student creation so notifications are observable through the
parent feed API, sanctioned psycopg peeks only for tables the API deliberately
does not expose (plan statuses, baselines, audit rows, run_stops snapshots).

Atomicity forcing mechanism (documented per the plan): the apply transaction's
LAST act is the unknown-acknowledgment gate — an acknowledgment naming a
(student, leg) the document does not list unplaceable 409s only after every
write (routes, links, stops, audit, feed rows, baselines, status flips) has
already happened inside the transaction, so asserting a clean world after the
409 proves the whole transaction rolled back as one atom.

Nairobi-window note: the apply path contains NO date-scoped SQL predicate
(nothing filters on a date), so the plan's conditional 00:30-Nairobi staging
test has no subject — stated here rather than tested.
"""

import os
import random
import time
import uuid

import httpx
import psycopg
import pytest
from psycopg.rows import dict_row

from conftest import DSN, purge_run

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="needs the local stack; set RUN_INTEGRATION=1",
)

BASE = os.environ.get("INTEGRATION_API_URL", "http://localhost:9001")

ADMIN = {"email": "admin@test.com", "password": "test1234."}
PARENT = {"email": "and7005@gmail.com", "password": "Test1234"}

SCHOOL_LAT, SCHOOL_LNG = -1.3000, 36.8000
EAST_HOMES = [(-1.290, 36.820), (-1.292, 36.824), (-1.288, 36.818), (-1.294, 36.822)]
WEST_HOMES = [(-1.310, 36.780), (-1.312, 36.776), (-1.308, 36.782), (-1.314, 36.778)]
DEPOT_EAST = (-1.285, 36.830)
DEPOT_WEST = (-1.320, 36.770)

MORNING, AFTERNOON = "morning", "afternoon"
PLAN_TYPES = ("route-updated", "route-unassigned")


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE, timeout=30) as c:
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


@pytest.fixture(scope="module")
def parent_headers(client):
    return login(client, PARENT["email"], PARENT["password"])


# --- builders -----------------------------------------------------------------

def _make_school(client, headers, marker: str, **overrides) -> dict:
    payload = {
        "name": f"IT ApplySchool {marker}",
        "lat": SCHOOL_LAT, "lng": SCHOOL_LNG,
        "morning_bell": "07:00", "afternoon_bell": "15:30",
    }
    payload.update(overrides)
    created = client.post("/api/fleet/schools", json=payload, headers=headers)
    assert created.status_code == 200, created.text
    return created.json()


def _make_bus(client, headers, name: str, *, capacity: int = 45,
              depot: tuple[float, float] | None = None,
              driver_id: str | None = None) -> dict:
    payload: dict = {"name": name, "capacity": capacity}
    if depot is not None:
        payload["depot_lat"], payload["depot_lng"] = depot
    if driver_id is not None:
        payload["driver_id"] = driver_id
    created = client.post("/api/fleet/buses", json=payload, headers=headers)
    assert created.status_code == 200, created.text
    return created.json()


def _student_payload(marker: str, i: int, school_id: str,
                     home: tuple[float, float] | None,
                     email: str | None = None,
                     route_ids: list[str] | None = None) -> dict:
    payload = {
        "name": f"IT Apply Kid {marker} {i}",
        "parent_name": f"IT Apply Parent {marker} {i}",
        "parent_phone": f"+2547130001{i:02d}",
        "parent_email": email or f"it-fpa-{marker}-{i}@test.local",
        "school_id": school_id,
        "route_ids": route_ids or [],
    }
    if home is not None:
        payload["home_lat"], payload["home_lng"] = home
        payload["home_address"] = f"IT Apply Home {marker} {i}"
    return payload


def _make_student(client, headers, marker: str, i: int, school_id: str,
                  home: tuple[float, float] | None, email: str | None = None,
                  route_ids: list[str] | None = None) -> dict:
    created = client.post(
        "/api/students",
        json=_student_payload(marker, i, school_id, home, email, route_ids),
        headers=headers,
    )
    assert created.status_code == 200, created.text
    return created.json()


def signup_parent(client, marker: str, tag: str) -> dict:
    email = f"it-fpa-{tag}-{marker}@test.local"
    response = client.post(
        "/api/auth/signup",
        json={"email": email, "password": "ParentPass1!",
              "full_name": f"IT FPA {tag} {marker}", "role": "parent"},
    )
    assert response.status_code == 200, response.text
    return {
        "id": response.json()["user"]["id"],
        "email": email,
        "headers": login(client, email, "ParentPass1!"),
    }


def _create_driver(client, admin_headers, marker: str) -> dict:
    for _ in range(5):
        pin = str(random.randint(100000, 999999))
        response = client.post(
            "/api/accounts/drivers",
            json={"full_name": f"IT FPA Driver {marker}",
                  "email": f"it-fpa-driver-{marker}@test.local",
                  "password": "test1234.", "phone": "+254711000091", "pin": pin},
            headers=admin_headers,
        )
        if response.status_code == 200:
            return {**response.json(), "pin": pin}
    pytest.fail(f"could not create throwaway driver: {response.text}")


def _confirm(client, headers, school_id: str, bus_ids: list[str]) -> dict:
    confirmed = client.post(
        "/api/fleet-plans/confirm-fleet",
        json={"school_id": school_id, "bus_ids": bus_ids},
        headers=headers,
    )
    assert confirmed.status_code == 200, confirmed.text
    return confirmed.json()


def _draft(client, headers, school_id: str, **kw) -> dict:
    drafted = client.post(
        "/api/fleet-plans/draft", json={"school_id": school_id, **kw}, headers=headers
    )
    assert drafted.status_code == 200, drafted.text
    return drafted.json()


def _review(client, headers, school_id: str) -> dict:
    got = client.get(
        "/api/fleet-plans/review", params={"school_id": school_id}, headers=headers
    )
    assert got.status_code == 200, got.text
    return got.json()


def _edit(client, headers, plan_id: str, verb: str, payload: dict) -> httpx.Response:
    return client.post(f"/api/fleet-plans/{plan_id}/{verb}", json=payload, headers=headers)


def _apply(client, headers, plan_id: str, *, confirmations=None,
           acknowledgments=None) -> httpx.Response:
    return client.post(
        f"/api/fleet-plans/{plan_id}/apply",
        json={"confirmations": confirmations or [],
              "acknowledgments": acknowledgments or []},
        headers=headers,
    )


def _acks_for(review: dict) -> list[dict]:
    """Acknowledgments covering every unplaceable (student, leg) in the draft."""
    return [
        {"student_id": u["student_id"], "leg": leg}
        for leg in (MORNING, AFTERNOON)
        for u in review["unplaceable"][leg]
    ]


# --- readers -------------------------------------------------------------------

def _placement(review: dict, student_id: str, leg: str):
    for bus in review["buses"]:
        for stop in bus["legs"][leg]["stops"]:
            if any(s["id"] == student_id for s in stop["students"]):
                return bus, stop
    return None, None


def _bus_of(review: dict, student_id: str, leg: str) -> str | None:
    bus, _ = _placement(review, student_id, leg)
    return bus["bus_id"] if bus else None


def _ride_map(review: dict) -> dict:
    return {(r["student_id"], r["leg"]): r for r in review["ride_times"]}


def _school_routes(client, headers, school_id: str) -> list[dict]:
    routes = client.get("/api/fleet/routes", headers=headers).json()
    return [r for r in routes if r["school_id"] == school_id]


def _plan_feed(client, headers) -> list[dict]:
    rows = client.get("/api/push/notifications", headers=headers).json()
    return [r for r in rows if r["type"] in PLAN_TYPES]


def _wait_until(predicate, timeout: float = 10.0, interval: float = 0.25) -> bool:
    """Poll for a background-task effect (the manual-edit suite's pattern)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


def _minutes(hhmm: str) -> int:
    h, m = (int(x) for x in hhmm.split(":"))
    return h * 60 + m


def _shift(hhmm: str, minutes: int) -> str:
    total = (_minutes(hhmm) + minutes) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


# --- sanctioned psycopg peeks ----------------------------------------------------
# Only for tables the API deliberately does not expose: plan rows and statuses,
# communicated baselines, the admin audit, and run_stops snapshots.

def _pg_all(sql: str, params=()) -> list[dict]:
    with psycopg.connect(DSN, autocommit=True, row_factory=dict_row) as pg:
        return pg.execute(sql, params).fetchall()


def _pg_one(sql: str, params=()) -> dict | None:
    rows = _pg_all(sql, params)
    return rows[0] if rows else None


def _pg_exec(sql: str, params=()) -> None:
    with psycopg.connect(DSN, autocommit=True) as pg:
        pg.execute(sql, params)


def _plan_statuses(school_id: str) -> dict[str, int]:
    counts = {"draft": 0, "applied": 0, "previous": 0}
    for row in _pg_all(
        "select status, count(*) as n from live_fleet_plans "
        "where school_id = %s group by status", (school_id,)
    ):
        if row["status"] in counts:
            counts[row["status"]] = int(row["n"])
    return counts


def _plan_status(plan_id: str) -> str | None:
    row = _pg_one("select status from live_fleet_plans where id = %s", (plan_id,))
    return row["status"] if row else None


def _audit_rows(school_id: str) -> list[dict]:
    return _pg_all(
        "select actor_email, action, detail from live_admin_audit "
        "where school_id = %s and action = 'plan-applied' order by created_at asc",
        (school_id,),
    )


def _baselines(student_id: str) -> dict[str, dict]:
    return {
        r["route_type"]: r
        for r in _pg_all(
            "select route_type, stop_name, scheduled_time, bus_id "
            "from live_communicated_stops where student_id = %s",
            (student_id,),
        )
    }


def _links(student_id: str) -> dict[str, str]:
    """leg -> bus_id for the student's live route links."""
    return {
        r["route_type"]: str(r["bus_id"]) if r["bus_id"] else None
        for r in _pg_all(
            "select sr.route_type, r.bus_id from live_student_routes sr "
            "join live_routes r on r.id = sr.route_id where sr.student_id = %s",
            (student_id,),
        )
    }


def _run_stop_rows(run_id: str) -> list[tuple]:
    return [
        (r["stop_order"], r["name"], r["scheduled_time"], r["lat"], r["lng"],
         r["is_school_gate"], str(r["student_id"]) if r["student_id"] else None)
        for r in _pg_all(
            "select stop_order, name, scheduled_time, lat, lng, is_school_gate, "
            "student_id from run_stops where run_id = %s "
            "order by stop_order asc, name asc",
            (run_id,),
        )
    ]


# --- fixture plumbing ------------------------------------------------------------

def _build_plan(client, headers, marker: str, *, buses, homes, seed=0,
                emails=None) -> dict:
    school = _make_school(client, headers, marker)
    bus_rows = [
        _make_bus(client, headers, f"IT ApplyBus {tag} {marker}",
                  capacity=capacity, depot=depot)
        for tag, capacity, depot in buses
    ]
    students = [
        _make_student(client, headers, marker, i, school["id"], home,
                      email=(emails[i] if emails else None))
        for i, home in enumerate(homes)
    ]
    _confirm(client, headers, school["id"], [b["id"] for b in bus_rows])
    plan = _draft(client, headers, school["id"], seed=seed)
    return {"school": school, "buses": bus_rows, "students": students, "plan": plan}


def _teardown(client, headers, fx: dict) -> None:
    for s in fx.get("students", []):
        client.delete(f"/api/students/{s['id']}", headers=headers)
    for b in fx.get("buses", []):
        client.delete(f"/api/fleet/buses/{b['id']}", headers=headers)
    # Deleting the school cascades its plan rows (011 FK); audit rows keep
    # their denormalized identity with school_id SET NULL (append-only table).
    client.delete(f"/api/fleet/schools/{fx['school']['id']}", headers=headers)


# --- gates (R22 / R23 / fleet drift) ----------------------------------------------

def test_apply_gates_unplaceable_enrolment_capacity_departure(client, admin_headers):
    """Gate order and copy: an unacknowledged unplaceable child 422s naming
    them; a student enrolled after generation 409s until confirmed; a bus
    capacity lowered below its drafted load 409s naming the bus; a departure
    409s until confirmed and the confirmed apply excludes the departed child.
    Every refused apply leaves zero side effects."""
    marker = uuid.uuid4().hex[:6]
    fx = _build_plan(
        client, admin_headers, marker,
        buses=[("A", 6, None)],
        homes=[EAST_HOMES[0], EAST_HOMES[1], EAST_HOMES[2], None],  # last: coordless
    )
    school_id = fx["school"]["id"]
    plan_id = fx["plan"]["id"]
    bus = fx["buses"][0]
    coordless = fx["students"][3]
    enrolled = None
    try:
        review = _review(client, admin_headers, school_id)
        assert {u["student_id"] for u in review["unplaceable"][MORNING]} == {coordless["id"]}
        acks = _acks_for(review)

        def assert_no_side_effects():
            assert _plan_status(plan_id) == "draft"
            assert _audit_rows(school_id) == []
            assert _school_routes(client, admin_headers, school_id) == []
            assert _baselines(fx["students"][0]["id"]) == {}

        # (c) unacknowledged unplaceable -> 422 naming them.
        refused = _apply(client, admin_headers, plan_id)
        assert refused.status_code == 422, refused.text
        assert coordless["name"] in refused.json()["detail"]
        assert_no_side_effects()

        # (a) a student enrolled after generation -> 409 until confirmed.
        enrolled = _make_student(client, admin_headers, marker, 9, school_id, WEST_HOMES[0])
        refused = _apply(client, admin_headers, plan_id, acknowledgments=acks)
        assert refused.status_code == 409, refused.text
        assert enrolled["name"] in refused.json()["detail"]
        assert "enrolled" in refused.json()["detail"]
        assert_no_side_effects()

        # (b) capacity lowered below the drafted load -> 409 naming the bus.
        lowered = client.put(
            f"/api/fleet/buses/{bus['id']}",
            json={"name": bus["name"], "capacity": 1},
            headers=admin_headers,
        )
        assert lowered.status_code == 200, lowered.text
        confirmations = [{"student_id": enrolled["id"], "kind": "enrolled"}]
        refused = _apply(client, admin_headers, plan_id,
                         confirmations=confirmations, acknowledgments=acks)
        assert refused.status_code == 409, refused.text
        assert bus["name"] in refused.json()["detail"]
        assert_no_side_effects()
        restored = client.put(
            f"/api/fleet/buses/{bus['id']}",
            json={"name": bus["name"], "capacity": 6},
            headers=admin_headers,
        )
        assert restored.status_code == 200, restored.text

        # (a) a departure since generation -> 409 until confirmed.
        departed = fx["students"][2]
        assert client.delete(
            f"/api/students/{departed['id']}", headers=admin_headers
        ).status_code == 200
        refused = _apply(client, admin_headers, plan_id,
                         confirmations=confirmations, acknowledgments=acks)
        assert refused.status_code == 409, refused.text
        assert departed["name"] in refused.json()["detail"]
        assert "departed" in refused.json()["detail"]
        assert_no_side_effects()

        # Fully confirmed apply succeeds; the departed child is excluded from
        # every write (no FK explosion, no stop row, no link).
        confirmations.append({"student_id": departed["id"], "kind": "departed"})
        applied = _apply(client, admin_headers, plan_id,
                         confirmations=confirmations, acknowledgments=acks)
        assert applied.status_code == 200, applied.text
        body = applied.json()
        assert body["already_applied"] is False
        assert body["routes_written"] == 2  # one bus, both legs
        assert _plan_status(plan_id) == "applied"
        routes = _school_routes(client, admin_headers, school_id)
        placed_ids = {
            s["student_id"] for r in routes for s in r["route_stops"]
            if s["student_id"] is not None
        }
        assert placed_ids == {fx["students"][0]["id"], fx["students"][1]["id"]}
        assert len(_audit_rows(school_id)) == 1
    finally:
        if enrolled:
            client.delete(f"/api/students/{enrolled['id']}", headers=admin_headers)
        _teardown(client, admin_headers, fx)


# --- confirmed-enrolled severing (link-without-stop guard) --------------------------

def test_enrolled_after_draft_manual_placement_is_severed_cleanly(client, admin_headers):
    """A child enrolled AFTER drafting, manually placed on a live route the
    document reuses, then confirmed 'enrolled' at the gate: the apply must
    leave them cleanly unassigned — links severed on every written leg, no
    stop rows, bus_id/pickup_time NULL (ridership_pattern kept) — never a
    link-without-stop phantom membership. Their family hears route-unassigned
    via the leg-removed path (the manual placement silently seeded their
    baseline), and the baseline is deleted with the send."""
    marker = uuid.uuid4().hex[:6]
    pe = signup_parent(client, marker, "pe")
    fx = _build_plan(
        client, admin_headers, marker,
        buses=[("A", 8, DEPOT_EAST)],
        homes=[EAST_HOMES[0], EAST_HOMES[1]],
    )
    school_id = fx["school"]["id"]
    plan_id = fx["plan"]["id"]
    bus = fx["buses"][0]
    route_m = enrolled = None
    try:
        # A live morning route on the drafted bus — the (bus, type) pair the
        # document will REUSE in place at apply.
        created = client.post(
            "/api/fleet/routes",
            json={"name": f"IT Apply Reused {marker}", "type": MORNING,
                  "bus_id": bus["id"], "school_id": school_id},
            headers=admin_headers,
        )
        assert created.status_code == 200, created.text
        route_m = created.json()

        # Enrolled after drafting, manually placed on that route (a roster
        # path: membership + stop row + silently seeded baseline).
        enrolled = _make_student(client, admin_headers, marker, 9, school_id,
                                 WEST_HOMES[0], email=pe["email"],
                                 route_ids=[route_m["id"]])
        assert _links(enrolled["id"]) == {MORNING: bus["id"]}
        assert _wait_until(lambda: MORNING in _baselines(enrolled["id"])), (
            "the roster path should silently seed the manual placement's baseline"
        )
        pattern_before = next(
            s["ridership_pattern"]
            for s in client.get("/api/students", headers=admin_headers).json()
            if s["id"] == enrolled["id"]
        )

        review = _review(client, admin_headers, school_id)
        assert _acks_for(review) == []  # both drafted children placed
        applied = _apply(
            client, admin_headers, plan_id,
            confirmations=[{"student_id": enrolled["id"], "kind": "enrolled"}],
        )
        assert applied.status_code == 200, applied.text

        # The reused route survived in place — and carries NO trace of the
        # confirmed-enrolled child: no link, no stop row.
        routes_after = _school_routes(client, admin_headers, school_id)
        assert route_m["id"] in {r["id"] for r in routes_after}
        assert _links(enrolled["id"]) == {}
        assert not any(
            s["student_id"] == enrolled["id"]
            for r in routes_after for s in r["route_stops"]
        )

        # Denormalized attributes cleared, pattern kept: cleanly unassigned.
        live = {s["id"]: s for s in client.get("/api/students", headers=admin_headers).json()}
        assert live[enrolled["id"]]["bus_id"] is None
        assert live[enrolled["id"]]["pickup_time"] is None
        assert live[enrolled["id"]]["ridership_pattern"] == pattern_before

        # The family was told (leg-removed -> route-unassigned) and the
        # consumed baseline is gone.
        pe_rows = _plan_feed(client, pe["headers"])
        assert [r["type"] for r in pe_rows] == ["route-unassigned"]
        assert pe_rows[0]["student_id"] == enrolled["id"]
        assert _baselines(enrolled["id"]) == {}
    finally:
        if enrolled:
            client.delete(f"/api/students/{enrolled['id']}", headers=admin_headers)
        _teardown(client, admin_headers, fx)
        client.delete(f"/api/accounts/parents/{pe['id']}", headers=admin_headers)


# --- multi-trip drift gate ----------------------------------------------------------

def test_bus_entering_multi_trip_chain_after_draft_blocks_apply(client, admin_headers):
    """A drafted bus that gained a trip_index>=2 route AFTER drafting is fleet
    drift: apply 409s naming the bus in the fleet-drift vocabulary instead of
    reaching the reconcile (whose (bus, type, trip_index) unique it would
    trip). The refused apply leaves zero side effects; removing the chain
    lets the same apply succeed."""
    marker = uuid.uuid4().hex[:6]
    fx = _build_plan(
        client, admin_headers, marker,
        buses=[("A", 8, DEPOT_EAST)],
        homes=[EAST_HOMES[0], EAST_HOMES[1]],
    )
    school_id = fx["school"]["id"]
    plan_id = fx["plan"]["id"]
    bus = fx["buses"][0]
    trip2 = None
    try:
        created = client.post(
            "/api/fleet/routes",
            json={"name": f"IT Apply Trip2 {marker}", "type": MORNING,
                  "bus_id": bus["id"], "school_id": school_id, "trip_index": 2},
            headers=admin_headers,
        )
        assert created.status_code == 200, created.text
        trip2 = created.json()

        refused = _apply(client, admin_headers, plan_id)
        assert refused.status_code == 409, refused.text
        detail = refused.json()["detail"]
        assert bus["name"] in detail
        assert "multi-trip chain" in detail and "re-draft" in detail
        assert _plan_status(plan_id) == "draft"
        assert _audit_rows(school_id) == []
        assert not any(
            r["plan_ordered"]
            for r in _school_routes(client, admin_headers, school_id)
        )

        # Un-drift the fleet: the same apply then goes through.
        assert client.delete(
            f"/api/fleet/routes/{trip2['id']}", headers=admin_headers
        ).status_code == 200
        trip2 = None
        applied = _apply(client, admin_headers, plan_id)
        assert applied.status_code == 200, applied.text
        assert _plan_status(plan_id) == "applied"
    finally:
        if trip2:
            client.delete(f"/api/fleet/routes/{trip2['id']}", headers=admin_headers)
        _teardown(client, admin_headers, fx)


# --- collapsed-stop privacy (per-child bodies + baselines) ---------------------------

def test_collapsed_stop_bodies_never_leak_sibling_names(client, admin_headers):
    """Two families collapsed onto ONE document stop (same coordinates): each
    recipient's body names only THEIR OWN child's stop (the address-label
    convention) — family A's text never contains family B's child's name —
    and each child's communicated baseline carries their own label, never the
    document's sibling-joined stop name."""
    marker = uuid.uuid4().hex[:6]
    pa = signup_parent(client, marker, "na")
    pb = signup_parent(client, marker, "nb")
    fx = _build_plan(
        client, admin_headers, marker,
        buses=[("A", 8, DEPOT_EAST)],
        homes=[EAST_HOMES[0], EAST_HOMES[0]],  # identical coords -> one stop
        emails=[pa["email"], pb["email"]],
    )
    school_id = fx["school"]["id"]
    kid_a, kid_b = fx["students"]
    label_a = f"IT Apply Home {marker} 0"
    label_b = f"IT Apply Home {marker} 1"
    try:
        review = _review(client, admin_headers, school_id)
        _bus, stop = _placement(review, kid_a["id"], MORNING)
        assert stop is not None
        assert {s["id"] for s in stop["students"]} == {kid_a["id"], kid_b["id"]}, (
            "same-coordinate homes should collapse into one document stop"
        )

        applied = _apply(client, admin_headers, fx["plan"]["id"],
                         acknowledgments=_acks_for(review))
        assert applied.status_code == 200, applied.text

        rows_a = _plan_feed(client, pa["headers"])
        rows_b = _plan_feed(client, pb["headers"])
        assert len(rows_a) == 1 and len(rows_b) == 1
        assert label_a in rows_a[0]["body"]
        assert kid_b["name"] not in rows_a[0]["body"]
        assert label_b not in rows_a[0]["body"]
        assert label_b in rows_b[0]["body"]
        assert kid_a["name"] not in rows_b[0]["body"]
        assert label_a not in rows_b[0]["body"]

        # Baselines are per-child truth: each carries its own address label.
        for kid, label in ((kid_a, label_a), (kid_b, label_b)):
            base = _baselines(kid["id"])
            assert set(base) == {MORNING, AFTERNOON}
            for leg in (MORNING, AFTERNOON):
                assert base[leg]["stop_name"] == label
    finally:
        _teardown(client, admin_headers, fx)
        for p in (pa, pb):
            client.delete(f"/api/accounts/parents/{p['id']}", headers=admin_headers)


# --- AE5 + reconcile + post-apply invariants ---------------------------------------

def test_ae5_mid_run_apply_reconcile_and_post_apply_invariants(client, admin_headers):
    """AE5/R11: an apply landing mid-run leaves the run's snapshot untouched
    and the run closable. The reconcile retires the surplus route (its
    unplaceable rider's family gets route-unassigned), reuses the drafted
    bus's existing route in place with its manual freeze cleared, and leaves
    a multi-trip chain untouched with zero notifications. Post-apply: links
    match the document, document times are materialized and stay frozen under
    the keyless (degraded) refresh, bus_id/pickup_time re-derive with the
    PM-only child NULL, ridership_pattern becomes live truth, and a run can
    START on an applied plan-ordered route."""
    marker = uuid.uuid4().hex[:6]
    p1 = signup_parent(client, marker, "p1")
    pc = signup_parent(client, marker, "pc")
    px = signup_parent(client, marker, "px")
    driver = _create_driver(client, admin_headers, marker)
    school = school2 = None
    buses: list[dict] = []
    students: list[dict] = []
    routes_made: list[dict] = []
    run_id = run2_id = None
    try:
        school = _make_school(client, admin_headers, marker)
        school2 = _make_school(client, admin_headers, marker + "2",
                               name=f"IT ApplySchool2 {marker}")
        bus_a = _make_bus(client, admin_headers, f"IT ApplyBus A {marker}",
                          capacity=8, depot=DEPOT_EAST)
        bus_b = _make_bus(client, admin_headers, f"IT ApplyBus B {marker}",
                          capacity=8, depot=DEPOT_WEST)
        bus_c = _make_bus(client, admin_headers, f"IT ApplyBus C {marker}",
                          capacity=8, driver_id=driver["id"])
        bus_m = _make_bus(client, admin_headers, f"IT ApplyBus M {marker}", capacity=8)
        buses = [bus_a, bus_b, bus_c, bus_m]

        def make_route(name, type_, bus_id, school_id, trip_index=1):
            created = client.post(
                "/api/fleet/routes",
                json={"name": name, "type": type_, "bus_id": bus_id,
                      "school_id": school_id, "trip_index": trip_index},
                headers=admin_headers,
            )
            assert created.status_code == 200, created.text
            routes_made.append(created.json())
            return created.json()

        # Pre-seeded live world: R_A on drafted bus A (will be reused in
        # place), R_C on unclaimed bus C (surplus -> retired), and a
        # multi-trip chain on bus M (trip 1 + trip 2, survives untouched).
        route_a = make_route(f"IT Apply Live A {marker}", MORNING, bus_a["id"], school["id"])
        route_c = make_route(f"IT Apply Live C {marker}", MORNING, bus_c["id"], school["id"])
        chain_1 = make_route(f"IT Apply Chain 1 {marker}", MORNING, bus_m["id"],
                             school["id"], trip_index=1)
        chain_2 = make_route(f"IT Apply Chain 2 {marker}", MORNING, bus_m["id"],
                             school["id"], trip_index=2)

        s1 = _make_student(client, admin_headers, marker, 0, school["id"],
                           EAST_HOMES[0], email=p1["email"], route_ids=[route_c["id"]])
        s2 = _make_student(client, admin_headers, marker, 1, school["id"],
                           EAST_HOMES[1], route_ids=[route_a["id"]])
        s3 = _make_student(client, admin_headers, marker, 2, school["id"], WEST_HOMES[0])
        s4 = _make_student(client, admin_headers, marker, 3, school["id"], WEST_HOMES[1])
        sc = _make_student(client, admin_headers, marker, 4, school["id"], None,
                           email=pc["email"], route_ids=[route_c["id"]])
        sx = _make_student(client, admin_headers, marker + "2", 0, school2["id"],
                           EAST_HOMES[3], email=px["email"], route_ids=[chain_2["id"]])
        students = [s1, s2, s3, s4, sc, sx]

        # Freeze R_A's manual order so apply provably clears the freeze.
        r_a = next(r for r in _school_routes(client, admin_headers, school["id"])
                   if r["id"] == route_a["id"])
        keys = [s["group_key"] for s in r_a["route_stops"] if s["group_key"]]
        frozen = client.put(
            f"/api/fleet/routes/{route_a['id']}/stop-order",
            json={"order": keys}, headers=admin_headers,
        )
        assert frozen.status_code == 200, frozen.text

        confirmed = _confirm(client, admin_headers, school["id"],
                             [bus_a["id"], bus_b["id"], bus_m["id"]])
        assert any(n["kind"] == "multi-trip-excluded" for n in confirmed["notices"])

        plan = _draft(client, admin_headers, school["id"], seed=3)
        narrowed = _edit(client, admin_headers, plan["id"], "pattern",
                         {"student_id": s4["id"], "pattern": "afternoon_only"})
        assert narrowed.status_code == 200, narrowed.text
        review = _review(client, admin_headers, school["id"])
        rides = _ride_map(review)

        # Start a run on the seeded surplus route, then apply mid-run.
        driver_headers = pin_login(client, driver["pin"])
        started = client.post("/api/runs/driver/start", json={"route_id": route_c["id"]},
                              headers=driver_headers)
        assert started.status_code == 200, started.text
        run_id = started.json()["id"]
        snapshot_before = _run_stop_rows(run_id)
        assert snapshot_before  # a real roster: s1 + sc stops

        applied = _apply(client, admin_headers, plan["id"],
                         acknowledgments=_acks_for(review))
        assert applied.status_code == 200, applied.text
        body = applied.json()
        assert body["routes_written"] == 4  # two drafted buses x two legs
        assert body["notified_family_count"] == 2  # p1 (updated) + pc (unassigned)

        # AE5: the run's snapshot is untouched and the run still closes —
        # drive the snapshot to the end (boarding requires the stop reached),
        # board everyone, end.
        assert _run_stop_rows(run_id) == snapshot_before
        for _ in range(started.json()["total_stops"]):
            arrived = client.post("/api/runs/driver/arrive",
                                  json={"run_id": run_id}, headers=driver_headers)
            assert arrived.status_code == 200, arrived.text
        for sid in (s1["id"], sc["id"]):
            boarded = client.post("/api/runs/driver/boarding",
                                  json={"student_id": sid, "on_bus": True},
                                  headers=driver_headers)
            assert boarded.status_code == 200, boarded.text
        ended = client.post("/api/runs/driver/end", json={"run_id": run_id},
                            headers=driver_headers)
        assert ended.status_code == 200, ended.text

        routes_after = _school_routes(client, admin_headers, school["id"])
        by_id = {r["id"]: r for r in routes_after}

        # Surplus retired; the chain (both trips) survives untouched.
        assert route_c["id"] not in by_id
        assert chain_1["id"] in by_id and chain_2["id"] in by_id
        for chain in (chain_1, chain_2):
            assert by_id[chain["id"]]["plan_ordered"] is False
        assert _links(sx["id"]) == {MORNING: bus_m["id"]}
        assert _plan_feed(client, px["headers"]) == []  # zero chain notifications

        # The drafted bus's pre-existing route was reused IN PLACE with the
        # freeze cleared; every materialized route is plan-ordered with
        # custom/manual clear and carries the degraded refresh-pending flag
        # (keyless stack: every post-commit refresh is offline).
        assert route_a["id"] in by_id
        materialized = [r for r in routes_after if r["plan_ordered"]]
        assert len(materialized) == 4
        for r in materialized:
            assert r["custom_stops"] is False
            assert r["manual_stop_order"] is False
            assert r["last_recalc_degraded"] is True
        assert by_id[route_a["id"]]["plan_ordered"] is True
        assert by_id[route_a["id"]]["manual_stop_order"] is False

        # Times frozen: every materialized student stop carries the document's
        # computed time; the gate rows carry the anchors.
        for r in materialized:
            leg = r["type"]
            for stop in r["route_stops"]:
                if stop["is_school_gate"]:
                    assert stop["scheduled_time"] == review["anchors"][leg]
                else:
                    assert stop["scheduled_time"] == rides[(stop["student_id"], leg)]["scheduled_time"]

        # Links match the document, per student and leg.
        for s in (s1, s2, s3):
            assert _links(s["id"]) == {
                MORNING: _bus_of(review, s["id"], MORNING),
                AFTERNOON: _bus_of(review, s["id"], AFTERNOON),
            }
        assert _links(s4["id"]) == {AFTERNOON: _bus_of(review, s4["id"], AFTERNOON)}
        assert _links(sc["id"]) == {}  # unplaceable: stale link severed with R_C

        # bus_id / pickup_time / ridership_pattern re-derived from the document.
        live = {s["id"]: s for s in client.get("/api/students", headers=admin_headers).json()}
        assert live[s1["id"]]["bus_id"] == _bus_of(review, s1["id"], MORNING)
        assert live[s1["id"]]["pickup_time"] == rides[(s1["id"], MORNING)]["scheduled_time"]
        assert live[s4["id"]]["pickup_time"] is None  # PM-only: morning-clock rule
        assert live[s4["id"]]["bus_id"] == _bus_of(review, s4["id"], AFTERNOON)
        assert live[s4["id"]]["ridership_pattern"] == "afternoon_only"

        # Notifications: the moved rider's family got exactly one
        # route-updated; the unplaceable rider's family exactly one
        # route-unassigned (their live membership made them notifiable).
        p1_rows = _plan_feed(client, p1["headers"])
        assert [r["type"] for r in p1_rows] == ["route-updated"]
        assert p1_rows[0]["student_id"] == s1["id"]
        pc_rows = _plan_feed(client, pc["headers"])
        assert [r["type"] for r in pc_rows] == ["route-unassigned"]
        assert pc_rows[0]["student_id"] == sc["id"]

        # Audit: one self-contained plan-applied record.
        audits = _audit_rows(school["id"])
        assert len(audits) == 1
        assert audits[0]["actor_email"] == ADMIN["email"]
        detail = audits[0]["detail"]
        assert detail["routes_written"] == 4
        assert detail["families_notified"] == 2
        assert detail["degraded"] is True  # keyless draft
        assert isinstance(detail["elapsed_ms"], int)

        # A run STARTS on an applied plan-ordered route (the custom_stops
        # refusal must not fire). Move the driver onto drafted bus A first.
        assert client.put(
            f"/api/fleet/buses/{bus_c['id']}",
            json={"name": bus_c["name"], "capacity": 8},
            headers=admin_headers,
        ).status_code == 200
        assert client.put(
            f"/api/fleet/buses/{bus_a['id']}",
            json={"name": bus_a["name"], "capacity": 8, "driver_id": driver["id"],
                  "depot_lat": DEPOT_EAST[0], "depot_lng": DEPOT_EAST[1]},
            headers=admin_headers,
        ).status_code == 200
        bus_a_morning = next(r for r in materialized
                             if r["bus_id"] == bus_a["id"] and r["type"] == MORNING)
        started2 = client.post("/api/runs/driver/start",
                               json={"route_id": bus_a_morning["id"]},
                               headers=pin_login(client, driver["pin"]))
        assert started2.status_code == 200, started2.text
        run2_id = started2.json()["id"]
    finally:
        purge_run(run2_id)
        purge_run(run_id)
        for s in students:
            client.delete(f"/api/students/{s['id']}", headers=admin_headers)
        for r in routes_made:
            client.delete(f"/api/fleet/routes/{r['id']}", headers=admin_headers)
        for b in buses:
            client.delete(f"/api/fleet/buses/{b['id']}", headers=admin_headers)
        for sch in (school, school2):
            if sch:
                client.delete(f"/api/fleet/schools/{sch['id']}", headers=admin_headers)
        client.delete(f"/api/accounts/drivers/{driver['id']}", headers=admin_headers)
        for p in (p1, pc, px):
            client.delete(f"/api/accounts/parents/{p['id']}", headers=admin_headers)


# --- notifications, baselines, status lifecycle -------------------------------------

def test_notification_matrix_baselines_and_status_lifecycle(client, admin_headers):
    """Across four applies of an identical document (same seed, unchanged
    inputs): the first apply notifies every family exactly once and seeds
    baselines; re-applying the applied plan is a 200 no-op; a +6-minute drift
    vs the communicated baseline notifies while +4 stays silent; a bus swap
    at identical times notifies; a still-unplaceable child stays silent
    throughout; narrowing to morning_only sends leg-removed and deletes the
    PM baseline; re-widening notifies again even though the new time matches
    the deleted baseline's. Statuses walk applied=1/previous=1/draft=0 with
    the one-level previous row deleted and the as-evolved capture preserving
    a manual stop edit made while live."""
    marker = uuid.uuid4().hex[:6]
    parents = {tag: signup_parent(client, marker, tag) for tag in ("p1", "p2", "p3", "p4", "p5")}
    emails = [parents[t]["email"] for t in ("p1", "p2", "p3", "p4", "p5")]
    fx = _build_plan(
        client, admin_headers, marker,
        buses=[("A", 8, DEPOT_EAST), ("B", 8, DEPOT_WEST)],
        homes=[EAST_HOMES[0], EAST_HOMES[1], WEST_HOMES[0], WEST_HOMES[1], None],
        seed=7, emails=emails,
    )
    school_id = fx["school"]["id"]
    s1, s2, s3, s4, s5 = (s["id"] for s in fx["students"])
    try:
        plan1 = fx["plan"]
        review1 = _review(client, admin_headers, school_id)
        rides1 = _ride_map(review1)
        acks = _acks_for(review1)
        assert {u["student_id"] for u in review1["unplaceable"][MORNING]} == {s5}

        def feed_count(tag: str) -> list[dict]:
            return _plan_feed(client, parents[tag]["headers"])

        # --- apply #1: whole school, once per family; baselines seeded -------
        applied1 = _apply(client, admin_headers, plan1["id"], acknowledgments=acks)
        assert applied1.status_code == 200, applied1.text
        assert applied1.json()["notified_family_count"] == 4
        for tag, sid in (("p1", s1), ("p2", s2), ("p3", s3), ("p4", s4)):
            rows = feed_count(tag)
            assert [r["type"] for r in rows] == ["route-updated"], tag
            assert rows[0]["student_id"] == sid
        assert feed_count("p5") == []  # never-communicated unplaceable: silent
        for sid in (s1, s2, s3, s4):
            base = _baselines(sid)
            assert set(base) == {MORNING, AFTERNOON}
            for leg in (MORNING, AFTERNOON):
                assert base[leg]["scheduled_time"] == rides1[(sid, leg)]["scheduled_time"]
        assert _baselines(s5) == {}
        # Even a FIRST apply preserves the pre-apply live state as 'previous'
        # (R21: "the plan it replaced" includes hand-built or empty routes) —
        # the first apply must not be the one un-restorable act.
        assert _plan_statuses(school_id) == {"draft": 0, "applied": 1, "previous": 1}

        # --- re-apply the applied plan: idempotent, zero side effects --------
        again = _apply(client, admin_headers, plan1["id"], acknowledgments=acks)
        assert again.status_code == 200, again.text
        assert again.json()["already_applied"] is True
        assert len(_audit_rows(school_id)) == 1
        assert len(feed_count("p1")) == 1

        # --- as-evolved manual edit, baseline tampering, live bus move -------
        # The manual reorder targets the morning route s3 will later be moved
        # ONTO, so the preserved 'previous' document must show BOTH live
        # evolutions: the reversed manual order and s3's appended stop.
        doc_bus_s3 = _bus_of(review1, s3, MORNING)
        routes = _school_routes(client, admin_headers, school_id)
        reorder_route = next(
            r for r in routes
            if r["type"] == MORNING and r["plan_ordered"] and r["bus_id"] != doc_bus_s3
        )
        keys = [s["group_key"] for s in sorted(reorder_route["route_stops"],
                                               key=lambda s: s["stop_order"])
                if s["group_key"]]
        assert len(keys) >= 2
        expected_manual = list(reversed(keys))
        assert client.put(
            f"/api/fleet/routes/{reorder_route['id']}/stop-order",
            json={"order": expected_manual}, headers=admin_headers,
        ).status_code == 200

        # Tamper the communicated baselines (sanctioned: the R15 thresholds
        # need a baseline that differs from what the next apply communicates).
        for sid, delta in ((s1, -6), (s2, -4)):
            base = _baselines(sid)[MORNING]
            _pg_exec(
                "update live_communicated_stops set scheduled_time = %s "
                "where student_id = %s and route_type = 'morning'",
                (_shift(base["scheduled_time"], delta), sid),
            )
        # Live bus swap for s3: move both legs to the OTHER bus's routes so
        # membership differs from the (identical) document at apply #2.
        other_routes = [r["id"] for r in routes
                        if r["plan_ordered"] and r["bus_id"] != doc_bus_s3]
        assert len(other_routes) == 2
        moved = client.put(
            f"/api/students/{s3}",
            json={**_student_payload(marker, 2, school_id, WEST_HOMES[0],
                                     email=parents["p3"]["email"]),
                  "route_ids": other_routes},
            headers=admin_headers,
        )
        assert moved.status_code == 200, moved.text

        # --- apply #2 (identical document): thresholds + bus change ----------
        plan2 = _draft(client, admin_headers, school_id, seed=7)
        review2 = _review(client, admin_headers, school_id)
        applied2 = _apply(client, admin_headers, plan2["id"],
                          acknowledgments=_acks_for(review2))
        assert applied2.status_code == 200, applied2.text
        assert set(applied2.json()["notified_families"]) == {
            parents["p1"]["id"], parents["p3"]["id"]
        }
        assert [r["type"] for r in feed_count("p1")] == ["route-updated"] * 2  # +6 min
        assert len(feed_count("p2")) == 1                                      # +4 min
        assert [r["type"] for r in feed_count("p3")] == ["route-updated"] * 2  # bus swap
        assert len(feed_count("p4")) == 1
        assert feed_count("p5") == []                                          # still silent
        # Baselines: updated only on send — the notified pair snaps back to
        # the document time, the silent +4 drift keeps what was communicated.
        assert _baselines(s1)[MORNING]["scheduled_time"] == rides1[(s1, MORNING)]["scheduled_time"]
        assert _baselines(s2)[MORNING]["scheduled_time"] == _shift(
            rides1[(s2, MORNING)]["scheduled_time"], -4)

        # Statuses: one applied, one previous (as-evolved), zero drafts; the
        # preserved document carries the manual stop order made while live.
        assert _plan_statuses(school_id) == {"draft": 0, "applied": 1, "previous": 1}
        assert _plan_status(plan1["id"]) == "previous"
        assert _plan_status(plan2["id"]) == "applied"
        preserved = _pg_one(
            "select document from live_fleet_plans where id = %s", (plan1["id"],)
        )["document"]
        captured = next(r for r in preserved["routes"] if r["id"] == reorder_route["id"])
        assert captured["manual_stop_order"] is True
        captured_keys = [
            f"{s['lat']:.6f},{s['lng']:.6f}"
            for s in sorted(captured["stops"], key=lambda s: (s["stop_order"], s["name"] or ""))
            if not s["is_school_gate"]
        ]
        # Sibling-free stops: one row per group, so the captured order is the
        # manual order (coordinate-derived keys, same rounding) with s3's stop
        # appended by the live move — both as-evolved edits preserved.
        s3_key = f"{WEST_HOMES[0][0]:.6f},{WEST_HOMES[0][1]:.6f}"
        assert captured_keys == expected_manual + [s3_key]

        # --- apply #3: narrow s4 -> leg-removed + PM baseline deleted ---------
        plan3 = _draft(client, admin_headers, school_id, seed=7)
        assert _edit(client, admin_headers, plan3["id"], "pattern",
                     {"student_id": s4, "pattern": "morning_only"}).status_code == 200
        review3 = _review(client, admin_headers, school_id)
        applied3 = _apply(client, admin_headers, plan3["id"],
                          acknowledgments=_acks_for(review3))
        assert applied3.status_code == 200, applied3.text
        assert applied3.json()["notified_families"] == [parents["p4"]["id"]]
        p4_rows = feed_count("p4")  # feed is newest first
        assert [r["type"] for r in p4_rows] == ["route-unassigned", "route-updated"]
        assert set(_baselines(s4)) == {MORNING}  # PM baseline deleted
        live = {s["id"]: s for s in client.get("/api/students", headers=admin_headers).json()}
        assert live[s4]["ridership_pattern"] == "morning_only"
        assert _plan_status(plan1["id"]) is None  # one-level history: row DELETED
        assert _plan_status(plan2["id"]) == "previous"

        # The next draft's basis reflects the applied pattern.
        plan4 = _draft(client, admin_headers, school_id, seed=7)
        basis_s4 = next(s for s in plan4["basis"]["students"] if s["id"] == s4)
        assert basis_s4["pattern"] == "morning_only"

        # --- apply #4: re-widen -> notifies although the time matches the
        # (deleted) stale baseline; the stale row must not mute the send.
        assert _edit(client, admin_headers, plan4["id"], "pattern",
                     {"student_id": s4, "pattern": "both_ways"}).status_code == 200
        review4 = _review(client, admin_headers, school_id)
        applied4 = _apply(client, admin_headers, plan4["id"],
                          acknowledgments=_acks_for(review4))
        assert applied4.status_code == 200, applied4.text
        assert applied4.json()["notified_families"] == [parents["p4"]["id"]]
        assert [r["type"] for r in feed_count("p4")] == [  # newest first
            "route-updated", "route-unassigned", "route-updated"
        ]
        assert set(_baselines(s4)) == {MORNING, AFTERNOON}
        assert _plan_status(plan2["id"]) is None
        assert _plan_status(plan3["id"]) == "previous"
        assert _plan_status(plan4["id"]) == "applied"
        assert len(_audit_rows(school_id)) == 4
    finally:
        _teardown(client, admin_headers, fx)
        for p in parents.values():
            client.delete(f"/api/accounts/parents/{p['id']}", headers=admin_headers)


# --- atomicity ----------------------------------------------------------------------

def test_atomicity_forced_late_failure_rolls_back_everything(client, admin_headers):
    """Forcing mechanism (documented in the DAO): the unknown-acknowledgment
    gate is deliberately the transaction's LAST act, after routes, links,
    stops, audit row, feed rows, baselines and the status flip have all been
    written. An acknowledgment naming a student the plan does not list
    unplaceable therefore 409s after everything — and the world must be
    byte-identical to before: no routes, no feed rows, no baselines, no
    audit, statuses unchanged."""
    marker = uuid.uuid4().hex[:6]
    p1 = signup_parent(client, marker, "pa")
    fx = _build_plan(
        client, admin_headers, marker,
        buses=[("A", 6, DEPOT_EAST)],
        homes=[EAST_HOMES[0], EAST_HOMES[1], None],
        emails=[p1["email"], None, None],
    )
    school_id = fx["school"]["id"]
    plan_id = fx["plan"]["id"]
    try:
        review = _review(client, admin_headers, school_id)
        acks = _acks_for(review)
        assert acks  # the coordless child is unplaceable on both legs

        refused = _apply(
            client, admin_headers, plan_id,
            acknowledgments=acks + [{"student_id": str(uuid.uuid4()), "leg": MORNING}],
        )
        assert refused.status_code == 409, refused.text
        assert "unplaceable" in refused.json()["detail"]

        # The whole transaction rolled back: nothing landed anywhere.
        assert _plan_status(plan_id) == "draft"
        assert _school_routes(client, admin_headers, school_id) == []
        assert _audit_rows(school_id) == []
        assert _plan_feed(client, p1["headers"]) == []
        for s in fx["students"]:
            assert _baselines(s["id"]) == {}
            assert _links(s["id"]) == {}

        # The same payload minus the bogus acknowledgment applies cleanly.
        applied = _apply(client, admin_headers, plan_id, acknowledgments=acks)
        assert applied.status_code == 200, applied.text
        assert len(_plan_feed(client, p1["headers"])) == 1
    finally:
        _teardown(client, admin_headers, fx)
        client.delete(f"/api/accounts/parents/{p1['id']}", headers=admin_headers)


# --- authorization --------------------------------------------------------------------

def test_non_admin_refused_on_apply(client, parent_headers):
    """Apply is admin-only: unauthenticated 401, parent 403 — no side effects
    (a phantom plan id keeps the check pure)."""
    phantom = str(uuid.uuid4())
    unauth = client.post(f"/api/fleet-plans/{phantom}/apply",
                         json={"confirmations": [], "acknowledgments": []})
    assert unauth.status_code == 401
    forbidden = client.post(f"/api/fleet-plans/{phantom}/apply",
                            json={"confirmations": [], "acknowledgments": []},
                            headers=parent_headers)
    assert forbidden.status_code == 403
