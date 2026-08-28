from collections.abc import Callable
from typing import TypeVar

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from psycopg import Error as PsycopgError

from app.core.auth import get_current_user
from app.core.config import get_settings
from app.core.errors import NotFoundError, SafeRideError, to_http_exception
from app.core.rate_limit import SlidingWindowLimiter, client_ip
from app.dao.membership_dao import MembershipDao
from app.schemas.auth import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    MembershipOut,
    MeResponse,
    OfferOut,
    PinLoginRequest,
    ProviderStateOut,
    ResetPasswordRequest,
    SignupRequest,
)
from app.services.auth_service import AuthService

router = APIRouter(prefix="/api/auth", tags=["auth"])
service = AuthService()
memberships_dao = MembershipDao()
T = TypeVar("T")

# Last reset link, exposed only in local dev for the email-less flow.
_last_reset_link: dict[str, str | None] = {"link": None}

# Brute-force protection. Per-account budgets are the tight ones; per-IP
# budgets are coarse safety nets sized for shared NATs (school office, test
# runners) while still blocking credential-stuffing request rates. The two
# coarse IP nets scale by AUTH_IP_RATE_MULTIPLIER (default 1 = production
# posture) so the local stack can absorb a full certification run's account
# churn; per-account and PIN budgets are security-tight and never scale.
_IP_BUDGET_MULTIPLIER = max(1, get_settings().auth_ip_rate_multiplier)
login_ip_limiter = SlidingWindowLimiter(
    max_attempts=100 * _IP_BUDGET_MULTIPLIER, window_seconds=300
)
login_account_limiter = SlidingWindowLimiter(max_attempts=10, window_seconds=300)
# The 4-digit PIN space is tiny, so PIN logins get the strictest IP budget.
pin_ip_limiter = SlidingWindowLimiter(max_attempts=10, window_seconds=60)
signup_ip_limiter = SlidingWindowLimiter(
    max_attempts=50 * _IP_BUDGET_MULTIPLIER, window_seconds=3600
)
forgot_ip_limiter = SlidingWindowLimiter(max_attempts=5, window_seconds=60)
forgot_account_limiter = SlidingWindowLimiter(max_attempts=3, window_seconds=900)
reset_ip_limiter = SlidingWindowLimiter(max_attempts=10, window_seconds=60)
# Change-password is authenticated, so the budget keys on the account itself
# (U8): 5 attempts / 15 min stops an attacker holding a stolen session from
# brute-forcing the current password, while never troubling a real user.
change_password_account_limiter = SlidingWindowLimiter(max_attempts=5, window_seconds=900)

_LOGIN_LIMIT_MESSAGE = "Too many login attempts. Try again shortly."
_RESET_LIMIT_MESSAGE = "Too many password reset attempts. Try again shortly."


def map_error(error: Exception) -> HTTPException:
    if isinstance(error, SafeRideError):
        return to_http_exception(error)
    if isinstance(error, PsycopgError) and error.sqlstate == "23505":
        return HTTPException(status_code=409, detail="Record already exists")
    return HTTPException(status_code=500, detail="Unexpected backend error")


def safe_call(action: Callable[[], T]) -> T:
    try:
        return action()
    except Exception as error:
        raise map_error(error) from error


@router.post("/signup")
def signup(request: SignupRequest, http_request: Request):
    signup_ip_limiter.check(client_ip(http_request), "Too many signups. Try again later.")
    return safe_call(
        lambda: service.signup(request.email, request.password, request.full_name, request.role)
    )


@router.post("/login")
def login(request: LoginRequest, http_request: Request):
    ip = client_ip(http_request)
    # The account budget is scoped per caller IP so a remote attacker cannot
    # lock a victim's account by spamming failures from elsewhere; the
    # blanket per-IP budget still caps one source probing many accounts.
    account_key = f"{ip}|{request.email.strip().lower()}"
    login_ip_limiter.check(ip, _LOGIN_LIMIT_MESSAGE)
    login_account_limiter.check(account_key, _LOGIN_LIMIT_MESSAGE)
    result = safe_call(lambda: service.login(request.email, request.password))
    # A successful login clears the account budget so legitimate users who
    # mistype a few times are not locked out after signing in.
    login_account_limiter.clear(account_key)
    return result


