"""Fleet-plan review and edit surface (U5): computed wall-clock review payload,
constraint-gated edits (move / reorder / pin / pattern / assign), the shared
diff vs live with the notified-family count, and the explicit in-draft
re-solve.

Run with the stack up (scripts/start-local.sh):

    RUN_INTEGRATION=1 ../.venv/bin/python -m pytest tests/integration/test_fleet_plan_review.py -q

Covers R9/R10/R24 plus the R15 baseline semantics: AM stop times walk backward
from the gate anchor and PM forward (pure arithmetic — the keyless stack is
deterministic); a violating edit answers 422 NAMING the constraint ('capacity'
or 'stop cap') and provably leaves the draft document unchanged; a one-leg
move flips the document pattern to split; a pattern narrowed to morning_only
drops the PM stop and updates times; assign places an unplaceable child at an
explicit position on both legs; re-solve keeps pins, discards unpinned manual
arrangements and says so; the diff notifies on a bus change, a >=5-minute
move, and a missing baseline — never on a +3-minute drift or a
still-unplaceable child — and its family count is the DISTINCT parent count.

Entities are 'IT '-prefixed and cleaned up in finally blocks. Baselines are
seeded straight into live_communicated_stops via the sanctioned psycopg
pattern: nothing writes that table before apply (U6), and the diff thresholds
need a baseline to diff against. Rows cascade away with their students.
"""

import math
import os
import uuid

import httpx
import psycopg
import pytest

from conftest import purge_accounts, DSN, school_sandbox

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="needs the local stack; set RUN_INTEGRATION=1",
)

BASE = os.environ.get("INTEGRATION_API_URL", "http://localhost:9001")

ADMIN = {"email": "admin@test.com", "password": "test1234."}
PARENT = {"email": "and7005@gmail.com", "password": "Test1234"}

SCHOOL_LAT, SCHOOL_LNG = -1.3000, 36.8000
# Two loose geographic clusters east and west of the school, so a two-bus
# partition is geometrically unambiguous (test 7 relies on that stability).
EAST_HOMES = [(-1.290, 36.820), (-1.292, 36.824), (-1.288, 36.818), (-1.294, 36.822)]
WEST_HOMES = [(-1.310, 36.780), (-1.312, 36.776), (-1.308, 36.782), (-1.314, 36.778)]
DEPOT_EAST = (-1.285, 36.830)
DEPOT_WEST = (-1.320, 36.770)

MORNING, AFTERNOON = "morning", "afternoon"


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE, timeout=30) as c:
        yield c


def login(client: httpx.Client, email: str, password: str) -> dict:
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


# Post-U6 world provisioning: one sandbox school per module (its own single-
# membership admin); per-test worlds share it and purge their plan rows.
_SANDBOX: dict = {}


@pytest.fixture(scope="module")
def sandbox():
    with school_sandbox(
        "IT ReviewSchool", lat=SCHOOL_LAT, lng=SCHOOL_LNG,
        morning_bell="07:00", afternoon_bell="15:30",
    ) as sb:
        _SANDBOX.clear()
        _SANDBOX.update(sb)
        try:
            yield sb
        finally:
            _SANDBOX.clear()


@pytest.fixture(scope="module")
def admin_headers(client, sandbox):
    return login(client, sandbox["email"], sandbox["password"])


@pytest.fixture(scope="module")
def parent_headers(client):
    return login(client, PARENT["email"], PARENT["password"])


# --- builders -----------------------------------------------------------------

def _make_school(client, headers, marker: str, **overrides) -> dict:
    # Post-U6 there is ONE school per module — the sandbox (see apply suite).
    assert _SANDBOX, "sandbox fixture not active"
    return {"id": _SANDBOX["id"], "name": _SANDBOX["name"]}


def _make_bus(client, headers, name: str, *, capacity: int,
              depot: tuple[float, float] | None = None) -> dict:
    payload: dict = {"name": name, "capacity": capacity}
    if depot is not None:
        payload["depot_lat"], payload["depot_lng"] = depot
    created = client.post("/api/fleet/buses", json=payload, headers=headers)
    assert created.status_code == 200, created.text
    return created.json()


def _student_payload(marker: str, i: int, school_id: str,
                     home: tuple[float, float] | None,
                     email: str | None = None) -> dict:
    payload = {
        "name": f"IT Review Kid {marker} {i}",
        "parent_name": f"IT Review Parent {marker} {i}",
        "parent_phone": f"+2547120001{i:02d}",
        "parent_email": email or f"it-fpr-{marker}-{i}@test.local",
        "school_id": school_id,
        "route_ids": [],
    }
    if home is not None:
        payload["home_lat"], payload["home_lng"] = home
        payload["home_address"] = f"IT Review Home {marker} {i}"
    return payload


