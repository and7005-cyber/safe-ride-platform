import datetime as dt

import pytest

from app.core.config import get_settings
from app.core.errors import BadRequestError, UnauthorizedError
from app.core.security import (
    hash_password,
    hash_pin_hmac,
    hash_session_token,
    verify_password,
)
from app.services.auth_service import AuthService

PEPPER = get_settings().pin_pepper


class FakeAuthDao:
    def __init__(self):
        self.users = {
            "u-admin": {
                "id": "u-admin", "email": "admin@test.com",
                "password_hash": hash_password("test1234."),
                "full_name": "Admin", "phone": None, "pin_hash": None, "role": "admin",
            },
            "u-driver": {
                "id": "u-driver", "email": "drive@test.com",
                "password_hash": hash_password("Test1234"),
                "full_name": "Driver", "phone": None,
                "pin_hash": hash_pin_hmac("1234", PEPPER), "role": "driver",
            },
        }
        self.sessions: dict[str, str] = {}
        self.revoked_all_for: list[str] = []
        self.kept_sessions: list[tuple[str, str]] = []
        self.voided_tokens_for: list[str] = []

    def get_user_by_email(self, email):
        for u in self.users.values():
            if u["email"].lower() == email.lower():
                return dict(u)
        return None

    def get_user_by_id(self, user_id):
        return dict(self.users[user_id]) if user_id in self.users else None

    def list_driver_pin_users(self):
        return [
            {"id": u["id"], "email": u["email"], "full_name": u["full_name"], "pin_hash": u["pin_hash"]}
            for u in self.users.values()
            if u["role"] == "driver" and u["pin_hash"]
        ]

    def create_user(self, email, password_hash, full_name, role, phone=None):
        uid = f"u-{email}"
        self.users[uid] = {
            "id": uid, "email": email, "password_hash": password_hash,
            "full_name": full_name, "phone": phone, "pin_hash": None, "role": role,
        }
        return {"id": uid, "email": email, "full_name": full_name}

    def create_session(self, user_id, token_hash, ttl_hours=16):
        self.sessions[token_hash] = user_id

    def get_session_user(self, token_hash):
        uid = self.sessions.get(token_hash)
        return dict(self.users[uid]) if uid else None

    def revoke_session(self, token_hash):
        self.sessions.pop(token_hash, None)

    def revoke_all_sessions(self, user_id):
        self.revoked_all_for.append(user_id)
        self.sessions = {k: v for k, v in self.sessions.items() if v != user_id}

    def revoke_other_sessions(self, user_id, keep_session_id):
        self.kept_sessions.append((user_id, keep_session_id))

    def get_user_credentials(self, user_id):
        user = self.users.get(user_id)
        if not user:
            return None
        return {"id": user_id, "email": user["email"], "password_hash": user["password_hash"]}

    def set_user_password(self, user_id, password_hash):
        user = self.users[user_id]
        user["password_hash"] = password_hash
        user["password_changed_at"] = dt.datetime.now(dt.timezone.utc)
        user["must_change_password"] = False
        user["temporary_password_expires_at"] = None
        self.voided_tokens_for.append(user_id)


class FakeAccountsDao:
    """Records signup auto-link calls (the linking rules themselves are covered
    in tests/services/test_parent_links.py)."""

    def __init__(self):
        self.linked: list[tuple] = []

    def link_parent_to_matching_students(self, parent_id, email):
        self.linked.append((parent_id, email))
        return 1


@pytest.fixture
def accounts():
    return FakeAccountsDao()


@pytest.fixture
def service(accounts):
    return AuthService(dao=FakeAuthDao(), accounts=accounts)


def test_login_returns_role(service):
    result = service.login("admin@test.com", "test1234.")
    assert result["user"]["role"] == "admin"
    assert result["token"]


def test_login_wrong_password_rejected(service):
    with pytest.raises(UnauthorizedError):
        service.login("admin@test.com", "nope")


def test_signup_rejects_admin_role(service):
    with pytest.raises(BadRequestError):
        service.signup("new@test.com", "secret1", "New", "admin")


