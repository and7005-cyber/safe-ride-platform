import logging
import math
from collections.abc import Callable
from typing import TypeVar

from fastapi import HTTPException
from psycopg import Error as PsycopgError

from app.core.errors import (
    IdempotencyConflictError,
    PingPacedError,
    PingRefusedError,
    PromptConflictError,
    SafeRideError,
    to_http_exception,
)

T = TypeVar("T")

logger = logging.getLogger("saferide.scope")

BAD_REQUEST_SQLSTATES = {"22P02", "22007", "22008", "23514"}


def map_error(error: Exception) -> HTTPException:
    if isinstance(error, (IdempotencyConflictError, PingRefusedError)):
        # Same shape as the prompt conflicts (GPS plan U7/R33; U14/R28):
        # `code` is what the client branches on; the detail never carries
        # the stored response or any of the request's own fields.
        return HTTPException(
            status_code=error.status_code,
            detail={"code": error.code, "message": str(error)},
        )
    if isinstance(error, PingPacedError):
        # The 429 is structured too (GPS plan U14): `code` plus how long the
        # stream is out of pace, and the standard Retry-After header.
        wait = max(1, math.ceil(error.retry_after_s))
        return HTTPException(
            status_code=error.status_code,
            detail={
                "code": error.code,
                "message": str(error),
                "retry_after_s": round(error.retry_after_s, 3),
            },
            headers={"Retry-After": str(wait)},
        )
    if isinstance(error, PromptConflictError):
        # Structured like the auth surface's second-factor refusals: the
        # client branches on `code`, never on the sentence (GPS plan U3).
        return HTTPException(
            status_code=error.status_code,
            detail={
                "code": error.code,
                "message": str(error),
                "prompt_state": error.prompt_state,
                "response": error.response,
            },
        )
    if isinstance(error, SafeRideError):
        return to_http_exception(error)
    if isinstance(error, PsycopgError):
        if error.sqlstate in BAD_REQUEST_SQLSTATES:
            return HTTPException(status_code=400, detail="Invalid request")
        if error.sqlstate == "23503":
            return HTTPException(status_code=404, detail="Referenced record was not found")
        if error.sqlstate == "23505":
            return HTTPException(status_code=409, detail="Record already exists")
        if error.sqlstate == "42501":
            # Row security refused a write the DAO predicates should already
            # have filtered: answer with the not-found contract, but log the
            # denial loudly — it means a scope bug, not a user mistake.
            diag = getattr(error, "diag", None)
            logger.error(
                "row-security denial sqlstate=42501 table=%s message=%s",
                getattr(diag, "table_name", None),
                getattr(diag, "message_primary", None),
            )
            return HTTPException(status_code=404, detail="Not found")
    return HTTPException(status_code=500, detail="Unexpected backend error")


def safe_call(action: Callable[[], T]) -> T:
    try:
        return action()
    except Exception as error:
        raise map_error(error) from error
