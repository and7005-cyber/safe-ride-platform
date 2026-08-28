from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api._helpers import safe_call
from app.core.auth import get_current_user
from app.core.permissions import require_director, require_staff
from app.core.scope import SchoolScope
from app.services.account_service import AccountService

router = APIRouter(prefix="/api/accounts", tags=["accounts"])
service = AccountService()


class CreateDriverPayload(BaseModel):
    full_name: str
    email: str
    password: str
    phone: str | None = None
    pin: str | None = None


class UpdateDriverPayload(BaseModel):
    full_name: str
    email: str
    phone: str | None = None
    pin: str | None = None  # blank keeps existing PIN


class UpdateParentPayload(BaseModel):
    full_name: str
    email: str
    phone: str | None = None


# Drivers (U6: school-scoped — a driver belongs to the creating school, R28) --

@router.get("/drivers")
def list_drivers(scope: SchoolScope = Depends(require_staff)):
    return safe_call(lambda: service.list_drivers(scope))


@router.post("/drivers")
def create_driver(
    payload: CreateDriverPayload,
    scope: SchoolScope = Depends(require_staff),
    user: dict = Depends(get_current_user),
):
    return safe_call(
        lambda: service.create_driver(
            scope, payload.email, payload.password, payload.full_name,
            payload.phone, payload.pin, actor=user,
        )
    )


@router.put("/drivers/{driver_id}")
def update_driver(
    driver_id: str,
    payload: UpdateDriverPayload,
    scope: SchoolScope = Depends(require_staff),
    user: dict = Depends(get_current_user),
):
    return safe_call(
        lambda: service.update_driver(
            scope, driver_id, payload.full_name, payload.email,
            payload.phone, payload.pin, actor=user,
        )
    )


@router.delete("/drivers/{driver_id}")
def delete_driver(
    driver_id: str,
    scope: SchoolScope = Depends(require_director),
    user: dict = Depends(get_current_user),
):
    return safe_call(
        lambda: (service.delete_driver(scope, driver_id, actor=user), {"ok": True})[1]
    )


# Parents (U6: only parents linked to this school's students; R33) ------------

@router.get("/parents")
def list_parents(scope: SchoolScope = Depends(require_staff)):
    return safe_call(lambda: service.list_parents(scope))


@router.put("/parents/{parent_id}")
def update_parent(
    parent_id: str,
    payload: UpdateParentPayload,
    scope: SchoolScope = Depends(require_staff),
    user: dict = Depends(get_current_user),
):
    return safe_call(
        lambda: service.update_parent(
            scope, parent_id, payload.full_name, payload.email, payload.phone,
            actor=user,
        )
    )


@router.delete("/parents/{parent_id}")
def delete_parent(
    parent_id: str,
    scope: SchoolScope = Depends(require_director),
    user: dict = Depends(get_current_user),
):
    # Deleting a parent account is account removal — director-only (R8
    # clarification in the plan's permission matrix).
    return safe_call(
        lambda: (service.delete_parent(scope, parent_id, actor=user), {"ok": True})[1]
    )

# Parent ↔ student links are derived from the emails on each student record
# (R11/R12) — see student create/update sync and auth signup. There are no
# manual link/unlink endpoints anymore.