@router.post("/pin-login")
def pin_login(request: PinLoginRequest, http_request: Request):
    ip = client_ip(http_request)
    pin_ip_limiter.check(ip, "Too many PIN attempts. Try again shortly.")
    result = safe_call(lambda: service.pin_login(request.pin))
    # A correct PIN clears the IP's attempt budget so legitimate repeated
    # logins (shared driver kiosk / office NAT) don't lock themselves out.
    pin_ip_limiter.clear(ip)
    return result


@router.post("/logout")
def logout(authorization: str | None = Header(default=None)):
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    service.logout(token)
    return {"ok": True}


@router.get("/me", response_model=MeResponse, response_model_by_alias=True)
def me(user: dict = Depends(get_current_user)):
    memberships = user.get("memberships") or []
    provider = user.get("provider")
    return MeResponse(
        id=user["id"],
        email=user["email"],
        fullName=user.get("full_name"),
        role=user.get("role"),
        memberships=[
            MembershipOut(
                schoolId=m["school_id"],
                schoolName=m.get("school_name"),
                schoolCode=m.get("school_code"),
                role=m["role"],
            )
            for m in memberships
            if m["state"] == "active"
        ],
        pendingOffers=[
            OfferOut(
                id=m.get("id"),
                schoolId=m["school_id"],
                schoolName=m.get("school_name"),
                schoolCode=m.get("school_code"),
                role=m["role"],
                offeredBy=m.get("offered_by_name"),
                offeredAt=m.get("created_at"),
            )
            for m in memberships
            if m["state"] == "offered"
        ],
        activeSchoolId=user.get("last_school_id"),
        provider=(
            ProviderStateOut(totpEnrolled=bool(provider.get("totp_enrolled")))
            if provider
            else None
        ),
        mustChangePassword=user.get("must_change_password", False),
    )


@router.post("/change-password")
def change_password(request: ChangePasswordRequest, user: dict = Depends(get_current_user)):
    """Any authenticated role; on the must-change allowlist (R30), and even
    then the CURRENT password is required. Keeps the calling session, revokes
    every other one."""
    change_password_account_limiter.check(
        str(user["id"]), "Too many password change attempts. Try again shortly."
    )
    safe_call(
        lambda: service.change_password(
            user, request.current_password, request.new_password
        )
    )
    return {"ok": True}


# Role offers (U8/AE15): auth-surface — the offer belongs to the CALLING
# account, no school header involved; anyone else's offer does not exist.

@router.post("/offers/{offer_id}/accept")
def accept_offer(offer_id: str, user: dict = Depends(get_current_user)):
    safe_call(lambda: memberships_dao.accept_offer(offer_id, actor=user))
    return {"ok": True}


@router.post("/offers/{offer_id}/decline")
def decline_offer(offer_id: str, user: dict = Depends(get_current_user)):
    safe_call(lambda: memberships_dao.decline_offer(offer_id, actor=user))
    return {"ok": True}


@router.post("/forgot-password")
def forgot_password(request: ForgotPasswordRequest, http_request: Request):
    account_key = request.email.strip().lower()
    forgot_ip_limiter.check(client_ip(http_request), _RESET_LIMIT_MESSAGE)
    forgot_account_limiter.check(account_key, _RESET_LIMIT_MESSAGE)
    link = safe_call(lambda: service.forgot_password(request.email))
    if get_settings().is_local:
        _last_reset_link["link"] = link
    return {"ok": True}


@router.post("/reset-password")
def reset_password(request: ResetPasswordRequest, http_request: Request):
    reset_ip_limiter.check(client_ip(http_request), _RESET_LIMIT_MESSAGE)
    safe_call(lambda: service.reset_password(request.token, request.password))
    return {"ok": True}


@router.get("/dev/last-reset-link")
def dev_last_reset_link():
    if not get_settings().is_local:
        raise to_http_exception(NotFoundError("Not found"))
    return {"link": _last_reset_link["link"]}
