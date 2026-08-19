"""Fleet-plan store and draft generation (U4).

Plans are JSONB documents in ``live_fleet_plans`` (migration 011), one row per
plan: ``document`` holds the solver output (per-bus AM/PM stop sequences,
student ids AND names, ride times, unplaceable list), ``basis`` snapshots the
inputs at generation (students + coords + patterns, fleet config) — both
denormalize names alongside ids so later gates can name departed children (the
007/010 name-rot precedent). Drafts are working documents, not queryable
facts; apply (U6) materializes them into the live route tables. Generation is
strictly read-only against ``live_routes`` / ``live_route_stops`` (R8).

One open draft per school (partial unique in 011): a second draft 409s unless
superseded. Superseding and discarding scrub ``document``/``basis`` to NULL,
keeping metadata — plan documents are an aggregate PII target (every child's
name and coordinates), so payloads never outlive the draft's working life.

The travel-time matrix is fetched in memory per generation request and never
persisted (Google ToS: no caching allowance for durations); the keyless local
stack takes the deterministic haversine-degraded path, flagged on the row.
"""
import logging
from typing import Any

from psycopg.types.json import Jsonb

from app.core.db import get_connection
from app.core.errors import ConflictError, NotFoundError
from app.services import geo_service, plan_solver

logger = logging.getLogger("saferide.fleet_plans")

# Constraint name for students excluded from the solver input because their
# home has no coordinates (unresolved triage rows, R7/U4).
UNRESOLVED_ADDRESS_CONSTRAINT = "unresolved address"

_META_COLUMNS = "id, status, created_at, applied_at"


def _pattern_legs(pattern: str | None) -> tuple[str, ...]:
    """The legs a ridership pattern rides — for naming a coordinate-less
    student unplaceable per leg, matching the solver's per-leg output shape."""
    if pattern == plan_solver.PATTERN_MORNING:
        return (plan_solver.LEG_MORNING,)
    if pattern == plan_solver.PATTERN_AFTERNOON:
        return (plan_solver.LEG_AFTERNOON,)
    return (plan_solver.LEG_MORNING, plan_solver.LEG_AFTERNOON)


def _multi_trip_bus_ids(conn, bus_ids: list) -> set[str]:
    """Buses holding any trip_index >= 2 route: multi-trip chains are out of
    drafting scope (Scope Boundaries) — excluded with a named notice."""
    if not bus_ids:
        return set()
    rows = conn.execute(
        "select distinct bus_id from live_routes "
        "where bus_id = any(%s::uuid[]) and trip_index >= 2",
        ([str(b) for b in bus_ids],),
    ).fetchall()
    return {str(r["bus_id"]) for r in rows}


