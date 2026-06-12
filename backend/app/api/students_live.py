from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api._helpers import safe_call
from app.core.auth import get_current_user, require_role
from app.dao.student_live_dao import StudentLiveDao

router = APIRouter(prefix="/api/students", tags=["students"])
dao = StudentLiveDao()
admin_only = require_role("admin")


class StudentPayload(BaseModel):
    name: str
    grade: str | None = None
    parent_name: str | None = None
    parent_phone: str | None = None
    parent_phone2: str | None = None
    parent_email: str | None = None
    home_address: str | None = None
    home_lat: float | None = None
    home_lng: float | None = None
    pickup_time: str | None = None
    status: str | None = "at-school"
    bus_id: str | None = None
    school_id: str | None = None
    route_ids: list[str] = []


class BulkRow(BaseModel):
    name: str
    grade: str | None = None
    parent_name: str | None = None
    parent_phone: str | None = None
    parent_phone2: str | None = None
    parent_email: str | None = None
    home_address: str | None = None
    home_lat: float | None = None
    home_lng: float | None = None
    pickup_time: str | None = None
    route_name: str | None = None


class BulkPayload(BaseModel):
    students: list[BulkRow]


@router.get("")
def list_students(user: dict = Depends(get_current_user)):
    return safe_call(dao.list_students)


@router.post("")
def create_student(payload: StudentPayload, user: dict = Depends(admin_only)):
    data = payload.model_dump()
    route_ids = data.pop("route_ids")
    return safe_call(lambda: dao.create_student(data, route_ids))


@router.put("/{student_id}")
def update_student(student_id: str, payload: StudentPayload, user: dict = Depends(admin_only)):
    data = payload.model_dump()
    route_ids = data.pop("route_ids")
    return safe_call(lambda: dao.update_student(student_id, data, route_ids))


@router.delete("/{student_id}")
def delete_student(student_id: str, user: dict = Depends(admin_only)):
    return safe_call(lambda: (dao.delete_student(student_id), {"ok": True})[1])


@router.post("/bulk")
def bulk_upload(payload: BulkPayload, user: dict = Depends(admin_only)):
    def run() -> dict:
        inserted = 0
        assignments = 0
        errors: list[str] = []
        for index, row in enumerate(payload.students):
            label = row.name or f"row {index + 1}"
            if not row.name or not row.grade or not row.parent_name or not row.parent_phone:
                errors.append(f"{label}: missing required field (name, grade, parent name, parent phone)")
                continue
            try:
                assignments += dao.insert_bulk_student(row.model_dump())
                inserted += 1
            except Exception as exc:  # noqa: BLE001 - surfaced per-row to the client
                errors.append(f"{label}: {exc}")
        return {"inserted": inserted, "parentAssignments": assignments, "errors": errors}

    return safe_call(run)
