"""Bearer-session auth dependencies for the live-model API surface."""
from collections.abc import Callable

from fastapi import Depends, Header, HTTPException

from app.services.auth_service import AuthService

_service = AuthService()


def _unauthorized() -> HTTPException:
    return HTTPException(status_code=401, detail="Authentication required")


def get_current_user(authorization: str | None = Header(default=None)) -> dict:
    if not authorization:
        raise _unauthorized()
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise _unauthorized()
    user = _service.resolve_session(token.strip())
    if not user:
        raise _unauthorized()
    return {
        "id": str(user["id"]),
        "email": user["email"],
        "full_name": user.get("full_name"),
        "phone": user.get("phone"),
        "role": user.get("role"),
    }


def require_role(*roles: str) -> Callable[[dict], dict]:
    allowed = set(roles)

    def dependency(user: dict = Depends(get_current_user)) -> dict:
        # An authenticated user with no role row is forbidden everywhere.
        if user.get("role") not in allowed:
            raise HTTPException(status_code=403, detail="You do not have access to this resource")
        return user

    return dependency
