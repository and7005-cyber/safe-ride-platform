import pytest
from fastapi.testclient import TestClient

from app.api import auth as auth_api
from app.core.errors import NotFoundError, UnauthorizedError
from app.main import create_app
from app.services.auth_service import (
    SCHOOL_PIN_LOCKED_MESSAGE,
    PinLoginError,
    SchoolPinLockedError,
)

SCHOOL_ID = "5cae0000-0000-0000-0000-000000000001"


@pytest.fixture(autouse=True)
def reset_limiters():
    limiters = [
        auth_api.login_ip_limiter,
        auth_api.login_account_limiter,
        auth_api.pin_ip_limiter,
        auth_api.school_lookup_ip_limiter,
        auth_api.signup_ip_limiter,
        auth_api.forgot_ip_limiter,
        auth_api.forgot_account_limiter,
        auth_api.reset_ip_limiter,
        auth_api.change_password_account_limiter,
        auth_api.verify_ip_limiter,
        auth_api.verify_account_limiter,
        auth_api.resend_account_limiter,
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
    # Hardening U10: the service takes the bound school and the source too.
    monkeypatch.setattr(
        auth_api.service,
        "pin_login",
        lambda pin, school_id, source=None: (_ for _ in ()).throw(
            UnauthorizedError("Invalid PIN")
        ),
    )

    body = {"pin": "0000", "schoolId": SCHOOL_ID}
    for _ in range(auth_api.pin_ip_limiter.max_attempts):
        response = client.post("/api/auth/pin-login", json=body)
        assert response.status_code == 401

    response = client.post("/api/auth/pin-login", json=body)
    assert response.status_code == 429


def test_successful_pin_login_clears_ip_budget(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        auth_api.service, "pin_login", lambda pin, school_id, source=None: {"token": "t"}
    )

    for _ in range(auth_api.pin_ip_limiter.max_attempts + 5):
        response = client.post(
            "/api/auth/pin-login", json={"pin": "1234", "schoolId": SCHOOL_ID}
        )
        assert response.status_code == 200


def test_pin_login_passes_the_bound_school_and_the_source(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = {}

    def stub(pin, school_id, source=None):
        seen.update(pin=pin, school_id=school_id, source=source)
        return {"token": "t"}

    monkeypatch.setattr(auth_api.service, "pin_login", stub)
    response = client.post(
        "/api/auth/pin-login", json={"pin": "482915", "schoolId": SCHOOL_ID}
    )
    assert response.status_code == 200
    assert seen == {"pin": "482915", "school_id": SCHOOL_ID, "source": "testclient"}
    # No school: the service decides (school-required); the field is optional.
    client.post("/api/auth/pin-login", json={"pin": "482915"})
    assert seen["school_id"] is None


def test_the_school_lock_answers_429_with_retry_after_the_browser_can_read(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        auth_api.service,
        "pin_login",
        lambda pin, school_id, source=None: (_ for _ in ()).throw(
            SchoolPinLockedError(SCHOOL_PIN_LOCKED_MESSAGE, retry_after_s=299.2)
        ),
    )
    response = client.post(
        "/api/auth/pin-login",
        json={"pin": "482915", "schoolId": SCHOOL_ID},
        headers={"Origin": "http://localhost:5173"},
    )
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "300"
    assert response.json()["detail"] == {
        "code": "school-pin-locked",
        "message": SCHOOL_PIN_LOCKED_MESSAGE,
        "retry_after_s": 300,
    }
    assert "Retry-After" in response.headers["access-control-expose-headers"]


@pytest.mark.parametrize("code", ["school-required", "invalid-pin"])
def test_pin_refusals_carry_their_code(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, code: str
) -> None:
    monkeypatch.setattr(
        auth_api.service,
        "pin_login",
        lambda pin, school_id, source=None: (_ for _ in ()).throw(PinLoginError(code, "x")),
    )
    response = client.post("/api/auth/pin-login", json={"pin": "482915"})
    assert response.status_code == 401
    assert response.json()["detail"] == {"code": code, "message": "x"}


def test_the_driver_code_lookup_is_rate_limited_per_ip(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        auth_api.service,
        "school_for_driver_code",
        lambda code: (_ for _ in ()).throw(NotFoundError("No school has that driver code")),
    )
    for _ in range(auth_api.school_lookup_ip_limiter.max_attempts):
        assert client.get("/api/auth/schools/ABCD-EFGH").status_code == 404
    assert client.get("/api/auth/schools/ABCD-EFGH").status_code == 429


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
    monkeypatch.setattr(auth_api.service, "forgot_password", lambda email: {"ok": True})

    payload = {"email": "victim@test.com"}
    for _ in range(auth_api.forgot_account_limiter.max_attempts):
        response = client.post("/api/auth/forgot-password", json=payload)
        assert response.status_code == 200

    response = client.post("/api/auth/forgot-password", json=payload)
    assert response.status_code == 429


def test_reset_password_is_rate_limited_per_ip(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(auth_api.service, "reset_password", lambda token, password: {"ok": True})

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


def test_forgot_password_account_budget_is_keyed_per_ip_and_email(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plan U7: the per-account budget keys on ``ip|email``, so a caller
    elsewhere cannot spend a victim's budget."""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "trust_proxy_headers", True)
    monkeypatch.setattr(auth_api.service, "forgot_password", lambda email: {"ok": True})
    payload = {"email": "victim@test.com"}
    attacker = {"X-Forwarded-For": "203.0.113.7"}
    for _ in range(auth_api.forgot_account_limiter.max_attempts):
        assert client.post("/api/auth/forgot-password", json=payload, headers=attacker).status_code == 200
    assert client.post("/api/auth/forgot-password", json=payload, headers=attacker).status_code == 429
    victim = {"X-Forwarded-For": "198.51.100.9"}
    assert client.post("/api/auth/forgot-password", json=payload, headers=victim).status_code == 200


def test_verify_email_is_rate_limited_per_ip(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plan U8: verify-email has a per-IP net like reset-password's (signed
    out here, so only the IP budget applies)."""
    monkeypatch.setattr(auth_api.service, "verify_email", lambda user, token: {"ok": True})
    for _ in range(auth_api.verify_ip_limiter.max_attempts):
        assert client.post("/api/auth/verify-email", json={"token": "t"}).status_code == 200
    response = client.post("/api/auth/verify-email", json={"token": "t"})
    assert response.status_code == 429
    assert "Too many verification attempts" in response.json()["detail"]


def test_verify_email_is_rate_limited_per_account(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.core.auth as core_auth

    monkeypatch.setattr(
        core_auth._service, "resolve_session", lambda token: _fake_session_user("vf-user-1")
    )
    seen: list = []
    monkeypatch.setattr(
        auth_api.service, "verify_email", lambda user, token: seen.append(user) or {"ok": True}
    )
    headers = {"Authorization": "Bearer test-token"}
    for _ in range(auth_api.verify_account_limiter.max_attempts):
        assert client.post("/api/auth/verify-email", json={"token": "t"}, headers=headers).status_code == 200
    assert client.post("/api/auth/verify-email", json={"token": "t"}, headers=headers).status_code == 429
    # The route hands the service the signed-in caller.
    assert seen[0]["id"] == "vf-user-1"


def test_verify_email_never_answers_401_to_a_dead_session(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DL10: a signed-out (or expired) reader gets the 400 sign-in state, not
    the client's session-expired sign-out."""
    import app.core.auth as core_auth

    monkeypatch.setattr(core_auth._service, "resolve_session", lambda token: None)
    response = client.post(
        "/api/auth/verify-email", json={"token": "t"},
        headers={"Authorization": "Bearer stale"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == {
        "code": "sign-in-required",
        "message": "Sign in to the account you are verifying",
    }


def test_resend_verification_is_limited_to_one_a_minute_per_account(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.core.auth as core_auth

    current = {"id": "rs-user-1"}
    monkeypatch.setattr(
        core_auth._service, "resolve_session", lambda token: _fake_session_user(current["id"])
    )
    monkeypatch.setattr(auth_api.service, "resend_verification", lambda user: {"ok": True})
    headers = {"Authorization": "Bearer test-token"}
    assert client.post("/api/auth/resend-verification", headers=headers).status_code == 200
    response = client.post("/api/auth/resend-verification", headers=headers)
    assert response.status_code == 429
    assert response.json()["detail"] == "Try again in a minute"
    current["id"] = "rs-user-2"
    assert client.post("/api/auth/resend-verification", headers=headers).status_code == 200


def test_resend_verification_requires_a_session(client: TestClient) -> None:
    assert client.post("/api/auth/resend-verification").status_code == 401


def test_dev_link_routes_are_404_outside_local(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "app_env", "production")
    for path in ("/api/auth/dev/last-reset-link", "/api/auth/dev/last-verification-link"):
        assert client.get(path).status_code == 404


def test_public_config_carries_the_global_mail_flag(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DL1: the flag is global (no session involved) and true only when a
    mail really goes out — or on the local stack's simulated transport."""
    from app.core.config import get_settings

    settings = get_settings()
    for env, enabled, expected in (
        ("production", False, False),
        ("production", True, True),
        ("local", False, True),
    ):
        monkeypatch.setattr(settings, "app_env", env)
        monkeypatch.setattr(settings, "mail_enabled", enabled)
        assert client.get("/api/push/config").json()["mailEnabled"] is expected
