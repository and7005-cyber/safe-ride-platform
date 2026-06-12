"""Sliding-window rate limiting for credential endpoints.

In-process and per-worker: good brute-force protection for a single-instance
deployment. If the API is ever scaled horizontally, swap the storage for a
shared backend (e.g. Redis) behind the same interface.
"""

import time
from collections import defaultdict
from threading import Lock

from fastapi import Request

from app.core.config import get_settings
from app.core.errors import TooManyRequestsError


class SlidingWindowLimiter:
    """Counts attempts per key inside a sliding time window."""

    def __init__(self, max_attempts: int, window_seconds: float) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._attempts: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()

    def check(self, key: str, message: str = "Too many attempts. Try again shortly.") -> None:
        """Record one attempt for `key`, raising TooManyRequestsError over budget."""
        now = time.monotonic()
        with self._lock:
            recent = [t for t in self._attempts[key] if now - t < self.window_seconds]
            if len(recent) >= self.max_attempts:
                self._attempts[key] = recent
                raise TooManyRequestsError(message)
            recent.append(now)
            self._attempts[key] = recent

    def clear(self, key: str) -> None:
        """Drop the budget for `key` (e.g. after a successful login)."""
        with self._lock:
            self._attempts.pop(key, None)

    def reset(self) -> None:
        """Drop all state. Intended for tests."""
        with self._lock:
            self._attempts.clear()


def client_ip(request: Request | None) -> str:
    """Resolve the caller IP, honouring X-Forwarded-For behind a trusted proxy.

    TRUST_PROXY_HEADERS must stay off unless every request reaches the API via
    a proxy that overwrites X-Forwarded-For; otherwise clients could spoof it
    to dodge per-IP limits.
    """
    if request is None:
        return "unknown"
    if get_settings().trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for", "")
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    if request.client:
        return request.client.host
    return "unknown"
