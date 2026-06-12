from app.core.config import get_settings
from app.core.errors import BadRequestError, UnauthorizedError
from app.core.security import (
    create_session_token,
    hash_password,
    hash_session_token,
    verify_password,
    verify_pin_hmac,
)
from app.dao.auth_dao import AuthDao

SIGNUP_ROLES = {"driver", "parent"}


class AuthService:
    def __init__(self, dao: AuthDao | None = None) -> None:
        self.dao = dao or AuthDao()
        self.pepper = get_settings().pin_pepper

    # --- session helpers ---------------------------------------------------

    def _issue_session(self, user: dict) -> dict:
        token = create_session_token()
        self.dao.create_session(user["id"], hash_session_token(token))
        return {
            "token": token,
            "user": {
                "id": str(user["id"]),
                "email": user["email"],
                "fullName": user.get("full_name"),
                "role": user.get("role"),
            },
        }

    def resolve_session(self, token: str) -> dict | None:
        if not token:
            return None
        return self.dao.get_session_user(hash_session_token(token))

    # --- flows -------------------------------------------------------------

    def signup(self, email: str, password: str, full_name: str, role: str) -> dict:
        if role not in SIGNUP_ROLES:
            raise BadRequestError("Role must be driver or parent")
        if self.dao.get_user_by_email(email):
            raise BadRequestError("An account with this email already exists")
        user = self.dao.create_user(email, hash_password(password), full_name, role)
        user["role"] = role
        return self._issue_session(user)

    def login(self, email: str, password: str) -> dict:
        user = self.dao.get_user_by_email(email)
        if not user or not verify_password(password, user["password_hash"]):
            raise UnauthorizedError("Invalid email or password")
        return self._issue_session(user)

    def pin_login(self, pin: str) -> dict:
        matches = [
            u
            for u in self.dao.list_driver_pin_users()
            if verify_pin_hmac(pin, u["pin_hash"], self.pepper)
        ]
        if len(matches) != 1:
            raise UnauthorizedError("Invalid PIN")
        user = self.dao.get_user_by_id(matches[0]["id"])
        return self._issue_session(user)

    def logout(self, token: str) -> None:
        if token:
            self.dao.revoke_session(hash_session_token(token))

    def forgot_password(self, email: str) -> str | None:
        """Always succeeds (no user enumeration). Returns the dev reset link."""
        user = self.dao.get_user_by_email(email)
        if not user:
            return None
        token = create_session_token()
        self.dao.create_reset_token(user["id"], hash_session_token(token))
        base = get_settings().app_base_url.rstrip("/")
        return f"{base}/reset-password?token={token}"

    def reset_password(self, token: str, password: str) -> None:
        record = self.dao.consume_reset_token(hash_session_token(token))
        if not record:
            raise BadRequestError("Reset link is invalid or has expired")
        self.dao.update_password(record["user_id"], hash_password(password))
        self.dao.revoke_all_sessions(record["user_id"])
