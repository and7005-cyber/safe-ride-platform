from collections.abc import Callable
from typing import TypeVar

from fastapi import APIRouter, HTTPException, Query
from psycopg import Error as PsycopgError

from app.core.errors import SafeRideError, to_http_exception
from app.dao.driver_dao import DriverDao
from app.schemas.driver import DriverLoginRequest, RecordDriverEventRequest
from app.services.driver_service import DriverService

router = APIRouter(prefix="/api/driver", tags=["driver"])
dao = DriverDao()
service = DriverService(dao)
T = TypeVar("T")

BAD_REQUEST_SQLSTATES = {
    "22P02",  # invalid_text_representation, such as malformed UUIDs
    "22007",  # invalid_datetime_format
    "22008",  # datetime_field_overflow
    "23514",  # check_violation
}


def map_error(error: Exception) -> HTTPException:
    if isinstance(error, SafeRideError):
        return to_http_exception(error)
    if isinstance(error, PsycopgError):
        if error.sqlstate in BAD_REQUEST_SQLSTATES:
            return HTTPException(status_code=400, detail="Invalid driver request")
        if error.sqlstate == "23503":
            return HTTPException(status_code=404, detail="Referenced record was not found")
        if error.sqlstate == "23505":
            return HTTPException(status_code=409, detail="Record already exists")
    return HTTPException(status_code=500, detail="Unexpected backend error")


def safe_call(action: Callable[[], T]) -> T:
    try:
        return action()
    except Exception as error:
        raise map_error(error) from error


@router.post("/login")
def login(request: DriverLoginRequest):
    return safe_call(lambda: service.verify_pin(request.pin))


@router.get("/trips/today")
def list_trips_today(session_token: str = Query(...), service_date: str = Query(...)):
    return safe_call(lambda: dao.list_trips_for_today(session_token, service_date))


@router.get("/trips/{trip_id}/passengers")
def list_trip_passengers(trip_id: str, session_token: str = Query(...)):
    return safe_call(lambda: dao.list_trip_passengers(session_token, trip_id))


@router.post("/events")
def record_event(request: RecordDriverEventRequest):
    return safe_call(lambda: {"eventId": service.record_event(request)})
