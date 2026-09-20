"""Fleet-plan drafting (U4, rescoped by U6): fleet confirmation, draft
generation, one-open-draft, scrub-on-supersede/discard, and
read-only-against-live-routes.

Runs the real ``create_app()`` in-process (TestClient) against the local
database — the U6 conversion under test lives in this working tree. Buses are
school-owned since U6, so confirm-fleet no longer claims or releases
anything: it validates that every selected bus is the active school's own
(a foreign or unknown id answers 404) and reports per-bus notices
(multi-trip excluded, depot-less proceeds).

The sandbox school comes from ``temp_school`` (SQL — school creation has no
staff API surface any more) with the seeded director.a granted a director
membership there; every request pins it via ``X-School-Id``. Entities are
'IT '-prefixed and cleaned up in finally blocks; plan rows are purged per
test (superseded/discarded rows have no API surface by design).
"""

import os
import uuid

import psycopg
import pytest

from conftest import DIRECTOR_A, DSN, TEST_PASSWORD, temp_school

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="needs the local stack; set RUN_INTEGRATION=1",
)

PARENT_EMAIL = "and7005@gmail.com"

SCHOOL_LAT, SCHOOL_LNG = -1.3000, 36.8000
# Two loose geographic clusters east and west of the school, so a two-bus
# partition is geometrically natural.
EAST_HOMES = [(-1.290, 36.820), (-1.292, 36.824), (-1.288, 36.818), (-1.294, 36.822)]
WEST_HOMES = [(-1.310, 36.780), (-1.312, 36.776), (-1.308, 36.782), (-1.314, 36.778)]
DEPOT_EAST = (-1.285, 36.830)
DEPOT_WEST = (-1.320, 36.770)


@pytest.fixture(scope="module")
def client(in_process_db):
    from fastapi.testclient import TestClient

    from app.main import create_app

    return TestClient(create_app())


@pytest.fixture(scope="module")
def school(in_process_db):
    marker = uuid.uuid4().hex[:6]
    with temp_school(
        f"IT PlanSchool {marker}", lat=SCHOOL_LAT, lng=SCHOOL_LNG
    ) as school_id:
        yield school_id


def login(client, email: str, password: str = TEST_PASSWORD) -> dict:
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


@pytest.fixture(scope="module")
def staff_headers(client, school):
    return {**login(client, DIRECTOR_A), "X-School-Id": school}


@pytest.fixture(scope="module")
def parent_headers(client):
    return login(client, PARENT_EMAIL)


# --- builders -----------------------------------------------------------------

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


def _make_student(client, headers, marker: str, i: int,
                  home: tuple[float, float] | None,
                  route_ids: list[str] | None = None) -> dict:
    payload = {
        "name": f"IT Plan Kid {marker} {i}",
        "parent_name": f"IT Plan Parent {marker} {i}",
        "parent_phone": f"+2547110001{i:02d}",
        "parent_email": f"it-fp-{marker}-{i}@test.local",
        "route_ids": route_ids or [],
    }
    if home is not None:
        payload["home_lat"], payload["home_lng"] = home
        payload["home_address"] = f"IT Plan Home {marker} {i}"
    created = client.post("/api/students", json=payload, headers=headers)
    assert created.status_code == 200, created.text
    return created.json()


def _confirm(client, headers, bus_ids: list[str]):
    return client.post(
        "/api/fleet-plans/confirm-fleet", json={"bus_ids": bus_ids}, headers=headers
    )


def _draft(client, headers, school_id: str, **kw):
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


def _purge_plans(school_id: str) -> None:
    """Teardown only: drop the sandbox school's plan rows so the next test's
    one-open-draft rule starts clean."""
    with psycopg.connect(DSN, autocommit=True) as pg:
        pg.execute("delete from live_fleet_plans where school_id = %s", (school_id,))


def _leg_student_ids(bus_doc: dict, leg: str) -> set[str]:
    return {
        s["id"] for stop in bus_doc["legs"][leg]["stops"] for s in stop["students"]
    }


# --- confirm-fleet ------------------------------------------------------------

