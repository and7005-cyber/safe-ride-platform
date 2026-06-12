from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api._helpers import safe_call
from app.core.auth import require_role
from app.services.account_service import AccountService

router = APIRouter(prefix="/api/accounts", tags=["accounts"])
service = AccountService()
admin_only = require_role("admin")


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


class LinkPayload(BaseModel):
    parent_id: str
    student_id: str


# Drivers --------------------------------------------------------------------

@router.get("/drivers")
def list_drivers(user: dict = Depends(admin_only)):
    return safe_call(service.list_drivers)


@router.post("/drivers")
def create_driver(payload: CreateDriverPayload, user: dict = Depends(admin_only)):
    return safe_call(
        lambda: service.create_driver(
            payload.email, payload.password, payload.full_name, payload.phone, payload.pin
        )
    )


@router.put("/drivers/{driver_id}")
def update_driver(driver_id: str, payload: UpdateDriverPayload, user: dict = Depends(admin_only)):
    return safe_call(
        lambda: service.update_driver(
            driver_id, payload.full_name, payload.email, payload.phone, payload.pin
        )
    )


@router.delete("/drivers/{driver_id}")
def delete_driver(driver_id: str, user: dict = Depends(admin_only)):
    return safe_call(lambda: (service.delete_driver(driver_id), {"ok": True})[1])


# Parents --------------------------------------------------------------------

@router.get("/parents")
def list_parents(user: dict = Depends(admin_only)):
    return safe_call(service.list_parents)


@router.put("/parents/{parent_id}")
def update_parent(parent_id: str, payload: UpdateParentPayload, user: dict = Depends(admin_only)):
    return safe_call(
        lambda: service.update_parent(parent_id, payload.full_name, payload.email, payload.phone)
    )


@router.delete("/parents/{parent_id}")
def delete_parent(parent_id: str, user: dict = Depends(admin_only)):
    return safe_call(lambda: (service.delete_parent(parent_id), {"ok": True})[1])


# Parent ↔ student assignments ----------------------------------------------

@router.get("/parent-students")
def list_parent_students(user: dict = Depends(admin_only)):
    return safe_call(service.list_parent_students)


@router.post("/parent-students")
def link_parent_student(payload: LinkPayload, user: dict = Depends(admin_only)):
    return safe_call(lambda: service.link_parent_student(payload.parent_id, payload.student_id))


@router.delete("/parent-students/{link_id}")
def unlink_parent_student(link_id: str, user: dict = Depends(admin_only)):
    return safe_call(lambda: (service.unlink_parent_student(link_id), {"ok": True})[1])
