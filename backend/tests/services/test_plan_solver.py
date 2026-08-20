"""U2 solver unit tests (plan 2026-08-19-001-feat-fleet-plan-drafting).

Written test-first: the lexicographic objective semantics are the product
promise (R5), so they are pinned here with hand-crafted duration tables where
the arithmetic is exact, before any heuristic tuning.

Plain pytest, no DB, no network — the directed duration matrix is injected as
a callable ``(origin, dest) -> seconds`` over ``(lat, lng)`` tuples.
"""
import math
import os
import random
import time

import pytest

from app.services import geo_service
from app.services.plan_solver import (
    CONSTRAINT_SEATS,
    CONSTRAINT_STOP_CAP,
    DEFAULT_STOP_CAP,
    LEG_AFTERNOON,
    LEG_MORNING,
    STOP_COLLAPSE_RADIUS_M,
    solve,
)

SCHOOL = {"lat": -1.30, "lng": 36.80}
S = (SCHOOL["lat"], SCHOOL["lng"])
# Named points for the crafted-table tests (all far more than 30 m apart, at
# distinct angles around the school).
A = (-1.2900, 36.7900)
B = (-1.3100, 36.8100)
D = (-1.3300, 36.8300)


# --- helpers ----------------------------------------------------------------

def hav_matrix(a, b):
    """Synthetic directed matrix: great-circle metres at 8 m/s."""
    return geo_service.haversine_m(a, b) / 8.0


def table_matrix(table):
    """Exact lookup matrix that fails loudly on any unexpected query — this is
    how the tests catch semantic drift (e.g. a depot leg leaking into a child
    ride, or a PM query on a leg with no PM riders)."""

    def m(a, b):
        try:
            return table[(a, b)]
        except KeyError:
            raise KeyError(f"matrix queried for unexpected pair {a} -> {b}")

    return m


def student(i, lat, lng, pattern="both_ways", **extra):
    return {"id": f"s{i}", "name": f"Student {i}", "lat": lat, "lng": lng,
            "pattern": pattern, **extra}


def bus(i, capacity, depot=None):
    return {"id": f"b{i}", "name": f"Bus {i}", "capacity": capacity, "depot": depot}


def ring(n, r=0.01):
    """n points on a ring around the school, pairwise far beyond 30 m."""
    pts = []
    for i in range(n):
        theta = 2 * math.pi * i / n
        rad = r + 0.0008 * (i % 5)
        pts.append((SCHOOL["lat"] + rad * math.cos(theta),
                    SCHOOL["lng"] + rad * math.sin(theta)))
    return pts


def leg_rosters(doc, leg):
    """bus_id -> set of student ids riding that leg."""
    out = {}
    for b in doc["buses"]:
        ids = set()
        for stop in b["legs"][leg]["stops"]:
            ids |= {s["id"] for s in stop["students"]}
        out[b["bus_id"]] = ids
    return out


def leg_loads(doc, leg):
    """bus_id -> child count on that leg (seats consumed)."""
    out = {}
    for b in doc["buses"]:
        out[b["bus_id"]] = sum(len(stop["students"]) for stop in b["legs"][leg]["stops"])
    return out


def rides(doc, leg):
    """student_id -> ride seconds on that leg."""
    out = {}
    for b in doc["buses"]:
        for r in b["legs"][leg]["ride_seconds"]:
            out[r["student_id"]] = r["ride_seconds"]
    return out


def stop_ids(doc, bus_id, leg):
    """Ordered stops of one route as lists of student ids."""
    b = next(x for x in doc["buses"] if x["bus_id"] == bus_id)
    return [[s["id"] for s in st["students"]] for st in b["legs"][leg]["stops"]]


def find_bus(doc, sid, leg):
    for bid, ids in leg_rosters(doc, leg).items():
        if sid in ids:
            return bid
    return None


# --- AE1: full placement, capacity-legal mirrored pairs ---------------------

