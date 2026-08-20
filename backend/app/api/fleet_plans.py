"""Fleet-plan endpoints: fleet confirmation, draft generation, current plans,
discard (U4), the review/edit surface (U5) — computed review payload with
wall-clock stop times and the diff vs live, the edit verbs (move / reorder /
pin / pattern / assign), the explicit in-draft re-solve — apply (U6): one
gated, transactional, audit-logged act that materializes the draft into the
live route tables, preserves the displaced live state, and notifies affected
families — and restore (U7): the same act with the preserved as-evolved
capture as its source, toggling apply and previous.

Review-edit contract (R9): every edit re-checks the hard constraints and a
violation answers 422 with the constraint NAMED ('capacity' or 'stop cap'),
leaving the draft document unchanged.

All admin-only: plan documents aggregate every enrolled child's name and home
coordinates, the R19 rationale's aggregate PII target.
"""
from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel

from app.api._helpers import safe_call
from app.core.auth import require_role
from app.dao.fleet_plan_dao import FleetPlanDao
from app.services.push_service import notify_route_changes

router = APIRouter(prefix="/api/fleet-plans", tags=["fleet-plans"])
dao = FleetPlanDao()
admin_only = require_role("admin")


class ConfirmFleetPayload(BaseModel):
    school_id: str
    # The complete claimed set for the school: listed buses are claimed,
    # previously claimed buses missing from the list are released (unless
    # they carry the school's applied plan routes).
    bus_ids: list[str] = []


class DraftPayload(BaseModel):
    school_id: str
    # Deterministic solver seed (defaults to 0): the persisted solver_seed
    # regenerates a disputed draft bit-identically.
    seed: int | None = None
    # One-open-draft rule: an existing draft 409s unless supersede, which
    # flips it to superseded and scrubs its document/basis payloads.
    supersede: bool = False


@router.post("/confirm-fleet")
def confirm_fleet(payload: ConfirmFleetPayload, user: dict = Depends(admin_only)):
    """Assign buses to the school (F1 step 1). A bus claimed by a different
    school 409s naming both; the response carries per-bus notices (multi-trip
    excluded from drafting, depot-less proceeds)."""
    return safe_call(lambda: dao.confirm_fleet(payload.school_id, payload.bus_ids))


@router.post("/draft")
def create_draft(payload: DraftPayload, user: dict = Depends(admin_only)):
    """Generate and persist a draft plan (F1 step 2 / F4): basis snapshot,
    in-memory travel-time matrix, solver run. Read-only against live routes
    (R8). Returns the created plan row."""
    return safe_call(
        lambda: dao.create_draft(
            payload.school_id,
            seed=payload.seed,
            supersede=payload.supersede,
            created_by=user["id"],
        )
    )


@router.get("/current")
def current_plans(school_id: str, user: dict = Depends(admin_only)):
    """The school's open draft (full document) plus applied/previous metadata
    without payloads. Review computation is U5's job. The previous metadata
    carries `drift` — the departed/enrolled rows restore's gate will demand
    confirmations for, computed by the same helper as the gate itself."""
    return safe_call(lambda: dao.current_plans(school_id))


@router.get("/review")
def review(school_id: str, user: dict = Depends(admin_only)):
    """The open draft's computed review surface (U5: R10/R24): document plus
    per-child ride times with wall-clock stop times (backward from the gate
    anchor for AM, forward for PM), per-bus capacity use, total driving,
    per-leg unplaceable lists, the diff vs live with the notified-family
    count, and `basis_drift` — the exact enrolled/address-changed/departed
    rows apply's R22 gate computes, so the apply payload can be assembled
    without a blind POST. Provider-free: pure arithmetic over the stored
    durations."""
    return safe_call(lambda: dao.review(school_id))


# --- review edits (U5) ----------------------------------------------------
# Each edit recomputes the affected route(s) along the fixed sequence and
# re-checks hard constraints: violations 422 naming 'capacity' or 'stop cap',
# draft unchanged.


class MovePayload(BaseModel):
    student_id: str
    to_bus_id: str
    # None = both legs the child rides (the default). An explicit single leg
    # on a both-legs rider flips the document pattern to split.
    legs: list[str] | None = None
    # 0-based insert position for a NEW stop on the target leg(s); None
    # appends. Ignored when the child joins an existing same-place stop.
    position: int | None = None


