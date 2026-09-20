"""The position read model: one fragment, every audience (GPS plan U8).

Run with the stack up (scripts/start-local.sh, then scripts/sync-api.sh):

    RUN_INTEGRATION=1 ../.venv/bin/python -m pytest tests/integration/test_parent_track_position.py -q

Covers R7, R9, R20 (the derived no-GPS marker), R22, R27 (the derivation
half), R37 and AE10/AE11/AE13 (Phase 1) of the origin document:

- parity: one bus read through the staff list, the parent Track and the
  parent children/profile endpoints is the same coordinates and time; the
  staff payload carries the source, freshness and the run's GPS state, both
  parent payloads exactly {lat, lng, position_at, stale} and nothing that
  could name a fix, an accuracy or an exception; the five write-side
  columns no longer travel;
- a run with taps and no fixes derives `no_gps_for_run`; a latest tap with a
  device reason derives `gps_off`; the first fix clears both with no write
  (nothing in the schema stores either);
- staleness: a position older than the threshold reads stale (age counted),
  a new fix clears it, and the legacy null-source shape is never stale;
- the parent readers resolve the bus through today's run the child is ON:
  a cross-bus afternoon rider's parent sees the afternoon bus, not the home
  bus; a parent with children at two schools sees each child's bus; a parent
  with no link sees nothing;
- retention: trail rows older than the school's retention count toward
  neither `gps_off` nor `no_gps_for_run`.

Isolation: an own throwaway school with two drivers, two buses, students A
(morning on bus 1, afternoon on bus 2 — the cross-bus rider) and B (both on
bus 1), and one parent account per child. The two-school case uses the
seeded cross-school parent and both seeded drivers; every run started here
is purged in a finally block and the bus's served position nulled with it
(purge_run deletes the run, not the pair End Run would have cleared).
"""

import os
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import psycopg
import pytest
from psycopg.rows import dict_row

from conftest import DSN, purge_accounts, purge_run, school_sandbox
from test_gps_actions import (
    _create_driver,
    arrive,
    action,
    end_run,
    fix,
    iso,
    login,
    pin_login,
    start,
)
from test_students_parents import signup_parent

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="needs the local stack; set RUN_INTEGRATION=1",
)

BASE = os.environ.get("INTEGRATION_API_URL", "http://localhost:9001")
SCHOOL_LAT, SCHOOL_LNG = -1.30, 36.80

STAFF_FIELDS = {
    "lat", "lng", "source", "position_at", "accuracy_m", "age_s", "stale",
    "gps_off", "no_gps_for_run", "label",
}
PARENT_FIELDS = {"lat", "lng", "position_at", "stale"}
RAW_COLUMNS = (
    "current_lat", "current_lng", "position_source", "position_at", "position_accuracy_m",
)
# Key fragments that must never appear anywhere in a parent payload (R22, R37).
FORBIDDEN_PARENT_KEYS = (
    "source", "accuracy", "exception", "gps", "age_s", "fix", "current_lat", "current_lng",
    "position_label", "position_state",
)

# Seeded identities (backend/db/seeds/003_local_snapshot.sql).
AMINA = {"email": "and7005@gmail.com", "password": "Test1234"}
ADMIN_A = {"email": "admin@test.com", "password": "test1234."}
DIRECTOR_B = {"email": "director.b@saferide.test", "password": "Test1234"}
DRIVER_A_PIN = "0322"
DRIVER_B_PIN = "7391"
FAITH, BEN, GRACE = "Faith Achieng", "Ben Barasa", "Grace Njeri"
SIMBA, BUS_B = "Simba", "IT Bus B"


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE, timeout=30) as c:
        yield c


def db():
    return psycopg.connect(DSN, autocommit=True, row_factory=dict_row)


@pytest.fixture(scope="module")
def sandbox():
    with school_sandbox("IT POS School", lat=SCHOOL_LAT, lng=SCHOOL_LNG) as sb:
        yield sb


@pytest.fixture(scope="module")
def admin_headers(client, sandbox):
    return login(client, sandbox["email"], sandbox["password"])


