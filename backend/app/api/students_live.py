from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api._helpers import safe_call
from app.core.auth import get_current_user, require_role
from app.core.errors import BadRequestError
from app.core.validation import clean_email, clean_phone
from app.dao.absence_dao import AbsenceDao
from app.dao.student_live_dao import StudentLiveDao
from app.services import geo_service

router = APIRouter(prefix="/api/students", tags=["students"])
dao = StudentLiveDao()
absence_dao = AbsenceDao()
admin_only = require_role("admin")


def _clean_student(data: dict, *, geocode_fallback: bool = True) -> dict:
    """Enforce the two-parent contact contract (R9–R10) and best-effort geocode
    a typed address so a student lands on a real route stop without needing a
    manual map pin (#4, #13).

    Every student needs a Parent 1 name, at least one phone across the two
    parent slots, and at least one email across the two slots — the emails
    drive parent-account linking (R11), the phones the emergency contact list.
    ``parent_phone2`` is Parent 2's phone (the pre-existing "second phone"
    column, reused rather than renamed).

    Single saves allow the free OSM fallback (so addresses get coordinates even
    with no maps key); bulk upload passes ``geocode_fallback=False`` to avoid
    hammering the free service's rate limit row-by-row.
    """
    if not str(data.get("parent_name") or "").strip():
        raise BadRequestError("Parent 1 name is required")
    data["parent_name"] = str(data["parent_name"]).strip()
    data["parent2_name"] = str(data.get("parent2_name") or "").strip() or None
    data["parent_phone"] = clean_phone(data.get("parent_phone"), field="parent 1 phone")
    data["parent_phone2"] = clean_phone(data.get("parent_phone2"), field="parent 2 phone")
    data["parent_email"] = clean_email(data.get("parent_email"), field="parent 1 email")
    data["parent2_email"] = clean_email(data.get("parent2_email"), field="parent 2 email")
    if not data["parent_phone"] and not data["parent_phone2"]:
        raise BadRequestError("At least one parent phone number is required")
    if not data["parent_email"] and not data["parent2_email"]:
        raise BadRequestError("At least one parent email is required")
    if data.get("home_address") and (data.get("home_lat") is None or data.get("home_lng") is None):
        hit = geo_service.geocode(data["home_address"], allow_fallback=geocode_fallback)
        if hit:
            data["home_lat"], data["home_lng"] = hit["lat"], hit["lng"]
    return data


class StudentPayload(BaseModel):
    name: str
    grade: str | None = None
    parent_name: str | None = None
    parent_phone: str | None = None
    parent_phone2: str | None = None  # Parent 2's phone (reused column)
    parent_email: str | None = None
    parent2_name: str | None = None
    parent2_email: str | None = None
    home_address: str | None = None
    home_lat: float | None = None
    home_lng: float | None = None
    pickup_time: str | None = None
    # Still accepted for backwards compatibility, but PUT ignores it: admin
    # edits must never reset a live status (R7).
    status: str | None = "at-school"
    bus_id: str | None = None
    school_id: str | None = None
    route_ids: list[str] = []


class BulkRow(BaseModel):
    name: str
    grade: str | None = None
    parent_name: str | None = None
    parent_phone: str | None = None
    parent_phone2: str | None = None  # Parent 2's phone (reused column)
    parent_email: str | None = None
    parent2_name: str | None = None
    parent2_email: str | None = None
    home_address: str | None = None
    home_lat: float | None = None
    home_lng: float | None = None
    pickup_time: str | None = None
    route_name: str | None = None


class BulkPayload(BaseModel):
    students: list[BulkRow]


@router.get("")
def list_students(user: dict = Depends(admin_only)):
    # Admin-only: rows carry parent contact PII and home coordinates, and the
    # email slots gate parent-account linking (R11). Drivers get their roster
    # via /api/runs/driver/context; parents via /api/parent-portal/children.
    return safe_call(dao.list_students)


@router.post("")
def create_student(payload: StudentPayload, user: dict = Depends(admin_only)):
    # The response carries stops_recalculated (U6/R10): false when an affected
    # auto route fell back to the preserved/pickup-time order instead of
    # recomputing geometry (the durable signal is live_routes.last_recalc_degraded).
    data = payload.model_dump()
    route_ids = data.pop("route_ids")
    return safe_call(lambda: dao.create_student(_clean_student(data), route_ids))


@router.put("/{student_id}")
def update_student(student_id: str, payload: StudentPayload, user: dict = Depends(admin_only)):
    # Carries stops_recalculated like create (U6/R10).
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
            if not row.name or not row.grade or not row.parent_name:
                errors.append(f"{label}: missing required field (name, grade, parent name)")
                continue
            if not row.parent_phone and not row.parent_phone2:
                errors.append(f"{label}: at least one parent phone is required")
                continue
            if not row.parent_email and not row.parent2_email:
                errors.append(f"{label}: at least one parent email is required")
                continue
            try:
                assignments += dao.insert_bulk_student(_clean_student(row.model_dump(), geocode_fallback=False))
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
def list_absences(date: str | None = None, user: dict = Depends(admin_only)):
    # Admin-only: named child absences are exactly the data the incidents
    # feed was locked down for; the sole consumer is the admin StudentsPage.
    # Rows carry scope ('day'/'morning'/'afternoon') and source
    # ('parent'/'driver'/'admin') so the UI can render partial parent
    # cancellations and gate its actions on provenance (U4).
    return safe_call(lambda: absence_dao.list_absences(date))


@router.post("/absences")
def mark_absent(payload: AbsencePayload, user: dict = Depends(admin_only)):
    # date=None defaults to today (Africa/Nairobi) inside the DAO. A
    # today-dated mark also sets the live status to 'absent' and appends the
    # run_absences snapshot of any active run carrying the student (R25b);
    # other dates are bookkeeping only. An admin mark is always whole-day:
    # the DAO escalates an existing partial parent cancellation to
    # scope='day', source='admin' (U4).
    return safe_call(
        lambda: absence_dao.mark_absent(payload.student_id, payload.date, payload.reason, user["id"])
    )


@router.delete("/absences/{absence_id}")
def clear_absence(absence_id: str, user: dict = Depends(admin_only)):
    # Clearing a today-dated absence 409s while the student is involved in an
    # active run of a type the absence covers ("End the run first"), else
    # resets an 'absent' status to 'at-school' when the row was whole-day.
    # Past/future-dated clears have no status side-effects.
    return safe_call(lambda: (absence_dao.clear_absence(absence_id), {"ok": True})[1])