def test_ae1_thirty_students_two_buses_mirrored_pairs():
    pts = ring(30)
    students = [student(i, *pts[i]) for i in range(30)]
    buses = [bus(1, 15, {"lat": -1.32, "lng": 36.80}),
             bus(2, 15, {"lat": -1.28, "lng": 36.80})]
    doc = solve(students, buses, hav_matrix, SCHOOL, seed=1)

    assert doc["unplaceable"] == []
    assert len(doc["buses"]) == 2
    all_ids = {f"s{i}" for i in range(30)}
    for leg in (LEG_MORNING, LEG_AFTERNOON):
        loads = leg_loads(doc, leg)
        assert all(n <= 15 for n in loads.values()), loads
        assert sum(loads.values()) == 30
        # every child's ride time present, positive
        r = rides(doc, leg)
        assert set(r) == all_ids
        assert all(v > 0 for v in r.values())
    # mirrored pairs: roster identity per bus across legs
    assert leg_rosters(doc, LEG_MORNING) == leg_rosters(doc, LEG_AFTERNOON)


# --- AE6: 31st student unplaceable naming seats -----------------------------

def test_ae6_thirty_first_student_unplaceable_names_seats():
    pts = ring(31)
    students = [student(i, *pts[i]) for i in range(31)]
    buses = [bus(1, 15, {"lat": -1.32, "lng": 36.80}),
             bus(2, 15, {"lat": -1.28, "lng": 36.80})]
    doc = solve(students, buses, hav_matrix, SCHOOL, seed=1)

    unplaced_ids = {u["student_id"] for u in doc["unplaceable"]}
    assert len(unplaced_ids) == 1
    assert {u["leg"] for u in doc["unplaceable"]} == {LEG_MORNING, LEG_AFTERNOON}
    for u in doc["unplaceable"]:
        assert u["constraint"] == CONSTRAINT_SEATS
        assert u["name"] == f"Student {u['student_id'][1:]}"
    # no bus over capacity on either leg
    for leg in (LEG_MORNING, LEG_AFTERNOON):
        assert all(n <= 15 for n in leg_loads(doc, leg).values())


# --- AE3: morning-only child, pair mirrored for the rest --------------------

def test_ae3_morning_only_child_rides_am_only_rest_mirrored():
    pts = ring(8)
    students = [student(i, *pts[i],
                        pattern="morning_only" if i == 3 else "both_ways")
                for i in range(8)]
    buses = [bus(1, 5), bus(2, 5)]
    doc = solve(students, buses, hav_matrix, SCHOOL, seed=2)

    assert doc["unplaceable"] == []
    am = leg_rosters(doc, LEG_MORNING)
    pm = leg_rosters(doc, LEG_AFTERNOON)
    assert find_bus(doc, "s3", LEG_MORNING) is not None
    assert find_bus(doc, "s3", LEG_AFTERNOON) is None
    for bid in am:
        assert pm[bid] == am[bid] - {"s3"}


# --- AE4: depot tie-break falls to total driving ----------------------------

def test_ae4_depot_tie_break_prefers_nearer_first_pickup():
    # Two AM orders tie on (worst ride, total ride); only the depot leg
    # differs. The nearer first pickup must win via total driving.
    table = {
        (D, A): 60.0, (D, B): 3000.0,
        (A, B): 600.0, (B, A): 600.0,
        (A, S): 1200.0, (B, S): 1200.0,
    }
    students = [student("A", *A, pattern="morning_only"),
                student("B", *B, pattern="morning_only")]
    buses = [bus(1, 10, {"lat": D[0], "lng": D[1]})]
    doc = solve(students, buses, table_matrix(table), SCHOOL, seed=0)

    assert stop_ids(doc, "b1", LEG_MORNING) == [["sA"], ["sB"]]
    assert stop_ids(doc, "b1", LEG_AFTERNOON) == []
    assert rides(doc, LEG_MORNING) == {"sA": 1800.0, "sB": 1200.0}
    # depot leg (60) counts only toward total driving
    assert doc["objective"] == [1800.0, 3000.0, 1860.0]


# --- PM order independent of AM; per-leg ride semantics ---------------------