def _accept_pending(client, parent_headers) -> None:
    pending = client.get("/api/parent-portal/pending", headers=parent_headers)
    for card in (pending.json() if pending.status_code == 200 else []):
        accepted = client.post(
            f"/api/parent-portal/pending/{card['schoolId']}/accept", headers=parent_headers,
        )
        assert accepted.status_code == 200, accepted.text


@pytest.fixture(scope="module")
def fleet(client, admin_headers, sandbox):
    marker = uuid.uuid4().hex[:6]
    driver1 = _create_driver(client, admin_headers, marker, 1)
    driver2 = _create_driver(client, admin_headers, marker, 2)
    parent_a_id, email_a, parent_a_headers = signup_parent(client, marker, "posa")
    parent_b_id, email_b, parent_b_headers = signup_parent(client, marker, "posb")
    created: dict = {"buses": [], "routes": [], "students": []}
    try:
        bus1 = client.post(
            "/api/fleet/buses",
            json={"name": f"IT POS Bus1 {marker}", "driver_id": driver1["id"]},
            headers=admin_headers,
        ).json()
        bus2 = client.post(
            "/api/fleet/buses",
            json={"name": f"IT POS Bus2 {marker}", "driver_id": driver2["id"]},
            headers=admin_headers,
        ).json()
        created["buses"] = [bus1["id"], bus2["id"]]

        def make_route(name: str, kind: str, bus_id: str) -> dict:
            response = client.post(
                "/api/fleet/routes",
                json={"name": f"IT POS {name} {marker}", "type": kind,
                      "bus_id": bus_id, "school_id": sandbox["id"]},
                headers=admin_headers,
            )
            assert response.status_code == 200, response.text
            return response.json()

        morning = make_route("Morning", "morning", bus1["id"])
        afternoon = make_route("Afternoon", "afternoon", bus1["id"])
        afternoon2 = make_route("Afternoon2", "afternoon", bus2["id"])
        created["routes"] = [morning["id"], afternoon["id"], afternoon2["id"]]

        def make_student(tag: str, lat: float, pickup: str, routes: list[str], email: str) -> dict:
            response = client.post(
                "/api/students",
                json={
                    "name": f"IT POS Kid{tag} {marker}", "parent_name": f"IT POS Parent{tag}",
                    "parent_phone": f"+2547120004{uuid.uuid4().int % 90 + 10}",
                    "parent_email": email,
                    "home_lat": lat, "home_lng": 36.79, "pickup_time": pickup,
                    "route_ids": routes,
                },
                headers=admin_headers,
            )
            assert response.status_code == 200, response.text
            student = response.json()
            created["students"].append(student["id"])
            return student

        # A rides bus 1 in the morning and bus 2 in the afternoon (the
        # cross-bus rider); B rides bus 1 both ways.
        a = make_student("A", -1.26, "06:20", [morning["id"], afternoon2["id"]], email_a)
        b = make_student("B", -1.27, "06:30", [morning["id"], afternoon["id"]], email_b)
        _accept_pending(client, parent_a_headers)
        _accept_pending(client, parent_b_headers)

        yield {
            "marker": marker, "school_id": sandbox["id"],
            "driver1": driver1, "driver2": driver2,
            "driver_headers": pin_login(client, driver1["pin"]),
            "driver2_headers": pin_login(client, driver2["pin"]),
            "bus1": bus1, "bus2": bus2,
            "morning": morning, "afternoon": afternoon, "afternoon2": afternoon2,
            "a": a, "b": b,
            "parent_a": parent_a_headers, "parent_b": parent_b_headers,
        }
    finally:
        for run in client.get("/api/runs", headers=admin_headers).json():
            if run.get("bus_id") in created["buses"]:
                purge_run(run["id"])
        for sid in created["students"]:
            for absence in client.get("/api/students/absences", headers=admin_headers).json():
                if absence["student_id"] == sid:
                    client.delete(f"/api/students/absences/{absence['id']}", headers=admin_headers)
            client.delete(f"/api/students/{sid}", headers=admin_headers)
        for rid in created["routes"]:
            client.delete(f"/api/fleet/routes/{rid}", headers=admin_headers)
        for bid in created["buses"]:
            client.delete(f"/api/fleet/buses/{bid}", headers=admin_headers)
        for driver in (driver1, driver2):
            client.delete(f"/api/accounts/drivers/{driver['id']}", headers=admin_headers)
        purge_accounts(parent_a_id, parent_b_id)


