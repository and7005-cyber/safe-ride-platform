"""Fleet plan solver (U2): partition students across buses and order each leg
under hard constraints and exact lexicographic objectives.

Pure Python, stdlib only — no DB, no network. The directed travel-time matrix
is injected (U3 provides it); the solver works in durations (seconds) only and
wall-clock times are computed downstream by the existing machinery
(``solve_morning_departure``).

Matrix contract
    ``matrix`` is either a callable ``(origin, dest) -> seconds`` over
    ``(lat, lng)`` tuples, or a mapping keyed by ``(origin, dest)`` pairs of
    such tuples. Durations are directed (``d(a, b)`` need not equal
    ``d(b, a)``) and must be non-negative. Only route-evaluation pairs are
    ever queried (consecutive stops, depot->first stop, last stop->school and
    their PM counterparts); seeding geometry uses haversine on raw
    coordinates, never the matrix.

Semantics pinned by the tests
    * Stops, not students, are the routing unit: homes within
      ``STOP_COLLAPSE_RADIUS_M`` collapse to one stop carrying combined
      demand. Seat capacity counts children; the stop cap counts stops.
    * Mirroring means roster identity per bus across legs, modulo patterns —
      never stop-order symmetry. The PM order is computed independently
      against the directed matrix.
    * Child ride seconds: AM = their stop -> school along the order;
      PM = school -> their stop. Depot->first (AM) and last->depot (PM) legs
      count only toward total driving.
    * Candidates compare as the exact tuple
      ``(worst child ride, total child ride, total driving)`` evaluated over
      both legs jointly. Internally the number of unplaced (student, leg)
      pairs is prepended so a plan that seats more children always dominates;
      the reported objective stays the three-tuple.
    * A split rider occupies a seat on each leg and rides *different* buses
      per leg (that is what the pattern means); a bus pin overrides the
      inequality if the caller pins both legs to one bus.
    * Unplaceable output names the binding constraint: ``seats`` if relaxing
      seat capacity alone would admit the unit on some bus, else ``stop cap``
      if relaxing the stop cap alone would; when both bind everywhere,
      ``seats``.

Determinism
    All search state derives from the explicit ``seed`` (``random.Random``)
    and ordered lists — no iteration over sets or unordered dicts where order
    affects results. The wall-clock cap is an anytime safety valve: the best
    plan found so far is always returned. Same seed + inputs -> identical
    output whenever the restart budget completes inside the cap (the case at
    pilot scale; a cap-truncated run at much larger scale trades
    reproducibility for availability, per the plan's headroom note).

Search shape
    Seeds: sweep by angle around the school (several offsets),
    largest-demand-first packing, capacity-balanced k-means, seeded-random
    restarts. Per-route ordering: exhaustive for tiny routes (exactly
    optimal), else cheapest insertion then 2-opt/or-opt. Inter-route:
    relocate and swap, screened by a cheap *achievable* bound (current order
    with the moved stop removed / cheapest-inserted); because the bound is a
    feasible ordering, ``bound < incumbent`` implies the fully re-ordered
    move also improves, so acceptance stays monotone and cheap.

Deliberate simplifications (documented product/engineering choices)
    * Collapse is greedy in student input order against each group's anchor
      (its first member) — siblings at one address are the target case.
    * Non-split members of a collapse group ride together as one unit; a bus
      pin on any of them pins the whole group. Groups whose members are
      pinned to *different* buses split into one unit per pinned bus (plus
      one for the unpinned rest).
    * An order pin fixes a stop at a 0-based position in its route's order
      (int = every leg the student rides; mapping = per leg). Positions are
      clamped to the route length; conflicting pins degrade deterministically
      to the unpinned ordering for that route.
"""
from __future__ import annotations

import dataclasses
import itertools
import math
import random
import time
from typing import Any, Callable, Mapping, Sequence

from app.services.geo_service import haversine_m

Point = tuple[float, float]
MatrixFn = Callable[[Point, Point], float]

# Homes within this distance collapse to one stop carrying combined demand.
# Deliberate product choice: siblings at one address share a stop (and a bus).
STOP_COLLAPSE_RADIUS_M = 30.0
# Server-side stop cap (shared meaning with the frontend PLANNER_STOPS_CAP).
DEFAULT_STOP_CAP = 24
# Anytime wall-clock budget for one solve call.
DEFAULT_TIME_CAP_S = 5.0

LEG_MORNING = "morning"
LEG_AFTERNOON = "afternoon"
LEGS = (LEG_MORNING, LEG_AFTERNOON)

PATTERN_BOTH = "both_ways"
PATTERN_MORNING = "morning_only"
PATTERN_AFTERNOON = "afternoon_only"
PATTERN_SPLIT = "split"
PATTERNS = (PATTERN_BOTH, PATTERN_MORNING, PATTERN_AFTERNOON, PATTERN_SPLIT)

CONSTRAINT_SEATS = "seats"
CONSTRAINT_STOP_CAP = "stop cap"

# Routes with at most this many stops are ordered by exhaustive enumeration —
# exactly optimal and cheaper than the heuristics at that size.
_EXHAUSTIVE_ORDER_MAX = 5
_MAX_IMPROVE_PASSES = 30
_ORDER_IMPROVE_PASSES = 3
_RANDOM_RESTARTS = 3
_SWEEP_OFFSETS = 4

