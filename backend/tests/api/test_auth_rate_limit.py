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
