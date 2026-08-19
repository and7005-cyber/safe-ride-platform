"""Fleet-plan drafting (U4): fleet confirmation, draft generation, one-open-
draft, scrub-on-supersede/discard, and read-only-against-live-routes.

Run with the stack up (scripts/start-local.sh):

    RUN_INTEGRATION=1 ../.venv/bin/python -m pytest tests/integration/test_fleet_plan_draft.py -q

Covers F1/AE1: confirm-fleet claims buses and reports per-bus notices
(multi-trip excluded, depot-less proceeds); a cross-school claim 409s naming
the bus and the claiming school; a draft yields mirrored AM/PM pairs with
per-child ride seconds and a populated basis, flagged degraded on the keyless
stack; drafting never touches live routes; a second draft 409s unless
superseded, and superseding/discarding scrubs the old row's document/basis
payloads (metadata kept — verified with a read-only SQL peek, since scrubbed
rows deliberately have no API surface); a coordinate-less student is listed
unplaceable with 'unresolved address'; non-admins are refused everywhere.

Entities are 'IT '-prefixed and cleaned up in finally blocks — the tests
create their own school/buses/students and never depend on seed data.
"""

import os
import uuid

import httpx
import psycopg
import pytest

from conftest import DSN

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="needs the local stack; set RUN_INTEGRATION=1",
)

BASE = os.environ.get("INTEGRATION_API_URL", "http://localhost:9001")

ADMIN = {"email": "admin@test.com", "password": "test1234."}
PARENT = {"email": "and7005@gmail.com", "password": "Test1234"}

SCHOOL_LAT, SCHOOL_LNG = -1.3000, 36.8000
# Two loose geographic clusters east and west of the school, so a two-bus
# partition is geometrically natural.
EAST_HOMES = [(-1.290, 36.820), (-1.292, 36.824), (-1.288, 36.818), (-1.294, 36.822)]
WEST_HOMES = [(-1.310, 36.780), (-1.312, 36.776), (-1.308, 36.782), (-1.314, 36.778)]
DEPOT_EAST = (-1.285, 36.830)
DEPOT_WEST = (-1.320, 36.770)


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE, timeout=30) as c:
        yield c