# Helpers ----------------------------------------------------------------------

def staff_bus(client, headers, bus_id: str) -> dict:
    rows = client.get("/api/fleet/buses", headers=headers).json()
    return next(b for b in rows if b["id"] == bus_id)


def staff_position(client, headers, bus_id: str) -> dict | None:
    return staff_bus(client, headers, bus_id)["position"]


def parent_track(client, headers, student_id: str) -> httpx.Response:
    return client.get("/api/parent-portal/track", params={"student_id": student_id}, headers=headers)


def child_row(client, headers, student_id: str) -> dict:
    return next(c for c in client.get("/api/parent-portal/children", headers=headers).json()
                if c["id"] == student_id)


def profile_row(client, headers, student_id: str) -> dict:
    return next(c for c in client.get("/api/parent-portal/profile", headers=headers).json()["children"]
                if c["id"] == student_id)


def parent_view(position: dict) -> dict:
    return {key: position[key] for key in PARENT_FIELDS}


def assert_parent_payload_is_clean(payload) -> None:
    """No key anywhere in a parent payload names a source, an accuracy, an
    exception or a raw position column (R22, R37)."""

    def walk(node, path):
        if isinstance(node, dict):
            for key, value in node.items():
                assert not any(fragment in key for fragment in FORBIDDEN_PARENT_KEYS), (path, key)
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, f"{path}[{i}]")

    walk(payload, "$")


def when(value: str) -> datetime:
    return datetime.fromisoformat(value)


def stop_name(run_id: str, stop_order: int) -> str:
    with db() as conn:
        return conn.execute(
            "select name from run_stops where run_id = %s and stop_order = %s "
            "order by is_school_gate desc limit 1",
            (run_id, stop_order),
        ).fetchone()["name"]


def checkpoint_label(run_id: str, stop_order: int) -> str:
    """The label rule for a checkpoint at ``stop_order`` (the retired Python
    loop's wording): the gate reads 'At school', a stop reads its name plus
    'en route to next' while a later stop exists."""
    with db() as conn:
        stop = conn.execute(
            "select name, is_school_gate, exists (select 1 from run_stops n "
            "where n.run_id = rs.run_id and n.stop_order = rs.stop_order + 1) as has_next "
            "from run_stops rs where rs.run_id = %s and rs.stop_order = %s "
            "order by rs.is_school_gate desc limit 1",
            (run_id, stop_order),
        ).fetchone()
    if stop["is_school_gate"]:
        return "At school"
    return f"At {stop['name']}" + (" · en route to next" if stop["has_next"] else "")


def age_position(bus_id: str, seconds: int) -> None:
    with db() as conn:
        conn.execute(
            "update live_buses set position_at = now() - make_interval(secs => %s) where id = %s",
            (seconds, bus_id),
        )


def clear_position(*bus_ids: str) -> None:
    """Teardown only: purge_run deletes the run but leaves the pair End Run
    would have nulled, and a leftover pair would read as a position on the
    next test's staff list."""
    with db() as conn:
        for bus_id in bus_ids:
            conn.execute(
                "update live_buses set current_lat = null, current_lng = null, "
                "position_source = null, position_at = null, position_accuracy_m = null "
                "where id = %s",
                (bus_id,),
            )


def set_retention(school_id: str, days: int | None) -> None:
    with db() as conn:
        conn.execute(
            "update live_schools set position_retention_days = %s where id = %s", (days, school_id)
        )


# Parity: one bus, three readers (R7, R9, R37; AE11's server half) ----------------