def test_confirm_fleet_validates_selection_and_reports_notices(
    client, school, staff_headers
):
    """U6 contract: no claims, no releases — a depot-less bus proceeds with a
    notice, a multi-trip bus is excluded from drafting with a named notice,
    deselection changes nothing, and an id the school does not own (foreign
    and nonexistent are indistinguishable) answers 404."""
    marker = uuid.uuid4().hex[:6]
    bus_a = _make_bus(client, staff_headers, f"IT PlanBus A {marker}", depot=DEPOT_EAST)
    bus_b = _make_bus(client, staff_headers, f"IT PlanBus B {marker}")  # no depot
    bus_c = _make_bus(client, staff_headers, f"IT PlanBus C {marker}", depot=DEPOT_WEST)
    chain_route = client.post(
        "/api/fleet/routes",
        json={"name": f"IT Plan Chain {marker}", "type": "morning",
              "bus_id": bus_c["id"], "trip_index": 2},
        headers=staff_headers,
    ).json()
    try:
        confirmed = _confirm(
            client, staff_headers, [bus_a["id"], bus_b["id"], bus_c["id"]]
        )
        assert confirmed.status_code == 200, confirmed.text
        body = confirmed.json()
        assert body["school_id"] == school
        assert body["released"] == []

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

        # Deselection releases nothing: ownership is creation-time now.
        reconfirmed = _confirm(client, staff_headers, [bus_a["id"]])
        assert reconfirmed.status_code == 200, reconfirmed.text
        assert reconfirmed.json()["released"] == []
        for bus in (bus_a, bus_b, bus_c):
            assert _bus_row(client, staff_headers, bus["id"])["school_id"] == school

        # An id this school does not own: 404, whole selection refused.
        phantom = str(uuid.uuid4())
        refused = _confirm(client, staff_headers, [bus_a["id"], phantom])
        assert refused.status_code == 404, refused.text
        assert phantom in refused.json()["detail"]
    finally:
        client.delete(f"/api/fleet/routes/{chain_route['id']}", headers=staff_headers)
        for bus in (bus_a, bus_b, bus_c):
            client.delete(f"/api/fleet/buses/{bus['id']}", headers=staff_headers)


# --- draft generation ---------------------------------------------------------

def test_draft_happy_path_two_mirrored_pairs(client, school, staff_headers):
    """AE1 shape: two capacity-legal mirrored pairs, per-child ride seconds
    present, basis snapshot populated, degraded flagged on the keyless stack."""
    marker = uuid.uuid4().hex[:6]
    bus_a = _make_bus(client, staff_headers, f"IT PlanBus A {marker}",
                      capacity=4, depot=DEPOT_EAST)
    bus_b = _make_bus(client, staff_headers, f"IT PlanBus B {marker}",
                      capacity=4, depot=DEPOT_WEST)
    students = []
    try:
        for i, home in enumerate(EAST_HOMES + WEST_HOMES):
            students.append(_make_student(client, staff_headers, marker, i, home))
        assert _confirm(
            client, staff_headers, [bus_a["id"], bus_b["id"]]
        ).status_code == 200

        drafted = _draft(client, staff_headers, school, seed=42)
        assert drafted.status_code == 200, drafted.text
        plan = drafted.json()
        assert plan["status"] == "draft"
        assert plan["school_id"] == school
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
        assert {s["id"] for s in basis["students"]} == all_ids
        assert all(s["plannable"] for s in basis["students"])
        assert all(s["name"].startswith("IT Plan Kid") for s in basis["students"])
        assert {b["id"] for b in basis["fleet"]} == {bus_a["id"], bus_b["id"]}
        assert all(b["capacity"] == 4 for b in basis["fleet"])
        assert basis["pins"] == []
        assert basis["excluded_buses"] == []

        # GET current returns the stored draft as-is; no applied/previous yet.
        current = client.get(
            "/api/fleet-plans/current", params={"school_id": school},
            headers=staff_headers,
        )
        assert current.status_code == 200, current.text
        assert current.json()["draft"]["id"] == plan["id"]
        assert current.json()["applied"] is None
        assert current.json()["previous"] is None
    finally:
        for s in students:
            client.delete(f"/api/students/{s['id']}", headers=staff_headers)
        client.delete(f"/api/fleet/buses/{bus_a['id']}", headers=staff_headers)
        client.delete(f"/api/fleet/buses/{bus_b['id']}", headers=staff_headers)
        _purge_plans(school)


