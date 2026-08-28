import pytest
from fastapi.testclient import TestClient

from app.api import auth as auth_api
from app.core.errors import UnauthorizedError
from app.main import create_app


@pytest.fixture(autouse=True)
def reset_limiters():
    limiters = [
        auth_api.login_ip_limiter,
        auth_api.login_account_limiter,
        auth_api.pin_ip_limiter,
        auth_api.signup_ip_limiter,
        auth_api.forgot_ip_limiter,
        auth_api.forgot_account_limiter,
        auth_api.reset_ip_limiter,
        auth_api.change_password_account_limiter,
    ]
    for limiter in limiters:
        limiter.reset()
    yield
    for limiter in limiters:
        limiter.reset()


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_pin_login_is_rate_limited_per_ip(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        auth_api.service,
        "pin_login",
        lambda pin: (_ for _ in ()).throw(UnauthorizedError("Invalid PIN")),
    )

    for _ in range(auth_api.pin_ip_limiter.max_attempts):
        response = client.post("/api/auth/pin-login", json={"pin": "0000"})
        assert response.status_code == 401

    response = client.post("/api/auth/pin-login", json={"pin": "0000"})
    assert response.status_code == 429


def test_successful_pin_login_clears_ip_budget(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(auth_api.service, "pin_login", lambda pin: {"token": "t"})

    for _ in range(auth_api.pin_ip_limiter.max_attempts + 5):
        response = client.post("/api/auth/pin-login", json={"pin": "1234"})
        assert response.status_code == 200


def test_login_is_rate_limited_per_account(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # U10: the login flow asks the provider branch first; a DB-less unit test
    # stubs it to "not a provider" so the standard stubbed path decides.
    monkeypatch.setattr(
        auth_api.provider_service,
        "begin_provider_login",
        lambda email, password: None,
    )
    monkeypatch.setattr(
        auth_api.service,
        "login",
        lambda email, password: (_ for _ in ()).throw(
            UnauthorizedError("Invalid email or password")
        ),
    )

    payload = {"email": "victim@test.com", "password": "wrong"}
    for _ in range(auth_api.login_account_limiter.max_attempts):
        response = client.post("/api/auth/login", json=payload)
        assert response.status_code == 401

    response = client.post("/api/auth/login", json=payload)
    assert response.status_code == 429
    assert "Too many login attempts" in response.json()["detail"]

    # The account budget is keyed case-insensitively.
    response = client.post(
        "/api/auth/login", json={"email": "VICTIM@test.com", "password": "wrong"}
    )
    assert response.status_code == 429

    # A different account from the same IP still has its own budget.
    response = client.post(
        "/api/auth/login", json={"email": "other@test.com", "password": "wrong"}
    )
    assert response.status_code == 401


def test_successful_login_clears_account_budget(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        auth_api.provider_service,
        "begin_provider_login",
        lambda email, password: None,
    )
    monkeypatch.setattr(auth_api.service, "login", lambda email, password: {"token": "t"})

    payload = {"email": "parent@test.com", "password": "right"}
    for _ in range(auth_api.login_account_limiter.max_attempts + 5):
        response = client.post("/api/auth/login", json=payload)
        assert response.status_code == 200


def test_forgot_password_is_rate_limited_per_account(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(auth_api.service, "forgot_password", lambda email: None)

    payload = {"email": "victim@test.com"}
    for _ in range(auth_api.forgot_account_limiter.max_attempts):
        response = client.post("/api/auth/forgot-password", json=payload)
        assert response.status_code == 200

    response = client.post("/api/auth/forgot-password", json=payload)
    assert response.status_code == 429


def test_reset_password_is_rate_limited_per_ip(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(auth_api.service, "reset_password", lambda token, password: None)

    payload = {"token": "token", "password": "NewPassword1"}
    for _ in range(auth_api.reset_ip_limiter.max_attempts):
        response = client.post("/api/auth/reset-password", json=payload)
        assert response.status_code == 200

    response = client.post("/api/auth/reset-password", json=payload)
    assert response.status_code == 429


def _fake_session_user(user_id: str) -> dict:
    """What core.auth.get_current_user expects resolve_session to return."""
    return {
        "id": user_id, "email": f"{user_id}@test.com", "full_name": "CP User",
        "phone": None, "role": "parent", "session_id": "11111111-1111-1111-1111-111111111111",
        "must_change_password": False, "memberships": [], "provider": None,
        "support_session": None, "parent_school_ids": [], "last_school_id": None,
        "totp_verified_at": None,
    }


def test_change_password_is_rate_limited_per_account(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """U8: the budget keys on the ACCOUNT (the surface is authenticated), so a
    stolen session cannot brute-force the current password — and a second
    account keeps its own budget."""
    import app.core.auth as core_auth

    current = {"id": "cp-user-1"}
    monkeypatch.setattr(
        core_auth._service, "resolve_session",
        lambda token: _fake_session_user(current["id"]),
    )
    monkeypatch.setattr(
        auth_api.service, "change_password", lambda user, cur, new: None
    )

    headers = {"Authorization": "Bearer test-token"}
    payload = {"currentPassword": "old", "newPassword": "NewPass123"}
    for _ in range(auth_api.change_password_account_limiter.max_attempts):
        response = client.post("/api/auth/change-password", json=payload, headers=headers)
        assert response.status_code == 200

    response = client.post("/api/auth/change-password", json=payload, headers=headers)
    assert response.status_code == 429
    assert "Too many password change attempts" in response.json()["detail"]

    # A different account from the same IP still has its own budget.
    current["id"] = "cp-user-2"
    response = client.post("/api/auth/change-password", json=payload, headers=headers)
    assert response.status_code == 200