def _make_student(client, headers, marker: str, i: int, school_id: str,
                  home: tuple[float, float] | None, email: str | None = None) -> dict:
    created = client.post(
        "/api/students",
        json=_student_payload(marker, i, school_id, home, email),
        headers=headers,
    )
    assert created.status_code == 200, created.text
    return created.json()


def signup_parent(client, marker: str, tag: str) -> dict:
    """A fresh parent account; fresh email per run = fresh limiter budget."""
    email = f"it-fpr-{tag}-{marker}@test.local"
    response = client.post(
        "/api/auth/signup",
        json={"email": email, "password": "ParentPass1!",
              "full_name": f"IT FPR {tag} {marker}", "role": "parent"},
    )
    assert response.status_code == 200, response.text
    return {"id": response.json()["user"]["id"], "email": email}


def _confirm(client, headers, school_id: str, bus_ids: list[str]) -> None:
    confirmed = client.post(
        "/api/fleet-plans/confirm-fleet",
        json={"school_id": school_id, "bus_ids": bus_ids},
        headers=headers,
    )
    assert confirmed.status_code == 200, confirmed.text


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


def _current_document(client, headers, school_id: str) -> dict:
    got = client.get(
        "/api/fleet-plans/current", params={"school_id": school_id}, headers=headers
    )
    assert got.status_code == 200, got.text
    return got.json()["draft"]["document"]


def _edit(client, headers, plan_id: str, verb: str, payload: dict) -> httpx.Response:
    return client.post(f"/api/fleet-plans/{plan_id}/{verb}", json=payload, headers=headers)


# --- review readers -------------------------------------------------------------

def _placement(review: dict, student_id: str, leg: str):
    """(bus, stop) dicts of the student's stop on ``leg`` in the review, or
    (None, None)."""
    for bus in review["buses"]:
        for stop in bus["legs"][leg]["stops"]:
            if any(s["id"] == student_id for s in stop["students"]):
                return bus, stop
    return None, None


def _bus_of(review: dict, student_id: str, leg: str) -> str | None:
    bus, _ = _placement(review, student_id, leg)
    return bus["bus_id"] if bus else None


def _leg_keys(review: dict, bus_id: str, leg: str) -> list[str]:
    bus = next(b for b in review["buses"] if b["bus_id"] == bus_id)
    return [stop["key"] for stop in bus["legs"][leg]["stops"]]


def _ride_map(review: dict) -> dict:
    return {(r["student_id"], r["leg"]): r for r in review["ride_times"]}


def _minutes(hhmm: str) -> int:
    h, m = (int(x) for x in hhmm.split(":"))
    return h * 60 + m


def _shift(hhmm: str, minutes: int) -> str:
    total = (_minutes(hhmm) + minutes) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


def _diff_cats(review: dict) -> set:
    return {
        (row["student_id"], row["leg"], category)
        for row in review["diff"]["rows"]
        for category in row["categories"]
    }


def _seed_baseline(student_id: str, leg: str, *, name, lat, lng, time, bus_id=None):
    """Stage a communicated baseline no API can produce before apply (U6):
    the sanctioned psycopg pattern, write-scoped to this table only. Rows
    cascade away with their student."""
    with psycopg.connect(DSN, autocommit=True) as pg:
        pg.execute(
            "insert into live_communicated_stops "
            "(student_id, route_type, stop_name, stop_lat, stop_lng, scheduled_time, bus_id) "
            "values (%s, %s, %s, %s, %s, %s, %s) "
            "on conflict (student_id, route_type) do update set "
            "stop_name = excluded.stop_name, stop_lat = excluded.stop_lat, "
            "stop_lng = excluded.stop_lng, scheduled_time = excluded.scheduled_time, "
            "bus_id = excluded.bus_id",
            (student_id, leg, name, lat, lng, time, bus_id),
        )


def _seed_exact_baseline(review: dict, student_id: str, leg: str,
                         *, time_shift_min: int = 0, lat_shift: float = 0.0):
    """Baseline = the review's computed stop for (student, leg), optionally
    shifted in time (minutes) or place (degrees latitude)."""
    bus, stop = _placement(review, student_id, leg)
    assert stop is not None, f"student {student_id} is not placed on {leg}"
    _seed_baseline(
        student_id, leg,
        name=stop["name"],
        lat=stop["lat"] + lat_shift, lng=stop["lng"],
        time=_shift(stop["scheduled_time"], time_shift_min),
        bus_id=bus["bus_id"],
    )