_RIDES = {
    PATTERN_BOTH: (LEG_MORNING, LEG_AFTERNOON),
    PATTERN_MORNING: (LEG_MORNING,),
    PATTERN_AFTERNOON: (LEG_AFTERNOON,),
    PATTERN_SPLIT: (LEG_MORNING, LEG_AFTERNOON),
}


# --- input normalisation ----------------------------------------------------

@dataclasses.dataclass
class _Rider:
    idx: int
    id: Any
    name: str
    lat: float
    lng: float
    pattern: str
    bus_pin: dict[str, Any]      # leg -> bus id
    order_pin: dict[str, int]    # leg -> 0-based position

    @property
    def legs(self) -> tuple[str, ...]:
        return _RIDES[self.pattern]


@dataclasses.dataclass
class _Bus:
    idx: int
    id: Any
    name: str
    capacity: int
    depot: Point | None


@dataclasses.dataclass
class _Cluster:
    idx: int
    lat: float
    lng: float
    members: list[_Rider]

    @property
    def point(self) -> Point:
        return (self.lat, self.lng)


@dataclasses.dataclass
class _Unit:
    """One indivisible assignable: a group of non-split cluster members that
    ride one bus on both their legs, or a single (split student, leg)."""
    idx: int
    cluster: _Cluster
    members: list[_Rider]
    seats: dict[str, int]        # leg -> children riding that leg
    pinned_bus: Any | None
    split_of: Any | None = None  # student id when this is a split leg-unit
    split_leg: str | None = None

    @property
    def legs(self) -> tuple[str, ...]:
        return tuple(leg for leg in LEGS if self.seats.get(leg, 0) > 0)

    @property
    def children(self) -> int:
        return sum(self.seats.values())


def _per_leg(value: Any, what: str) -> dict[str, Any]:
    """Normalise a scalar-or-mapping pin to a per-leg dict."""
    if value is None:
        return {}
    if isinstance(value, Mapping):
        bad = set(value) - set(LEGS)
        if bad:
            raise ValueError(f"unknown leg(s) {sorted(bad)} in {what}")
        return {leg: value[leg] for leg in LEGS if leg in value and value[leg] is not None}
    return {leg: value for leg in LEGS}


def _parse_students(students: Sequence[Mapping[str, Any]]) -> list[_Rider]:
    riders = []
    for i, s in enumerate(students):
        pattern = s.get("pattern") or PATTERN_BOTH
        if pattern not in PATTERNS:
            raise ValueError(f"student {s.get('id')!r}: unknown pattern {pattern!r}")
        if s.get("lat") is None or s.get("lng") is None:
            raise ValueError(f"student {s.get('id')!r} has no coordinates; "
                             "the caller must filter unresolved students")
        riders.append(_Rider(
            idx=i, id=s["id"], name=str(s.get("name") or s["id"]),
            lat=float(s["lat"]), lng=float(s["lng"]), pattern=pattern,
            bus_pin=_per_leg(s.get("bus_pin"), "bus_pin"),
            order_pin={leg: int(v) for leg, v in
                       _per_leg(s.get("order_pin"), "order_pin").items()},
        ))
    return riders


def _parse_buses(buses: Sequence[Mapping[str, Any]]) -> list[_Bus]:
    fleet = []
    for i, b in enumerate(buses):
        depot: Point | None = None
        d = b.get("depot")
        if d is not None and d.get("lat") is not None and d.get("lng") is not None:
            depot = (float(d["lat"]), float(d["lng"]))
        elif b.get("depot_lat") is not None and b.get("depot_lng") is not None:
            depot = (float(b["depot_lat"]), float(b["depot_lng"]))
        fleet.append(_Bus(idx=i, id=b["id"], name=str(b.get("name") or b["id"]),
                          capacity=int(b["capacity"]), depot=depot))
    return fleet


def _collapse(riders: list[_Rider]) -> list[_Cluster]:
    """Greedy stop collapse in input order against each group's anchor."""
    clusters: list[_Cluster] = []
    for r in riders:
        for c in clusters:
            if haversine_m((r.lat, r.lng), (c.lat, c.lng)) <= STOP_COLLAPSE_RADIUS_M:
                c.members.append(r)
                break
        else:
            clusters.append(_Cluster(idx=len(clusters), lat=r.lat, lng=r.lng,
                                     members=[r]))
    return clusters


def _uniform_pin(rider: _Rider) -> Any | None:
    """The single pinned bus of a non-split rider over the legs they ride."""
    pins = {rider.bus_pin[leg] for leg in rider.legs if leg in rider.bus_pin}
    if len(pins) > 1:
        raise ValueError(f"student {rider.id!r} is not split but carries "
                         "conflicting per-leg bus pins")
    return next(iter(pins)) if pins else None