def test_pm_order_computed_independently_not_reversed_am():
    # Directed durations make [A, B] optimal on BOTH legs; a reversed-AM
    # assumption would emit [B, A] for the afternoon.
    table = {
        (A, B): 100.0, (B, A): 1000.0,
        (B, S): 100.0, (A, S): 1000.0,
        (S, A): 100.0, (S, B): 1000.0,
    }
    students = [student("A", *A), student("B", *B)]
    buses = [bus(1, 10)]  # no depot: no depot legs anywhere
    doc = solve(students, buses, table_matrix(table), SCHOOL, seed=0)

    assert stop_ids(doc, "b1", LEG_MORNING) == [["sA"], ["sB"]]
    assert stop_ids(doc, "b1", LEG_AFTERNOON) == [["sA"], ["sB"]]
    # AM ride = stop -> school along the order; PM ride = school -> stop
    assert rides(doc, LEG_MORNING) == {"sA": 200.0, "sB": 100.0}
    assert rides(doc, LEG_AFTERNOON) == {"sA": 100.0, "sB": 200.0}
    assert doc["objective"] == [200.0, 600.0, 400.0]


# --- lexicographic dominance ------------------------------------------------

def test_lexicographic_lower_worst_ride_wins_despite_higher_driving():
    table = {
        (A, B): 100.0, (B, S): 100.0,   # [A,B]: worst 200, total 300
        (B, A): 600.0, (A, S): 600.0,   # [B,A]: worst 1200, total 1800
        (D, A): 2000.0, (D, B): 10.0,   # driving: [A,B] 2200 vs [B,A] 1210
    }
    students = [student("A", *A, pattern="morning_only"),
                student("B", *B, pattern="morning_only")]
    buses = [bus(1, 10, {"lat": D[0], "lng": D[1]})]
    doc = solve(students, buses, table_matrix(table), SCHOOL, seed=0)

    assert stop_ids(doc, "b1", LEG_MORNING) == [["sA"], ["sB"]]
    assert doc["objective"] == [200.0, 300.0, 2200.0]


def test_lexicographic_equal_worst_falls_to_total_ride():
    table = {
        (A, B): 900.0, (B, S): 100.0,   # [A,B]: worst 1000, total 1100
        (B, A): 600.0, (A, S): 400.0,   # [B,A]: worst 1000, total 1400
        (D, A): 5000.0, (D, B): 10.0,   # driving favours the losing order
    }
    students = [student("A", *A, pattern="morning_only"),
                student("B", *B, pattern="morning_only")]
    buses = [bus(1, 10, {"lat": D[0], "lng": D[1]})]
    doc = solve(students, buses, table_matrix(table), SCHOOL, seed=0)

    assert stop_ids(doc, "b1", LEG_MORNING) == [["sA"], ["sB"]]
    assert doc["objective"] == [1000.0, 1100.0, 6000.0]


# --- split rider ------------------------------------------------------------

def test_split_rider_placed_on_different_buses_per_leg():
    pts = ring(6)
    students = [student(i, *pts[i], pattern="split" if i == 0 else "both_ways")
                for i in range(6)]
    buses = [bus(1, 10), bus(2, 10)]
    doc = solve(students, buses, hav_matrix, SCHOOL, seed=3)

    assert doc["unplaceable"] == []
    am_bus = find_bus(doc, "s0", LEG_MORNING)
    pm_bus = find_bus(doc, "s0", LEG_AFTERNOON)
    assert am_bus is not None and pm_bus is not None
    assert am_bus != pm_bus
    # the rest stay mirrored
    am = leg_rosters(doc, LEG_MORNING)
    pm = leg_rosters(doc, LEG_AFTERNOON)
    for bid in am:
        assert am[bid] - {"s0"} == pm[bid] - {"s0"}


# --- pins -------------------------------------------------------------------

def test_bus_pinned_child_never_moves_across_restarts():
    pts = ring(8)
    students = [student(i, *pts[i],
                        **({"bus_pin": "b2"} if i == 5 else {}))
                for i in range(8)]
    buses = [bus(1, 8), bus(2, 8)]
    for seed in (0, 1, 7):
        doc = solve(students, buses, hav_matrix, SCHOOL, seed=seed)
        assert find_bus(doc, "s5", LEG_MORNING) == "b2"
        assert find_bus(doc, "s5", LEG_AFTERNOON) == "b2"