# --- fixture plumbing ------------------------------------------------------------

def _build_plan(client, headers, marker: str, *, buses, homes, seed=0,
                emails=None) -> dict:
    """School + claimed buses + students + draft. ``buses`` is a list of
    (tag, capacity, depot) tuples; ``homes`` a list of (lat, lng) or None
    (coordinate-less); ``emails`` an optional aligned list of parent emails."""
    school = _make_school(client, headers, marker)
    bus_rows = [
        _make_bus(client, headers, f"IT ReviewBus {tag} {marker}",
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
    # The school is the module sandbox: sweep its plan rows, routes, runs
    # and audit rows by SQL (the retired school DELETE used to cascade them).
    with psycopg.connect(DSN, autocommit=True) as pg:
        for table in (
            "live_fleet_plans", "live_runs", "live_routes", "live_admin_audit",
        ):
            pg.execute(
                f"delete from {table} where school_id = %s",  # noqa: S608
                (fx["school"]["id"],),
            )


# --- review surface (R10) --------------------------------------------------------

def test_review_surface_wall_clock_times_and_first_communication(client, admin_headers):
    """The review payload carries per-child ride times with computed HH:MM
    stop times (AM backward from the gate anchor, PM forward), per-bus
    capacity use, total driving, per-leg unplaceable lists, and the diff —
    on which a school with no baselines is all first-communication rows with
    zero linked families."""
    marker = uuid.uuid4().hex[:6]
    fx = _build_plan(
        client, admin_headers, marker,
        buses=[("A", 4, DEPOT_EAST), ("B", 4, DEPOT_WEST)],
        homes=EAST_HOMES + WEST_HOMES, seed=42,
    )
    try:
        review = _review(client, admin_headers, fx["school"]["id"])
        assert review["anchors"] == {MORNING: "07:00", AFTERNOON: "15:30"}
        assert review["plan"]["degraded"] is True  # keyless stack

        rides = _ride_map(review)
        for s in fx["students"]:
            for leg in (MORNING, AFTERNOON):
                row = rides[(s["id"], leg)]
                ride = row["ride_seconds"]
                assert ride > 0
                t = row["scheduled_time"]
                if leg == MORNING:
                    # anchor - ride, floored to the minute: the pickup is never
                    # promised later than the drive allows.
                    assert _minutes("07:00") - _minutes(t) == math.ceil(ride / 60)
                else:
                    assert _minutes(t) - _minutes("15:30") == int(ride // 60)

        total = 0.0
        for bus in review["buses"]:
            for leg in (MORNING, AFTERNOON):
                use = bus["capacity_use"][leg]
                assert use["capacity"] == 4
                assert 0 < use["children"] <= 4
                assert use["children"] == bus["legs"][leg]["children"]
                total += bus["legs"][leg]["driving_seconds"]
                for stop in bus["legs"][leg]["stops"]:
                    assert stop["key"] and stop["scheduled_time"]
        assert abs(review["total_driving_seconds"] - total) < 1e-6
        assert abs(review["objective"][2] - total) < 1e-6
        assert review["unplaceable"] == {MORNING: [], AFTERNOON: []}

        # No baselines yet: every placed (student, leg) is a first
        # communication; nobody has a linked parent account.
        cats = _diff_cats(review)
        for s in fx["students"]:
            for leg in (MORNING, AFTERNOON):
                assert (s["id"], leg, "first-communication") in cats
        assert review["diff"]["notified_family_count"] == 0
    finally:
        _teardown(client, admin_headers, fx)


# --- constraint-gated edits (R9) ---------------------------------------------------

def test_move_over_capacity_names_constraint_and_leaves_document_unchanged(
    client, admin_headers
):
    marker = uuid.uuid4().hex[:6]
    fx = _build_plan(
        client, admin_headers, marker,
        buses=[("A", 4, DEPOT_EAST), ("B", 4, DEPOT_WEST)],
        homes=EAST_HOMES + WEST_HOMES,
    )
    try:
        plan_id = fx["plan"]["id"]
        review = _review(client, admin_headers, fx["school"]["id"])
        student = fx["students"][0]["id"]
        source = _bus_of(review, student, MORNING)
        target = next(b["bus_id"] for b in review["buses"] if b["bus_id"] != source)
        before = _current_document(client, admin_headers, fx["school"]["id"])

        blocked = _edit(client, admin_headers, plan_id, "move",
                        {"student_id": student, "to_bus_id": target})
        assert blocked.status_code == 422, blocked.text
        assert "capacity" in blocked.json()["detail"]

        after = _current_document(client, admin_headers, fx["school"]["id"])
        assert after == before  # the violating edit changed nothing
    finally:
        _teardown(client, admin_headers, fx)


def test_reorder_past_stop_cap_names_constraint(client, admin_headers):
    marker = uuid.uuid4().hex[:6]
    fx = _build_plan(
        client, admin_headers, marker,
        buses=[("A", 4, DEPOT_EAST)], homes=EAST_HOMES[:2],
    )
    try:
        plan_id = fx["plan"]["id"]
        review = _review(client, admin_headers, fx["school"]["id"])
        bus_id = review["buses"][0]["bus_id"]
        before = _current_document(client, admin_headers, fx["school"]["id"])

        # The echoed order IS the requested sequence: 25 stops break the cap.
        blocked = _edit(client, admin_headers, plan_id, "reorder", {
            "bus_id": bus_id, "leg": MORNING,
            "order": [f"-1.2{i:02d}000,36.800000" for i in range(25)],
        })
        assert blocked.status_code == 422, blocked.text
        assert "stop cap" in blocked.json()["detail"]

        after = _current_document(client, admin_headers, fx["school"]["id"])
        assert after == before
    finally:
        _teardown(client, admin_headers, fx)


def test_move_both_legs_by_default_one_leg_flips_pattern_split(client, admin_headers):
    marker = uuid.uuid4().hex[:6]
    fx = _build_plan(
        client, admin_headers, marker,
        buses=[("A", 6, DEPOT_EAST), ("B", 6, DEPOT_WEST)],
        homes=EAST_HOMES[:2] + WEST_HOMES[:2],
    )
    try:
        plan_id = fx["plan"]["id"]
        school_id = fx["school"]["id"]
        review = _review(client, admin_headers, school_id)
        student = fx["students"][0]["id"]
        source = _bus_of(review, student, MORNING)
        assert source == _bus_of(review, student, AFTERNOON)  # mirrored pair
        target = next(b["bus_id"] for b in review["buses"] if b["bus_id"] != source)

        moved = _edit(client, admin_headers, plan_id, "move",
                      {"student_id": student, "to_bus_id": target})
        assert moved.status_code == 200, moved.text
        review = _review(client, admin_headers, school_id)
        assert _bus_of(review, student, MORNING) == target
        assert _bus_of(review, student, AFTERNOON) == target  # both legs moved
        assert student not in review["patterns"]  # still both_ways

        # Explicit one-leg move: the legs now ride different buses -> split.
        moved = _edit(client, admin_headers, plan_id, "move",
                      {"student_id": student, "to_bus_id": source, "legs": [MORNING]})
        assert moved.status_code == 200, moved.text
        review = _review(client, admin_headers, school_id)
        assert _bus_of(review, student, MORNING) == source
        assert _bus_of(review, student, AFTERNOON) == target
        assert review["patterns"][student] == "split"
        assert review["document"]["patterns"][student] == "split"
    finally:
        _teardown(client, admin_headers, fx)


def test_pattern_change_to_morning_only_drops_pm_stop_and_updates_times(
    client, admin_headers
):
    marker = uuid.uuid4().hex[:6]
    fx = _build_plan(
        client, admin_headers, marker,
        buses=[("A", 6, DEPOT_EAST)], homes=EAST_HOMES[:3],
    )
    try:
        plan_id = fx["plan"]["id"]
        school_id = fx["school"]["id"]
        review = _review(client, admin_headers, school_id)
        student = fx["students"][0]["id"]
        bus_id = _bus_of(review, student, AFTERNOON)
        pm_driving_before = next(
            b for b in review["buses"] if b["bus_id"] == bus_id
        )["legs"][AFTERNOON]["driving_seconds"]

        changed = _edit(client, admin_headers, plan_id, "pattern",
                        {"student_id": student, "pattern": "morning_only"})
        assert changed.status_code == 200, changed.text

        review = _review(client, admin_headers, school_id)
        assert _bus_of(review, student, MORNING) is not None
        assert _bus_of(review, student, AFTERNOON) is None  # PM stop dropped
        rides = _ride_map(review)
        assert (student, MORNING) in rides
        assert (student, AFTERNOON) not in rides
        assert review["patterns"][student] == "morning_only"
        assert review["unplaceable"] == {MORNING: [], AFTERNOON: []}
        pm_driving_after = next(
            b for b in review["buses"] if b["bus_id"] == bus_id
        )["legs"][AFTERNOON]["driving_seconds"]
        assert pm_driving_after < pm_driving_before  # times recomputed
    finally:
        _teardown(client, admin_headers, fx)


def test_assign_places_unplaceable_child_at_position_on_both_legs(client, admin_headers):
    """5 children on 2x2 seats leave exactly one both-ways child unplaceable
    ('seats'). Narrowing one placed child per leg frees a seat on each leg of
    one bus; assign then places the unplaceable child there at position 0 on
    both legs and they leave the unplaceable list."""
    marker = uuid.uuid4().hex[:6]
    fx = _build_plan(
        client, admin_headers, marker,
        buses=[("A", 2, DEPOT_EAST), ("B", 2, DEPOT_WEST)],
        homes=EAST_HOMES[:3] + WEST_HOMES[:2],
    )
    try:
        plan_id = fx["plan"]["id"]
        school_id = fx["school"]["id"]
        review = _review(client, admin_headers, school_id)
        unplaceable = review["unplaceable"]
        assert len(unplaceable[MORNING]) == 1 and len(unplaceable[AFTERNOON]) == 1
        u = unplaceable[MORNING][0]
        assert u["constraint"] == "seats"
        assert unplaceable[AFTERNOON][0]["student_id"] == u["student_id"]

        # Free one AM seat and one PM seat on the same bus.
        bus = review["buses"][0]
        riders = [s["id"] for stop in bus["legs"][MORNING]["stops"]
                  for s in stop["students"]]
        assert len(riders) == 2
        for student, pattern in zip(riders, ("morning_only", "afternoon_only")):
            changed = _edit(client, admin_headers, plan_id, "pattern",
                            {"student_id": student, "pattern": pattern})
            assert changed.status_code == 200, changed.text

        assigned = _edit(client, admin_headers, plan_id, "assign", {
            "student_id": u["student_id"], "bus_id": bus["bus_id"], "position": 0,
        })
        assert assigned.status_code == 200, assigned.text

        review = _review(client, admin_headers, school_id)
        for leg in (MORNING, AFTERNOON):
            placed_bus, stop = _placement(review, u["student_id"], leg)
            assert placed_bus["bus_id"] == bus["bus_id"]
            leg_stops = placed_bus["legs"][leg]["stops"]
            assert leg_stops[0]["key"] == stop["key"]  # the explicit position
            assert (u["student_id"], leg) in _ride_map(review)
        assert review["unplaceable"] == {MORNING: [], AFTERNOON: []}
    finally:
        _teardown(client, admin_headers, fx)


# --- re-solve (R13) -----------------------------------------------------------------

def test_resolve_keeps_pinned_child_discards_unpinned_reorder_and_says_so(
    client, admin_headers
):
    marker = uuid.uuid4().hex[:6]
    fx = _build_plan(
        client, admin_headers, marker,
        buses=[("A", 4, DEPOT_EAST), ("B", 4, DEPOT_WEST)],
        homes=EAST_HOMES + WEST_HOMES, seed=11,
    )
    try:
        plan_id = fx["plan"]["id"]
        school_id = fx["school"]["id"]
        original = _review(client, admin_headers, school_id)
        student = fx["students"][0]["id"]
        bus_id = _bus_of(original, student, MORNING)
        original_order = _leg_keys(original, bus_id, MORNING)
        assert len(original_order) >= 2

        # Unpinned manual arrangement: reverse the leg's stop order.
        reordered = _edit(client, admin_headers, plan_id, "reorder", {
            "bus_id": bus_id, "leg": MORNING, "order": list(reversed(original_order)),
        })
        assert reordered.status_code == 200, reordered.text
        assert _leg_keys(
            _review(client, admin_headers, school_id), bus_id, MORNING
        ) == list(reversed(original_order))

        # Pin the child to their bus (both legs), then re-solve.
        pinned = _edit(client, admin_headers, plan_id, "pin",
                       {"student_id": student, "bus": bus_id})
        assert pinned.status_code == 200, pinned.text

        resolved = _edit(client, admin_headers, plan_id, "resolve", {})
        assert resolved.status_code == 200, resolved.text
        body = resolved.json()
        assert body["resolve"]["discarded_unpinned_arrangements"] is True
        assert "discarded" in body["resolve"]["message"]

        review = _review(client, admin_headers, school_id)
        # The pinned child stayed put; the manual reorder did not survive.
        assert _bus_of(review, student, MORNING) == bus_id
        assert _bus_of(review, student, AFTERNOON) == bus_id
        assert _leg_keys(review, bus_id, MORNING) == original_order
        assert student in review["pins"]  # pins survive the re-solve
    finally:
        _teardown(client, admin_headers, fx)


# --- diff vs live (R24 / R15) ---------------------------------------------------------

def test_diff_bus_change_time_thresholds_baselines_and_family_count(
    client, admin_headers
):
    """Against live routes and seeded baselines: a bus change and a +6-minute
    move appear, a +3-minute move does not, a missing baseline notifies
    (first communication), a child unplaceable with neither membership nor
    baseline stays silent, and the notified-family count is the DISTINCT
    parent count over the affected children."""
    marker = uuid.uuid4().hex[:6]
    p24 = signup_parent(client, marker, "p24")  # shared by S2 and S4
    p5 = signup_parent(client, marker, "p5")
    p7 = signup_parent(client, marker, "p7")
    emails = [None, p24["email"], None, p24["email"], p5["email"], None, p7["email"]]
    homes = [EAST_HOMES[0], EAST_HOMES[1], EAST_HOMES[2], EAST_HOMES[3],
             WEST_HOMES[0], None, WEST_HOMES[1]]
    fx = _build_plan(
        client, admin_headers, marker,
        buses=[("A", 8, DEPOT_EAST), ("B", 8, DEPOT_WEST)],
        homes=homes, emails=emails,
    )
    s1, s2, s3, s4, s5, s6, s7 = (s["id"] for s in fx["students"])
    bus_c = route_c = None
    try:
        # Live membership for S5 on a THIRD bus the plan does not know:
        # document bus != live bus -> bus-change, whatever the solver chose.
        bus_c = _make_bus(client, admin_headers, f"IT ReviewBus C {marker}", capacity=8)
        route_c = client.post(
            "/api/fleet/routes",
            json={"name": f"IT Review Live {marker}", "type": MORNING,
                  "bus_id": bus_c["id"], "school_id": fx["school"]["id"]},
            headers=admin_headers,
        ).json()
        updated = client.put(
            f"/api/students/{s5}",
            json={**_student_payload(marker, 4, fx["school"]["id"], WEST_HOMES[0],
                                     email=p5["email"]),
                  "route_ids": [route_c["id"]]},
            headers=admin_headers,
        )
        assert updated.status_code == 200, updated.text

        review = _review(client, admin_headers, fx["school"]["id"])
        # S6 has no coordinates: unplaceable both legs, no membership, no
        # baseline -> must stay silent.
        assert {u["student_id"] for u in review["unplaceable"][MORNING]} == {s6}

        for leg in (MORNING, AFTERNOON):
            _seed_exact_baseline(review, s1, leg)                      # untouched
            _seed_exact_baseline(review, s5, leg)                      # bus change only
        _seed_exact_baseline(review, s2, MORNING, time_shift_min=-6)   # +6 min move
        _seed_exact_baseline(review, s2, AFTERNOON)
        _seed_exact_baseline(review, s3, MORNING, time_shift_min=-3)   # +3 min drift
        _seed_exact_baseline(review, s3, AFTERNOON)
        _seed_exact_baseline(review, s7, MORNING, lat_shift=0.01)      # place change
        _seed_exact_baseline(review, s7, AFTERNOON)
        # S4: no baseline at all -> first communication.

        review = _review(client, admin_headers, fx["school"]["id"])
        cats = _diff_cats(review)
        rows_by_student: dict[str, list] = {}
        for row in review["diff"]["rows"]:
            rows_by_student.setdefault(row["student_id"], []).append(row)

        assert (s2, MORNING, "time-move") in cats
        assert (s4, MORNING, "first-communication") in cats
        assert (s4, AFTERNOON, "first-communication") in cats
        assert (s5, MORNING, "bus-change") in cats
        assert (s7, MORNING, "place-change") in cats

        assert s1 not in rows_by_student          # exact baseline, no membership
        assert s3 not in rows_by_student          # +3 min is below the threshold
        assert s6 not in rows_by_student          # still-unplaceable stays silent
        assert all(r["leg"] == MORNING for r in rows_by_student[s2])
        assert all(r["leg"] == MORNING for r in rows_by_student[s5])
        assert all(r["leg"] == MORNING for r in rows_by_student[s7])

        # Distinct families: S2 and S4 share one parent; S5 and S7 have one
        # each; S2's morning row alone never double-counts the household.
        assert set(review["diff"]["notified_families"]) == {
            p24["id"], p5["id"], p7["id"]
        }
        assert review["diff"]["notified_family_count"] == 3
    finally:
        if route_c:
            client.delete(f"/api/fleet/routes/{route_c['id']}", headers=admin_headers)
        if bus_c:
            client.delete(f"/api/fleet/buses/{bus_c['id']}", headers=admin_headers)
        _teardown(client, admin_headers, fx)
        for p in (p24, p5, p7):
            purge_accounts(p['id'])


def test_diff_newly_unplaceable_and_leg_removed(client, admin_headers):
    """A child with a baseline who becomes unplaceable notifies (per leg with
    live knowledge only); a pattern narrowed in review makes the dropped
    leg's baseline a leg-removed row."""
    marker = uuid.uuid4().hex[:6]
    fx = _build_plan(
        client, admin_headers, marker,
        buses=[("A", 2, DEPOT_EAST)], homes=EAST_HOMES[:3],
    )
    try:
        plan_id = fx["plan"]["id"]
        school_id = fx["school"]["id"]
        review = _review(client, admin_headers, school_id)
        u = review["unplaceable"][MORNING][0]["student_id"]
        placed = [s["id"] for s in fx["students"] if s["id"] != u]
        y = placed[0]

        # U was told a morning stop once; Y's baselines match the plan.
        _seed_baseline(u, MORNING, name="IT Old Stop", lat=EAST_HOMES[0][0],
                       lng=EAST_HOMES[0][1], time="06:45")
        for leg in (MORNING, AFTERNOON):
            _seed_exact_baseline(review, y, leg)

        changed = _edit(client, admin_headers, plan_id, "pattern",
                        {"student_id": y, "pattern": "morning_only"})
        assert changed.status_code == 200, changed.text

        review = _review(client, admin_headers, school_id)
        cats = _diff_cats(review)
        assert (u, MORNING, "newly-unplaceable") in cats
        assert not any(sid == u and leg == AFTERNOON for sid, leg, _ in cats)
        assert (y, AFTERNOON, "leg-removed") in cats
        assert not any(sid == y and leg == MORNING for sid, leg, _ in cats)
    finally:
        _teardown(client, admin_headers, fx)


def test_diff_place_change_is_physical_not_nominal(client, admin_headers):
    """The place-change diff compares PLACES, not display names: a baseline
    carrying the address-label name at the child's exact document coordinates
    stays silent although the document stop shows a different (solver) name,
    and a baseline jittered WITHIN the solver's 30 m collapse radius stays
    silent too — while a genuine move far beyond it still fires (pinned by
    test_diff_bus_change_time_thresholds_baselines_and_family_count)."""
    marker = uuid.uuid4().hex[:6]
    fx = _build_plan(
        client, admin_headers, marker,
        buses=[("A", 8, DEPOT_EAST)],
        homes=[EAST_HOMES[0], EAST_HOMES[1]],
    )
    s1, s2 = (s["id"] for s in fx["students"])
    try:
        review = _review(client, admin_headers, fx["school"]["id"])
        bus, stop = _placement(review, s1, MORNING)
        assert stop is not None
        # S1: same coords, same time, same bus — but the ADDRESS-LABEL name
        # (what apply's baselines carry), not the document's display name.
        _seed_baseline(
            s1, MORNING,
            name=f"IT Review Home {marker} 0",
            lat=stop["lat"], lng=stop["lng"],
            time=stop["scheduled_time"], bus_id=bus["bus_id"],
        )
        _seed_exact_baseline(review, s1, AFTERNOON)
        # S2: coordinates jittered ~22 m — inside the collapse radius, the
        # same physical stop.
        _seed_exact_baseline(review, s2, MORNING, lat_shift=0.0002)
        _seed_exact_baseline(review, s2, AFTERNOON)

        review = _review(client, admin_headers, fx["school"]["id"])
        assert not any(
            row["student_id"] in (s1, s2) for row in review["diff"]["rows"]
        ), review["diff"]["rows"]
    finally:
        _teardown(client, admin_headers, fx)


# --- basis drift on the read path (R22) --------------------------------------------

def test_review_carries_basis_drift_rows(client, admin_headers):
    """The review payload's `basis_drift` is the same enrolled/address-changed
    /departed list apply's gate will demand confirmations for — computed by
    the shared helper, so a caller can assemble the apply payload without a
    blind POST."""
    marker = uuid.uuid4().hex[:6]
    fx = _build_plan(
        client, admin_headers, marker,
        buses=[("A", 8, DEPOT_EAST)],
        homes=[EAST_HOMES[0], EAST_HOMES[1]],
    )
    school_id = fx["school"]["id"]
    moved = fx["students"][1]
    enrolled = None
    try:
        review = _review(client, admin_headers, school_id)
        assert review["basis_drift"] == []

        enrolled = _make_student(client, admin_headers, marker, 9, school_id,
                                 WEST_HOMES[0])
        addressed = client.put(
            f"/api/students/{moved['id']}",
            json=_student_payload(marker, 1, school_id, WEST_HOMES[1]),
            headers=admin_headers,
        )
        assert addressed.status_code == 200, addressed.text

        drift = _review(client, admin_headers, school_id)["basis_drift"]
        assert {
            "kind": "enrolled", "student_id": enrolled["id"],
            "name": enrolled["name"],
        } in drift
        assert {
            "kind": "address-changed", "student_id": moved["id"],
            "name": moved["name"],
        } in drift
        assert len(drift) == 2
    finally:
        if enrolled:
            client.delete(f"/api/students/{enrolled['id']}", headers=admin_headers)
        _teardown(client, admin_headers, fx)


# --- keyless determinism ---------------------------------------------------------------

def test_keyless_recompute_is_deterministic(client, admin_headers):
    """Two identical edits produce identical computed times: the degraded
    recompute path is pure arithmetic (same constants as the solver matrix)."""
    marker = uuid.uuid4().hex[:6]
    fx = _build_plan(
        client, admin_headers, marker,
        buses=[("A", 4, DEPOT_EAST), ("B", 4, DEPOT_WEST)],
        homes=EAST_HOMES[:2] + WEST_HOMES[:2],
    )
    try:
        plan_id = fx["plan"]["id"]
        school_id = fx["school"]["id"]
        review = _review(client, admin_headers, school_id)
        student = fx["students"][0]["id"]
        source = _bus_of(review, student, MORNING)
        target = next(b["bus_id"] for b in review["buses"] if b["bus_id"] != source)

        def snapshot() -> tuple:
            r = _review(client, admin_headers, school_id)
            return (
                [(b["bus_id"], leg, tuple(
                    (s["key"], s["scheduled_time"], s["ride_seconds"])
                    for s in b["legs"][leg]["stops"]
                )) for b in r["buses"] for leg in (MORNING, AFTERNOON)],
                r["total_driving_seconds"],
            )

        assert _edit(client, admin_headers, plan_id, "move",
                     {"student_id": student, "to_bus_id": target}).status_code == 200
        first = snapshot()
        assert _edit(client, admin_headers, plan_id, "move",
                     {"student_id": student, "to_bus_id": source}).status_code == 200
        assert _edit(client, admin_headers, plan_id, "move",
                     {"student_id": student, "to_bus_id": target}).status_code == 200
        assert snapshot() == first
    finally:
        _teardown(client, admin_headers, fx)


# --- authorization -----------------------------------------------------------------

def test_non_admin_refused_on_every_review_endpoint(client, admin_headers, parent_headers):
    """Every U5 endpoint is admin-only: unauthenticated calls 401, a parent
    session 403 — with no side effects."""
    phantom_school = str(uuid.uuid4())
    phantom_plan = str(uuid.uuid4())
    phantom = str(uuid.uuid4())
    calls = [
        lambda h: client.get(
            "/api/fleet-plans/review", params={"school_id": phantom_school}, headers=h
        ),
        lambda h: client.post(
            f"/api/fleet-plans/{phantom_plan}/move",
            json={"student_id": phantom, "to_bus_id": phantom}, headers=h,
        ),
        lambda h: client.post(
            f"/api/fleet-plans/{phantom_plan}/reorder",
            json={"bus_id": phantom, "leg": MORNING, "order": []}, headers=h,
        ),
        lambda h: client.post(
            f"/api/fleet-plans/{phantom_plan}/pin",
            json={"student_id": phantom, "bus": phantom}, headers=h,
        ),
        lambda h: client.post(
            f"/api/fleet-plans/{phantom_plan}/pattern",
            json={"student_id": phantom, "pattern": "morning_only"}, headers=h,
        ),
        lambda h: client.post(
            f"/api/fleet-plans/{phantom_plan}/assign",
            json={"student_id": phantom, "bus_id": phantom}, headers=h,
        ),
        lambda h: client.post(f"/api/fleet-plans/{phantom_plan}/resolve", headers=h),
    ]
    for call in calls:
        assert call(None).status_code == 401
        assert call(parent_headers).status_code == 403