def _build_units(clusters: list[_Cluster]) -> list[_Unit]:
    units: list[_Unit] = []

    def add(members: list[_Rider], cluster: _Cluster, pinned: Any | None,
            split_of: Any = None, split_leg: str | None = None) -> None:
        seats = {leg: 0 for leg in LEGS}
        for m in members:
            legs = (split_leg,) if split_leg else m.legs
            for leg in legs:
                seats[leg] += 1
        if not any(seats.values()):
            return
        units.append(_Unit(idx=len(units), cluster=cluster, members=members,
                           seats=seats, pinned_bus=pinned,
                           split_of=split_of, split_leg=split_leg))

    for cluster in clusters:
        non_split = [m for m in cluster.members if m.pattern != PATTERN_SPLIT]
        if non_split:
            pin_values: list[Any] = []
            for m in non_split:
                p = _uniform_pin(m)
                if p is not None and p not in pin_values:
                    pin_values.append(p)
            if len(pin_values) <= 1:
                # a pinned sibling drags the unpinned ones along: one unit
                add(non_split, cluster, pin_values[0] if pin_values else None)
            else:
                for p in pin_values:
                    add([m for m in non_split if _uniform_pin(m) == p], cluster, p)
                rest = [m for m in non_split if _uniform_pin(m) is None]
                if rest:
                    add(rest, cluster, None)
        for m in cluster.members:
            if m.pattern == PATTERN_SPLIT:
                for leg in m.legs:
                    add([m], cluster, m.bus_pin.get(leg),
                        split_of=m.id, split_leg=leg)
    return units


def _cluster_order_pins(clusters: list[_Cluster]) -> dict[int, dict[str, int]]:
    """cluster idx -> {leg: pinned position}; first pinning member wins."""
    pins: dict[int, dict[str, int]] = {}
    for c in clusters:
        for m in c.members:
            for leg, pos in m.order_pin.items():
                pins.setdefault(c.idx, {}).setdefault(leg, pos)
    return pins


def _matrix_fn(matrix: MatrixFn | Mapping) -> MatrixFn:
    if callable(matrix):
        raw = matrix
    elif isinstance(matrix, Mapping):
        raw = lambda a, b: matrix[(a, b)]  # noqa: E731
    else:
        raise TypeError("matrix must be a callable (origin, dest) -> seconds "
                        "or a mapping keyed by (origin, dest) point pairs")
    cache: dict[tuple[Point, Point], float] = {}

    def d(a: Point, b: Point) -> float:
        if a == b:
            return 0.0
        key = (a, b)
        if key not in cache:
            cache[key] = float(raw(a, b))
        return cache[key]

    return d


# --- assignment state -------------------------------------------------------

class _State:
    def __init__(self, units: list[_Unit], buses: list[_Bus], stop_cap: int):
        self.units = units
        self.buses = buses
        self.stop_cap = stop_cap
        self.assignment: list[int | None] = [None] * len(units)
        self.load = [{leg: 0 for leg in LEGS} for _ in buses]
        self.stops: list[dict[str, dict[int, int]]] = [
            {leg: {} for leg in LEGS} for _ in buses]
        self.split_at: dict[tuple[Any, str], int] = {}

    def can_place(self, u: _Unit, b: int, *, ignore_seats: bool = False,
                  ignore_cap: bool = False) -> bool:
        bus = self.buses[b]
        if u.split_of is not None:
            other = LEG_AFTERNOON if u.split_leg == LEG_MORNING else LEG_MORNING
            if self.split_at.get((u.split_of, other)) == b and u.pinned_bus is None:
                return False  # split means different buses per leg
        for leg in u.legs:
            if not ignore_seats and self.load[b][leg] + u.seats[leg] > bus.capacity:
                return False
            if (not ignore_cap and u.cluster.idx not in self.stops[b][leg]
                    and len(self.stops[b][leg]) >= self.stop_cap):
                return False
        return True

    def place(self, u: _Unit, b: int) -> None:
        self.assignment[u.idx] = b
        for leg in u.legs:
            self.load[b][leg] += u.seats[leg]
            legstops = self.stops[b][leg]
            legstops[u.cluster.idx] = legstops.get(u.cluster.idx, 0) + u.seats[leg]
        if u.split_of is not None:
            self.split_at[(u.split_of, u.split_leg)] = b

    def unplace(self, u: _Unit) -> None:
        b = self.assignment[u.idx]
        if b is None:
            return
        self.assignment[u.idx] = None
        for leg in u.legs:
            self.load[b][leg] -= u.seats[leg]
            legstops = self.stops[b][leg]
            legstops[u.cluster.idx] -= u.seats[leg]
            if legstops[u.cluster.idx] <= 0:
                del legstops[u.cluster.idx]
        if u.split_of is not None:
            del self.split_at[(u.split_of, u.split_leg)]


# --- route ordering and evaluation ------------------------------------------

_ZERO_KEY = (0.0, 0.0, 0.0)


@dataclasses.dataclass
class _RouteEval:
    order: tuple[int, ...]           # cluster ids in visit order
    rides: dict[int, float]          # cluster id -> per-child ride seconds
    worst: float
    total: float                     # child-weighted total ride seconds
    driving: float                   # includes depot legs

    @property
    def key(self) -> tuple[float, float, float]:
        return (self.worst, self.total, self.driving)