def test_one_bus_reads_identically_for_staff_and_both_parent_readers(client, admin_headers, fleet):
    h = fleet["driver_headers"]
    bus_id = fleet["bus1"]["id"]
    b_id = fleet["b"]["id"]
    pb = fleet["parent_b"]
    run_id = start(client, h, fleet["morning"]["id"],
                   fix_body=fix(lat=-1.2000, lng=36.7000)).json()["id"]
    try:
        # Start Run: the school checkpoint, all qualifiers describing it; the
        # five write-side columns and the retired label loop are gone.
        bus = staff_bus(client, admin_headers, bus_id)
        assert not any(column in bus for column in RAW_COLUMNS)
        assert "position_label" not in bus and "position_state" not in bus
        pos = bus["position"]
        assert set(pos) == STAFF_FIELDS
        assert (pos["lat"], pos["lng"]) == (SCHOOL_LAT, SCHOOL_LNG)
        assert pos["source"] == "checkpoint" and pos["accuracy_m"] is None
        assert pos["label"] == "Starting — at school"
        assert pos["stale"] is False and pos["gps_off"] is False and pos["no_gps_for_run"] is False
        assert 0 <= pos["age_s"] < 120

        # AE11 (server half): an Arrive fix captured 20 s before the tap is
        # served with the CAPTURE time as its freshness, not the receipt time.
        f1 = fix(lat=-1.2611, lng=36.7911, accuracy=8.0,
                 captured_at=iso(datetime.now(timezone.utc) - timedelta(seconds=20)))
        assert arrive(client, h, run_id, expected=1, fix_body=f1).status_code == 200
        pos = staff_position(client, admin_headers, bus_id)
        assert (pos["lat"], pos["lng"], pos["accuracy_m"]) == (f1["lat"], f1["lng"], f1["accuracy_m"])
        assert pos["source"] == "action" and pos["label"] == "Phone GPS"
        assert when(pos["position_at"]) == when(f1["captured_at"])
        assert 20 <= pos["age_s"] < 90 and pos["stale"] is False

        # Parent B's child rides this run: Track, children and profile serve
        # the same coordinates and time as the staff list — allowlist only.
        track = parent_track(client, pb, b_id)
        assert track.status_code == 200, track.text
        track = track.json()
        assert set(track) == {"student", "stops", "run", "bus", "bus_position"}
        assert track["bus"] == {"id": bus_id, "name": fleet["bus1"]["name"]}
        assert track["run"]["id"] == run_id and track["run"]["stops_completed"] == 1
        assert set(track["bus_position"]) == PARENT_FIELDS
        assert track["bus_position"] == parent_view(pos)
        assert track["bus_position"]["position_at"] == pos["position_at"]  # string-identical
        assert "bus_current_lat" not in track["student"]
        child = child_row(client, pb, b_id)
        assert set(child["bus_position"]) == PARENT_FIELDS
        assert child["bus_position"] == track["bus_position"]
        assert profile_row(client, pb, b_id)["bus_position"] == track["bus_position"]
        for payload in (track, child, profile_row(client, pb, b_id)):
            assert_parent_payload_is_clean(payload)
        # The stops follow the run's route and still mark the family's own.
        assert any(s["is_own"] for s in track["stops"])
        assert any(s["is_school_gate"] for s in track["stops"])

        # End Run (R11): nothing is served; the completed run still comes back
        # for the stop ticks, without a bus or a position.
        ended = end_run(client, h, run_id, fix_body=fix())
        assert ended.status_code == 200, ended.text
        assert staff_position(client, admin_headers, bus_id) is None
        track = parent_track(client, pb, b_id).json()
        assert track["run"]["status"] == "completed"
        assert track["bus"] is None and track["bus_position"] is None
        assert child_row(client, pb, b_id)["bus_position"] is None
    finally:
        purge_run(run_id)
        clear_position(bus_id)


# No GPS for this run and the GPS-off marker (R20, F5; AE2) ------------------------

