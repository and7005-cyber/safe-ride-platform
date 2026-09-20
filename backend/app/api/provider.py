"""The provider console (U10): Kuumbai's cross-school surface.

Every route requires an active provider account (``require_provider`` —
``ProviderScope``, outside any school; the ``X-School-Id`` header plays no
part here) and, until the second factor is confirmed, the enrolment 409 in
``get_current_user`` locks everything but ``totp/enrol`` + ``totp/confirm``.
Step-in, account create/remove and TOTP reset additionally demand a code
verified within fifteen minutes ("step-up"); school creation deliberately
rides on the ordinary session (only step-in and account lifecycle are
step-up-gated — the plan gates nothing else).

A router-level dependency runs the lazy four-hour janitor: any of this
provider's support sessions past the hard lifetime is closed as 'expired'
before the request proceeds.
"""

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api import _helpers
from app.core.auth import get_current_user
from app.core.permissions import require_provider
from app.core.rate_limit import SlidingWindowLimiter, client_ip
from app.core.scope import ProviderScope
from app.services.provider_service import AuthCodeError, ProviderService

service = ProviderService()

# Step-up code guesses are authenticated and replay-guarded already; this
# per-account budget just caps a stolen-session brute force on the code space.
stepup_account_limiter = SlidingWindowLimiter(max_attempts=10, window_seconds=60)
_STEP_UP_LIMIT_MESSAGE = "Too many code attempts. Try again shortly."


def map_error(error: Exception) -> HTTPException:
    if isinstance(error, AuthCodeError):
        # The one refusal family the client must branch on by machine code.
        return HTTPException(
            status_code=error.status_code,
            detail={"code": error.code, "message": str(error)},
        )
    return _helpers.map_error(error)


def safe_call(action):
    try:
        return action()
    except Exception as error:
        raise map_error(error) from error


def _expire_stale_sessions(scope: ProviderScope = Depends(require_provider)) -> None:
    """Router-level lazy janitor (U10): runs after the provider guard on
    every console request, so a four-hour-old step-in is settled as
    'expired' the next time its provider shows up."""
    service.dao.expire_stale_support_sessions(scope.user_id)


router = APIRouter(
    prefix="/api/provider",
    tags=["provider"],
    dependencies=[Depends(_expire_stale_sessions)],
)


def _check_code_budget(user: dict, code: str | None) -> None:
    if str(code or "").strip():
        stepup_account_limiter.check(str(user["id"]), _STEP_UP_LIMIT_MESSAGE)


class _CamelModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class CreateSchoolPayload(_CamelModel):
    name: str
    lat: float | None = None
    lng: float | None = None
    morning_bell: str | None = Field(default=None, alias="morningBell")
    afternoon_bell: str | None = Field(default=None, alias="afternoonBell")
    director_email: str = Field(alias="directorEmail")
    director_name: str | None = Field(default=None, alias="directorName")


class StepInPayload(_CamelModel):
    school_id: str = Field(alias="schoolId")
    reason: str | None = None
    code: str | None = None


class CreateAccountPayload(_CamelModel):
    email: str
    full_name: str | None = Field(default=None, alias="fullName")
    code: str | None = None


class StepUpPayload(_CamelModel):
    """Body for step-up-gated actions with no other fields (remove, reset)."""

    code: str | None = None


class ConfirmTotpPayload(_CamelModel):
    code: str


# --- schools ------------------------------------------------------------------


@router.get("/schools")
def list_schools(scope: ProviderScope = Depends(require_provider)):
    # Counts and setup state only — never a roster (R24).
    return safe_call(lambda: service.list_schools())


@router.post("/schools")
def create_school(
    payload: CreateSchoolPayload,
    scope: ProviderScope = Depends(require_provider),
    user: dict = Depends(get_current_user),
):
    return safe_call(
        lambda: service.create_school(
            user,
            name=payload.name,
            lat=payload.lat,
            lng=payload.lng,
            morning_bell=payload.morning_bell,
            afternoon_bell=payload.afternoon_bell,
            director_email=payload.director_email,
            director_name=payload.director_name,
        )
    )


# --- step-in / step-out -------------------------------------------------------


@router.post("/step-in")
def step_in(
    payload: StepInPayload,
    request: Request,
    scope: ProviderScope = Depends(require_provider),
    user: dict = Depends(get_current_user),
    user_agent: str | None = Header(default=None),
):
    _check_code_budget(user, payload.code)
    return safe_call(
        lambda: service.step_in(
            user,
            school_id=payload.school_id,
            reason=payload.reason,
            code=payload.code,
            ip=client_ip(request),
            user_agent=user_agent,
        )
    )


@router.post("/step-out")
def step_out(
    scope: ProviderScope = Depends(require_provider),
    user: dict = Depends(get_current_user),
):
    return safe_call(lambda: service.step_out(user))


# --- audit reader -------------------------------------------------------------


@router.get("/audit")
def list_audit(
    school_id: str | None = None,
    support_session_id: str | None = None,
    scope: ProviderScope = Depends(require_provider),
):
    # Real identities, unmasked — the provider-side reader (R25).
    return safe_call(lambda: service.list_audit(school_id, support_session_id))


# --- provider accounts --------------------------------------------------------


@router.get("/accounts")
def list_accounts(scope: ProviderScope = Depends(require_provider)):
    return safe_call(lambda: service.list_accounts())


@router.post("/accounts")
def create_account(
    payload: CreateAccountPayload,
    scope: ProviderScope = Depends(require_provider),
    user: dict = Depends(get_current_user),
):
    _check_code_budget(user, payload.code)
    return safe_call(
        lambda: service.create_account(
            user, email=payload.email, full_name=payload.full_name,
            code=payload.code,
        )
    )


@router.delete("/accounts/{user_id}", status_code=204)
def remove_account(
    user_id: str,
    payload: StepUpPayload | None = None,
    scope: ProviderScope = Depends(require_provider),
    user: dict = Depends(get_current_user),
):
    code = payload.code if payload else None
    _check_code_budget(user, code)
    safe_call(lambda: service.remove_account(user, user_id, code=code))
    return None


@router.post("/accounts/{user_id}/reset-totp")
def reset_totp(
    user_id: str,
    payload: StepUpPayload | None = None,
    scope: ProviderScope = Depends(require_provider),
    user: dict = Depends(get_current_user),
):
    code = payload.code if payload else None
    _check_code_budget(user, code)
    return safe_call(lambda: service.reset_totp(user, user_id, code=code))


# --- the calling provider's own second factor ---------------------------------


@router.post("/totp/enrol")
def enrol_totp(
    scope: ProviderScope = Depends(require_provider),
    user: dict = Depends(get_current_user),
):
    # The secret and otpauth URI appear in this response ONCE — never logged.
    return safe_call(lambda: service.enrol(user))


@router.post("/totp/confirm")
def confirm_totp(
    payload: ConfirmTotpPayload,
    scope: ProviderScope = Depends(require_provider),
    user: dict = Depends(get_current_user),
):
    stepup_account_limiter.check(str(user["id"]), _STEP_UP_LIMIT_MESSAGE)
    return safe_call(lambda: service.confirm_enrolment(user, payload.code))
