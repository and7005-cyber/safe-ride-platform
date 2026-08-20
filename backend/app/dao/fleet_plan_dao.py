"""Fleet-plan store, draft generation (U4), review/edit (U5), apply (U6) and
restore (U7).

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

This module remains the single import path; the implementation lives in the
``app.dao.fleet_plan`` package (``_shared`` vocabulary/helpers plus the
``draft`` / ``review`` / ``apply_restore`` / ``slot_ins`` method groups).
``FleetPlanDao`` is composed from those groups below, and every module-level
name consumers reference (the router, push fan-out's lazy imports) is
re-exported here unchanged.
"""
# Original module-level imports, retained verbatim for module-attribute
# parity: anything that resolved as ``fleet_plan_dao.<name>`` before the
# package split still does. PushDao/PushService are also used by __init__.
import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Mapping

from psycopg.types.json import Jsonb

from app.core.db import get_connection
from app.core.errors import BadRequestError, ConflictError, NotFoundError, SafeRideError
from app.dao.fleet_dao import (
    _ROUTE_GEOMETRY_INPUTS_SQL,
    _depot_leg,
    _stop_label,
    resolve_gate_anchor,
)
from app.dao.push_dao import PushDao
from app.dao.student_live_dao import _derive_student_bus
from app.services import geo_service, plan_solver, slot_in_service
from app.services.push_service import PushService

# Re-exported shared surface — the SAME objects the method groups use
# (push_service's lazy imports rely on that identity: thresholds cannot
# drift). Every name here was module-level in this file before the split.
from app.dao.fleet_plan._shared import (
    CONSTRAINT_CAPACITY,
    CONSTRAINT_STOP_CAP,
    DIFF_BUS_CHANGE,
    DIFF_FIRST_COMMUNICATION,
    DIFF_LEG_REMOVED,
    DIFF_NEWLY_UNPLACEABLE,
    DIFF_PLACE_CHANGE,
    DIFF_TIME_MOVE,
    DRIFT_ADDRESS_CHANGED,
    DRIFT_DEPARTED,
    DRIFT_ENROLLED,
    NOTIFY_MOVE_THRESHOLD_MIN,
    UNASSIGNED_CONSTRAINT,
    UNRESOLVED_ADDRESS_CONSTRAINT,
    PlanConstraintError,
    UnacknowledgedUnplaceableError,
    _CAPTURE_LIVE_SQL,
    _COORD_EPS,
    _META_COLUMNS,
    _add_student_to_leg,
    _basis_student,
    _bus_doc,
    _check_bus_leg,
    _effective_pattern,
    _empty_leg_doc,
    _find_placement,
    _fleet_drift_problems,
    _floats_differ,
    _hhmm_to_minutes,
    _leg_label,
    _leg_rides,
    _multi_trip_bus_ids,
    _normalize_per_leg,
    _pattern_legs,
    _place_differs,
    _pop_student,
    _recompute_legs,
    _refresh_objective,
    _roster_drift,
    _shift_hhmm,
    _stop_key,
    _validate_leg,
    computed_stop_times,
    diff_plan_vs_live,
    logger,
    plan_gate_anchors,
)
from app.dao.fleet_plan.apply_restore import ApplyRestoreOps
from app.dao.fleet_plan.draft import DraftOps
from app.dao.fleet_plan.review import ReviewOps
from app.dao.fleet_plan.slot_ins import SlotInOps


class FleetPlanDao(DraftOps, ReviewOps, ApplyRestoreOps, SlotInOps):
    def __init__(self) -> None:
        # Apply (U6) writes feed rows through the conn-threaded PushDao insert
        # and delivers push post-commit through the service; both are cheap,
        # stateless constructions.
        self._push_dao = PushDao()
        self._push_service = PushService(self._push_dao)