def test_taps_without_fixes_derive_no_gps_and_gps_off_and_the_first_fix_clears_both(
    client, admin_headers, fleet,
):
    h = fleet["driver_headers"]
    bus_id = fleet["bus1"]["id"]
    pb = fleet["parent_b"]
    b_id = fleet["b"]["id"]
    run_id = start(client, h, fleet["morning"]["id"], fix_body={"reason": "denied"}).json()["id"]
    try:
        # Nothing in the schema stores either flag: they are read off the trail.
        with db() as conn:
            stored = conn.execute(
                "select table_name, column_name from information_schema.columns "
                "where table_name in ('live_buses', 'live_runs') "
                "and (column_name like '%%gps%%' or column_name like '%%stale%%')"
            ).fetchall()
        assert stored == []

        pos = staff_position(client, admin_headers, bus_id)
        assert pos["source"] == "checkpoint" and pos["label"] == "Starting — at school"
        assert pos["gps_off"] is True and pos["no_gps_for_run"] is True

        # A tap with a device reason keeps GPS-off; the checkpoint label names
        # the stop reached and the leg ahead.
        assert arrive(client, h, run_id, expected=1, fix_body={"reason": "timeout"}).status_code == 200
        pos = staff_position(client, admin_headers, bus_id)
        assert pos["label"] == f"At {stop_name(run_id, 1)} · en route to next"
        assert pos["label"] == checkpoint_label(run_id, 1)
        assert pos["gps_off"] is True and pos["no_gps_for_run"] is True

        # An older client's tap (no fix key at all, reason `none`) is not a
        # device saying no: GPS-off drops, "no GPS for this run" stands.
        assert arrive(client, h, run_id, expected=2, include_fix=False).status_code == 200
        pos = staff_position(client, admin_headers, bus_id)
        assert pos["gps_off"] is False and pos["no_gps_for_run"] is True
        assert pos["source"] == "checkpoint"

        assert arrive(client, h, run_id, expected=3, fix_body={"reason": "unavailable"}).status_code == 200
        pos = staff_position(client, admin_headers, bus_id)
        assert pos["gps_off"] is True and pos["no_gps_for_run"] is True

        # The parent sees the checkpoint position and none of this (R22).
        track = parent_track(client, pb, b_id).json()
        assert track["bus_position"] == parent_view(pos)
        assert_parent_payload_is_clean(track)

        # The first fix clears both — no write beyond the tap's own trail row.
        with db() as conn:
            rows_before = conn.execute(
                "select count(*) as n from run_positions where run_id = %s", (run_id,)
            ).fetchone()["n"]
        f = fix(lat=-1.2733, lng=36.7933, accuracy=20.0)
        boarded = action(client, h, "/api/runs/driver/boarding",
                         {"student_id": b_id, "on_bus": True}, fix_body=f)
        assert boarded.status_code == 200, boarded.text
        with db() as conn:
            rows_after = conn.execute(
                "select count(*) as n from run_positions where run_id = %s", (run_id,)
            ).fetchone()["n"]
        assert rows_after == rows_before + 1
        pos = staff_position(client, admin_headers, bus_id)
        assert pos["gps_off"] is False and pos["no_gps_for_run"] is False
        assert pos["source"] == "action" and pos["label"] == "Phone GPS"

        # A later denied tap re-raises GPS-off only: the run has had a fix.
        absent = action(client, h, "/api/runs/driver/absent", {"student_id": fleet["a"]["id"]},
                        fix_body={"reason": "denied"})
        assert absent.status_code == 200, absent.text
        pos = staff_position(client, admin_headers, bus_id)
        assert pos["gps_off"] is True and pos["no_gps_for_run"] is False
    finally:
        purge_run(run_id)
        clear_position(bus_id)
        for absence in client.get("/api/students/absences", headers=admin_headers).json():
            if absence["student_id"] == fleet["a"]["id"]:
                client.delete(f"/api/students/absences/{absence['id']}", headers=admin_headers)


# Staleness (R27; AE13, Phase 1 half) ----------------------------------------------