def _eval_order(order: Sequence[int], counts: Mapping[int, int], leg: str,
                depot: Point | None, school: Point,
                pt: Sequence[Point], d: MatrixFn) -> _RouteEval:
    """Full evaluation of one visit order, including per-cluster ride times."""
    if not order:
        return _RouteEval((), {}, 0.0, 0.0, 0.0)
    rides: dict[int, float] = {}
    if leg == LEG_MORNING:
        acc = d(pt[order[-1]], school)
        rides[order[-1]] = acc
        for i in range(len(order) - 2, -1, -1):
            acc = d(pt[order[i]], pt[order[i + 1]]) + acc
            rides[order[i]] = acc
        driving = rides[order[0]]
        if depot is not None:
            driving += d(depot, pt[order[0]])
    else:
        acc = d(school, pt[order[0]])
        rides[order[0]] = acc
        for i in range(1, len(order)):
            acc = acc + d(pt[order[i - 1]], pt[order[i]])
            rides[order[i]] = acc
        driving = rides[order[-1]]
        if depot is not None:
            driving += d(pt[order[-1]], depot)
    worst = max(rides[c] for c in order)
    total = sum(rides[c] * counts[c] for c in order)
    return _RouteEval(tuple(order), rides, worst, total, driving)


class _Evaluator:
    """Route-level evaluation with memoisation. Cache keys are fully value
    determined (bus, leg, sorted (cluster, count) roster) so repeated
    evaluation during local search is a dict lookup."""

    def __init__(self, d: MatrixFn, school: Point, clusters: list[_Cluster],
                 buses: list[_Bus], order_pins: dict[int, dict[str, int]],
                 deadline: float):
        self.d = d
        self.school = school
        self.points = [c.point for c in clusters]
        self.buses = buses
        self.order_pins = order_pins
        self.deadline = deadline
        self.cache: dict[tuple, _RouteEval] = {}

    # -- lean scan key (no allocations); durations are non-negative, so the
    # worst AM ride is the first stop's and the worst PM ride the last's.

    def order_key(self, order: Sequence[int], counts: Mapping[int, int],
                  leg: str, depot: Point | None) -> tuple[float, float, float]:
        if not order:
            return _ZERO_KEY
        d, pt, school = self.d, self.points, self.school
        if leg == LEG_MORNING:
            acc = d(pt[order[-1]], school)
            total = acc * counts[order[-1]]
            for i in range(len(order) - 2, -1, -1):
                acc += d(pt[order[i]], pt[order[i + 1]])
                total += acc * counts[order[i]]
            driving = acc
            if depot is not None:
                driving += d(depot, pt[order[0]])
        else:
            acc = d(school, pt[order[0]])
            total = acc * counts[order[0]]
            for i in range(1, len(order)):
                acc += d(pt[order[i - 1]], pt[order[i]])
                total += acc * counts[order[i]]
            driving = acc
            if depot is not None:
                driving += d(pt[order[-1]], depot)
        return (acc, total, driving)

    def _pins_for(self, ids: Sequence[int], leg: str, n: int) -> dict[int, int]:
        return {cid: min(self.order_pins[cid][leg], n - 1)
                for cid in ids
                if cid in self.order_pins and leg in self.order_pins[cid]}

    def route(self, b: int, leg: str, counts: Mapping[int, int]) -> _RouteEval:
        key = (b, leg, tuple(sorted(counts.items())))
        hit = self.cache.get(key)
        if hit is None:
            hit = self._best_order(b, leg, dict(counts))
            self.cache[key] = hit
        return hit

    def bound_key(self, b: int, leg: str, base: _RouteEval | None,
                  new_counts: Mapping[int, int]) -> tuple[float, float, float]:
        """Cheap *achievable* upper bound for the optimally ordered route on
        ``new_counts``: the current order with vanished stops dropped and at
        most one new stop cheapest-inserted. Because the bound's order is
        feasible, ``bound < incumbent plan key`` implies the fully re-ordered
        plan improves too, so screening with it never breaks monotonicity."""
        if not new_counts:
            return _ZERO_KEY
        ids = sorted(new_counts)
        if self._pins_for(ids, leg, len(ids)):
            return self.route(b, leg, new_counts).key  # exact; pins are rare
        base_order = base.order if base is not None else ()
        order = [c for c in base_order if c in new_counts]
        added = [c for c in ids if c not in base_order]
        if len(added) > 1:  # only whole-unit moves produce bounds; fall back
            return self.route(b, leg, new_counts).key
        depot = self.buses[b].depot
        if added:
            cid = added[0]
            best_key, best_pos = None, 0
            for pos in range(len(order) + 1):
                cand = order[:pos] + [cid] + order[pos:]
                k = self.order_key(cand, new_counts, leg, depot)
                if best_key is None or k < best_key:
                    best_key, best_pos = k, pos
            order.insert(best_pos, cid)
            return best_key
        return self.order_key(order, new_counts, leg, depot)

    # -- ordering search ----------------------------------------------------

    def _best_order(self, b: int, leg: str, counts: dict[int, int]) -> _RouteEval:
        ids = sorted(counts)
        depot = self.buses[b].depot
        n = len(ids)
        pins = self._pins_for(ids, leg, n)
        if n <= _EXHAUSTIVE_ORDER_MAX:
            order = self._exhaustive(ids, counts, leg, depot, pins)
        else:
            order = self._heuristic(ids, counts, leg, depot, pins)
        return _eval_order(order, counts, leg, depot, self.school,
                           self.points, self.d)

    def _exhaustive(self, ids, counts, leg, depot, pins) -> tuple[int, ...]:
        best_key, best_perm = None, None
        for enforce in (True, False) if pins else (False,):
            for perm in itertools.permutations(ids):
                if enforce and any(perm[pos] != cid for cid, pos in pins.items()):
                    continue
                k = self.order_key(perm, counts, leg, depot)
                if best_key is None or k < best_key:
                    best_key, best_perm = k, perm
            if best_perm is not None:
                return best_perm  # conflicting pins degrade to unpinned
        return best_perm

    def _heuristic(self, ids, counts, leg, depot, pins) -> tuple[int, ...]:
        if leg == LEG_MORNING:
            far = lambda cid: self.d(self.points[cid], self.school)  # noqa: E731
        else:
            far = lambda cid: self.d(self.school, self.points[cid])  # noqa: E731
        unpinned = sorted((cid for cid in ids if cid not in pins),
                          key=lambda cid: (-far(cid), cid))
        order: list[int] = []
        for cid in unpinned:  # cheapest insertion by driving increase
            best_pos, best_driving = 0, None
            for pos in range(len(order) + 1):
                cand = order[:pos] + [cid] + order[pos:]
                drv = self.order_key(cand, counts, leg, depot)[2]
                if best_driving is None or drv < best_driving:
                    best_pos, best_driving = pos, drv
            order.insert(best_pos, cid)
        for cid, pos in sorted(pins.items(), key=lambda kv: (kv[1], kv[0])):
            order.insert(min(pos, len(order)), cid)

        best_key = self.order_key(order, counts, leg, depot)
        pinned_set = set(pins)

        def pins_ok(cand: Sequence[int]) -> bool:
            return all(cand[pos] == cid for cid, pos in pins.items())

        for _ in range(_ORDER_IMPROVE_PASSES):
            if time.monotonic() > self.deadline:
                break
            improved = False
            n = len(order)
            # 2-opt: reverse a segment containing no pinned stop
            for i in range(n - 1):
                for j in range(i + 1, n):
                    if pinned_set and pinned_set & set(order[i:j + 1]):
                        continue
                    cand = order[:i] + order[i:j + 1][::-1] + order[j + 1:]
                    k = self.order_key(cand, counts, leg, depot)
                    if k < best_key:
                        best_key, order, improved = k, cand, True
            # or-opt: move one unpinned stop to another position
            for i in range(n):
                if order[i] in pinned_set:
                    continue
                for k_pos in range(n):
                    if k_pos == i:
                        continue
                    cand = order[:i] + order[i + 1:]
                    cand = cand[:k_pos] + [order[i]] + cand[k_pos:]
                    if pins and not pins_ok(cand):
                        continue
                    k = self.order_key(cand, counts, leg, depot)
                    if k < best_key:
                        best_key, order, improved = k, cand, True
            if not improved:
                break
        return tuple(order)


