"""Slot-in proposal engine (U12; origin R12, R13, R15; AE2, AE7).

When a student becomes plannable — a new enrolment with resolved coordinates,
or an address change re-resolved — and lacks a route for a leg their pattern
requires, while the school has applied plan routes (``plan_ordered``), this
engine generates a one-line placement proposal by cheapest insertion across
the applied routes: every feasible position on every applied route of the
school's claimed buses, scored by the lexicographic effect on the plan
(worst child ride, total child ride, total driving — the solver's exact
objective, reusing ``plan_solver._eval_order`` over a small in-memory matrix
from ``geo_service.compute_duration_matrix`` covering the applied routes'
points plus the new home; the matrix is never persisted, per the U3 ToS
rule). Existing children NEVER move: an insertion adds one stop (or joins an
existing same-place stop) and leaves the surviving order untouched, which is
also exactly what a frozen (``manual_stop_order``) route requires — AE7's
"respects the frozen order" holds by construction, and frozen applied routes
remain candidates because a manual reorder deliberately leaves
``plan_ordered`` set (fleet_dao.reorder_route_stops).

Proposal shape and text pin AE2: "Bus 2, between stop 3 and 4, +4 minutes
for two children" — position among the leg's stops (1-based; "before stop 1"
/ "after stop N" at the edges, "joining stop K" when the home collapses into
an existing stop within the solver's 30 m radius) plus the effect on other
children's times (the shared detour delta, rounded to minutes; children
whose ride grows by less than half a minute are not "delayed"). A both-ways
rider gets ONE proposal covering both legs on ONE bus (mirroring is roster
identity — the bus is chosen by the joint lexicographic effect over both
legs, each leg at its own cheapest position); the one-line ``text`` states
the MORNING leg (the canonical roster statement), with the per-leg details
in the structured record. A split rider gets one proposal PER LEG,
independently acceptable; each split leg scores its own cheapest bus — the
solver's different-buses-per-leg rule is advisory here, since the admin
reviews every proposal before it lands (documented simplification).

Infeasible everywhere (capacity or stop cap on every claimed bus): NO
proposal — the student surfaces on the slot-in list as unplaceable with the
binding constraint named using the solver's attribution rule ("seats" if
relaxing seat capacity alone would admit them somewhere, else "stop cap",
else "seats").

Storage decision (documented): proposals live as a JSONB list under the
``slot_in_proposals`` key INSIDE the school's APPLIED plan row's
``document`` — the "JSONB list on the applied plan row" option. No schema
change (the U12 release carries no migration), admin-only reads like every
plan document, and the lifecycle is exactly right: when the applied plan is
displaced (next apply or restore) its document is overwritten with the
as-evolved capture and pending proposals die with the plan they were
computed against. ``fleet_plan_dao`` owns accept/dismiss/list against the
same key.

Aging: a proposal never auto-applies; after ``SLOT_IN_AGING_DAYS`` (default
14, configurable via the ``SLOT_IN_AGING_DAYS`` env var — an env-read module
constant rather than a ``Settings`` field, keeping the change inside the U12
file set; origin defers the default to planning, "a config value, not
schema") it is marked expired: still listed, no longer acceptable, the
student still visibly unassigned.

Trigger: dispatched best-effort in BackgroundTasks from the students_live
mutations (create / update / bulk commit), beside the U13 notify wiring — a
failed proposal generation must never fail the enrolment, so everything here
is wrapped and logged, never raised. Generation is idempotent per student:
it replaces any stored records for the triggered students (an address change
re-proposes; a student whose legs are now all linked has their records
dropped).

Because the trigger is ordinary CRUD, the pass is bounded and lock-light
(mirroring ``fleet_dao.regenerate_route_stops``'s two-phase shape): the
duration-matrix fan-out and the insertion scoring run with NO transaction
open, the point count is capped by ``SLOT_IN_MAX_MATRIX_POINTS`` (over the
cap the pass skips with one log line and the student stays visibly
unassigned), and the plan row is locked only for the short write, which is
discarded outright if the applied plan changed mid-compute (the
``_generate_for_school`` fingerprint rule).
"""
from __future__ import annotations

import datetime as dt
import itertools
import logging
import os
import uuid
from typing import Any