def test_a_position_older_than_the_threshold_reads_stale_and_a_new_fix_clears_it(
    client, admin_headers, fleet,
):
    from app.core.config import get_settings

    # The staleness threshold is a system default (Settings, GPS plan U11),
    # never per school; the stack under test runs the default.
    GPS_STALE_AFTER_S = get_settings().gps_stale_after_s
    assert GPS_STALE_AFTER_S == 90
    h = fleet["driver_headers"]
    bus_id = fleet["bus1"]["id"]
    pb = fleet["parent_b"]
    b_id = fleet["b"]["id"]
    run_id = start(client, h, fleet["morning"]["id"], fix_body=fix()).json()["id"]
    try:
        f1 = fix(lat=-1.2611, lng=36.7911, accuracy=8.0)
        assert arrive(client, h, run_id, expected=1, fix_body=f1).status_code == 200
        assert staff_position(client, admin_headers, bus_id)["stale"] is False

        # Past the threshold: stale for staff and parents alike, the age
        # counted, the position still served (never hidden during a run).
        age_position(bus_id, GPS_STALE_AFTER_S + 1)
        pos = staff_position(client, admin_headers, bus_id)
        assert pos["stale"] is True and pos["age_s"] >= GPS_STALE_AFTER_S + 1
        assert (pos["lat"], pos["lng"]) == (f1["lat"], f1["lng"])
        track = parent_track(client, pb, b_id).json()
        assert track["bus_position"]["stale"] is True
        assert track["bus_position"] == parent_view(pos)
        assert child_row(client, pb, b_id)["bus_position"]["stale"] is True

        # Just inside the threshold: fresh.
        age_position(bus_id, GPS_STALE_AFTER_S - 1)
        assert staff_position(client, admin_headers, bus_id)["stale"] is False

        # Past it again, then a new fix clears it — the tap, no other write.
        age_position(bus_id, 600)
        assert staff_position(client, admin_headers, bus_id)["stale"] is True
        f2 = fix(lat=-1.2622, lng=36.7922, accuracy=10.0)
        assert arrive(client, h, run_id, expected=2, fix_body=f2).status_code == 200
        pos = staff_position(client, admin_headers, bus_id)
        assert pos["stale"] is False and pos["age_s"] < 60
        assert (pos["lat"], pos["lng"]) == (f2["lat"], f2["lng"])
        assert parent_track(client, pb, b_id).json()["bus_position"]["stale"] is False

        # A checkpoint reached minutes ago is minutes old too (R27 says so
        # rather than hiding it): age a checkpoint past the threshold.
        assert arrive(client, h, run_id, expected=3, include_fix=False).status_code == 200
        pos = staff_position(client, admin_headers, bus_id)
        assert pos["source"] == "checkpoint" and pos["stale"] is False
        age_position(bus_id, 600)
        assert staff_position(client, admin_headers, bus_id)["stale"] is True

        # The legacy / rollback shape: a pair with no source is a checkpoint
        # of unknown age — served, labelled by the run, never stale.
        with db() as conn:
            conn.execute(
                "update live_buses set position_source = null, position_at = null, "
                "position_accuracy_m = null where id = %s",
                (bus_id,),
            )
        pos = staff_position(client, admin_headers, bus_id)
        assert pos is not None
        assert pos["source"] is None and pos["position_at"] is None and pos["age_s"] is None
        assert pos["stale"] is False
        assert pos["label"] == checkpoint_label(run_id, 3)
        track = parent_track(client, pb, b_id).json()
        assert track["bus_position"] == {
            "lat": pos["lat"], "lng": pos["lng"], "position_at": None, "stale": False,
        }
    finally:
        purge_run(run_id)
        clear_position(bus_id)


# The run the child is on, not the home bus (R37) -----------------------------------

