import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from pydantic import BaseModel

from app.api._helpers import safe_call
from app.core.auth import require_role
from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.core.rate_limit import SlidingWindowLimiter
from app.dao.absence_dao import AbsenceDao
from app.dao.incident_dao import IncidentDao
from app.dao.parent_live_dao import ParentLiveDao
from app.services.push_service import PushService

logger = logging.getLogger("saferide.parent")

router = APIRouter(prefix="/api/parent-portal", tags=["parent-portal"])
dao = ParentLiveDao()
absence_dao = AbsenceDao()
incident_dao = IncidentDao()
push_service = PushService()
parent_only = require_role("parent")

# Ownership is the sole boundary for both Cancel-a-Ride verbs and it evaluates
# FIRST: non-existent and non-linked student ids get this identical 404
# (matching /track) before any guard runs — a guard's 409 fired first would
# leak another child's live on-bus state to harvested UUIDs.
_CHILD_NOT_FOUND = "Child not found for this parent"

# Roster-flap protection, POST + DELETE combined (U5): per-account, in-process
# best-effort (per Lambda container) — the durable bound stays the
# (student, date) unique row plus no-op side-effect suppression. 20/hour: a
# 3-child household legitimately cycling cancel/withdraw approaches 10.
cancel_ride_limiter = SlidingWindowLimiter(max_attempts=20, window_seconds=3600)
_CANCEL_LIMIT_MESSAGE = "Too many cancellation changes. Please try again later."


@router.get("/children")
def children(user: dict = Depends(parent_only)):
    return safe_call(lambda: dao.list_children(user["id"]))


@router.get("/track")
def track(student_id: str = Query(...), user: dict = Depends(parent_only)):
    def run():
        result = dao.get_track(user["id"], student_id)
        if result is None:
            raise NotFoundError("Child not found for this parent")
        return result

    return safe_call(run)


@router.get("/alerts")
def alerts(
    window_hours: int | None = Query(default=None, ge=1, le=8760),
    min_age_hours: int | None = Query(default=None, ge=1, le=8760),
    limit: int = Query(default=50, ge=1),
    user: dict = Depends(parent_only),
):
    """Incidents feed for the parent's buses, newest first.

    window_hours narrows to a rolling window and min_age_hours excludes rows
    younger than that age, server-side so the cap applies after the exclusion
    (U9: R5–R7 — Recent = window 24; History = min_age 24 + window 168,
    disjoint). Both are capped at 8760 (a year): unbounded values reach the
    DB's interval arithmetic and 500 (SQLSTATE 22015 is not a bad-request
    state in safe_call). limit defaults to 50 and is hard-capped at 200 in
    the DAO.
    """
    return safe_call(
        lambda: dao.list_alerts(
            user["id"], window_hours=window_hours, min_age_hours=min_age_hours, limit=limit
        )
    )


@router.get("/profile")
def profile(user: dict = Depends(parent_only)):
    return safe_call(lambda: dao.get_profile(user["id"]))


# Cancel-a-Ride (U5: R14, R16–R19; AE4) ----------------------------------------

class CancelRidePayload(BaseModel):
    student_id: str
    scope: str  # 'morning' | 'afternoon' | 'day'


def _validate_cancel_scope(scope: str) -> None:
    # Same message as the DAO's _validate_scope; validated here too because
    # the guards interpret the scope before any DAO statement runs.
    if scope not in ("day", "morning", "afternoon"):
        raise BadRequestError("Scope must be one of: day, morning, afternoon")


def _covered_types(scope: str) -> tuple[str, ...]:
    return ("morning", "afternoon") if scope == "day" else (scope,)


def _record_cancellation_incident(user: dict, context: dict, scope: str) -> None:
    """School-side channel for a parent cancellation (R17): a student-stamped
    'cancellation' incident on the admin Alerts page, inserted DAO-direct.
    Never push_service.notify_incident — that fans out to every family on
    the bus and this row names a child (the household's channel is
    notify_ride_cancelled). Bus context comes from the child's covered
    route, earliest still-affected run type first; the acting parent is
    named only in the description — the driver columns stay NULL."""
    try:
        student = context["student"]
        route_buses = context["route_buses"]
        candidates = ("morning", "afternoon") if scope == "day" else (scope,)
        bus = next((route_buses[t] for t in candidates if route_buses.get(t)), {})
        labels = {
            "morning": "morning ride",
            "afternoon": "afternoon ride",
            "day": "all rides today",
        }
        actor = f"{user.get('full_name') or 'Parent'} ({user.get('email')})"
        incident_dao.create_cancellation_incident(
            student_id=str(student["id"]),
            description=f"{student['name']}: {labels[scope]} cancelled by parent — {actor}.",
            bus_id=str(bus["bus_id"]) if bus.get("bus_id") else None,
            bus_name=bus.get("bus_name"),
            run_type=scope if scope in ("morning", "afternoon") else None,
        )
    except Exception:
        logger.exception("recording cancellation incident failed")


