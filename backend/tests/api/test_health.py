"""GET /api/health (hardening U11, AE12): database-aware, versioned, bounded.

The pool is replaced by fakes here; tests/integration/test_health_db.py runs
the same probe against the real database (and a dead one).
"""

from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from psycopg_pool import PoolTimeout

from app.api import health as health_api
from app.core.config import get_settings
from app.main import create_app


class FakeConnection:
    def __init__(self, fail_on: str | None = None) -> None:
        self.statements: list[tuple[str, tuple | None]] = []
        self.fail_on = fail_on

    def execute(self, sql: str, params: tuple | None = None):
        self.statements.append((sql, params))
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("canceling statement due to statement timeout")
        return self

    def fetchone(self):
        return (1,)


class FakePool:
    """Stands in for get_global_connection: counts checkouts and the wait
    timeout asked for, and can refuse like a pool whose database is down."""

    def __init__(self, *, refuse: bool = False, fail_on: str | None = None) -> None:
        self.refuse = refuse
        self.fail_on = fail_on
        self.checkouts: list[float | None] = []
        self.connections: list[FakeConnection] = []

    @contextmanager
    def __call__(self, timeout: float | None = None):
        self.checkouts.append(timeout)
        if self.refuse:
            raise PoolTimeout("couldn't get a connection after 1.00 sec")
        connection = FakeConnection(self.fail_on)
        self.connections.append(connection)
        yield connection


@pytest.fixture(autouse=True)
def _fresh_cache():
    health_api.reset_cache()
    yield
    health_api.reset_cache()


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture
def clock(monkeypatch):
    now = {"t": 1000.0}
    monkeypatch.setattr(health_api, "_now", lambda: now["t"])
    return now


def _use(monkeypatch, pool: FakePool) -> FakePool:
    monkeypatch.setattr(health_api, "get_global_connection", pool)
    return pool


def test_a_working_pool_answers_ok_with_the_version(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "app_version", "a66d53b")
    pool = _use(monkeypatch, FakePool())

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok", "version": "a66d53b"}
    assert len(pool.checkouts) == 1


def test_a_failing_pool_answers_503_db_error_and_still_reports_the_version(
    client, monkeypatch, caplog
):
    monkeypatch.setattr(get_settings(), "app_version", "a66d53b")
    _use(monkeypatch, FakePool(refuse=True))

    with caplog.at_level("WARNING", logger="saferide.health"):
        response = client.get("/api/health")

    assert response.status_code == 503
    assert response.json() == {"status": "error", "db": "error", "version": "a66d53b"}
    (record,) = [r for r in caplog.records if r.name == "saferide.health"]
    # The class only — a connection error's text names the host and the role.
    assert record.getMessage() == "health-db-error error=PoolTimeout sqlstate=-"
    assert record.exc_info is None


def test_a_failing_query_answers_503(client, monkeypatch):
    _use(monkeypatch, FakePool(fail_on=health_api.HEALTH_QUERY))

    response = client.get("/api/health")

    assert response.status_code == 503
    assert response.json()["db"] == "error"


def test_the_probe_is_bounded_to_one_second_both_ways(client, monkeypatch):
    pool = _use(monkeypatch, FakePool())

    client.get("/api/health")

    assert pool.checkouts == [1.0]  # pool wait
    (connection,) = pool.connections
    # The transaction-local statement timeout comes before the query.
    assert connection.statements == [
        ("select set_config('statement_timeout', %s, true)", ("1000",)),
        ("select 1", None),
    ]


def test_the_outcome_is_cached_for_five_seconds(client, monkeypatch, clock):
    pool = _use(monkeypatch, FakePool())

    for _ in range(10):
        assert client.get("/api/health").status_code == 200
    clock["t"] += 4.9
    client.get("/api/health")
    assert len(pool.checkouts) == 1

    clock["t"] += 0.2  # 5.1 s after the probe
    client.get("/api/health")
    assert len(pool.checkouts) == 2


def test_a_failure_is_cached_too_and_recovery_shows_after_the_window(
    client, monkeypatch, clock
):
    failing = _use(monkeypatch, FakePool(refuse=True))
    assert client.get("/api/health").status_code == 503
    working = _use(monkeypatch, FakePool())
    assert client.get("/api/health").status_code == 503  # still the cached failure
    assert len(failing.checkouts) == 1 and working.checkouts == []

    clock["t"] += health_api.HEALTH_CACHE_SECONDS
    assert client.get("/api/health").status_code == 200
    assert len(working.checkouts) == 1


def test_off_the_deploy_the_version_is_dev():
    from app.core.config import Settings

    assert Settings.model_fields["app_version"].default == "dev"
    assert Settings.model_fields["app_version"].alias == "APP_VERSION"