class ReorderPayload(BaseModel):
    bus_id: str
    leg: str
    # The FULL ordered list of the leg's stop keys — the `key` each review
    # stop row carries — echoed back verbatim in the admin's chosen order
    # (the shipped stop-order contract's shape).
    order: list[str]


class PinPayload(BaseModel):
    student_id: str
    # Bus pin: one bus id for every leg the child rides, or a per-leg mapping
    # {"morning": ..., "afternoon": ...}. Stored in the document; re-solve
    # honours it as a solver input.
    bus: str | dict[str, str] | None = None
    # Order pin: 0-based stop position, scalar or per-leg mapping.
    order: int | dict[str, int] | None = None
    # 'bus' | 'order' | 'all' — removes that pin dimension instead of setting.
    unpin: str | None = None


class PatternPayload(BaseModel):
    student_id: str
    pattern: str


class AssignPayload(BaseModel):
    student_id: str
    bus_id: str
    # 0-based insert position, scalar or per-leg mapping; None appends.
    position: int | dict[str, int] | None = None
    # None = every leg the child is listed unplaceable for.
    legs: list[str] | None = None


@router.post("/{plan_id}/move")
def move_student(plan_id: str, payload: MovePayload, user: dict = Depends(admin_only)):
    """Move a placed child between buses — both legs by default; an explicit
    one-leg move flips the document pattern to split (stored pattern stays
    authoritative). Recomputes both affected routes' times."""
    return safe_call(
        lambda: dao.move_student(
            plan_id, payload.student_id, payload.to_bus_id,
            legs=payload.legs, position=payload.position,
        )
    )


@router.post("/{plan_id}/reorder")
def reorder_stops(plan_id: str, payload: ReorderPayload, user: dict = Depends(admin_only)):
    """Reorder one route's stops (explicit full-order echo of stop keys).
    Recomputes times along the new fixed sequence — never a re-ordering call."""
    return safe_call(
        lambda: dao.reorder_stops(plan_id, payload.bus_id, payload.leg, payload.order)
    )


@router.post("/{plan_id}/pin")
def set_pin(plan_id: str, payload: PinPayload, user: dict = Depends(admin_only)):
    """Pin/unpin a child: bus pin (per leg or both) and order pin, stored in
    the document so re-solve honours them."""
    return safe_call(
        lambda: dao.set_pin(
            plan_id, payload.student_id,
            bus=payload.bus, order=payload.order, unpin=payload.unpin,
        )
    )


@router.post("/{plan_id}/pattern")
def set_pattern(plan_id: str, payload: PatternPayload, user: dict = Depends(admin_only)):
    """Change a child's ridership pattern in the draft only (never
    live_students). Removing a leg drops the child's stop from that leg and
    updates times."""
    return safe_call(
        lambda: dao.set_pattern(plan_id, payload.student_id, payload.pattern)
    )


@router.post("/{plan_id}/assign")
def assign_student(plan_id: str, payload: AssignPayload, user: dict = Depends(admin_only)):
    """Place a currently-unplaceable child onto a bus at an explicit position,
    per pattern legs — the manual placement path until slot-ins ship."""
    return safe_call(
        lambda: dao.assign_student(
            plan_id, payload.student_id, payload.bus_id,
            position=payload.position, legs=payload.legs,
        )
    )


@router.post("/{plan_id}/resolve")
def resolve_draft(plan_id: str, user: dict = Depends(admin_only)):
    """Re-run the solver on the draft's basis with the document's pins and
    pattern edits as inputs (fresh in-memory matrix). Unpinned manual
    arrangements are discarded — the response says so explicitly."""
    return safe_call(lambda: dao.resolve_draft(plan_id))


class ApplyConfirmation(BaseModel):
    # One basis-drift item (R22), confirmed by kind + student:
    # 'enrolled' | 'address-changed' | 'departed'.
    student_id: str
    kind: str


class ApplyAcknowledgment(BaseModel):
    # One unplaceable child acknowledged by name and leg (R23).
    student_id: str
    leg: str


class ApplyPayload(BaseModel):
    # Every basis-drift item since generation must appear here or apply 409s
    # listing the unconfirmed ones.
    confirmations: list[ApplyConfirmation] = []
    # Every document unplaceable (student, leg) must appear here or apply
    # 422s naming the missing ones.
    acknowledgments: list[ApplyAcknowledgment] = []


