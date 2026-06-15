from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api._helpers import safe_call
from app.core.auth import get_current_user, require_role
from app.core.validation import clean_email, clean_phone
from app.dao.absence_dao import AbsenceDao
from app.dao.student_live_dao import StudentLiveDao
from app.services import geo_service

router = APIRouter(prefix="/api/students", tags=["students"])
dao = StudentLiveDao()
absence_dao = AbsenceDao()
admin_only = require_role("admin")


def _clean_student(data: dict) -> dict:
    """Validate phones/email and best-effort geocode a typed address so a
    student lands on a real route stop without needing a manual map pin (#4, #13)."""
    data["parent_phone"] = clean_phone(data.get("parent_phone"), field="parent phone")
    data["parent_phone2"] = clean_phone(data.get("parent_phone2"), field="second phone")
    data["parent_email"] = clean_email(data.get("parent_email"), field="parent email")
    if data.get("home_address") and (data.get("home_lat") is None or data.get("home_lng") is None):
        # allow_fallback=False → no network call unless a maps key is configured.
        hit = geo_service.geocode(data["home_address"], allow_fallback=False)
        if hit:
            data["home_lat"], data["home_lng"] = hit["lat"], hit["lng"]
    return data


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
    return safe_call(lambda: dao.create_student(_clean_student(data), route_ids))


@router.put("/{student_id}")
def update_student(student_id: str, payload: StudentPayload, user: dict = Depends(admin_only)):
    data = payload.model_dump()
    route_ids = data.pop("route_ids")
    return safe_call(lambda: dao.update_student(student_id, _clean_student(data), route_ids))


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
                assignments += dao.insert_bulk_student(_clean_student(row.model_dump()))
                inserted += 1
            except Exception as exc:  # noqa: BLE001 - surfaced per-row to the client
                errors.append(f"{label}: {exc}")
        return {"inserted": inserted, "parentAssignments": assignments, "errors": errors}

    return safe_call(run)


# Absences (#7) --------------------------------------------------------------

class AbsencePayload(BaseModel):
    student_id: str
    date: str | None = None  # defaults to today (Africa/Nairobi)
    reason: str | None = None


@router.get("/absences")
def list_absences(date: str | None = None, user: dict = Depends(get_current_user)):
    return safe_call(lambda: absence_dao.list_absences(date))


@router.post("/absences")
def mark_absent(payload: AbsencePayload, user: dict = Depends(admin_only)):
    # date=None defaults to today (Africa/Nairobi) inside the DAO.
    return safe_call(
        lambda: absence_dao.mark_absent(payload.student_id, payload.date, payload.reason, user["id"])
    )


@router.delete("/absences/{absence_id}")
def clear_absence(absence_id: str, user: dict = Depends(admin_only)):
    return safe_call(lambda: (absence_dao.clear_absence(absence_id), {"ok": True})[1])