from psycopg import Rollback
from psycopg.types.json import Jsonb

from app.core.db import UNSET, get_connection, scoped_transaction
from app.services import geo_service
from app.services.plan_solver import (
    CONSTRAINT_SEATS,
    CONSTRAINT_STOP_CAP,
    DEFAULT_STOP_CAP,
    LEG_AFTERNOON,
    LEG_MORNING,
    LEGS,
    PATTERN_AFTERNOON,
    PATTERN_MORNING,
    PATTERN_SPLIT,
    STOP_COLLAPSE_RADIUS_M,
    _eval_order,  # deliberate internals reuse — the plan names U2's evaluation as the scoring authority
)

logger = logging.getLogger("saferide.slot_in")

# The document key on the APPLIED plan row holding the proposal list.
PROPOSALS_KEY = "slot_in_proposals"

# Configurable aging window (config value, not schema — see module docstring).
SLOT_IN_AGING_DAYS = int(os.environ.get("SLOT_IN_AGING_DAYS", "14"))

# Bounds the CRUD-triggered background fan-out: generation runs off EVERY
# ordinary student create/update/bulk-upload, and its duration matrix
# (``geo_service.compute_duration_matrix``) is a chunked sequential network
# fan-out with an 8 s timeout per chunk — unbounded, a large school makes
# routine student CRUD run long. Over this many matrix points (applied
# routes' stops + school gate + depots + the triggering homes) the pass
# skips generation with one log line and writes nothing: the student stays
# visibly unassigned, resolvable via the review surface. Termly planning has
# no such bound because the admin invoked it deliberately.
SLOT_IN_MAX_MATRIX_POINTS = int(os.environ.get("SLOT_IN_MAX_MATRIX_POINTS", "120"))

# A per-child ride increase below this rounds to zero minutes and does not
# count as a delay in the proposal statement.
_DELAY_FLOOR_S = 30.0

_NUM_WORDS = {
    1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
    6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten",
}

Point = tuple[float, float]


def utcnow_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_expired(record: dict, *, now: dt.datetime | None = None) -> bool:
    """Whether a stored record is past the aging window. A malformed
    ``created_at`` is treated as fresh (never silently un-acceptable)."""
    raw = record.get("created_at")
    if not isinstance(raw, str):
        return False
    try:
        created = dt.datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError:
        return False
    now = now or dt.datetime.now(dt.timezone.utc)
    return (now - created) > dt.timedelta(days=SLOT_IN_AGING_DAYS)


def _pattern_legs(pattern: str | None) -> tuple[str, ...]:
    if pattern == PATTERN_MORNING:
        return (LEG_MORNING,)
    if pattern == PATTERN_AFTERNOON:
        return (LEG_AFTERNOON,)
    return (LEG_MORNING, LEG_AFTERNOON)


def _children_phrase(n: int) -> str:
    word = _NUM_WORDS.get(n, str(n))
    return f"{word} child" if n == 1 else f"{word} children"


def _position_phrase(position: int, stop_count: int, join: bool) -> str:
    """1-based wording over the leg's CURRENT stops for a 0-based insertion
    index: inserting before stop index p lands between stops p and p+1."""
    if join:
        return f"joining stop {position + 1}"
    if position <= 0:
        return "before stop 1"
    if position >= stop_count:
        return f"after stop {stop_count}"
    return f"between stop {position} and {position + 1}"


def _leg_statement(leg_info: dict) -> str:
    pos = _position_phrase(
        leg_info["position"], leg_info["stop_count"], leg_info["join"]
    )
    minutes = leg_info["delay_minutes"]
    delayed = leg_info["delayed_children"]
    if minutes >= 1 and delayed >= 1:
        unit = "minute" if minutes == 1 else "minutes"
        return f"{pos}, +{minutes} {unit} for {_children_phrase(delayed)}"
    return f"{pos}, no delay for other children"


def _primary_leg(legs: dict[str, dict]) -> str:
    return LEG_MORNING if LEG_MORNING in legs else LEG_AFTERNOON


def proposal_text(bus_name: str, legs: dict[str, dict]) -> str:
    """The one-line AE2 statement, composed from the primary (morning-first)
    leg: 'Bus 2, between stop 3 and 4, +4 minutes for two children'."""
    return f"{bus_name}, {_leg_statement(legs[_primary_leg(legs)])}"


