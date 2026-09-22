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


class PromptConflictError(ConflictError):
    """A prompt answer the ledger cannot take (GPS plan U3/R23, R34).

    Carries a machine-readable ``code`` — ``prompt-already-answered`` when a
    different answer arrives after one was recorded, ``prompt-resolved`` when
    any answer arrives after Arrive, End Run or force-close closed the prompt
    as unanswered — plus the recorded state, so the client can drop the card
    and refresh without parsing the message. A replay of the same answer is
    not a conflict and never raises this.
    """

    def __init__(
        self, message: str, *, code: str, prompt_state: str | None, response: str | None
    ):
        super().__init__(message)
        self.code = code
        self.prompt_state = prompt_state
        self.response = response


class TooManyRequestsError(SafeRideError):
    status_code = 429


def to_http_exception(error: SafeRideError) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail=str(error))