def test_draft_excludes_multi_trip_bus_and_keeps_depotless(
    client, school, staff_headers
):
    """A multi-trip bus is absent from the drafted document and named in the
    basis exclusion list; a depot-less bus drafts normally."""
    marker = uuid.uuid4().hex[:6]
    bus_chain = _make_bus(client, staff_headers, f"IT PlanBus M {marker}", depot=DEPOT_EAST)
    bus_plain = _make_bus(client, staff_headers, f"IT PlanBus N {marker}", capacity=10)
    chain_route = client.post(
        "/api/fleet/routes",
        json={"name": f"IT Plan Chain {marker}", "type": "morning",
              "bus_id": bus_chain["id"], "trip_index": 2},
        headers=staff_headers,
    ).json()
    students = []
    try:
        for i, home in enumerate(EAST_HOMES[:2]):
            students.append(_make_student(client, staff_headers, marker, i, home))
        assert _confirm(
            client, staff_headers, [bus_chain["id"], bus_plain["id"]]
        ).status_code == 200

        drafted = _draft(client, staff_headers, school)
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
            client.delete(f"/api/students/{s['id']}", headers=staff_headers)
        client.delete(f"/api/fleet/routes/{chain_route['id']}", headers=staff_headers)
        client.delete(f"/api/fleet/buses/{bus_chain['id']}", headers=staff_headers)
        client.delete(f"/api/fleet/buses/{bus_plain['id']}", headers=staff_headers)
        _purge_plans(school)


def test_draft_is_read_only_against_live_routes(client, school, staff_headers):
    """R8: generation never alters live routes — the school's route payload
    (rows, ordering, times, flags) is byte-identical before and after."""
    marker = uuid.uuid4().hex[:6]
    bus = _make_bus(client, staff_headers, f"IT PlanBus L {marker}", depot=DEPOT_EAST)
    route = client.post(
        "/api/fleet/routes",
        json={"name": f"IT Plan Live {marker}", "type": "morning",
              "bus_id": bus["id"], "school_id": school},
        headers=staff_headers,
    ).json()
    students = []
    try:
        for i, home in enumerate(EAST_HOMES[:3]):
            students.append(
                _make_student(client, staff_headers, marker, i, home,
                              route_ids=[route["id"]])
            )
        assert _confirm(client, staff_headers, [bus["id"]]).status_code == 200

        def route_snapshot() -> dict:
            routes = client.get("/api/fleet/routes", headers=staff_headers).json()
            return next(r for r in routes if r["id"] == route["id"])

        before = route_snapshot()
        assert len(before["route_stops"]) > 0  # the baseline is a real route

        drafted = _draft(client, staff_headers, school)
        assert drafted.status_code == 200, drafted.text
        document = drafted.json()["document"]
        assert any(_leg_student_ids(b, "morning") for b in document["buses"])

        after = route_snapshot()
        assert after == before  # row count, ordering, times, flags — identical
    finally:
        for s in students:
            client.delete(f"/api/students/{s['id']}", headers=staff_headers)
        client.delete(f"/api/fleet/routes/{route['id']}", headers=staff_headers)
        client.delete(f"/api/fleet/buses/{bus['id']}", headers=staff_headers)
        _purge_plans(school)


# --- one-open-draft / scrub ---------------------------------------------------

def test_second_draft_conflicts_then_supersede_scrubs(client, school, staff_headers):
    """One-open-draft: a second draft 409s naming the open one; with
    supersede the old row flips to superseded and its document/basis are
    scrubbed to NULL with metadata kept."""
    marker = uuid.uuid4().hex[:6]
    bus = _make_bus(client, staff_headers, f"IT PlanBus S {marker}",
                    capacity=4, depot=DEPOT_EAST)
    students = []
    try:
        for i, home in enumerate(EAST_HOMES[:2]):
            students.append(_make_student(client, staff_headers, marker, i, home))
        assert _confirm(client, staff_headers, [bus["id"]]).status_code == 200

        first = _draft(client, staff_headers, school, seed=7)
        assert first.status_code == 200, first.text
        first_id = first.json()["id"]

        blocked = _draft(client, staff_headers, school)
        assert blocked.status_code == 409, blocked.text
        assert first_id in blocked.json()["detail"]

        superseded = _draft(client, staff_headers, school, supersede=True)
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
            "/api/fleet-plans/current", params={"school_id": school},
            headers=staff_headers,
        ).json()
        assert current["draft"]["id"] == second_id
    finally:
        for s in students:
            client.delete(f"/api/students/{s['id']}", headers=staff_headers)
        client.delete(f"/api/fleet/buses/{bus['id']}", headers=staff_headers)
        _purge_plans(school)