# --- world loading -------------------------------------------------------------


def _load_routes(conn, school_id: str) -> list[dict]:
    """The school's applied plan routes with their stop groups. Candidates =
    ``plan_ordered`` routes on an in-service bus (a manual freeze keeps
    ``plan_ordered`` set, so frozen routes stay candidates — AE7); a route
    whose stops have lost coordinates is skipped defensively (unevaluable)."""
    routes = conn.execute(
        "select r.id, r.type, r.bus_id, r.manual_stop_order, "
        "b.name as bus_name, b.capacity, b.depot_lat, b.depot_lng "
        "from live_routes r join live_buses b on b.id = r.bus_id "
        "where r.school_id = %s and r.plan_ordered "
        "and b.availability = 'in-service' "
        "order by b.name asc, r.id asc",
        (school_id,),
    ).fetchall()
    out: list[dict] = []
    for r in routes:
        rows = conn.execute(
            "select stop_order, lat, lng, student_id, is_school_gate "
            "from live_route_stops where route_id = %s and not is_school_gate "
            "and student_id is not null "
            "order by stop_order asc, name asc",
            (r["id"],),
        ).fetchall()
        groups: list[dict] = []
        by_order: dict[int, dict] = {}
        children = 0
        unlocatable = False
        for row in rows:
            children += 1
            g = by_order.get(row["stop_order"])
            if g is None:
                if row["lat"] is None or row["lng"] is None:
                    unlocatable = True
                    break
                g = {
                    "order": row["stop_order"],
                    "point": (float(row["lat"]), float(row["lng"])),
                    "students": [],
                }
                by_order[row["stop_order"]] = g
                groups.append(g)
            g["students"].append(str(row["student_id"]))
        if unlocatable:
            logger.warning(
                "slot-in: route %s has coordinate-less stops — skipped as a candidate",
                r["id"],
            )
            continue
        depot = None
        if r["depot_lat"] is not None and r["depot_lng"] is not None:
            # Drafted buses are single-trip (multi-trip chains are excluded
            # from drafting scope), so the depot boundary leg applies to this
            # route on both directions.
            depot = (float(r["depot_lat"]), float(r["depot_lng"]))
        out.append({
            "id": str(r["id"]),
            "leg": r["type"],
            "bus_id": str(r["bus_id"]),
            "bus_name": r["bus_name"],
            "capacity": r["capacity"],
            "frozen": bool(r["manual_stop_order"]),
            "depot": depot,
            "groups": groups,
            "children": children,
        })
    return out


def _matrix_points(routes: list[dict], school_pt: Point,
                   homes: list[Point]) -> list[Point]:
    """Every deduplicated point the insertion evaluation can query — gate,
    depots, applied stop groups, the triggering homes — in first-seen order.
    Split from :func:`_matrix_fn` so the ``SLOT_IN_MAX_MATRIX_POINTS`` guard
    can count the fan-out BEFORE any provider call is made."""
    seen: set[Point] = set()
    points: list[Point] = []

    def add(p: Point) -> None:
        if p not in seen:
            seen.add(p)
            points.append(p)

    add(school_pt)
    for route in routes:
        if route["depot"] is not None:
            add(route["depot"])
        for g in route["groups"]:
            add(g["point"])
    for h in homes:
        add(h)
    return points


def _matrix_fn(points: list[Point]):
    """One in-memory directed matrix over ``points`` (from
    :func:`_matrix_points`). Returns ``(d, degraded)`` — never persisted
    (U3). Must be called with NO transaction open on the generation
    connection: the provider round-trips must not extend any lock or
    snapshot (the ``regenerate_route_stops`` rule)."""
    index = {p: i for i, p in enumerate(points)}
    result = geo_service.compute_duration_matrix(
        [{"lat": p[0], "lng": p[1]} for p in points]
    )
    grid = result["matrix"]

    def d(a: Point, b: Point) -> float:
        return float(grid[index[a]][index[b]])

    return d, bool(result["degraded"])


# --- evaluation ----------------------------------------------------------------