def test_a_cross_bus_afternoon_riders_parent_sees_the_bus_of_the_run_the_child_is_on(
    client, admin_headers, fleet,
):
    a_id, b_id = fleet["a"]["id"], fleet["b"]["id"]
    pa, pb = fleet["parent_a"], fleet["parent_b"]
    bus1, bus2 = fleet["bus1"], fleet["bus2"]
    # A's derived home bus is bus 1 (morning-preferring); the afternoon run
    # is on bus 2.
    assert parent_track(client, pa, a_id).json()["student"]["bus_id"] == bus1["id"]
    run_id = start(client, fleet["driver2_headers"], fleet["afternoon2"]["id"],
                   fix_body=fix(lat=-1.2700, lng=36.7600)).json()["id"]
    try:
        f = fix(lat=-1.2655, lng=36.7955, accuracy=11.0)
        assert arrive(client, fleet["driver2_headers"], run_id, expected=1, fix_body=f).status_code == 200
        bus2_pos = staff_position(client, admin_headers, bus2["id"])
        assert (bus2_pos["lat"], bus2_pos["lng"]) == (f["lat"], f["lng"])
        assert staff_position(client, admin_headers, bus1["id"]) is None

        track = parent_track(client, pa, a_id).json()
        assert track["student"]["bus_id"] == bus1["id"]           # the home bus, unchanged
        assert track["bus"] == {"id": bus2["id"], "name": bus2["name"]}  # the run's bus
        assert track["bus_position"] == parent_view(bus2_pos)
        assert track["run"]["id"] == run_id and track["run"]["type"] == "afternoon"
        # The stops are the afternoon route's: the gate leads and A's own is there.
        assert track["stops"][0]["is_school_gate"] is True
        assert any(s["is_own"] for s in track["stops"])
        assert child_row(client, pa, a_id)["bus_position"] == track["bus_position"]

        # B is not on that run and bus 1 has none: nothing is served.
        track_b = parent_track(client, pb, b_id).json()
        assert track_b["bus"] is None and track_b["bus_position"] is None
        assert child_row(client, pb, b_id)["bus_position"] is None
        # And neither parent may read the other's child (the 404 boundary).
        assert parent_track(client, pb, a_id).status_code == 404
        assert parent_track(client, pa, b_id).status_code == 404
    finally:
        purge_run(run_id)
        clear_position(bus1["id"], bus2["id"])


def _clear_absences(client, headers, student_name: str) -> None:
    for absence in client.get("/api/students/absences", headers=headers).json():
        if absence.get("student_name") == student_name:
            client.delete(f"/api/students/absences/{absence['id']}", headers=headers)


def _purge_todays_runs(client, headers, bus_id: str) -> None:
    """Today's runs for the bus only (a completed one blocks a same-day
    restart); the seed's prior-day open run is deliberately left alone."""
    today = datetime.now(timezone(timedelta(hours=3))).date().isoformat()
    for run in client.get("/api/runs", headers=headers).json():
        if run.get("bus_id") == bus_id and str(run.get("date"))[:10] == today:
            purge_run(run["id"])


