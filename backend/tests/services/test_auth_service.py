import pytest

from app.core.config import get_settings
from app.core.errors import BadRequestError, UnauthorizedError
from app.core.security import hash_password, hash_pin_hmac, hash_session_token
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


def test_signup_creates_parent_session(service):
    result = service.signup("p@test.com", "secret1", "Parent", "parent")
    assert result["user"]["role"] == "parent"


def test_parent_signup_auto_links_matching_students(service, accounts):
    result = service.signup("  p@test.com  ", "secret1", "Parent", "parent")
    # Linked with the new account's id and the cleaned email (R11).
    assert accounts.linked == [(result["user"]["id"], "p@test.com")]


def test_driver_signup_does_not_link_students(service, accounts):
    service.signup("d@test.com", "secret1", "Driver", "driver")
    assert accounts.linked == []


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