def _route_eval(route: dict, school_pt: Point, d, order_points: list[Point],
                counts: list[int]):
    """Solver evaluation of one visit order (U2 internals): points indexed
    positionally, children counts per stop."""
    pt = list(order_points)
    order = list(range(len(pt)))
    count_map = {i: counts[i] for i in order}
    return _eval_order(order, count_map, route["leg"], route["depot"],
                       school_pt, pt, d)


def _insertion_options(route: dict, school_pt: Point, d, home: Point) -> dict:
    """Feasibility + every candidate insertion for one (route, home).

    Returns ``{seats_ok, cap_ok, options}`` where each option is
    ``{position, join, eval, delay_seconds, delayed_student_ids}``. A join
    (home within the solver's 30 m collapse radius of an existing stop) adds
    a seat but no stop and delays nobody. Existing children never move: only
    the one new point enters the order.
    """
    capacity = route["capacity"]
    seats_ok = capacity is None or route["children"] + 1 <= capacity
    groups = route["groups"]
    join_index = next(
        (i for i, g in enumerate(groups)
         if geo_service.haversine_m(home, g["point"]) <= STOP_COLLAPSE_RADIUS_M),
        None,
    )
    cap_ok = join_index is not None or len(groups) + 1 <= DEFAULT_STOP_CAP
    if not seats_ok or not cap_ok:
        return {"seats_ok": seats_ok, "cap_ok": cap_ok, "options": []}

    base_points = [g["point"] for g in groups]
    base_counts = [len(g["students"]) for g in groups]

    if join_index is not None:
        counts = list(base_counts)
        counts[join_index] += 1
        ev = _route_eval(route, school_pt, d, base_points, counts)
        return {
            "seats_ok": True, "cap_ok": True,
            "options": [{
                "position": join_index, "join": True, "eval": ev,
                "delay_seconds": 0.0, "delayed_student_ids": [],
            }],
        }

    base_eval = _route_eval(route, school_pt, d, base_points, base_counts)
    options = []
    for pos in range(len(groups) + 1):
        pts = base_points[:pos] + [home] + base_points[pos:]
        counts = base_counts[:pos] + [1] + base_counts[pos:]
        ev = _route_eval(route, school_pt, d, pts, counts)
        # Existing stop i's ride sits at index i (i < pos) or i+1 (i >= pos)
        # in the new order; the detour delta is shared by every delayed stop.
        delay = 0.0
        delayed: list[str] = []
        for i, g in enumerate(groups):
            new_idx = i if i < pos else i + 1
            delta = ev.rides[new_idx] - base_eval.rides[i]
            if delta >= _DELAY_FLOOR_S:
                delay = max(delay, delta)
                delayed.extend(g["students"])
        options.append({
            "position": pos, "join": False, "eval": ev,
            "delay_seconds": delay, "delayed_student_ids": delayed,
        })
    return {"seats_ok": True, "cap_ok": True, "options": options}


def _school_key(base_evals: dict[str, Any], replacements: dict[str, Any]) -> tuple:
    """The plan-level lexicographic tuple (worst, total, driving) with the
    candidate's routes swapped in — only the affected routes change, so the
    school-wide objective is the base evals with replacements."""
    worst = total = driving = 0.0
    for rid, ev in base_evals.items():
        use = replacements.get(rid, ev)
        if use.worst > worst:
            worst = use.worst
        total += use.total
        driving += use.driving
    return (worst, total, driving)