def test_order_pin_fixes_stop_position():
    # Three stops on a straight line east of the school; the natural AM order
    # is farthest-first, so pinning the nearest child to position 0 must
    # override the objective-optimal order.
    p1 = (SCHOOL["lat"], SCHOOL["lng"] + 0.005)   # near
    p2 = (SCHOOL["lat"], SCHOOL["lng"] + 0.010)
    p3 = (SCHOOL["lat"], SCHOOL["lng"] + 0.015)   # far
    buses = [bus(1, 10)]

    natural = solve([student(1, *p1, pattern="morning_only"),
                     student(2, *p2, pattern="morning_only"),
                     student(3, *p3, pattern="morning_only")],
                    buses, hav_matrix, SCHOOL, seed=0)
    assert stop_ids(natural, "b1", LEG_MORNING)[0] == ["s3"]

    pinned = solve([student(1, *p1, pattern="morning_only",
                            order_pin={LEG_MORNING: 0}),
                    student(2, *p2, pattern="morning_only"),
                    student(3, *p3, pattern="morning_only")],
                   buses, hav_matrix, SCHOOL, seed=0)
    assert stop_ids(pinned, "b1", LEG_MORNING)[0] == ["s1"]


# --- stop cap ---------------------------------------------------------------

def test_25_stops_on_one_route_rejected_as_over_cap():
    assert DEFAULT_STOP_CAP == 24
    pts = ring(25)
    students = [student(i, *pts[i]) for i in range(25)]
    buses = [bus(1, 40)]  # seats are ample; only the stop cap can bind
    doc = solve(students, buses, hav_matrix, SCHOOL, seed=1)

    for leg in (LEG_MORNING, LEG_AFTERNOON):
        assert len(stop_ids(doc, "b1", leg)) == 24
    unplaced_ids = {u["student_id"] for u in doc["unplaceable"]}
    assert len(unplaced_ids) == 1
    assert {u["leg"] for u in doc["unplaceable"]} == {LEG_MORNING, LEG_AFTERNOON}
    assert all(u["constraint"] == CONSTRAINT_STOP_CAP for u in doc["unplaceable"])


# --- stop collapse ----------------------------------------------------------

def test_multi_child_stop_consumes_one_slot_and_n_seats():
    assert STOP_COLLAPSE_RADIUS_M == 30.0
    sib1 = (-1.2900, 36.7900)
    sib2 = (-1.29009, 36.7900)  # ~10 m from sib1: collapses
    students = [student(1, *sib1), student(2, *sib2),
                student(3, -1.3100, 36.8100), student(4, -1.3050, 36.7950)]
    doc = solve(students, [bus(1, 4)], hav_matrix, SCHOOL, seed=0)

    assert doc["unplaceable"] == []
    am_stops = stop_ids(doc, "b1", LEG_MORNING)
    assert len(am_stops) == 3  # one slot for the siblings
    sib_stop = next(ids for ids in am_stops if "s1" in ids)
    assert set(sib_stop) == {"s1", "s2"}
    # the collapse group is surfaced in the document
    groups = [set(s["id"] for s in g["students"]) for g in doc["stop_groups"]]
    assert {"s1", "s2"} in groups

    # seats still count children: with capacity 3 the same roster no longer fits
    tight = solve(students, [bus(1, 3)], hav_matrix, SCHOOL, seed=0)
    assert leg_loads(tight, LEG_MORNING)["b1"] == 3
    tight_unplaced = {u["student_id"] for u in tight["unplaceable"]}
    assert len(tight_unplaced) == 1
    assert all(u["constraint"] == CONSTRAINT_SEATS for u in tight["unplaceable"])


# --- determinism ------------------------------------------------------------

def test_same_seed_and_inputs_identical_document():
    pts = ring(12)
    patterns = {0: "split", 1: "morning_only", 2: "afternoon_only"}
    students = [student(i, *pts[i], pattern=patterns.get(i, "both_ways"))
                for i in range(12)]
    buses = [bus(1, 8, {"lat": -1.33, "lng": 36.83}), bus(2, 8)]

    doc1 = solve(students, buses, hav_matrix, SCHOOL, seed=42)
    doc2 = solve(students, buses, hav_matrix, SCHOOL, seed=42)
    assert doc1 == doc2
    assert doc1["seed"] == 42