@router.post("/cancel-ride")
def cancel_ride(
    payload: CancelRidePayload,
    background_tasks: BackgroundTasks,
    user: dict = Depends(parent_only),
):
    """Same-day scoped cancellation for a linked child (R14, R16, R17; AE4).

    Order is load-bearing: per-account limiter → ownership (the fixed 404,
    before any guard) → per-scope guards (409, written for parents) → the
    atomic set_scope upsert (the provenance ratchet maps a staff-sourced row
    to the friendly 409 here). Completion is judged PER SCOPE: 'morning' is
    blocked once the morning run completed, 'afternoon' once the afternoon
    completed, and 'day' only when BOTH completed — a 'day' cancel after a
    completed morning means "not riding the rest of today" and records the
    afternoon (U13's dialog copy says so). Side effects (admin alert +
    household confirmations) fire only when the stored scope actually
    changed — a duplicate submit is a 200 with no side effects.
    """
    cancel_ride_limiter.check(str(user["id"]), _CANCEL_LIMIT_MESSAGE)

    def run() -> tuple[dict, str, dict]:
        context = dao.cancel_ride_context(user["id"], payload.student_id)
        if context is None:
            raise NotFoundError(_CHILD_NOT_FOUND)
        _validate_cancel_scope(payload.scope)
        name = context["student"]["name"]
        completed = {r["type"] for r in context["runs"] if r["status"] == "completed"}
        active = {r["type"] for r in context["runs"] if r["status"] != "completed"}
        scope = payload.scope
        if scope == "day":
            if "morning" in completed and "afternoon" in completed:
                raise ConflictError(
                    "Both of today's runs are already completed — "
                    f"there is nothing left to cancel for {name} today."
                )
            if "morning" in completed:
                scope = "afternoon"
            elif "afternoon" in completed:
                scope = "morning"
        elif scope in completed:
            raise ConflictError(
                f"The {scope} run has already been completed today — "
                "that ride can no longer be cancelled."
            )
        if context["student"]["status"] == "on-bus" and any(
            t in active for t in _covered_types(scope)
        ):
            raise ConflictError(
                f"{name} is on the bus right now — a ride in progress cannot be "
                "cancelled. Please contact the school office or the driver."
            )
        result = absence_dao.set_scope(
            payload.student_id, scope, user["id"], reason="Cancelled by parent"
        )
        if result is None:
            raise ConflictError(
                f"The school has already marked {name} absent today — "
                "please contact the office to change it."
            )
        return context, scope, result

    context, effective_scope, result = safe_call(run)
    if result["changed"]:
        background_tasks.add_task(
            _record_cancellation_incident, user, context, effective_scope
        )
        background_tasks.add_task(
            push_service.notify_ride_cancelled, context["student"], effective_scope
        )
    return {"ok": True, "scope": result["scope"], "changed": result["changed"]}


@router.delete("/cancel-ride")
def withdraw_cancel_ride(payload: CancelRidePayload, user: dict = Depends(parent_only)):
    """Withdraw a cancellation (R18): allowed on parent-sourced rows only,
    and only while NO covered-type run row exists today for the half being
    withdrawn — run-row EXISTENCE, not the active-run predicate, which would
    reopen withdrawal after completion and rewrite history. Withdrawing one
    half of a merged 'day' downgrades the row to the other half;
    withdraw_scope owns that transition and the status reset on any exit
    from 'day'. Same order as POST: limiter → ownership 404 → guards. The
    guards here are friendly pre-reads; the atomic statement stays the
    authority. No side effects: the earlier admin alert intentionally stands
    (accepted behavior — a withdrawal notice is deferred follow-up work).
    """
    cancel_ride_limiter.check(str(user["id"]), _CANCEL_LIMIT_MESSAGE)

    def run() -> dict:
        context = dao.cancel_ride_context(user["id"], payload.student_id)
        if context is None:
            raise NotFoundError(_CHILD_NOT_FOUND)
        _validate_cancel_scope(payload.scope)
        name = context["student"]["name"]
        absence = context["absence"]
        if absence is None:
            raise ConflictError(f"There is no cancellation for {name} today to withdraw.")
        if absence["source"] != "parent":
            raise ConflictError(
                f"{name}'s absence today was recorded by the school — "
                "please contact the office to change it."
            )
        row_scope = absence["scope"]
        # Withdrawing a half of a merged 'day' row is the downgrade path;
        # any other mismatch (row is a partial, request names the other
        # half or 'day') withdraws nothing — say so instead of no-op'ing.
        half_of_day = row_scope == "day" and payload.scope in ("morning", "afternoon")
        if payload.scope != row_scope and not half_of_day:
            raise ConflictError(f"Only the {row_scope} ride is cancelled for {name} today.")
        existing = {r["type"] for r in context["runs"]}
        blocked = [t for t in ("morning", "afternoon")
                   if t in _covered_types(payload.scope) and t in existing]
        if blocked:
            if len(blocked) == 2:
                raise ConflictError(
                    "Today's runs have already started — "
                    f"{name}'s cancellation can no longer be withdrawn."
                )
            started = blocked[0]
            other = "afternoon" if started == "morning" else "morning"
            if payload.scope == "day":
                raise ConflictError(
                    f"The {started} run has already started — you can still "
                    f"withdraw the {other} half of {name}'s cancellation."
                )
            raise ConflictError(
                f"The {started} run has already started — {name}'s {started} "
                "cancellation can no longer be withdrawn."
            )
        result = absence_dao.withdraw_scope(payload.student_id, payload.scope, user["id"])
        if result is None:
            # The atomic statement refused despite the pre-reads: a staff
            # escalation, a concurrent withdrawal, or a covered run starting
            # landed in between.
            raise ConflictError(
                f"Could not withdraw — {name}'s absence was just updated or "
                "a run has just started. Please refresh and try again."
            )
        return result

    result = safe_call(run)
    return {"ok": True, "deleted": result["deleted"], "scope": result["scope"]}