@router.post("/{plan_id}/apply")
def apply_plan(plan_id: str, payload: ApplyPayload, user: dict = Depends(admin_only)):
    """Apply the draft (U6): ONE provider-free transaction — gates re-checked
    against the locked snapshot, displaced live state preserved as 'previous'
    (one-level history), routes reconciled in place, links and stops
    materialized from the document, audit row, feed rows and baselines — then
    post-commit geometry refresh (never times) and awaited push delivery.
    Re-applying an applied plan is idempotent by status (200, no side
    effects)."""
    return safe_call(
        lambda: dao.apply_plan(
            plan_id,
            confirmations=[c.model_dump() for c in payload.confirmations],
            acknowledgments=[a.model_dump() for a in payload.acknowledgments],
            actor=user,
        )
    )


@router.post("/{plan_id}/restore")
def restore_plan(plan_id: str, payload: ApplyPayload, user: dict = Depends(admin_only)):
    """Restore the preserved 'previous' plan (U7): an apply whose source is
    the as-evolved capture, addressed by the PRESERVED plan's id (from
    GET /current) so a gateway-timeout retry hits the now-applied row and
    no-ops instead of toggling the school back. Same payload as apply: roster
    drift since the capture (a departed child dropped by name, a child
    enrolled after the capture left routeless) is confirmed via
    `confirmations`; fleet drift 409s naming the bus — no partial restore.
    Same machinery, same order: diff pre-mutation, one-level previous toggle,
    audit ('plan-restored'), feed rows and baselines in-transaction, then
    geometry-only refresh and awaited push. Restoring the already-applied
    plan is a 200 no-op."""
    return safe_call(
        lambda: dao.restore_plan(
            plan_id,
            confirmations=[c.model_dump() for c in payload.confirmations],
            acknowledgments=[a.model_dump() for a in payload.acknowledgments],
            actor=user,
        )
    )


@router.post("/{plan_id}/discard")
def discard_draft(plan_id: str, user: dict = Depends(admin_only)):
    """Discard an open draft: status flips to discarded and its
    document/basis payloads are scrubbed (metadata kept)."""
    return safe_call(lambda: dao.discard_draft(plan_id))


# --- slot-in proposals (U12) -----------------------------------------------
# Generated in BackgroundTasks by the students_live triggers (beside the U13
# wiring), stored on the school's APPLIED plan row (slot_in_service documents
# the decision), and acted on here. All admin-only like every plan surface.


class SlotInActionPayload(BaseModel):
    school_id: str
    # The stored record's id (from GET /slot-ins).
    proposal_id: str


@router.get("/slot-ins")
def list_slot_ins(school_id: str, user: dict = Depends(admin_only)):
    """The school's pending slot-in proposals and unplaceable notices, each
    stated as position + effect (AE2), with aged-out records marked (never
    auto-applied, never auto-removed — the student stays visibly
    unassigned). 404 when the school has no applied plan."""
    return safe_call(lambda: dao.list_slot_ins(school_id))


@router.post("/slot-ins/accept")
def accept_slot_in(
    payload: SlotInActionPayload, background_tasks: BackgroundTasks,
    user: dict = Depends(admin_only),
):
    """Accept one proposal (U12): applies JUST that insertion — the stop
    materialized at the stated position along the fixed order (a frozen
    route keeps its manual order, AE7), times recomputed along the sequence,
    the student linked — then notifies ONLY the affected families through
    the shipped U13 diff/baseline pipeline post-commit (the new child as a
    first communication, co-riders on the >= 5-minute rule; no audit row —
    the manual-edit path's contract)."""
    result = safe_call(
        lambda: dao.accept_slot_in(payload.school_id, payload.proposal_id)
    )
    background_tasks.add_task(
        notify_route_changes,
        route_ids=result["route_ids"],
        student_ids=[result["student_id"]],
    )
    return result


@router.post("/slot-ins/dismiss")
def dismiss_slot_in(payload: SlotInActionPayload, user: dict = Depends(admin_only)):
    """Dismiss one record: removed from the store with zero live-table side
    effects — the student stays visibly unassigned."""
    return safe_call(lambda: dao.dismiss_slot_in(payload.school_id, payload.proposal_id))