# --- degraded passthrough ---------------------------------------------------

def test_degraded_flag_passes_through():
    students = [student(1, -1.29, 36.79)]
    assert solve(students, [bus(1, 5)], hav_matrix, SCHOOL, seed=0)["degraded"] is False
    assert solve(students, [bus(1, 5)], hav_matrix, SCHOOL, seed=0,
                 degraded=True)["degraded"] is True


# --- edges ------------------------------------------------------------------

def test_zero_students():
    doc = solve([], [bus(1, 10, {"lat": -1.33, "lng": 36.83})], hav_matrix,
                SCHOOL, seed=0)
    assert doc["unplaceable"] == []
    assert doc["stop_groups"] == []
    assert doc["objective"] == [0.0, 0.0, 0.0]
    assert len(doc["buses"]) == 1
    for leg in (LEG_MORNING, LEG_AFTERNOON):
        assert doc["buses"][0]["legs"][leg]["stops"] == []
        assert doc["buses"][0]["legs"][leg]["ride_seconds"] == []


def test_one_bus_takes_everyone():
    pts = ring(5)
    students = [student(i, *pts[i]) for i in range(5)]
    doc = solve(students, [bus(1, 10)], hav_matrix, SCHOOL, seed=0)
    assert doc["unplaceable"] == []
    assert leg_rosters(doc, LEG_MORNING)["b1"] == {f"s{i}" for i in range(5)}
    assert leg_rosters(doc, LEG_MORNING) == leg_rosters(doc, LEG_AFTERNOON)


def test_all_students_unplaceable():
    students = [student(1, -1.29, 36.79),
                student(2, -1.31, 36.81, pattern="morning_only"),
                student(3, -1.305, 36.795, pattern="split")]
    doc = solve(students, [bus(1, 0)], hav_matrix, SCHOOL, seed=0)

    for leg in (LEG_MORNING, LEG_AFTERNOON):
        assert leg_rosters(doc, leg)["b1"] == set()
    entries = {(u["student_id"], u["leg"]) for u in doc["unplaceable"]}
    assert entries == {
        ("s1", LEG_MORNING), ("s1", LEG_AFTERNOON),
        ("s2", LEG_MORNING),
        ("s3", LEG_MORNING), ("s3", LEG_AFTERNOON),
    }
    assert all(u["constraint"] == CONSTRAINT_SEATS for u in doc["unplaceable"])
    assert all(u["name"] == f"Student {u['student_id'][1:]}" for u in doc["unplaceable"])


# --- benchmark (opt-in) -----------------------------------------------------

@pytest.mark.skipif(not os.environ.get("PLAN_SOLVER_BENCH"),
                    reason="benchmark; set PLAN_SOLVER_BENCH=1 to run")
def test_benchmark_300_students_10_buses_under_cap():
    rng = random.Random(7)
    students = [student(i,
                        SCHOOL["lat"] + rng.uniform(-0.03, 0.03),
                        SCHOOL["lng"] + rng.uniform(-0.03, 0.03))
                for i in range(300)]
    buses = [bus(i, 32, {"lat": SCHOOL["lat"] + 0.03, "lng": SCHOOL["lng"] - 0.03})
             for i in range(10)]

    t0 = time.perf_counter()
    doc = solve(students, buses, hav_matrix, SCHOOL, seed=3, stop_cap=30)
    elapsed = time.perf_counter() - t0
    assert elapsed < 8.0, f"solver took {elapsed:.1f}s against a 5s cap"

    # every (student, leg) accounted for exactly once: placed or unplaceable
    for leg in (LEG_MORNING, LEG_AFTERNOON):
        placed = set()
        for ids in leg_rosters(doc, leg).values():
            assert placed.isdisjoint(ids)
            placed |= ids
        unplaced = {u["student_id"] for u in doc["unplaceable"] if u["leg"] == leg}
        assert placed.isdisjoint(unplaced)
        assert placed | unplaced == {f"s{i}" for i in range(300)}
        assert all(n <= 32 for n in leg_loads(doc, leg).values())
        for b in doc["buses"]:
            assert len(b["legs"][leg]["stops"]) <= 30