def _best_bus_candidate(
    needed_legs: list[str],
    routes: list[dict],
    base_evals: dict[str, Any],
    school_pt: Point,
    d,
    home: Point,
) -> tuple[dict | None, str | None]:
    """Cheapest insertion across every claimed bus serving ALL needed legs.

    Returns ``(candidate, constraint)``: the winning candidate
    ``{bus_id, bus_name, legs: {leg: option+route}}`` or ``None`` with the
    binding constraint named per the solver's attribution rule.
    """
    by_bus: dict[str, dict[str, dict]] = {}
    for route in routes:
        by_bus.setdefault(route["bus_id"], {})[route["leg"]] = route

    best: tuple[tuple, tuple, dict] | None = None
    any_cap_only_blocked = False   # feasible if seats were relaxed
    any_seats_only_blocked = False  # feasible if the stop cap were relaxed
    for bus_id in sorted(by_bus):
        legs_map = by_bus[bus_id]
        if any(leg not in legs_map for leg in needed_legs):
            continue
        per_leg = {leg: _insertion_options(legs_map[leg], school_pt, d, home)
                   for leg in needed_legs}
        if all(f["cap_ok"] for f in per_leg.values()) and not all(
            f["seats_ok"] for f in per_leg.values()
        ):
            any_cap_only_blocked = True
        if all(f["seats_ok"] for f in per_leg.values()) and not all(
            f["cap_ok"] for f in per_leg.values()
        ):
            any_seats_only_blocked = True
        if any(not f["options"] for f in per_leg.values()):
            continue
        # Joint enumeration over per-leg positions (bounded by the stop cap):
        # the plan-level key decides, position tuple breaks ties determinist-
        # ically, bus order (sorted id) breaks the rest.
        for combo in itertools.product(*(per_leg[leg]["options"] for leg in needed_legs)):
            repl = {
                legs_map[leg]["id"]: opt["eval"]
                for leg, opt in zip(needed_legs, combo)
            }
            key = _school_key(base_evals, repl)
            tie = tuple(opt["position"] for opt in combo)
            if best is None or (key, tie) < (best[0], best[1]):
                best = (key, tie, {
                    "bus_id": bus_id,
                    "bus_name": legs_map[needed_legs[0]]["bus_name"],
                    "legs": {
                        leg: {**opt, "route": legs_map[leg]}
                        for leg, opt in zip(needed_legs, combo)
                    },
                })
    if best is not None:
        return best[2], None
    # Binding-constraint attribution (the solver's rule): 'seats' if relaxing
    # seat capacity alone would admit the child on some bus, else 'stop cap'
    # if relaxing the cap alone would; when both bind everywhere, 'seats'.
    if any_cap_only_blocked:
        return None, CONSTRAINT_SEATS
    if any_seats_only_blocked:
        return None, CONSTRAINT_STOP_CAP
    return None, CONSTRAINT_SEATS


# --- record building -----------------------------------------------------------


def _leg_record(opt: dict) -> dict:
    route = opt["route"]
    minutes = int(round(opt["delay_seconds"] / 60.0))
    delayed = sorted(set(opt["delayed_student_ids"])) if minutes >= 1 else []
    info = {
        "route_id": route["id"],
        "position": int(opt["position"]),
        "join": bool(opt["join"]),
        "stop_count": len(route["groups"]),
        "frozen": bool(route["frozen"]),
        "delay_minutes": minutes if delayed else 0,
        "delayed_children": len(delayed),
        "delayed_student_ids": delayed,
    }
    info["statement"] = _leg_statement(info)
    return info


def _proposal_records(student: dict, needed_legs: list[str], routes: list[dict],
                      base_evals: dict, school_pt: Point, d) -> list[dict]:
    """The stored records for one student: one proposal covering every needed
    leg for a non-split rider, one per leg for a split rider, or one
    unplaceable record naming the binding constraint."""
    home: Point = (float(student["home_lat"]), float(student["home_lng"]))
    pattern = student["ridership_pattern"] or "both_ways"
    leg_sets = (
        [[leg] for leg in needed_legs] if pattern == PATTERN_SPLIT
        else [list(needed_legs)]
    )
    records: list[dict] = []
    for legs in leg_sets:
        candidate, constraint = _best_bus_candidate(
            legs, routes, base_evals, school_pt, d, home
        )
        base = {
            "id": uuid.uuid4().hex,
            "student_id": str(student["id"]),
            "student_name": student["name"],
            "pattern": pattern,
            "created_at": utcnow_iso(),
        }
        if candidate is None:
            records.append({
                **base,
                "kind": "unplaceable",
                "constraint": constraint,
                "legs": {leg: {"constraint": constraint} for leg in legs},
                "text": (
                    f"No feasible insertion for {student['name']} — {constraint}"
                ),
            })
            continue
        leg_infos = {leg: _leg_record(candidate["legs"][leg]) for leg in LEGS
                     if leg in candidate["legs"]}
        records.append({
            **base,
            "kind": "proposal",
            "bus_id": candidate["bus_id"],
            "bus_name": candidate["bus_name"],
            "legs": leg_infos,
            "text": proposal_text(candidate["bus_name"], leg_infos),
        })
    return records