# --- plan scoring -----------------------------------------------------------

def _route_counts(state: _State) -> dict[tuple[int, str], dict[int, int]]:
    counts: dict[tuple[int, str], dict[int, int]] = {}
    for u in state.units:
        b = state.assignment[u.idx]
        if b is None:
            continue
        for leg in u.legs:
            per = counts.setdefault((b, leg), {})
            per[u.cluster.idx] = per.get(u.cluster.idx, 0) + u.seats[leg]
    return counts


def _score(state: _State, ev: _Evaluator) -> tuple[int, float, float, float]:
    counts = _route_counts(state)
    worst = total = driving = 0.0
    for b in range(len(state.buses)):
        for leg in LEGS:
            c = counts.get((b, leg))
            if not c:
                continue
            r = ev.route(b, leg, c)
            if r.worst > worst:
                worst = r.worst
            total += r.total
            driving += r.driving
    unplaced = sum(u.children for u in state.units
                   if state.assignment[u.idx] is None)
    return (unplaced, worst, total, driving)


# --- seeding ----------------------------------------------------------------

def _place_pinned(state: _State, bus_index: dict[Any, int]) -> None:
    for u in state.units:
        if u.pinned_bus is None:
            continue
        b = bus_index[u.pinned_bus]
        if state.can_place(u, b):
            state.place(u, b)


def _free_units(state: _State) -> list[_Unit]:
    return [u for u in state.units
            if u.pinned_bus is None and state.assignment[u.idx] is None]


def _sweep(state: _State, school: Point, offset: int) -> None:
    nb = len(state.buses)
    free = _free_units(state)
    free.sort(key=lambda u: (math.atan2(u.cluster.lat - school[0],
                                        u.cluster.lng - school[1]), u.idx))
    seq = free[offset:] + free[:offset]
    cursor = 0
    for u in seq:
        for b in list(range(cursor, nb)) + list(range(0, cursor)):
            if state.can_place(u, b):
                state.place(u, b)
                if b >= cursor:
                    cursor = b
                break