class FleetPlanDao:
    # --- fleet confirmation (F1 step 1) ------------------------------------

    def confirm_fleet(self, school_id: str, bus_ids: list[str]) -> dict[str, Any]:
        """Assign the listed buses to the school (``live_buses.school_id``).

        A bus already claimed by a DIFFERENT school blocks the whole confirm
        with a 409 naming the bus and the claiming school. Buses previously
        claimed by THIS school but deselected are released — unless they carry
        the school's applied plan routes (``plan_ordered``), in which case
        they stay claimed with a notice: unclaiming a bus that is actively
        serving the applied plan would orphan its routes.

        The response reports per-bus notices: a multi-trip bus (any
        trip_index >= 2 route) is claimed but EXCLUDED from drafting; a
        depot-less bus proceeds with a notice.
        """
        requested = list(dict.fromkeys(str(b) for b in bus_ids))
        with get_connection() as conn:
            school = conn.execute(
                "select id, name from live_schools where id = %s", (school_id,)
            ).fetchone()
            if not school:
                raise NotFoundError("School not found")
            # Lock every bus this confirmation may touch — the requested set
            # plus the school's currently claimed buses — in sorted-id order
            # (the global lock-order convention). for update of b only: the
            # left-joined school row is read-only here.
            rows = conn.execute(
                "select b.id, b.name, b.school_id, b.availability, "
                "b.depot_lat, b.depot_lng, s.name as claiming_school_name "
                "from live_buses b left join live_schools s on s.id = b.school_id "
                "where b.id = any(%s::uuid[]) or b.school_id = %s "
                "order by b.id for update of b",
                (requested, school_id),
            ).fetchall()
            by_id = {str(r["id"]): r for r in rows}
            missing = [bid for bid in requested if bid not in by_id]
            if missing:
                raise NotFoundError(f"Bus {missing[0]} was not found")
            for bid in requested:
                row = by_id[bid]
                if row["school_id"] is not None and str(row["school_id"]) != str(school_id):
                    claiming = row["claiming_school_name"] or "another school"
                    raise ConflictError(
                        f"Bus {row['name']} is already claimed by {claiming} — "
                        "release it there before adding it to this school's fleet"
                    )

            # Deselected buses: previously claimed by THIS school, not in the
            # list — released unless they carry the school's applied plan routes.
            released: list[dict] = []
            retained: list[dict] = []
            for r in rows:
                bid = str(r["id"])
                if bid in requested or str(r["school_id"] or "") != str(school_id):
                    continue
                keeps_plan_routes = conn.execute(
                    "select 1 from live_routes "
                    "where bus_id = %s and school_id = %s and plan_ordered limit 1",
                    (r["id"], school_id),
                ).fetchone()
                if keeps_plan_routes:
                    retained.append(r)
                else:
                    conn.execute(
                        "update live_buses set school_id = null where id = %s", (r["id"],)
                    )
                    released.append(r)

            if requested:
                conn.execute(
                    "update live_buses set school_id = %s where id = any(%s::uuid[])",
                    (school_id, requested),
                )

            multi_trip = _multi_trip_bus_ids(conn, requested)
            notices: list[dict] = []
            buses_out: list[dict] = []
            for bid in requested:
                r = by_id[bid]
                excluded = bid in multi_trip
                has_depot = r["depot_lat"] is not None and r["depot_lng"] is not None
                if excluded:
                    notices.append({
                        "bus_id": bid, "bus_name": r["name"], "kind": "multi-trip-excluded",
                        "message": (
                            f"Bus {r['name']} runs a multi-trip chain and is excluded "
                            "from drafting — multi-trip chains are out of drafting scope"
                        ),
                    })
                elif not has_depot:
                    notices.append({
                        "bus_id": bid, "bus_name": r["name"], "kind": "no-depot",
                        "message": (
                            f"Bus {r['name']} has no depot set — drafting proceeds "
                            "without its depot leg"
                        ),
                    })
                buses_out.append({
                    "id": bid, "name": r["name"],
                    "excluded_from_drafting": excluded, "has_depot": has_depot,
                })
            for r in retained:
                notices.append({
                    "bus_id": str(r["id"]), "bus_name": r["name"],
                    "kind": "kept-applied-routes",
                    "message": (
                        f"Bus {r['name']} was deselected but keeps its claim — it "
                        "carries this school's applied plan routes"
                    ),
                })
        return {
            "ok": True,
            "school_id": str(school["id"]),
            "school_name": school["name"],
            "buses": buses_out,
            "released": [str(r["id"]) for r in released],
            "notices": notices,
        }

    # --- draft generation (F1 step 2 / F4) ---------------------------------

    def create_draft(
        self, school_id: str, *, seed: int | None, supersede: bool, created_by: str | None
    ) -> dict[str, Any]:
        """Snapshot the basis, fetch the matrix, run the solver, persist the
        draft. One open draft per school: an existing draft 409s unless
        ``supersede``, which flips it to superseded AND scrubs its
        document/basis payloads (metadata kept — the retention rule).

        Read-only against live routes by construction: nothing here writes
        ``live_routes`` / ``live_route_stops`` / ``live_student_routes`` (R8).

        Everything runs on one connection/transaction. The matrix fetch does
        happen inside it — unlike fleet regeneration this holds no route
        locks, only the school's own draft row, and a concurrent draft for
        the same school resolves via the 011 partial unique (409), so the
        short provider hold is an accepted simplification at pilot scale.
        """
        seed_val = int(seed) if seed is not None else 0
        with get_connection() as conn:
            school = conn.execute(
                "select id, name, lat, lng from live_schools where id = %s", (school_id,)
            ).fetchone()
            if not school:
                raise NotFoundError("School not found")
            if school["lat"] is None or school["lng"] is None:
                raise ConflictError(
                    f"School {school['name']} has no gate coordinates — set its "
                    "location before drafting"
                )

            existing = conn.execute(
                "select id, created_at from live_fleet_plans "
                "where school_id = %s and status = 'draft' for update",
                (school_id,),
            ).fetchone()
            if existing and not supersede:
                raise ConflictError(
                    f"An open draft already exists for {school['name']} "
                    f"(draft {existing['id']}, created "
                    f"{existing['created_at']:%Y-%m-%d %H:%M}) — supersede it or "
                    "discard it first"
                )
            if existing:
                # Scrub on supersede: payloads emptied, metadata kept. The
                # partial unique cannot defer, so demote before the new insert.
                conn.execute(
                    "update live_fleet_plans set status = 'superseded', "
                    "document = null, basis = null where id = %s",
                    (existing["id"],),
                )

            students = conn.execute(
                "select id, name, home_lat, home_lng, ridership_pattern "
                "from live_students where school_id = %s order by name asc, id asc",
                (school_id,),
            ).fetchall()
            buses = conn.execute(
                "select id, name, capacity, depot_lat, depot_lng from live_buses "
                "where school_id = %s and availability = 'in-service' order by id asc",
                (school_id,),
            ).fetchall()
            multi_trip = _multi_trip_bus_ids(conn, [b["id"] for b in buses])

            # Plannable = home coordinates present; the rest are listed
            # unplaceable with the constraint named and are NOT solver input.
            plannable = [s for s in students if s["home_lat"] is not None and s["home_lng"] is not None]
            unresolved = [s for s in students if s["home_lat"] is None or s["home_lng"] is None]

            solver_students = [
                {
                    "id": str(s["id"]), "name": s["name"],
                    "lat": s["home_lat"], "lng": s["home_lng"],
                    "pattern": s["ridership_pattern"] or plan_solver.PATTERN_BOTH,
                }
                for s in plannable
            ]
            included_buses = [b for b in buses if str(b["id"]) not in multi_trip]
            solver_buses = [
                {
                    "id": str(b["id"]), "name": b["name"], "capacity": b["capacity"],
                    "depot": (
                        {"lat": b["depot_lat"], "lng": b["depot_lng"]}
                        if b["depot_lat"] is not None and b["depot_lng"] is not None
                        else None
                    ),
                }
                for b in included_buses
            ]

            # All student homes + the school + the depots go through one matrix
            # call; the solver collapses nearby homes itself, so every point it
            # can ever query is in this list. The matrix result is an N×N grid
            # indexed by this points order; the solver wants a callable over
            # (lat, lng) tuples — bridge with an index dict and a closure.
            points: list[dict] = [
                {"lat": float(s["lat"]), "lng": float(s["lng"])} for s in solver_students
            ]
            points.append({"lat": float(school["lat"]), "lng": float(school["lng"])})
            for b in solver_buses:
                if b["depot"] is not None:
                    points.append(
                        {"lat": float(b["depot"]["lat"]), "lng": float(b["depot"]["lng"])}
                    )
            matrix_result = geo_service.compute_duration_matrix(points)
            grid = matrix_result["matrix"]
            index = {(p["lat"], p["lng"]): i for i, p in enumerate(points)}

            def matrix_fn(origin: tuple[float, float], dest: tuple[float, float]) -> float:
                return grid[index[origin]][index[dest]]

            document = plan_solver.solve(
                solver_students,
                solver_buses,
                matrix_fn,
                {"lat": school["lat"], "lng": school["lng"]},
                seed=seed_val,
                stop_cap=plan_solver.DEFAULT_STOP_CAP,
                degraded=bool(matrix_result["degraded"]),
            )
            # Unresolved-triage students join the unplaceable list per leg
            # their pattern rides, with the binding constraint named (R7).
            for s in unresolved:
                for leg in _pattern_legs(s["ridership_pattern"]):
                    document["unplaceable"].append({
                        "student_id": str(s["id"]), "name": s["name"],
                        "leg": leg, "constraint": UNRESOLVED_ADDRESS_CONSTRAINT,
                    })

            basis = {
                "version": 1,
                "school": {
                    "id": str(school["id"]), "name": school["name"],
                    "lat": school["lat"], "lng": school["lng"],
                },
                "students": [
                    {
                        "id": str(s["id"]), "name": s["name"],
                        "lat": s["home_lat"], "lng": s["home_lng"],
                        "pattern": s["ridership_pattern"] or plan_solver.PATTERN_BOTH,
                        "plannable": s["home_lat"] is not None and s["home_lng"] is not None,
                    }
                    for s in students
                ],
                # No pins exist at draft time — review edits (U5) add them.
                "pins": [],
                "fleet": [
                    {
                        "id": str(b["id"]), "name": b["name"], "capacity": b["capacity"],
                        "depot_lat": b["depot_lat"], "depot_lng": b["depot_lng"],
                    }
                    for b in included_buses
                ],
                "excluded_buses": [
                    {
                        "id": str(b["id"]), "name": b["name"],
                        "reason": "multi-trip chain — out of drafting scope",
                    }
                    for b in buses
                    if str(b["id"]) in multi_trip
                ],
                "matrix_provider": matrix_result["provider"],
            }

            degraded = bool(matrix_result["degraded"] or document.get("degraded"))
            row = conn.execute(
                "insert into live_fleet_plans "
                "(school_id, status, document, basis, solver_seed, degraded, created_by) "
                "values (%s, 'draft', %s, %s, %s, %s, %s) returning *",
                (school_id, Jsonb(document), Jsonb(basis), seed_val, degraded, created_by),
            ).fetchone()
        logger.info(
            "fleet plan draft %s for school %s: seed=%s degraded=%s students=%d "
            "plannable=%d buses=%d excluded=%d",
            row["id"], school_id, seed_val, degraded, len(students),
            len(plannable), len(included_buses), len(buses) - len(included_buses),
        )
        return dict(row)

    # --- reads --------------------------------------------------------------

    def current_plans(self, school_id: str) -> dict[str, Any]:
        """The school's open draft (full row) plus applied/previous metadata
        WITHOUT their document/basis payloads. Full review computation is
        U5's job — the draft's stored document is returned as-is."""
        with get_connection() as conn:
            school = conn.execute(
                "select id from live_schools where id = %s", (school_id,)
            ).fetchone()
            if not school:
                raise NotFoundError("School not found")
            draft = conn.execute(
                "select * from live_fleet_plans where school_id = %s and status = 'draft'",
                (school_id,),
            ).fetchone()
            # 'applied' uniqueness is DAO-enforced, not indexed — read the
            # newest defensively.
            applied = conn.execute(
                f"select {_META_COLUMNS} from live_fleet_plans "
                "where school_id = %s and status = 'applied' "
                "order by applied_at desc nulls last, created_at desc limit 1",
                (school_id,),
            ).fetchone()
            previous = conn.execute(
                f"select {_META_COLUMNS} from live_fleet_plans "
                "where school_id = %s and status = 'previous'",
                (school_id,),
            ).fetchone()
        return {
            "draft": dict(draft) if draft else None,
            "applied": dict(applied) if applied else None,
            "previous": dict(previous) if previous else None,
        }

    # --- discard ------------------------------------------------------------

    def discard_draft(self, plan_id: str) -> dict[str, Any]:
        """Discard an open draft: status -> discarded, document/basis scrubbed
        to NULL (metadata kept) — the retention rule, mechanized. Draft only:
        any other status 409s by name."""
        with get_connection() as conn:
            row = conn.execute(
                "select id, status from live_fleet_plans where id = %s for update",
                (plan_id,),
            ).fetchone()
            if not row:
                raise NotFoundError("Plan not found")
            if row["status"] != "draft":
                raise ConflictError(
                    f"Only a draft can be discarded — this plan is {row['status']}"
                )
            updated = conn.execute(
                "update live_fleet_plans set status = 'discarded', "
                f"document = null, basis = null where id = %s returning {_META_COLUMNS}",
                (plan_id,),
            ).fetchone()
        return {"ok": True, **dict(updated)}
