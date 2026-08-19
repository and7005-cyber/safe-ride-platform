"""Fleet-plan endpoints (U4): fleet confirmation, draft generation, current
plans, discard. F1/F4 on the backend — the review/edit surface (U5) and apply
(U6) land in later units on this same router.

All admin-only: plan documents aggregate every enrolled child's name and home
coordinates, the R19 rationale's aggregate PII target.
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api._helpers import safe_call
from app.core.auth import require_role
from app.dao.fleet_plan_dao import FleetPlanDao

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
    without payloads. Review computation is U5's job."""
    return safe_call(lambda: dao.current_plans(school_id))


@router.post("/{plan_id}/discard")
def discard_draft(plan_id: str, user: dict = Depends(admin_only)):
    """Discard an open draft: status flips to discarded and its
    document/basis payloads are scrubbed (metadata kept)."""
    return safe_call(lambda: dao.discard_draft(plan_id))
