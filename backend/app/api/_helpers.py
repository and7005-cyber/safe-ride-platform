from collections.abc import Callable
from typing import TypeVar

from fastapi import HTTPException
from psycopg import Error as PsycopgError

from app.core.errors import SafeRideError, to_http_exception

T = TypeVar("T")

BAD_REQUEST_SQLSTATES = {"22P02", "22007", "22008", "23514"}


def map_error(error: Exception) -> HTTPException:
    if isinstance(error, SafeRideError):
        return to_http_exception(error)
    if isinstance(error, PsycopgError):
        if error.sqlstate in BAD_REQUEST_SQLSTATES:
            return HTTPException(status_code=400, detail="Invalid request")
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
