"""Bearer-session auth dependencies for the live-model API surface."""
from collections.abc import Callable

from fastapi import Depends, Header, HTTPException, Request

from app.core.scope import is_password_change_exempt, is_totp_enrolment_exempt
from app.services.auth_service import AuthService

_service = AuthService()


def _unauthorized() -> HTTPException:
    return HTTPException(status_code=401, detail="Authentication required")


def get_current_user(
    request: Request, authorization: str | None = Header(default=None)
) -> dict:
    if not authorization:
        raise _unauthorized()
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise _unauthorized()
    user = _service.resolve_session(token.strip())
    if not user:
        raise _unauthorized()
    if user.get("must_change_password") and not is_password_change_exempt(
        request.url.path
    ):
        # A temporary password opens nothing but the change screen (R30).
        raise HTTPException(
            status_code=409,
            detail={
                "code": "password-change-required",
                "message": "You must change your temporary password first",
            },
        )
    provider_state = user.get("provider")
    if (
        provider_state is not None
        and not provider_state.get("totp_enrolled")
        and not is_totp_enrolment_exempt(request.url.path)
    ):
        # An unenrolled provider's session opens nothing but enrolment (U10):
        # every provider and school route answers 409 until confirm succeeds.
        raise HTTPException(
            status_code=409,
            detail={
                "code": "totp-enrolment-required",
                "message": "You must enrol your second factor first",
            },
        )
    return {
        "id": str(user["id"]),
        "email": user["email"],
        "full_name": user.get("full_name"),
        "phone": user.get("phone"),
        "role": user.get("role"),
        # Tenancy enrichment (U5): consumed by app.core.permissions.
        "session_id": str(user["session_id"]),
        "must_change_password": bool(user.get("must_change_password")),
        "memberships": user.get("memberships") or [],
        "provider": user.get("provider"),
        "support_session": user.get("support_session"),
        "parent_school_ids": user.get("parent_school_ids") or [],
        "last_school_id": (
            str(user["last_school_id"]) if user.get("last_school_id") else None
        ),
        "totp_verified_at": user.get("totp_verified_at"),
    }


def require_role(*roles: str) -> Callable[[dict], dict]:
    allowed = set(roles)

    def dependency(user: dict = Depends(get_current_user)) -> dict:
        # An authenticated user with no role row is forbidden everywhere.
        if user.get("role") not in allowed:
            raise HTTPException(status_code=403, detail="You do not have access to this resource")
        return user

    return dependency
