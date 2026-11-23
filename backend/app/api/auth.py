import logging
import math
from collections.abc import Callable
from typing import TypeVar

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from psycopg import Error as PsycopgError

from app.core.auth import get_current_user, get_optional_session_user
from app.core.config import get_settings
from app.core.errors import NotFoundError, SafeRideError, to_http_exception
from app.core.rate_limit import SlidingWindowLimiter, client_ip
from app.core.request_context import UNEXPECTED_ERROR_DETAIL, log_unexpected_error
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
    SupportSessionOut,
    TotpRequest,
    VerifyEmailRequest,
)
from app.services.auth_service import (
    AuthService,
    PinLoginError,
    PinSessionError,
    SchoolPinLockedError,
    VerificationError,
)
from app.services.provider_service import AuthCodeError, ProviderService

_scope_logger = logging.getLogger("saferide.scope")

router = APIRouter(prefix="/api/auth", tags=["auth"])
service = AuthService()
provider_service = ProviderService()
memberships_dao = MembershipDao()
T = TypeVar("T")

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
# The provider code step (U10): its own per-IP net on top of the pre-auth
# token's five-attempt budget — the token caps one stolen password, the IP
# budget caps token-farming request rates. Never scaled.
totp_ip_limiter = SlidingWindowLimiter(max_attempts=10, window_seconds=60)
# The 4-digit PIN space is tiny, so PIN logins get the strictest IP budget.
# Hardening U10 adds the database-backed lock per (school, source) behind it
# (auth_service.pin_login): this in-process net resets on a cold start.
pin_ip_limiter = SlidingWindowLimiter(max_attempts=10, window_seconds=60)
# The driver code lookup (hardening U10): public, and throttled at the API
# Gateway (D6). Forty bits cannot be walked at any of these rates; the
# in-process net just keeps one source from hammering it. Scales with the
# other coarse IP nets for the local certification run.
school_lookup_ip_limiter = SlidingWindowLimiter(
    max_attempts=30 * _IP_BUDGET_MULTIPLIER, window_seconds=60
)
signup_ip_limiter = SlidingWindowLimiter(
    max_attempts=50 * _IP_BUDGET_MULTIPLIER, window_seconds=3600
)
forgot_ip_limiter = SlidingWindowLimiter(max_attempts=5, window_seconds=60)
# Keyed on "ip|email" (plan U7), like the login account budget: a remote
# caller cannot use up a victim's budget from elsewhere. The per-recipient
# mail cap (three token mails an hour, counted from the token rows) is the
# account-wide bound behind it.
forgot_account_limiter = SlidingWindowLimiter(max_attempts=3, window_seconds=900)
reset_ip_limiter = SlidingWindowLimiter(max_attempts=10, window_seconds=60)
# verify-email (plan U8) is session-bound, so the tight budget keys on the
# signed-in account and never scales; the per-IP net mirrors reset-password's
# ten a minute and scales with the other coarse IP nets, because one local
# certification run verifies dozens of fresh accounts from one address.
verify_ip_limiter = SlidingWindowLimiter(
    max_attempts=10 * _IP_BUDGET_MULTIPLIER, window_seconds=60
)
verify_account_limiter = SlidingWindowLimiter(max_attempts=10, window_seconds=900)
# Resend (DL9): one new link a minute per account ("Try again in a minute"),
# on top of the per-recipient mail cap.
resend_account_limiter = SlidingWindowLimiter(max_attempts=1, window_seconds=60)
# Change-password is authenticated, so the budget keys on the account itself
# (U8): 5 attempts / 15 min stops an attacker holding a stolen session from
# brute-forcing the current password, while never troubling a real user.
change_password_account_limiter = SlidingWindowLimiter(max_attempts=5, window_seconds=900)

_LOGIN_LIMIT_MESSAGE = "Too many login attempts. Try again shortly."
_RESET_LIMIT_MESSAGE = "Too many password reset attempts. Try again shortly."
_VERIFY_LIMIT_MESSAGE = "Too many verification attempts. Try again shortly."
_RESEND_LIMIT_MESSAGE = "Try again in a minute"


