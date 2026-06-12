from collections.abc import Callable
from typing import TypeVar

from fastapi import APIRouter, HTTPException, Query
from psycopg import errors as pg_errors

from app.core.errors import SafeRideError, to_http_exception
from app.dao.admin_dao import AdminDao
from app.schemas.admin import (
    CorrectTripPassengerStatusRequest,
    CreateBusRequest,
    CreateDriverRequest,
    CreateParentContactRequest,
    CreateParentLinkRequest,
    CreateStudentSetupRequest,
    CreateStudentRequest,
    CreateTripPassengerRequest,
    CreateTripRequest,
    MarkDailyAttendanceRequest,
    UpdateStudentRequest,
)
from app.services.admin_service import AdminService

router = APIRouter(prefix="/api/admin", tags=["admin"])
dao = AdminDao()
service = AdminService(dao)
T = TypeVar("T")


def map_error(error: Exception) -> HTTPException:
    if isinstance(error, SafeRideError):
        return to_http_exception(error)
    if isinstance(error, pg_errors.UniqueViolation):
        return HTTPException(status_code=409, detail="Record already exists")
    if isinstance(error, pg_errors.ForeignKeyViolation):
        return HTTPException(status_code=404, detail="Referenced record was not found")
    if isinstance(error, (pg_errors.CheckViolation, pg_errors.InvalidTextRepresentation)):
        return HTTPException(status_code=400, detail="Invalid admin request")
    return HTTPException(status_code=500, detail="Unexpected backend error")


def safe_call(action: Callable[[], T]) -> T:
    try:
        return action()
    except Exception as error:
        raise map_error(error) from error


@router.get("/trips/active")
def list_active_trips(school_id: str = Query(...)):
    return safe_call(lambda: dao.list_active_trips(school_id))


@router.get("/students")
def list_students(school_id: str = Query(...)):
    return safe_call(lambda: dao.list_students(school_id))


@router.get("/student-directory")
def list_student_directory(school_id: str = Query(...)):
    return safe_call(lambda: dao.list_student_directory(school_id))


@router.patch("/students/{student_id}")
def update_student(student_id: str, request: UpdateStudentRequest):
    return safe_call(lambda: service.update_student(student_id, request))


@router.post("/student-setups")
def create_student_setup(request: CreateStudentSetupRequest):
    return safe_call(lambda: service.create_student_setup(request))


@router.get("/buses")
def list_buses(school_id: str = Query(...)):
    return safe_call(lambda: dao.list_buses(school_id))


@router.get("/drivers")
def list_drivers(school_id: str = Query(...)):
    return safe_call(lambda: dao.list_drivers(school_id))


@router.get("/trips")
def list_trips(school_id: str = Query(...)):
    return safe_call(lambda: dao.list_trips(school_id))


@router.get("/trips/completed")
def list_completed_trips(school_id: str = Query(...)):
    return safe_call(lambda: dao.list_completed_trips(school_id))


@router.get("/alerts")
def list_driver_alerts(school_id: str = Query(...)):
    return safe_call(lambda: dao.list_driver_alerts(school_id))


@router.post("/buses")
def create_bus(request: CreateBusRequest):
    return safe_call(lambda: dao.create_bus(request))


@router.post("/students")
def create_student(request: CreateStudentRequest):
    return safe_call(lambda: dao.create_student(request))


@router.post("/drivers")
def create_driver(request: CreateDriverRequest):
    return safe_call(lambda: service.create_driver(request))


@router.post("/parent-contacts")
def create_parent_contact(request: CreateParentContactRequest):
    return safe_call(lambda: dao.upsert_parent_contact(request))


@router.post("/parent-links")
def create_parent_link(request: CreateParentLinkRequest):
    return safe_call(lambda: dao.create_parent_link(request))


@router.post("/trips")
def create_trip(request: CreateTripRequest):
    return safe_call(lambda: dao.create_trip(request))


@router.post("/trip-passengers")
def create_trip_passenger(request: CreateTripPassengerRequest):
    return safe_call(lambda: dao.create_trip_passenger(request))


@router.post("/daily-attendance")
def mark_daily_attendance(request: MarkDailyAttendanceRequest):
    return safe_call(lambda: service.mark_daily_attendance(request))


@router.post("/trip-passenger-corrections")
def correct_trip_passenger_status(request: CorrectTripPassengerStatusRequest):
    return safe_call(lambda: {"auditId": service.correct_trip_passenger_status(request)})
