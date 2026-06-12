from collections.abc import Callable
from typing import TypeVar

from fastapi import APIRouter, HTTPException
from psycopg import Error as PsycopgError

from app.core.errors import SafeRideError, to_http_exception
from app.schemas.parent import RegisterPushSubscriptionRequest
from app.services.parent_service import ParentService

router = APIRouter(prefix="/api/parent", tags=["parent"])
service = ParentService()
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
            return HTTPException(status_code=400, detail="Invalid parent request")
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


@router.get("/trips/{token}")
def get_trip_progress(token: str):
    return safe_call(lambda: service.get_trip_progress(token))


@router.post("/push-subscriptions")
def register_push_subscription(request: RegisterPushSubscriptionRequest):
    return safe_call(
        lambda: service.register_push_subscription(
            request.token,
            request.subscription.endpoint,
            request.subscription.keys.p256dh,
            request.subscription.keys.auth,
        )
    )