def login(client: httpx.Client, email: str, password: str) -> dict:
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    token = response.json()["token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def admin_headers(client):
    return login(client, ADMIN["email"], ADMIN["password"])


@pytest.fixture(scope="module")
def parent_headers(client):
    return login(client, PARENT["email"], PARENT["password"])


# --- builders -----------------------------------------------------------------

def _make_school(client, headers, marker: str, **overrides) -> dict:
    payload = {"name": f"IT PlanSchool {marker}", "lat": SCHOOL_LAT, "lng": SCHOOL_LNG}
    payload.update(overrides)
    created = client.post("/api/fleet/schools", json=payload, headers=headers)
    assert created.status_code == 200, created.text
    return created.json()


def _make_bus(client, headers, name: str, *, capacity: int | None = None,
              depot: tuple[float, float] | None = None) -> dict:
    payload: dict = {"name": name}
    if capacity is not None:
        payload["capacity"] = capacity
    if depot is not None:
        payload["depot_lat"], payload["depot_lng"] = depot
    created = client.post("/api/fleet/buses", json=payload, headers=headers)
    assert created.status_code == 200, created.text
    return created.json()


def _make_student(client, headers, marker: str, i: int, school_id: str,
                  home: tuple[float, float] | None, route_ids: list[str] | None = None) -> dict:
    payload = {
        "name": f"IT Plan Kid {marker} {i}",
        "parent_name": f"IT Plan Parent {marker} {i}",
        "parent_phone": f"+2547110001{i:02d}",
        "parent_email": f"it-fp-{marker}-{i}@test.local",
        "school_id": school_id,
        "route_ids": route_ids or [],
    }
    if home is not None:
        payload["home_lat"], payload["home_lng"] = home
        payload["home_address"] = f"IT Plan Home {marker} {i}"
    created = client.post("/api/students", json=payload, headers=headers)
    assert created.status_code == 200, created.text
    return created.json()


def _confirm(client, headers, school_id: str, bus_ids: list[str]) -> httpx.Response:
    return client.post(
        "/api/fleet-plans/confirm-fleet",
        json={"school_id": school_id, "bus_ids": bus_ids},
        headers=headers,
    )


def _draft(client, headers, school_id: str, **kw) -> httpx.Response:
    return client.post(
        "/api/fleet-plans/draft", json={"school_id": school_id, **kw}, headers=headers
    )


def _bus_row(client, headers, bus_id: str) -> dict:
    buses = client.get("/api/fleet/buses", headers=headers).json()
    return next(b for b in buses if b["id"] == bus_id)


def _plan_db_row(plan_id: str) -> dict:
    """Read-only SQL peek at a plan row: superseded/discarded rows have no API
    surface by design, and the scrub they undergo is exactly what this suite
    must prove. Never used to mutate."""
    with psycopg.connect(DSN, row_factory=psycopg.rows.dict_row) as pg:
        return pg.execute(
            "select status, document, basis, solver_seed, created_at "
            "from live_fleet_plans where id = %s",
            (plan_id,),
        ).fetchone()


def _leg_student_ids(bus_doc: dict, leg: str) -> set[str]:
    return {
        s["id"] for stop in bus_doc["legs"][leg]["stops"] for s in stop["students"]
    }


# --- confirm-fleet ------------------------------------------------------------

def test_confirm_fleet_claims_buses_and_reports_notices(client, admin_headers):
    """Claiming sets live_buses.school_id; a depot-less bus proceeds with a
    notice; a bus with a trip_index-2 route is claimed but excluded from
    drafting with a named notice; deselecting releases the claim."""
    marker = uuid.uuid4().hex[:6]
    school = _make_school(client, admin_headers, marker)
    bus_a = _make_bus(client, admin_headers, f"IT PlanBus A {marker}", depot=DEPOT_EAST)
    bus_b = _make_bus(client, admin_headers, f"IT PlanBus B {marker}")  # no depot
    bus_c = _make_bus(client, admin_headers, f"IT PlanBus C {marker}", depot=DEPOT_WEST)
    chain_route = client.post(
        "/api/fleet/routes",
        json={"name": f"IT Plan Chain {marker}", "type": "morning",
              "bus_id": bus_c["id"], "trip_index": 2},
        headers=admin_headers,
    ).json()
    try:
        confirmed = _confirm(
            client, admin_headers, school["id"], [bus_a["id"], bus_b["id"], bus_c["id"]]
        )
        assert confirmed.status_code == 200, confirmed.text
        body = confirmed.json()

        for bus in (bus_a, bus_b, bus_c):
            assert _bus_row(client, admin_headers, bus["id"])["school_id"] == school["id"]

        by_id = {b["id"]: b for b in body["buses"]}
        assert by_id[bus_a["id"]]["excluded_from_drafting"] is False
        assert by_id[bus_b["id"]]["excluded_from_drafting"] is False
        assert by_id[bus_c["id"]]["excluded_from_drafting"] is True

        notices = {n["bus_id"]: n for n in body["notices"]}
        assert bus_a["id"] not in notices
        assert notices[bus_b["id"]]["kind"] == "no-depot"
        assert bus_b["name"] in notices[bus_b["id"]]["message"]
        assert notices[bus_c["id"]]["kind"] == "multi-trip-excluded"
        assert bus_c["name"] in notices[bus_c["id"]]["message"]

        # Deselecting B and C releases their claims; A keeps its own.
        reconfirmed = _confirm(client, admin_headers, school["id"], [bus_a["id"]])
        assert reconfirmed.status_code == 200, reconfirmed.text
        assert set(reconfirmed.json()["released"]) == {bus_b["id"], bus_c["id"]}
        assert _bus_row(client, admin_headers, bus_a["id"])["school_id"] == school["id"]
        assert _bus_row(client, admin_headers, bus_b["id"])["school_id"] is None
        assert _bus_row(client, admin_headers, bus_c["id"])["school_id"] is None
    finally:
        client.delete(f"/api/fleet/routes/{chain_route['id']}", headers=admin_headers)
        for bus in (bus_a, bus_b, bus_c):
            client.delete(f"/api/fleet/buses/{bus['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/schools/{school['id']}", headers=admin_headers)


def test_confirm_fleet_cross_school_claim_conflicts(client, admin_headers):
    """A bus claimed by school A refuses school B's confirm with a 409 naming
    the bus and the claiming school."""
    marker = uuid.uuid4().hex[:6]
    school_a = _make_school(client, admin_headers, f"{marker}-A")
    school_b = _make_school(client, admin_headers, f"{marker}-B")
    bus = _make_bus(client, admin_headers, f"IT PlanBus X {marker}", depot=DEPOT_EAST)
    try:
        first = _confirm(client, admin_headers, school_a["id"], [bus["id"]])
        assert first.status_code == 200, first.text

        second = _confirm(client, admin_headers, school_b["id"], [bus["id"]])
        assert second.status_code == 409, second.text
        detail = second.json()["detail"]
        assert bus["name"] in detail, detail
        assert school_a["name"] in detail, detail
        # The failed confirm changed nothing: the claim still belongs to A.
        assert _bus_row(client, admin_headers, bus["id"])["school_id"] == school_a["id"]
    finally:
        client.delete(f"/api/fleet/buses/{bus['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/schools/{school_a['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/schools/{school_b['id']}", headers=admin_headers)


# --- draft generation ---------------------------------------------------------

def test_draft_happy_path_two_mirrored_pairs(client, admin_headers):
    """AE1 shape: two capacity-legal mirrored pairs, per-child ride seconds
    present, basis snapshot populated, degraded flagged on the keyless stack."""
    marker = uuid.uuid4().hex[:6]
    school = _make_school(client, admin_headers, marker)
    bus_a = _make_bus(client, admin_headers, f"IT PlanBus A {marker}",
                      capacity=4, depot=DEPOT_EAST)
    bus_b = _make_bus(client, admin_headers, f"IT PlanBus B {marker}",
                      capacity=4, depot=DEPOT_WEST)
    students = []
    try:
        for i, home in enumerate(EAST_HOMES + WEST_HOMES):
            students.append(
                _make_student(client, admin_headers, marker, i, school["id"], home)
            )
        assert _confirm(
            client, admin_headers, school["id"], [bus_a["id"], bus_b["id"]]
        ).status_code == 200

        drafted = _draft(client, admin_headers, school["id"], seed=42)
        assert drafted.status_code == 200, drafted.text
        plan = drafted.json()
        assert plan["status"] == "draft"
        assert plan["school_id"] == school["id"]
        assert plan["solver_seed"] == 42
        assert plan["degraded"] is True  # keyless stack: haversine matrix

        document = plan["document"]
        assert document["degraded"] is True
        assert document["unplaceable"] == []
        assert len(document["buses"]) == 2

        all_ids = {s["id"] for s in students}
        seen_am: set[str] = set()
        for bus_doc in document["buses"]:
            am = _leg_student_ids(bus_doc, "morning")
            pm = _leg_student_ids(bus_doc, "afternoon")
            # Mirrored pair: roster identity across legs (all both_ways).
            assert am == pm
            assert am, "each bus must carry riders at this capacity"
            assert len(am) <= bus_doc["capacity"]
            assert not (am & seen_am)
            seen_am |= am
            for leg in ("morning", "afternoon"):
                rides = bus_doc["legs"][leg]["ride_seconds"]
                assert {r["student_id"] for r in rides} == am
                assert all(
                    isinstance(r["ride_seconds"], (int, float)) and r["ride_seconds"] >= 0
                    for r in rides
                )
                assert bus_doc["legs"][leg]["driving_seconds"] > 0
        assert seen_am == all_ids  # every child rides, exactly once per leg

        basis = plan["basis"]
        assert basis["school"]["name"] == school["name"]
        assert {s["id"] for s in basis["students"]} == all_ids
        assert all(s["plannable"] for s in basis["students"])
        assert all(s["name"].startswith("IT Plan Kid") for s in basis["students"])
        assert {b["id"] for b in basis["fleet"]} == {bus_a["id"], bus_b["id"]}
        assert all(b["capacity"] == 4 for b in basis["fleet"])
        assert basis["pins"] == []
        assert basis["excluded_buses"] == []

        # GET current returns the stored draft as-is; no applied/previous yet.
        current = client.get(
            "/api/fleet-plans/current", params={"school_id": school["id"]},
            headers=admin_headers,
        )
        assert current.status_code == 200, current.text
        assert current.json()["draft"]["id"] == plan["id"]
        assert current.json()["applied"] is None
        assert current.json()["previous"] is None
    finally:
        for s in students:
            client.delete(f"/api/students/{s['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/buses/{bus_a['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/buses/{bus_b['id']}", headers=admin_headers)
        # Deleting the school cascades its plan rows (011 FK).
        client.delete(f"/api/fleet/schools/{school['id']}", headers=admin_headers)


def test_draft_excludes_multi_trip_bus_and_keeps_depotless(client, admin_headers):
    """A claimed multi-trip bus is absent from the drafted document and named
    in the basis exclusion list; a depot-less bus drafts normally."""
    marker = uuid.uuid4().hex[:6]
    school = _make_school(client, admin_headers, marker)
    bus_chain = _make_bus(client, admin_headers, f"IT PlanBus M {marker}", depot=DEPOT_EAST)
    bus_plain = _make_bus(client, admin_headers, f"IT PlanBus N {marker}", capacity=10)
    chain_route = client.post(
        "/api/fleet/routes",
        json={"name": f"IT Plan Chain {marker}", "type": "morning",
              "bus_id": bus_chain["id"], "trip_index": 2},
        headers=admin_headers,
    ).json()
    students = []
    try:
        for i, home in enumerate(EAST_HOMES[:2]):
            students.append(
                _make_student(client, admin_headers, marker, i, school["id"], home)
            )
        assert _confirm(
            client, admin_headers, school["id"], [bus_chain["id"], bus_plain["id"]]
        ).status_code == 200

        drafted = _draft(client, admin_headers, school["id"])
        assert drafted.status_code == 200, drafted.text
        plan = drafted.json()

        bus_ids_in_doc = {b["bus_id"] for b in plan["document"]["buses"]}
        assert bus_ids_in_doc == {bus_plain["id"]}
        assert plan["document"]["unplaceable"] == []

        excluded = plan["basis"]["excluded_buses"]
        assert [b["id"] for b in excluded] == [bus_chain["id"]]
        assert excluded[0]["name"] == bus_chain["name"]
        assert "multi-trip" in excluded[0]["reason"]
        assert [b["id"] for b in plan["basis"]["fleet"]] == [bus_plain["id"]]
    finally:
        for s in students:
            client.delete(f"/api/students/{s['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/routes/{chain_route['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/buses/{bus_chain['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/buses/{bus_plain['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/schools/{school['id']}", headers=admin_headers)


def test_draft_is_read_only_against_live_routes(client, admin_headers):
    """R8: generation never alters live routes — the school's route payload
    (rows, ordering, times, flags) is byte-identical before and after."""
    marker = uuid.uuid4().hex[:6]
    school = _make_school(client, admin_headers, marker)
    bus = _make_bus(client, admin_headers, f"IT PlanBus L {marker}", depot=DEPOT_EAST)
    route = client.post(
        "/api/fleet/routes",
        json={"name": f"IT Plan Live {marker}", "type": "morning",
              "bus_id": bus["id"], "school_id": school["id"]},
        headers=admin_headers,
    ).json()
    students = []
    try:
        for i, home in enumerate(EAST_HOMES[:3]):
            students.append(
                _make_student(client, admin_headers, marker, i, school["id"], home,
                              route_ids=[route["id"]])
            )
        assert _confirm(client, admin_headers, school["id"], [bus["id"]]).status_code == 200

        def route_snapshot() -> dict:
            routes = client.get("/api/fleet/routes", headers=admin_headers).json()
            return next(r for r in routes if r["id"] == route["id"])

        before = route_snapshot()
        assert len(before["route_stops"]) > 0  # the baseline is a real route

        drafted = _draft(client, admin_headers, school["id"])
        assert drafted.status_code == 200, drafted.text
        document = drafted.json()["document"]
        assert any(_leg_student_ids(b, "morning") for b in document["buses"])

        after = route_snapshot()
        assert after == before  # row count, ordering, times, flags — identical
    finally:
        for s in students:
            client.delete(f"/api/students/{s['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/routes/{route['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/buses/{bus['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/schools/{school['id']}", headers=admin_headers)


# --- one-open-draft / scrub ---------------------------------------------------

def test_second_draft_conflicts_then_supersede_scrubs(client, admin_headers):
    """One-open-draft: a second draft 409s naming the open one; with
    supersede the old row flips to superseded and its document/basis are
    scrubbed to NULL with metadata kept."""
    marker = uuid.uuid4().hex[:6]
    school = _make_school(client, admin_headers, marker)
    bus = _make_bus(client, admin_headers, f"IT PlanBus S {marker}",
                    capacity=4, depot=DEPOT_EAST)
    students = []
    try:
        for i, home in enumerate(EAST_HOMES[:2]):
            students.append(
                _make_student(client, admin_headers, marker, i, school["id"], home)
            )
        assert _confirm(client, admin_headers, school["id"], [bus["id"]]).status_code == 200

        first = _draft(client, admin_headers, school["id"], seed=7)
        assert first.status_code == 200, first.text
        first_id = first.json()["id"]

        blocked = _draft(client, admin_headers, school["id"])
        assert blocked.status_code == 409, blocked.text
        assert first_id in blocked.json()["detail"]

        superseded = _draft(client, admin_headers, school["id"], supersede=True)
        assert superseded.status_code == 200, superseded.text
        second_id = superseded.json()["id"]
        assert second_id != first_id

        old = _plan_db_row(first_id)
        assert old["status"] == "superseded"
        assert old["document"] is None
        assert old["basis"] is None
        assert old["solver_seed"] == 7  # metadata kept
        assert old["created_at"] is not None

        current = client.get(
            "/api/fleet-plans/current", params={"school_id": school["id"]},
            headers=admin_headers,
        ).json()
        assert current["draft"]["id"] == second_id
    finally:
        for s in students:
            client.delete(f"/api/students/{s['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/buses/{bus['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/schools/{school['id']}", headers=admin_headers)


def test_discard_scrubs_draft(client, admin_headers):
    """Discard flips a draft to discarded and scrubs its payloads; a second
    discard 409s (draft only)."""
    marker = uuid.uuid4().hex[:6]
    school = _make_school(client, admin_headers, marker)
    bus = _make_bus(client, admin_headers, f"IT PlanBus D {marker}",
                    capacity=4, depot=DEPOT_WEST)
    students = []
    try:
        for i, home in enumerate(WEST_HOMES[:2]):
            students.append(
                _make_student(client, admin_headers, marker, i, school["id"], home)
            )
        assert _confirm(client, admin_headers, school["id"], [bus["id"]]).status_code == 200
        drafted = _draft(client, admin_headers, school["id"], seed=3)
        assert drafted.status_code == 200, drafted.text
        plan_id = drafted.json()["id"]

        discarded = client.post(
            f"/api/fleet-plans/{plan_id}/discard", headers=admin_headers
        )
        assert discarded.status_code == 200, discarded.text
        assert discarded.json()["status"] == "discarded"

        row = _plan_db_row(plan_id)
        assert row["status"] == "discarded"
        assert row["document"] is None
        assert row["basis"] is None
        assert row["solver_seed"] == 3  # metadata kept

        current = client.get(
            "/api/fleet-plans/current", params={"school_id": school["id"]},
            headers=admin_headers,
        ).json()
        assert current["draft"] is None

        again = client.post(
            f"/api/fleet-plans/{plan_id}/discard", headers=admin_headers
        )
        assert again.status_code == 409, again.text
    finally:
        for s in students:
            client.delete(f"/api/students/{s['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/buses/{bus['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/schools/{school['id']}", headers=admin_headers)


# --- unresolved addresses -----------------------------------------------------

def test_coordless_student_listed_unplaceable_unresolved_address(client, admin_headers):
    """A student without home coordinates is not solver input: they appear in
    the unplaceable list with constraint 'unresolved address' for each leg,
    and in the basis as non-plannable."""
    marker = uuid.uuid4().hex[:6]
    school = _make_school(client, admin_headers, marker)
    bus = _make_bus(client, admin_headers, f"IT PlanBus U {marker}",
                    capacity=4, depot=DEPOT_EAST)
    placed = nogeo = None
    try:
        placed = _make_student(client, admin_headers, marker, 0, school["id"], EAST_HOMES[0])
        nogeo = _make_student(client, admin_headers, marker, 1, school["id"], None)
        assert _confirm(client, admin_headers, school["id"], [bus["id"]]).status_code == 200

        drafted = _draft(client, admin_headers, school["id"])
        assert drafted.status_code == 200, drafted.text
        plan = drafted.json()

        unplaceable = plan["document"]["unplaceable"]
        entries = [u for u in unplaceable if u["student_id"] == nogeo["id"]]
        assert {e["leg"] for e in entries} == {"morning", "afternoon"}
        assert all(e["constraint"] == "unresolved address" for e in entries)
        assert all(e["name"] == nogeo["name"] for e in entries)

        # Never in a roster; the placed sibling rides normally.
        for bus_doc in plan["document"]["buses"]:
            for leg in ("morning", "afternoon"):
                assert nogeo["id"] not in _leg_student_ids(bus_doc, leg)
        assert any(
            placed["id"] in _leg_student_ids(b, "morning")
            for b in plan["document"]["buses"]
        )

        by_id = {s["id"]: s for s in plan["basis"]["students"]}
        assert by_id[nogeo["id"]]["plannable"] is False
        assert by_id[placed["id"]]["plannable"] is True
    finally:
        for s in (placed, nogeo):
            if s:
                client.delete(f"/api/students/{s['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/buses/{bus['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/schools/{school['id']}", headers=admin_headers)


# --- authorization ------------------------------------------------------------

def test_non_admin_refused_on_every_endpoint(client, admin_headers, parent_headers):
    """Every fleet-plan endpoint is admin-only: unauthenticated calls 401,
    a parent session 403 — with no side effects."""
    phantom_school = str(uuid.uuid4())
    phantom_plan = str(uuid.uuid4())
    calls = [
        lambda h: client.post(
            "/api/fleet-plans/confirm-fleet",
            json={"school_id": phantom_school, "bus_ids": []},
            headers=h,
        ),
        lambda h: client.post(
            "/api/fleet-plans/draft", json={"school_id": phantom_school}, headers=h
        ),
        lambda h: client.get(
            "/api/fleet-plans/current", params={"school_id": phantom_school}, headers=h
        ),
        lambda h: client.post(f"/api/fleet-plans/{phantom_plan}/discard", headers=h),
    ]
    for call in calls:
        assert call(None).status_code == 401
        assert call(parent_headers).status_code == 403