def test_signup_rejects_driver_role(service, accounts):
    # U8: driver self-signup is retired — schools create their drivers (R5).
    with pytest.raises(BadRequestError, match="Role must be parent"):
        service.signup("d@test.com", "secret1", "Driver", "driver")
    assert accounts.linked == []


def test_signup_creates_parent_session(service):
    result = service.signup("p@test.com", "secret1", "Parent", "parent")
    assert result["user"]["role"] == "parent"


def test_parent_signup_auto_links_matching_students(service, accounts):
    result = service.signup("  p@test.com  ", "secret1", "Parent", "parent")
    # Linked with the new account's id and the cleaned email (R11).
    assert accounts.linked == [(result["user"]["id"], "p@test.com")]


def test_pin_login_single_match(service):
    result = service.pin_login("1234")
    assert result["user"]["role"] == "driver"


def test_pin_login_unknown_pin_rejected(service):
    with pytest.raises(UnauthorizedError):
        service.pin_login("9999")


def test_resolve_session_round_trip(service):
    token = service.login("admin@test.com", "test1234.")["token"]
    resolved = service.resolve_session(token)
    assert resolved["role"] == "admin"


def test_logout_invalidates_session(service):
    token = service.login("admin@test.com", "test1234.")["token"]
    service.logout(token)
    assert service.resolve_session(token) is None


# Temporary passwords (U8: R6) --------------------------------------------------


def _make_temporary(dao: FakeAuthDao, user_id: str, *, expired: bool) -> None:
    delta = dt.timedelta(hours=-1 if expired else 1)
    dao.users[user_id]["must_change_password"] = True
    dao.users[user_id]["temporary_password_expires_at"] = (
        dt.datetime.now(dt.timezone.utc) + delta
    )


def test_login_rejects_an_expired_temporary_password(service):
    _make_temporary(service.dao, "u-admin", expired=True)
    with pytest.raises(UnauthorizedError, match="Invalid email or password"):
        service.login("admin@test.com", "test1234.")


def test_login_accepts_an_unexpired_temporary_password(service):
    _make_temporary(service.dao, "u-admin", expired=False)
    result = service.login("admin@test.com", "test1234.")
    assert result["user"]["mustChangePassword"] is True


# Change password (U8: R29) -----------------------------------------------------


def _session_user(user_id: str) -> dict:
    return {"id": user_id, "session_id": "sess-keep"}


def test_change_password_requires_the_correct_current_password(service):
    with pytest.raises(BadRequestError, match="Current password is incorrect"):
        service.change_password(_session_user("u-admin"), "wrong", "NewPass123")
    # The hash is untouched and no sessions were revoked.
    assert verify_password("test1234.", service.dao.users["u-admin"]["password_hash"])
    assert service.dao.kept_sessions == []


def test_change_password_rotates_hash_and_revokes_other_sessions(service):
    _make_temporary(service.dao, "u-admin", expired=False)
    service.change_password(_session_user("u-admin"), "test1234.", "NewPass123")
    user = service.dao.users["u-admin"]
    assert verify_password("NewPass123", user["password_hash"])
    assert not verify_password("test1234.", user["password_hash"])
    # The forced-change flag and expiry are cleared, the change is stamped,
    # outstanding tokens are voided, and only OTHER sessions are revoked.
    assert user["must_change_password"] is False
    assert user["temporary_password_expires_at"] is None
    assert user["password_changed_at"] is not None
    assert service.dao.voided_tokens_for == ["u-admin"]
    assert service.dao.kept_sessions == [("u-admin", "sess-keep")]
    assert service.dao.revoked_all_for == []


def test_reset_password_clears_the_temporary_flag_and_revokes_all(service):
    # The forgot-password reset is a user-set password too: same single path.
    _make_temporary(service.dao, "u-admin", expired=False)
    service.dao.consume_reset_token = lambda token_hash: {"user_id": "u-admin"}
    service.reset_password("raw-token", "NewPass456")
    user = service.dao.users["u-admin"]
    assert verify_password("NewPass456", user["password_hash"])
    assert user["must_change_password"] is False
    assert service.dao.revoked_all_for == ["u-admin"]