# --- trigger entry point -------------------------------------------------------


def propose_slot_ins(student_ids: list[str], scope: object = UNSET) -> dict | None:
    """Generate (or refresh) slot-in records for the given students.

    Dispatched via BackgroundTasks beside the U13 notify wiring — BEST
    EFFORT: any failure is logged and swallowed, never raised into the
    enrolment's response path. Per school: requires an APPLIED plan row (the
    storage) and at least one ``plan_ordered`` route (the trigger condition);
    otherwise a silent no-op. Stored records for the triggered students are
    replaced wholesale (drop-then-append), so a student whose legs are all
    linked simply loses their stale records.

    ``scope`` is the dispatching request's scope (U7): the connection opens
    through it, and each phase's mid-connection commit runs through
    ``scoped_transaction`` so the transaction-local GUC survives the commit
    boundaries. An unthreaded dispatch inside a SchoolScope request fails
    loudly under the strict seam (and is swallowed into the None return —
    never a silent partial success).
    """
    try:
        summary: dict[str, int] = {"students": 0, "records": 0}
        sids = sorted({str(s) for s in (student_ids or [])})
        if not sids:
            return summary
        with get_connection(scope) as conn:
            students = conn.execute(
                "select id, name, school_id, home_lat, home_lng, ridership_pattern "
                "from live_students where id = any(%s::uuid[]) "
                "order by school_id, name, id",
                (sids,),
            ).fetchall()
            by_school: dict[str, list[dict]] = {}
            for s in students:
                if s["school_id"] is None:
                    continue
                by_school.setdefault(str(s["school_id"]), []).append(dict(s))
            for school_id in sorted(by_school):
                counts = _generate_for_school(
                    conn, school_id, by_school[school_id], scope
                )
                if counts:
                    summary["students"] += counts["students"]
                    summary["records"] += counts["records"]
        logger.info(
            "slot-in generation: triggered=%d refreshed_students=%d records=%d",
            len(sids), summary["students"], summary["records"],
        )
        return summary
    except Exception:  # noqa: BLE001 — best-effort by contract (see docstring)
        logger.exception("slot-in proposal generation failed")
        return None


