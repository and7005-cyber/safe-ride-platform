from fastapi import HTTPException


class SafeRideError(Exception):
    status_code = 500


class BadRequestError(SafeRideError):
    status_code = 400


class UnauthorizedError(SafeRideError):
    status_code = 401


class ForbiddenError(SafeRideError):
    status_code = 403


class NotFoundError(SafeRideError):
    status_code = 404


class ConflictError(SafeRideError):
    status_code = 409


class TooManyRequestsError(SafeRideError):
    status_code = 429


def to_http_exception(error: SafeRideError) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail=str(error))