def _size_first(state: _State) -> None:
    free = _free_units(state)
    free.sort(key=lambda u: (-u.children, u.idx))
    for u in free:
        for b in range(len(state.buses)):
            if state.can_place(u, b):
                state.place(u, b)
                break


def _balanced_kmeans(state: _State) -> None:
    nb = len(state.buses)
    free = _free_units(state)
    if not free or nb == 0:
        return
    centers: list[Point] = [free[0].cluster.point]
    while len(centers) < nb:
        cand = max(free, key=lambda u: (min(haversine_m(u.cluster.point, c)
                                            for c in centers), -u.idx))
        centers.append(cand.cluster.point)
    for round_ in range(2):
        if round_:
            sums: list[list[float]] = [[0.0, 0.0, 0.0] for _ in range(nb)]
            for u in free:
                b = state.assignment[u.idx]
                if b is not None:
                    sums[b][0] += u.cluster.lat
                    sums[b][1] += u.cluster.lng
                    sums[b][2] += 1
            centers = [(s[0] / s[2], s[1] / s[2]) if s[2] else centers[i]
                       for i, s in enumerate(sums)]
            for u in free:
                state.unplace(u)
        for u in free:
            for b in sorted(range(nb),
                            key=lambda b: (haversine_m(u.cluster.point,
                                                       centers[b]), b)):
                if state.can_place(u, b):
                    state.place(u, b)
                    break


def _random_fill(state: _State, rng: random.Random) -> None:
    nb = len(state.buses)
    free = _free_units(state)
    rng.shuffle(free)
    for u in free:
        order = list(range(nb))
        rng.shuffle(order)
        for b in order:
            if state.can_place(u, b):
                state.place(u, b)
                break


# --- local search -----------------------------------------------------------