def map_error(error: Exception) -> HTTPException:
    if isinstance(error, SchoolPinLockedError):
        # Hardening U10 (D3, DL4): the screen counts down from Retry-After.
        wait = max(1, math.ceil(error.retry_after_s))
        return HTTPException(
            status_code=error.status_code,
            detail={"code": error.code, "message": str(error), "retry_after_s": wait},
            headers={"Retry-After": str(wait)},
        )
    if isinstance(error, (AuthCodeError, VerificationError, PinSessionError, PinLoginError)):
        # Refusals the client must branch on: the detail carries a machine
        # code — second-factor (U10: `preauth-voided`,
        # `totp-step-up-required`), verify-email (hardening U8, DL10:
        # `sign-in-required`, `wrong-account`, `link-invalid`), the
        # self-service change from a PIN session (hardening U9: `pin-session`)
        # and the school-bound PIN sign-in (hardening U10: `school-required`,
        # `invalid-pin`).
        return HTTPException(
            status_code=error.status_code,
            detail={"code": error.code, "message": str(error)},
        )
    if isinstance(error, SafeRideError):
        return to_http_exception(error)
    if isinstance(error, PsycopgError) and error.sqlstate == "23505":
        return HTTPException(status_code=409, detail="Record already exists")
    if isinstance(error, PsycopgError) and error.sqlstate == "42501":
        # Same contract as api/_helpers.map_error: a row-security denial is a
        # scope bug — log loudly, answer with not-found.
        diag = getattr(error, "diag", None)
        _scope_logger.error(
            "row-security denial sqlstate=42501 table=%s message=%s",
            getattr(diag, "table_name", None),
            getattr(diag, "message_primary", None),
        )
        return HTTPException(status_code=404, detail="Not found")
    # Same contract as api/_helpers.map_error (hardening U11): the one
    # redacted record, then the 500 the body handler stamps with the id.
    log_unexpected_error(error)
    return HTTPException(status_code=500, detail=UNEXPECTED_ERROR_DETAIL)


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
    result = safe_call(lambda: _login_flow(request.email, request.password))
    # A successful login clears the account budget so legitimate users who
    # mistype a few times are not locked out after signing in.
    login_account_limiter.clear(account_key)
    return result


def _login_flow(email: str, password: str):
    """An active provider identity NEVER gets a session from the password
    alone (U10/AE19): it gets a five-minute pre-auth token and the flag for
    the second step. Everyone else follows the standard login."""
    preauth = provider_service.begin_provider_login(email, password)
    if preauth is not None:
        return preauth
    return service.login(email, password)


@router.post("/totp")
def totp(request: TotpRequest, http_request: Request):
    """The provider login's second step: pre-auth token (+ code once
    enrolled) → bearer session with ``totp_verified_at`` stamped."""
    totp_ip_limiter.check(
        client_ip(http_request), "Too many code attempts. Try again shortly."
    )
    return safe_call(
        lambda: provider_service.complete_totp(request.token, request.code)
    )


@router.post("/pin-login")
def pin_login(request: PinLoginRequest, http_request: Request):
    """{pin, schoolId} → a PIN session at that school (hardening U10).
    401 {code: school-required | invalid-pin}; 429 {code: school-pin-locked}
    with Retry-After once the source has failed too often at the school; a
    plain 429 from the per-IP net before that."""
    ip = client_ip(http_request)
    pin_ip_limiter.check(ip, "Too many PIN attempts. Try again shortly.")
    result = safe_call(lambda: service.pin_login(request.pin, request.school_id, source=ip))
    # A correct PIN clears the IP's attempt budget so legitimate repeated
    # logins (shared driver kiosk / office NAT) don't lock themselves out.
    pin_ip_limiter.clear(ip)
    return result


@router.get("/schools/{code}")
def school_by_driver_code(code: str, http_request: Request):
    """The driver code lookup (hardening U10): public, throttled. The typed
    code is normalised (case, hyphens and spaces, O/I/L look-alikes) and
    answers {schoolId, name} — nothing else — or 404. Phones store the
    answer, so a regenerated code leaves them signed in while the old link
    stops resolving."""
    school_lookup_ip_limiter.check(
        client_ip(http_request), "Too many tries. Wait a minute and try again."
    )
    return safe_call(lambda: service.school_for_driver_code(code))


