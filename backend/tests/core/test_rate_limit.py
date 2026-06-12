import pytest

from app.core import rate_limit
from app.core.config import Settings
from app.core.errors import TooManyRequestsError
from app.core.rate_limit import SlidingWindowLimiter, client_ip


class FakeRequest:
    def __init__(self, host: str | None = "10.0.0.1", headers: dict | None = None) -> None:
        self.headers = headers or {}
        self.client = type("Client", (), {"host": host})() if host else None


def test_limiter_allows_up_to_max_attempts() -> None:
    limiter = SlidingWindowLimiter(max_attempts=3, window_seconds=60)

    for _ in range(3):
        limiter.check("key")

    with pytest.raises(TooManyRequestsError):
        limiter.check("key")


def test_limiter_keys_are_independent() -> None:
    limiter = SlidingWindowLimiter(max_attempts=1, window_seconds=60)

    limiter.check("a")
    limiter.check("b")

    with pytest.raises(TooManyRequestsError):
        limiter.check("a")


def test_limiter_window_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = {"now": 1000.0}
    monkeypatch.setattr(rate_limit.time, "monotonic", lambda: clock["now"])
    limiter = SlidingWindowLimiter(max_attempts=2, window_seconds=10)

    limiter.check("key")
    limiter.check("key")
    clock["now"] += 11

    limiter.check("key")  # does not raise: old attempts fell out of the window


def test_limiter_clear_resets_budget() -> None:
    limiter = SlidingWindowLimiter(max_attempts=1, window_seconds=60)

    limiter.check("key")
    limiter.clear("key")

    limiter.check("key")  # does not raise


def test_client_ip_uses_socket_host_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        rate_limit, "get_settings", lambda: Settings(TRUST_PROXY_HEADERS=False, _env_file=None)
    )
    request = FakeRequest(host="10.0.0.1", headers={"x-forwarded-for": "1.2.3.4"})

    assert client_ip(request) == "10.0.0.1"


def test_client_ip_honours_forwarded_for_behind_trusted_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rate_limit, "get_settings", lambda: Settings(TRUST_PROXY_HEADERS=True, _env_file=None)
    )
    request = FakeRequest(host="172.17.0.1", headers={"x-forwarded-for": "1.2.3.4, 172.17.0.1"})

    assert client_ip(request) == "1.2.3.4"


def test_client_ip_falls_back_when_forwarded_header_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rate_limit, "get_settings", lambda: Settings(TRUST_PROXY_HEADERS=True, _env_file=None)
    )
    request = FakeRequest(host="172.17.0.1")

    assert client_ip(request) == "172.17.0.1"


def test_client_ip_handles_missing_request() -> None:
    assert client_ip(None) == "unknown"