class _Improver:
    """Relocate/swap with bound screening, plus placement and exchange moves
    for unplaced units. Every acceptance strictly decreases the exact plan
    key, so the loop terminates without cycling."""

    def __init__(self, state: _State, ev: _Evaluator,
                 bus_index: dict[Any, int], deadline: float):
        self.state = state
        self.ev = ev
        self.bus_index = bus_index
        self.deadline = deadline
        self.key = _score(state, ev)
        self._refresh()

    def _refresh(self) -> None:
        self.counts = _route_counts(self.state)
        self.summ = {rk: self.ev.route(rk[0], rk[1], c)
                     for rk, c in self.counts.items()}
        self.unplaced = self.key[0]

    def _expired(self) -> bool:
        return time.monotonic() > self.deadline

    def _cand_key(self, repl: dict[tuple[int, str], tuple[float, float, float]]
                  ) -> tuple[int, float, float, float]:
        # Accumulation order mirrors _score exactly.
        worst = total = driving = 0.0
        for b in range(len(self.state.buses)):
            for leg in LEGS:
                rk = (b, leg)
                if rk in repl:
                    w, t, dr = repl[rk]
                elif rk in self.summ:
                    w, t, dr = self.summ[rk].key
                else:
                    continue
                if w > worst:
                    worst = w
                total += t
                driving += dr
        return (self.unplaced, worst, total, driving)

    def _shifted(self, rk: tuple[int, str], unit: _Unit, leg: str,
                 sign: int) -> dict[int, int]:
        c = dict(self.counts.get(rk, {}))
        cid = unit.cluster.idx
        c[cid] = c.get(cid, 0) + sign * unit.seats[leg]
        if c[cid] <= 0:
            del c[cid]
        return c

    def _accept(self, prev_key: tuple) -> bool:
        """Polish the mutated state; keep only a strict exact improvement."""
        key = _score(self.state, self.ev)
        if key < prev_key:
            self.key = key
            self._refresh()
            return True
        return False

    def run(self) -> None:
        for _ in range(_MAX_IMPROVE_PASSES):
            if self._expired():
                return
            if self._place_unplaced():
                continue
            if self._relocate():
                continue
            if self._swap():
                continue
            if self._exchange():
                continue
            return

    # -- moves ---------------------------------------------------------------

    def _place_unplaced(self) -> bool:
        state, ev = self.state, self.ev
        improved = False
        for u in state.units:
            if state.assignment[u.idx] is not None:
                continue
            candidates = ([self.bus_index[u.pinned_bus]]
                          if u.pinned_bus is not None
                          else range(len(state.buses)))
            best_b, best_key = None, self.key
            for b in candidates:
                if state.can_place(u, b):
                    state.place(u, b)
                    k2 = _score(state, ev)
                    if k2 < best_key:
                        best_b, best_key = b, k2
                    state.unplace(u)
            if best_b is not None:
                state.place(u, best_b)
                self.key = best_key
                self._refresh()
                improved = True
        return improved

    def _relocate(self) -> bool:
        state = self.state
        nb = len(state.buses)
        improved = False
        for u in state.units:
            if self._expired():
                return improved
            b0 = state.assignment[u.idx]
            if b0 is None or u.pinned_bus is not None:
                continue
            state.unplace(u)
            best_b, best_key = None, self.key
            for b in range(nb):
                if b == b0 or not state.can_place(u, b):
                    continue
                repl = {}
                for leg in u.legs:
                    src = self._shifted((b0, leg), u, leg, -1)
                    tgt = self._shifted((b, leg), u, leg, +1)
                    repl[(b0, leg)] = self.ev.bound_key(
                        b0, leg, self.summ.get((b0, leg)), src)
                    repl[(b, leg)] = self.ev.bound_key(
                        b, leg, self.summ.get((b, leg)), tgt)
                k2 = self._cand_key(repl)
                if k2 < best_key:
                    best_b, best_key = b, k2
            if best_b is not None:
                prev = self.key
                state.place(u, best_b)
                if self._accept(prev):
                    improved = True
                    continue
                state.unplace(u)  # float-order quirk: bound lied, roll back
            state.place(u, b0)
        return improved

    def _swap(self) -> bool:
        state = self.state
        improved = False
        for i, u in enumerate(state.units):
            if u.pinned_bus is not None or state.assignment[u.idx] is None:
                continue
            for v in state.units[i + 1:]:
                if self._expired():
                    return improved
                if v.pinned_bus is not None:
                    continue
                bu = state.assignment[u.idx]
                bv = state.assignment[v.idx]
                if bv is None or bv == bu:
                    continue
                state.unplace(u)
                state.unplace(v)
                if not (state.can_place(u, bv) and state.can_place(v, bu)):
                    state.place(u, bu)
                    state.place(v, bv)
                    continue
                repl = {}
                for leg in LEGS:
                    u_leg, v_leg = leg in u.legs, leg in v.legs
                    if not (u_leg or v_leg):
                        continue
                    cu = dict(self.counts.get((bu, leg), {}))
                    cv = dict(self.counts.get((bv, leg), {}))
                    if u_leg:
                        self._shift_into(cu, u, leg, -1)
                        self._shift_into(cv, u, leg, +1)
                    if v_leg:
                        self._shift_into(cu, v, leg, +1)
                        self._shift_into(cv, v, leg, -1)
                    repl[(bu, leg)] = self.ev.bound_key(
                        bu, leg, self.summ.get((bu, leg)), cu)
                    repl[(bv, leg)] = self.ev.bound_key(
                        bv, leg, self.summ.get((bv, leg)), cv)
                if self._cand_key(repl) < self.key:
                    prev = self.key
                    state.place(u, bv)
                    state.place(v, bu)
                    if self._accept(prev):
                        improved = True
                        continue
                    state.unplace(u)
                    state.unplace(v)
                state.place(u, bu)
                state.place(v, bv)
        return improved

    @staticmethod
    def _shift_into(c: dict[int, int], unit: _Unit, leg: str, sign: int) -> None:
        cid = unit.cluster.idx
        c[cid] = c.get(cid, 0) + sign * unit.seats[leg]
        if c[cid] <= 0:
            del c[cid]

    def _exchange(self) -> bool:
        """Bring an unplaced unit in for a placed one (the displaced unit is
        re-placed wherever it still fits, or left out if it is smaller)."""
        state, ev = self.state, self.ev
        nb = len(state.buses)
        improved = False
        for u in state.units:
            if state.assignment[u.idx] is not None:
                continue
            for v in state.units:
                if self._expired():
                    return improved
                if v is u or v.pinned_bus is not None:
                    continue
                bv = state.assignment[v.idx]
                if bv is None:
                    continue
                if u.pinned_bus is not None and self.bus_index[u.pinned_bus] != bv:
                    continue
                state.unplace(v)
                if not state.can_place(u, bv):
                    state.place(v, bv)
                    continue
                state.place(u, bv)
                best_b, best_key = None, None
                for b in range(nb):
                    if state.can_place(v, b):
                        state.place(v, b)
                        k2 = _score(state, ev)
                        if best_key is None or k2 < best_key:
                            best_b, best_key = b, k2
                        state.unplace(v)
                if best_b is not None:
                    state.place(v, best_b)
                    k2 = best_key
                else:
                    k2 = _score(state, ev)
                if k2 < self.key:
                    self.key = k2
                    self._refresh()
                    improved = True
                    break
                if best_b is not None:
                    state.unplace(v)
                state.unplace(u)
                state.place(v, bv)
        return improved


# --- unplaceable attribution ------------------------------------------------

def _binding_constraint(state: _State, u: _Unit,
                        bus_index: dict[Any, int]) -> str:
    buses = ([bus_index[u.pinned_bus]] if u.pinned_bus is not None
             else range(len(state.buses)))
    for b in buses:
        if state.can_place(u, b, ignore_seats=True):
            return CONSTRAINT_SEATS
    for b in buses:
        if state.can_place(u, b, ignore_cap=True):
            return CONSTRAINT_STOP_CAP
    return CONSTRAINT_SEATS


def _unplaceable(state: _State, bus_index: dict[Any, int]) -> list[dict]:
    entries: list[tuple[int, int, dict]] = []
    for u in state.units:
        if state.assignment[u.idx] is not None:
            continue
        constraint = _binding_constraint(state, u, bus_index)
        for m in u.members:
            legs = (u.split_leg,) if u.split_leg else m.legs
            for leg in legs:
                entries.append((m.idx, LEGS.index(leg), {
                    "student_id": m.id, "name": m.name,
                    "leg": leg, "constraint": constraint,
                }))
    entries.sort(key=lambda e: (e[0], e[1]))
    return [e[2] for e in entries]