def _generate_for_school(
    conn, school_id: str, students: list[dict], scope: object = UNSET
) -> dict | None:
    """One school's generation pass — two phases, mirroring
    ``fleet_dao.regenerate_route_stops``: the slow provider work runs with no
    LOCK held, and the plan-row lock is taken only for the short write.

    Phase 1 (reads only, NO locks): the applied plan's identity fingerprint
    (id + applied_at), the school, the candidate routes and the triggering
    students' link state. The duration matrix and the insertion scoring run
    between the phases on plain in-memory data.

    Both phases run inside ``scoped_transaction(conn, scope)`` (U7): the
    plain ``conn.commit()`` boundaries this used to place here silently
    dropped the transaction-local school GUC, so every statement after the
    first commit ran unscoped — exactly the hazard the U5 seam test pins.
    ``scoped_transaction`` re-arms the GUC on both sides of each block, so
    the phase-2 write (and any follow-up statement) still carries the
    school set. On a connection whose checkout armed the GUC the blocks
    nest as savepoints and the pool release commits the lot; phase 1 takes
    no locks, so the provider fan-out between the phases still extends no
    lock (READ COMMITTED holds no cross-statement snapshot either).

    Size guard: the matrix point count is capped by
    ``SLOT_IN_MAX_MATRIX_POINTS`` (this is CRUD-triggered work — see the
    constant's comment). Over the cap the pass skips with one WARNING and
    writes nothing; the students stay visibly unassigned.

    Phase 2 (short locked write): take the applied plan row FOR UPDATE (the
    storage row — serializes concurrent generations and the accept/dismiss
    writers, and matches apply's plan-row-first lock order; no route locks
    are taken), and RE-VALIDATE the row against the phase-1 fingerprint.
    Fingerprint-discard rule: if the applied plan changed between the
    phases (a new apply displaced the row — different id — or a restore
    re-applied over it — same id, new applied_at), the computed proposals
    were scored against a plan that no longer governs the live routes, so
    they are dropped (``Rollback`` inside the block) with one log line —
    the best-effort contract; the mutation stream that displaced the plan
    owns any regeneration. On a match, the merge target is the LOCKED
    row's freshly-read document, so accept/dismiss edits that landed
    between the phases survive the wholesale per-student replace.
    """
    with scoped_transaction(conn, scope):
        plan = conn.execute(
            "select id, applied_at from live_fleet_plans "
            "where school_id = %s and status = 'applied' "
            "order by applied_at desc nulls last, created_at desc limit 1",
            (school_id,),
        ).fetchone()
        school = conn.execute(
            "select id, lat, lng from live_schools where id = %s", (school_id,)
        ).fetchone()
        routes = _load_routes(conn, school_id) if plan else []
        linked: set[tuple[str, str]] = set()
        for r in conn.execute(
            "select sr.student_id, sr.route_type from live_student_routes sr "
            "where sr.student_id = any(%s::uuid[])",
            ([str(s["id"]) for s in students],),
        ).fetchall():
            linked.add((str(r["student_id"]), r["route_type"]))
    # phase 1 over — no lock held across the provider fan-out below

    if not plan:
        return None  # no applied plan — the trigger condition fails silently
    if not school or school["lat"] is None or school["lng"] is None:
        return None

    targets: list[tuple[dict, list[str]]] = []
    refreshed: set[str] = set()
    for s in students:
        sid = str(s["id"])
        if s["home_lat"] is None or s["home_lng"] is None:
            continue  # not plannable — U10's triage owns unresolved addresses
        needed = [leg for leg in _pattern_legs(s["ridership_pattern"])
                  if (sid, leg) not in linked]
        refreshed.add(sid)  # stale records drop even when nothing is needed
        if needed:
            targets.append((s, needed))
    if not refreshed:
        return {"students": 0, "records": 0}

    new_records: list[dict] = []
    if targets and routes:
        school_pt: Point = (float(school["lat"]), float(school["lng"]))
        homes = [(float(s["home_lat"]), float(s["home_lng"])) for s, _ in targets]
        points = _matrix_points(routes, school_pt, homes)
        if len(points) > SLOT_IN_MAX_MATRIX_POINTS:
            logger.warning(
                "slot-in generation skipped: %d points > cap %d "
                "(SLOT_IN_MAX_MATRIX_POINTS, school %s) — the triggered "
                "students stay visibly unassigned, resolvable via the "
                "review surface",
                len(points), SLOT_IN_MAX_MATRIX_POINTS, school_id,
            )
            return None
        d, _degraded = _matrix_fn(points)
        base_evals = {
            route["id"]: _route_eval(
                route, school_pt, d,
                [g["point"] for g in route["groups"]],
                [len(g["students"]) for g in route["groups"]],
            )
            for route in routes
        }
        for s, needed in targets:
            new_records.extend(
                _proposal_records(s, needed, routes, base_evals, school_pt, d)
            )

    # Phase 2 — the short locked write, gated on the plan fingerprint. The
    # scoped_transaction keeps the GUC armed through the write and re-arms
    # after it, so the proposals land (and stay readable) under A's scope
    # even after the block's boundary.
    discarded = False
    with scoped_transaction(conn, scope):
        locked = conn.execute(
            "select id, applied_at, document from live_fleet_plans "
            "where school_id = %s and status = 'applied' "
            "order by applied_at desc nulls last, created_at desc limit 1 for update",
            (school_id,),
        ).fetchone()
        if (locked is None or str(locked["id"]) != str(plan["id"])
                or locked["applied_at"] != plan["applied_at"]):
            discarded = True
            raise Rollback  # drop the block's work; nothing was merged
        document = dict(locked["document"] or {})
        kept = [r for r in (document.get(PROPOSALS_KEY) or [])
                if str(r.get("student_id")) not in refreshed]
        document[PROPOSALS_KEY] = kept + new_records
        conn.execute(
            "update live_fleet_plans set document = %s where id = %s",
            (Jsonb(document), locked["id"]),
        )
    if discarded:
        logger.info(
            "slot-in generation discarded for school %s: the applied plan "
            "changed mid-compute (fingerprint drift) — proposals dropped, "
            "best-effort contract",
            school_id,
        )
        return None
    return {"students": len(refreshed), "records": len(new_records)}
