"""The director's staff surface (U8): create, offer, list, remove, reset.

Everything here is school-scoped (X-School-Id + membership); management
actions are director-only, and a stepped-in provider qualifies automatically
because its resolved scope carries ``role='director'`` (U5). The viewing
route accepts coordinators too — the Staff page hides itself for them in the
frontend (U12), but seeing the list leaks nothing they could not already see.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.api._helpers import safe_call
from app.core.auth import get_current_user
from app.core.permissions import require_director, require_staff
from app.core.rate_limit import SlidingWindowLimiter
from app.core.scope import SchoolScope
from app.services.staff_service import StaffService

router = APIRouter(prefix="/api/staff", tags=["staff"])
service = StaffService()

# Staff-creation blast protection: per acting user (not per IP — this is an
# authenticated surface), in-process best-effort like the auth.py limiters.
# 20/hour covers a whole onboarding session while stopping a stuck retry loop
# (or a hijacked director session) from minting accounts unattended.
staff_create_limiter = SlidingWindowLimiter(max_attempts=20, window_seconds=3600)
_STAFF_CREATE_LIMIT_MESSAGE = "Too many staff invitations this hour. Try again later."


class CreateStaffPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    email: str
    full_name: str | None = Field(default=None, alias="fullName")
    role: str  # director | coordinator — validated in the service


@router.get("")
def list_staff(scope: SchoolScope = Depends(require_staff)):
    return safe_call(lambda: service.list_staff(scope))


@router.post("")
def create_staff(
    payload: CreateStaffPayload,
    scope: SchoolScope = Depends(require_director),
    user: dict = Depends(get_current_user),
):
    staff_create_limiter.check(str(user["id"]), _STAFF_CREATE_LIMIT_MESSAGE)
    return safe_call(
        lambda: service.create_staff(
            scope, payload.email, payload.full_name, payload.role, actor=user
        )
    )


@router.delete("/{user_id}", status_code=204)
def remove_staff(
    user_id: str,
    scope: SchoolScope = Depends(require_director),
    user: dict = Depends(get_current_user),
):
    # Self-removal is allowed; the last-director lock (AE4) answers 409.
    safe_call(lambda: service.remove_staff(scope, user_id, actor=user))
    return None


@router.delete("/offers/{offer_id}", status_code=204)
def cancel_offer(
    offer_id: str,
    scope: SchoolScope = Depends(require_director),
    user: dict = Depends(get_current_user),
):
    safe_call(lambda: service.cancel_offer(scope, offer_id, actor=user))
    return None


@router.post("/{user_id}/reset-password")
def reset_password(
    user_id: str,
    scope: SchoolScope = Depends(require_director),
    user: dict = Depends(get_current_user),
):
    # The response carries the new temporary password ONCE (AE23); the audit
    # row records who reset whom — never the password.
    return safe_call(lambda: service.reset_password(scope, user_id, actor=user))