def test_discard_scrubs_draft(client, school, staff_headers):
    """Discard flips a draft to discarded and scrubs its payloads; a second
    discard 409s (draft only)."""
    marker = uuid.uuid4().hex[:6]
    bus = _make_bus(client, staff_headers, f"IT PlanBus D {marker}",
                    capacity=4, depot=DEPOT_WEST)
    students = []
    try:
        for i, home in enumerate(WEST_HOMES[:2]):
            students.append(_make_student(client, staff_headers, marker, i, home))
        assert _confirm(client, staff_headers, [bus["id"]]).status_code == 200
        drafted = _draft(client, staff_headers, school, seed=3)
        assert drafted.status_code == 200, drafted.text
        plan_id = drafted.json()["id"]

        discarded = client.post(
            f"/api/fleet-plans/{plan_id}/discard", headers=staff_headers
        )
        assert discarded.status_code == 200, discarded.text
        assert discarded.json()["status"] == "discarded"

        row = _plan_db_row(plan_id)
        assert row["status"] == "discarded"
        assert row["document"] is None
        assert row["basis"] is None
        assert row["solver_seed"] == 3  # metadata kept

        current = client.get(
            "/api/fleet-plans/current", params={"school_id": school},
            headers=staff_headers,
        ).json()
        assert current["draft"] is None

        again = client.post(
            f"/api/fleet-plans/{plan_id}/discard", headers=staff_headers
        )
        assert again.status_code == 409, again.text
    finally:
        for s in students:
            client.delete(f"/api/students/{s['id']}", headers=staff_headers)
        client.delete(f"/api/fleet/buses/{bus['id']}", headers=staff_headers)
        _purge_plans(school)


# --- unresolved addresses -----------------------------------------------------

def test_coordless_student_listed_unplaceable_unresolved_address(
    client, school, staff_headers
):
    """A student without home coordinates is not solver input: they appear in
    the unplaceable list with constraint 'unresolved address' for each leg,
    and in the basis as non-plannable."""
    marker = uuid.uuid4().hex[:6]
    bus = _make_bus(client, staff_headers, f"IT PlanBus U {marker}",
                    capacity=4, depot=DEPOT_EAST)
    placed = nogeo = None
    try:
        placed = _make_student(client, staff_headers, marker, 0, EAST_HOMES[0])
        nogeo = _make_student(client, staff_headers, marker, 1, None)
        assert _confirm(client, staff_headers, [bus["id"]]).status_code == 200

        drafted = _draft(client, staff_headers, school)
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
                client.delete(f"/api/students/{s['id']}", headers=staff_headers)
        client.delete(f"/api/fleet/buses/{bus['id']}", headers=staff_headers)
        _purge_plans(school)


# --- authorization ------------------------------------------------------------

def test_non_staff_refused_on_every_endpoint(client, school, parent_headers):
    """Unauthenticated calls 401 everywhere. A parent session: 403 on the
    school-scoped confirm-fleet (no membership, no header) and 404 when they
    name a school they cannot access; 403 on the still-admin-guarded plan
    endpoints (U7 converts those) — with no side effects."""
    phantom_school = str(uuid.uuid4())
    phantom_plan = str(uuid.uuid4())

    confirm = lambda h: client.post(  # noqa: E731
        "/api/fleet-plans/confirm-fleet", json={"bus_ids": []}, headers=h
    )
    assert confirm(None).status_code == 401
    assert confirm(parent_headers).status_code == 403
    named = client.post(
        "/api/fleet-plans/confirm-fleet",
        json={"bus_ids": []},
        headers={**parent_headers, "X-School-Id": school},
    )
    assert named.status_code == 404  # a school they cannot access: not found

    for call in (
        lambda h: client.post(
            "/api/fleet-plans/draft", json={"school_id": phantom_school}, headers=h
        ),
        lambda h: client.get(
            "/api/fleet-plans/current", params={"school_id": phantom_school}, headers=h
        ),
        lambda h: client.post(f"/api/fleet-plans/{phantom_plan}/discard", headers=h),
    ):
        assert call(None).status_code == 401
        assert call(parent_headers).status_code == 403
