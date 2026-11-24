"""GET /api/health against a real database, a slow one and a dead one, and the
U11 headers on the running API container (hardening U11, AE12).

Run with the local stack up:

    RUN_INTEGRATION=1 ../.venv/bin/python -m pytest tests/integration/test_health_db.py -q

The in-process tests drive the app's own pool (``in_process_db``); the
container tests go to http://localhost:9001 like test_live_api.
"""

import logging
import os
import time
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import API_URL, DSN

from app.api import health as health_api
from app.core import db as app_db
from app.core.config import get_settings
from app.main import create_app

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="needs the local stack; set RUN_INTEGRATION=1",
)

FIVE_HEADERS = {
    "strict-transport-security": "max-age=31536000; includeSubDomains",
    "x-content-type-options": "nosniff",
    "cache-control": "no-store",
    "content-security-policy": "default-src 'none'; frame-ancestors 'none'",
}
VITE_ORIGIN = "http://localhost:5173"


@pytest.fixture
def client(in_process_db):
    health_api.reset_cache()
    yield TestClient(create_app())
    health_api.reset_cache()


def _in_use() -> int:
    """Connections handed out and not returned. ``pool_size`` also counts
    connections still being opened, so let the pool fill first."""
    pool = app_db.get_pool()
    pool.wait(timeout=5)
    stats = pool.get_stats()
    return stats["pool_size"] - stats["pool_available"]


# --- in process -----------------------------------------------------------------


def test_a_working_database_answers_ok_with_the_version(client):
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok", "db": "ok", "version": get_settings().app_version,
    }
    assert _in_use() == 0


def test_a_slow_query_is_cut_at_one_second_and_the_connection_comes_back(
    client, monkeypatch, caplog
):
    monkeypatch.setattr(health_api, "HEALTH_QUERY", "select pg_sleep(5)")

    started = time.monotonic()
    with caplog.at_level(logging.WARNING, logger="saferide.health"):
        response = client.get("/api/health")
    elapsed = time.monotonic() - started

    assert response.status_code == 503
    assert response.json()["db"] == "error"
    assert elapsed < 3, elapsed
    assert any(
        r.getMessage() == "health-db-error error=QueryCanceled sqlstate=57014"
        for r in caplog.records
    ), [r.getMessage() for r in caplog.records]
    assert _in_use() == 0
    # The timeout was transaction-local: the pool's next checkout is clean.
    with app_db.get_global_connection() as connection:
        setting = connection.execute("show statement_timeout").fetchone()
    assert list(setting.values())[0] != "1s"


def test_a_dead_database_answers_503_within_about_a_second(client):
    settings = get_settings()
    app_db.close_pool()
    settings.database_url = "postgresql://saferide:saferide@localhost:1/saferide"
    try:
        started = time.monotonic()
        response = client.get("/api/health")
        elapsed = time.monotonic() - started
    finally:
        app_db.close_pool()
        settings.database_url = DSN

    assert response.status_code == 503
    assert response.json() == {
        "status": "error", "db": "error", "version": settings.app_version,
    }
    assert elapsed < 3, elapsed
    # And recovery shows once the cache window has passed.
    health_api.reset_cache()
    assert client.get("/api/health").status_code == 200


def test_a_burst_of_probes_costs_one_query(client, monkeypatch):
    calls = []
    real = health_api._database_answers
    monkeypatch.setattr(health_api, "_database_answers", lambda: calls.append(1) or real())

    for _ in range(20):
        assert client.get("/api/health").status_code == 200

    assert len(calls) == 1
    assert _in_use() == 0


# --- the running container -----------------------------------------------------


@pytest.fixture(scope="module")
def live():
    with httpx.Client(base_url=API_URL, timeout=20) as c:
        yield c


def _assert_headers(response: httpx.Response) -> None:
    assert uuid.UUID(response.headers["x-request-id"]).version == 4
    for header, value in FIVE_HEADERS.items():
        assert response.headers.get(header) == value, (response.url, header)


def test_the_container_health_is_database_aware(live):
    response = live.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok" and body["db"] == "ok"
    assert body["version"]  # "dev" off a deploy
    _assert_headers(response)


def test_the_container_never_echoes_an_inbound_request_id(live):
    response = live.get("/api/health", headers={"X-Request-Id": "planted-id-0003"})

    assert response.headers["x-request-id"] != "planted-id-0003"
    _assert_headers(response)


def test_the_container_stamps_404s_401s_and_preflights(live):
    responses = [
        live.get("/api/no-such-route"),
        live.get("/api/auth/me"),
        live.options(
            "/api/auth/login",
            headers={"Origin": VITE_ORIGIN, "Access-Control-Request-Method": "POST"},
        ),
    ]
    assert [r.status_code for r in responses] == [404, 401, 200]
    for response in responses:
        _assert_headers(response)


def test_the_container_lets_the_browser_read_the_id(live):
    response = live.get("/api/health", headers={"Origin": VITE_ORIGIN})

    assert response.headers["access-control-allow-origin"] == VITE_ORIGIN
    assert "X-Request-Id" in response.headers["access-control-expose-headers"]


def test_the_local_container_still_serves_the_docs(live):
    # APP_ENV=local in backend/.env; production answers 404 (unit suite).
    assert live.get("/openapi.json").status_code == 200
    assert live.get("/docs").status_code == 200