def test_a_parent_with_children_at_two_schools_sees_each_childs_bus_and_an_unlinked_parent_sees_nothing(
    client, fleet,
):
    """The seeded cross-school parent (Amina: Faith at school A on Simba, Ben
    at school B on IT Bus B, Grace without a bus) with both seeded drivers
    on a run; the sandbox parent has no link to either school (AE10)."""
    amina = login(client, AMINA["email"], AMINA["password"])
    admin_a = login(client, ADMIN_A["email"], ADMIN_A["password"])
    director_b = login(client, DIRECTOR_B["email"], DIRECTOR_B["password"])
    driver_a = pin_login(client, DRIVER_A_PIN)
    driver_b = pin_login(client, DRIVER_B_PIN)
    children = {c["name"]: c for c in client.get("/api/parent-portal/children", headers=amina).json()}
    assert {FAITH, BEN, GRACE} <= set(children)
    simba_id = children[FAITH]["bus_id"]
    bus_b_id = children[BEN]["bus_id"]
    for headers, bus_id, name in ((admin_a, simba_id, FAITH), (director_b, bus_b_id, BEN)):
        _purge_todays_runs(client, headers, bus_id)
        _clear_absences(client, headers, name)
    clear_position(simba_id, bus_b_id)

    def morning_route(headers) -> str:
        context = client.get("/api/runs/driver/context", headers=headers).json()
        return next(r["id"] for r in context["routes"] if r["type"] == "morning")

    run_a = run_b = None
    try:
        run_a = start(client, driver_a, morning_route(driver_a), fix_body=fix()).json()["id"]
        run_b = start(client, driver_b, morning_route(driver_b), fix_body=fix()).json()["id"]
        fa = fix(lat=-1.2905, lng=36.7830, accuracy=7.0)
        fb = fix(lat=-1.3600, lng=36.7450, accuracy=9.0)
        assert arrive(client, driver_a, run_a, expected=1, fix_body=fa).status_code == 200
        assert arrive(client, driver_b, run_b, expected=1, fix_body=fb).status_code == 200
        simba = staff_position(client, admin_a, simba_id)
        bus_b = staff_position(client, director_b, bus_b_id)
        assert (simba["lat"], simba["lng"]) == (fa["lat"], fa["lng"])
        assert (bus_b["lat"], bus_b["lng"]) == (fb["lat"], fb["lng"])

        children = {c["name"]: c for c in client.get("/api/parent-portal/children", headers=amina).json()}
        assert children[FAITH]["bus_position"] == parent_view(simba)
        assert children[BEN]["bus_position"] == parent_view(bus_b)
        assert children[GRACE]["bus_position"] is None
        faith_track = parent_track(client, amina, children[FAITH]["id"]).json()
        ben_track = parent_track(client, amina, children[BEN]["id"]).json()
        assert faith_track["bus"]["name"] == SIMBA and faith_track["bus_position"] == parent_view(simba)
        assert ben_track["bus"]["name"] == BUS_B and ben_track["bus_position"] == parent_view(bus_b)
        for payload in (faith_track, ben_track, list(children.values())):
            assert_parent_payload_is_clean(payload)

        # The sandbox parent has no link at either school: the seeded children
        # do not exist for them, and their own child is not on a run.
        pa = fleet["parent_a"]
        assert parent_track(client, pa, children[FAITH]["id"]).status_code == 404
        assert parent_track(client, pa, children[BEN]["id"]).status_code == 404
        assert all(c["bus_position"] is None
                   for c in client.get("/api/parent-portal/children", headers=pa).json())
    finally:
        purge_run(run_a)
        purge_run(run_b)
        clear_position(simba_id, bus_b_id)


# Retention (R12): old trail rows do not feed the derivation -----------------------

def test_a_trail_row_older_than_retention_counts_toward_neither_gps_off_nor_no_gps(
    client, admin_headers, fleet,
):
    h = fleet["driver_headers"]
    bus_id = fleet["bus1"]["id"]
    school = fleet["school_id"]
    run_id = start(client, h, fleet["morning"]["id"], fix_body=fix()).json()["id"]
    try:
        pos = staff_position(client, admin_headers, bus_id)
        assert pos["gps_off"] is False and pos["no_gps_for_run"] is False

        # Retention 7 days (the minimum); the Start Run's fix-bearing row is
        # received 8 days ago: nothing inside retention, so nothing counts.
        set_retention(school, 7)
        with db() as conn:
            conn.execute(
                "update run_positions set received_at = now() - interval '8 days' "
                "where run_id = %s",
                (run_id,),
            )
        pos = staff_position(client, admin_headers, bus_id)
        assert pos["gps_off"] is False and pos["no_gps_for_run"] is False

        # A fresh denied tap is now the ONLY row inside retention: GPS-off,
        # and "no GPS for this run" — the old fix no longer vouches for the run.
        assert arrive(client, h, run_id, expected=1, fix_body={"reason": "denied"}).status_code == 200
        pos = staff_position(client, admin_headers, bus_id)
        assert pos["gps_off"] is True and pos["no_gps_for_run"] is True

        # Widen retention: the old fix counts again; the latest tap is still a no.
        set_retention(school, 365)
        pos = staff_position(client, admin_headers, bus_id)
        assert pos["gps_off"] is True and pos["no_gps_for_run"] is False
    finally:
        set_retention(school, None)
        purge_run(run_id)
        clear_position(bus_id)