# --- document ---------------------------------------------------------------

def _document(state: _State, ev: _Evaluator, clusters: list[_Cluster],
              seed: int, degraded: bool) -> dict:
    counts = _route_counts(state)
    # (bus, leg, cluster) -> children riding, in rider input order
    stop_children: dict[tuple[int, str, int], list[_Rider]] = {}
    for u in state.units:
        b = state.assignment[u.idx]
        if b is None:
            continue
        for leg in u.legs:
            for m in u.members:
                m_legs = (u.split_leg,) if u.split_leg else m.legs
                if leg in m_legs:
                    stop_children.setdefault((b, leg, u.cluster.idx), []).append(m)

    worst = total = driving = 0.0
    bus_docs = []
    for bus in state.buses:
        legs_doc: dict[str, dict] = {}
        for leg in LEGS:
            c = counts.get((bus.idx, leg))
            if not c:
                legs_doc[leg] = {"stops": [], "ride_seconds": [],
                                 "driving_seconds": 0.0}
                continue
            r = ev.route(bus.idx, leg, c)
            stops = []
            ride_rows = []
            for cid in r.order:
                children = sorted(stop_children[(bus.idx, leg, cid)],
                                  key=lambda m: m.idx)
                stops.append({
                    "lat": clusters[cid].lat,
                    "lng": clusters[cid].lng,
                    "name": " / ".join(m.name for m in children),
                    "students": [{"id": m.id, "name": m.name}
                                 for m in children],
                })
                for m in children:
                    ride_rows.append({"student_id": m.id, "name": m.name,
                                      "ride_seconds": r.rides[cid]})
            legs_doc[leg] = {"stops": stops, "ride_seconds": ride_rows,
                             "driving_seconds": r.driving}
            if r.worst > worst:
                worst = r.worst
            total += r.total
            driving += r.driving
        bus_docs.append({"bus_id": bus.id, "bus_name": bus.name,
                         "capacity": bus.capacity, "legs": legs_doc})

    bus_index = {b.id: b.idx for b in state.buses}
    return {
        "version": 1,
        "seed": seed,
        "degraded": bool(degraded),
        "objective": [worst, total, driving],
        "buses": bus_docs,
        "unplaceable": _unplaceable(state, bus_index),
        "stop_groups": [{
            "lat": c.lat, "lng": c.lng,
            "name": " / ".join(m.name for m in c.members),
            "students": [{"id": m.id, "name": m.name} for m in c.members],
        } for c in clusters],
    }


# --- entry point ------------------------------------------------------------

def solve(students: Sequence[Mapping[str, Any]],
          buses: Sequence[Mapping[str, Any]],
          matrix: MatrixFn | Mapping,
          school: Mapping[str, Any],
          *,
          stop_cap: int = DEFAULT_STOP_CAP,
          seed: int = 0,
          time_cap_s: float = DEFAULT_TIME_CAP_S,
          degraded: bool = False) -> dict:
    """Draft a fleet plan and return it as a plain JSON-safe dict.

    See the module docstring for the matrix contract, objective semantics,
    pin formats, and the document schema. ``degraded`` is a passthrough flag
    set by the caller when the matrix came from the haversine fallback.
    """
    deadline = time.monotonic() + time_cap_s
    riders = _parse_students(students)
    fleet = _parse_buses(buses)
    clusters = _collapse(riders)
    units = _build_units(clusters)
    order_pins = _cluster_order_pins(clusters)
    school_pt: Point = (float(school["lat"]), float(school["lng"]))
    bus_index = {b.id: b.idx for b in fleet}
    for u in units:
        if u.pinned_bus is not None and u.pinned_bus not in bus_index:
            raise ValueError(f"bus pin references unknown bus {u.pinned_bus!r}")

    ev = _Evaluator(_matrix_fn(matrix), school_pt, clusters, fleet,
                    order_pins, deadline)
    rng = random.Random(seed)

    n_units = len(units)
    offsets = sorted({(i * max(n_units, 1)) // _SWEEP_OFFSETS
                      for i in range(_SWEEP_OFFSETS)})
    builders: list[Callable[[_State], None]] = []
    builders.extend((lambda st, o=o: _sweep(st, school_pt, o)) for o in offsets)
    builders.append(_size_first)
    builders.append(_balanced_kmeans)
    builders.extend((lambda st: _random_fill(st, rng))
                    for _ in range(_RANDOM_RESTARTS))

    best_key: tuple | None = None
    best_assignment: list[int | None] | None = None
    for i, build in enumerate(builders):
        if i > 0 and time.monotonic() > deadline:
            break  # anytime: keep the best found so far
        state = _State(units, fleet, stop_cap)
        _place_pinned(state, bus_index)
        build(state)
        improver = _Improver(state, ev, bus_index, deadline)
        improver.run()
        if best_key is None or improver.key < best_key:
            best_key, best_assignment = improver.key, list(state.assignment)

    final = _State(units, fleet, stop_cap)
    for u_idx, b in enumerate(best_assignment or []):
        if b is not None:
            final.place(units[u_idx], b)
    return _document(final, ev, clusters, seed, degraded)
