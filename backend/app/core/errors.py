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


class ClosureRefusedError(ConflictError):
    """The closure gate refused: named children have no recorded outcome (U11).

    Carries the blocking set so the router can raise the office alert naming
    them. The alternative was re-deriving the set after the transaction rolled
    back, or parsing it out of the message — the first can disagree with what
    the driver was actually told, and the second makes the wording load-bearing.
    """

    def __init__(self, message: str, *, run_id: str, blocking: list[dict]):
        super().__init__(message)
        self.run_id = run_id
        self.blocking = blocking


class TooManyRequestsError(SafeRideError):
    status_code = 429


def to_http_exception(error: SafeRideError) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail=str(error))