@router.post("/logout")
def logout(authorization: str | None = Header(default=None)):
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    # A stepped-in provider's logout also ends the support session, with its
    # own cause on the record (U10/AE29). No-op for everyone else.
    provider_service.end_support_on_logout(token)
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
            ProviderStateOut(
                totpEnrolled=bool(provider.get("totp_enrolled")),
                # Step-up freshness (U10): the dialog shows the code input up
                # front when the session's last code is stale.
                totpVerifiedAt=(
                    user["totp_verified_at"].isoformat()
                    if user.get("totp_verified_at")
                    else None
                ),
            )
            if provider
            else None
        ),
        supportSession=(
            SupportSessionOut(
                id=support["id"],
                schoolId=support["school_id"],
                schoolName=support.get("school_name"),
                schoolCode=support.get("school_code"),
                reason=support.get("reason"),
                startedAt=support.get("started_at"),
            )
            if (support := user.get("support_session"))
            else None
        ),
        mustChangePassword=user.get("must_change_password", False),
        emailVerified=user.get("email_verified", True),
        signedInWithPin=user.get("pin_session", False),
    )


@router.post("/change-password")
def change_password(request: ChangePasswordRequest, user: dict = Depends(get_current_user)):
    """Any authenticated role; on the must-change allowlist (R30), and even
    then the CURRENT password is required. Keeps the calling session, revokes
    every other one. A session created by PIN answers 403 {code:
    "pin-session"} (hardening U9) unless it must replace a temporary
    password."""
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
    """The neutral answer for every address (plan U7): "If an account exists
    for this address, a reset link has been sent"."""
    ip = client_ip(http_request)
    forgot_ip_limiter.check(ip, _RESET_LIMIT_MESSAGE)
    forgot_account_limiter.check(f"{ip}|{request.email.strip().lower()}", _RESET_LIMIT_MESSAGE)
    return safe_call(lambda: service.forgot_password(request.email))


@router.post("/reset-password")
def reset_password(request: ResetPasswordRequest, http_request: Request):
    """Consumes a reset link. Answers a fresh session ({token, user}) so the
    reset page lands signed in — except for a provider account, which must
    still pass its second factor and is sent to sign-in."""
    reset_ip_limiter.check(client_ip(http_request), _RESET_LIMIT_MESSAGE)
    return safe_call(lambda: service.reset_password(request.token, request.password))


@router.post("/verify-email")
def verify_email(
    request: VerifyEmailRequest,
    http_request: Request,
    user: dict | None = Depends(get_optional_session_user),
):
    """Session-bound (plan U8): 400 {code: sign-in-required | wrong-account |
    link-invalid} unless the signed-in caller owns the live token. Never a
    401 — a signed-out reader gets the page's sign-in state, not the client's
    session-expired sign-out."""
    verify_ip_limiter.check(client_ip(http_request), _VERIFY_LIMIT_MESSAGE)
    if user is not None:
        verify_account_limiter.check(user["id"], _VERIFY_LIMIT_MESSAGE)
    return safe_call(lambda: service.verify_email(user, request.token))


@router.post("/resend-verification")
def resend_verification(user: dict = Depends(get_current_user)):
    """A new verification link for the signed-in, unverified caller; earlier
    links stop working. Same answer under the per-recipient cap or over it."""
    resend_account_limiter.check(user["id"], _RESEND_LIMIT_MESSAGE)
    return safe_call(lambda: service.resend_verification(user))


# Local dev only (404 elsewhere): the simulated transport's last link per
# purpose, for the email-less local flows and the e2e suite. The existing-
# account mail carries a reset link, so it lands under last-reset-link.

@router.get("/dev/last-reset-link")
def dev_last_reset_link():
    if not get_settings().is_local:
        raise to_http_exception(NotFoundError("Not found"))
    return {"link": service.mail.last_link("reset")}


@router.get("/dev/last-verification-link")
def dev_last_verification_link():
    if not get_settings().is_local:
        raise to_http_exception(NotFoundError("Not found"))
    return {"link": service.mail.last_link("verify")}
