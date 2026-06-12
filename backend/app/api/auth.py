import time
from collections import defaultdict
from collections.abc import Callable
from threading import Lock
from typing import TypeVar

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from psycopg import Error as PsycopgError

from app.core.auth import get_current_user
from app.core.config import get_settings
from app.core.errors import NotFoundError, SafeRideError, TooManyRequestsError, to_http_exception
from app.schemas.auth import (
    ForgotPasswordRequest,
    LoginRequest,
    PinLoginRequest,
    ResetPasswordRequest,
    SignupRequest,
)
from app.services.auth_service import AuthService

router = APIRouter(prefix="/api/auth", tags=["auth"])
service = AuthService()
T = TypeVar("T")

# Last reset link, exposed only in local dev for the email-less flow.
_last_reset_link: dict[str, str | None] = {"link": None}

# Naive in-process rate limiter for PIN login brute-force protection.
_pin_attempts: dict[str, list[float]] = defaultdict(list)
_pin_lock = Lock()
_PIN_WINDOW_SECONDS = 60
_PIN_MAX_ATTEMPTS = 10


def _client_ip(request: Request) -> str:
    if request.client:
        return request.client.host
    return "unknown"


def _check_pin_rate_limit(ip: str) -> None:
    now = time.monotonic()
    with _pin_lock:
        recent = [t for t in _pin_attempts[ip] if now - t < _PIN_WINDOW_SECONDS]
        if len(recent) >= _PIN_MAX_ATTEMPTS:
            _pin_attempts[ip] = recent
            raise TooManyRequestsError("Too many PIN attempts. Try again shortly.")
        recent.append(now)
        _pin_attempts[ip] = recent


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
def signup(request: SignupRequest):
    return safe_call(
        lambda: service.signup(request.email, request.password, request.full_name, request.role)
    )


@router.post("/login")
def login(request: LoginRequest):
    return safe_call(lambda: service.login(request.email, request.password))


@router.post("/pin-login")
def pin_login(request: PinLoginRequest, http_request: Request):
    ip = _client_ip(http_request)
    _check_pin_rate_limit(ip)
    result = safe_call(lambda: service.pin_login(request.pin))
    # A correct PIN clears the IP's attempt budget so legitimate repeated
    # logins (shared driver kiosk / office NAT) don't lock themselves out.
    with _pin_lock:
        _pin_attempts.pop(ip, None)
    return result


@router.post("/logout")
def logout(authorization: str | None = Header(default=None)):
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    service.logout(token)
    return {"ok": True}


@router.get("/me")
def me(user: dict = Depends(get_current_user)):
    return {
        "id": user["id"],
        "email": user["email"],
        "fullName": user.get("full_name"),
        "role": user.get("role"),
    }


@router.post("/forgot-password")
def forgot_password(request: ForgotPasswordRequest):
    link = safe_call(lambda: service.forgot_password(request.email))
    if get_settings().is_local:
        _last_reset_link["link"] = link
    return {"ok": True}


@router.post("/reset-password")
def reset_password(request: ResetPasswordRequest):
    safe_call(lambda: service.reset_password(request.token, request.password))
    return {"ok": True}


@router.get("/dev/last-reset-link")
def dev_last_reset_link():
    if not get_settings().is_local:
        raise to_http_exception(NotFoundError("Not found"))
    return {"link": _last_reset_link["link"]}
