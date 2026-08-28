"""Shared integration-test helpers.

Kept deliberately small: these tests drive the product through its own API on
purpose, and anything that goes around it hides a contract rather than checking
one. `purge_run` is the one sanctioned exception, and only for teardown — see
its docstring.
"""

import os

import psycopg

DSN = os.environ.get("DATABASE_URL", "postgresql://saferide:saferide@localhost:5432/saferide")


def purge_run(run_id: str | None) -> None:
    """Remove a run out-of-band. **Teardown only** — never inside an assertion.

    The suite's hygiene model is to delete a run once a test is done with it,
    because a completed run blocks the same route from starting again that day
    and the next test would be gated by the last one's leftovers.

    Since U7 the product refuses to delete a completed run dated today: its
    participation rows are the only evidence of who was on that bus, and
    cascading them would flip a whole roster from at school to at home in the
    middle of the day. That refusal is a real guarantee and tests must not have
    a product-level backdoor around it, so cleanup drops to SQL instead.

    run_stops, run_absences and run_participation all cascade on the run row.
    """
    if not run_id:
        return
    with psycopg.connect(DSN, autocommit=True) as pg:
        pg.execute("delete from live_runs where id = %s", (run_id,))


# --- Tenancy fixtures (U1) ---------------------------------------------------
# Shared identities and login helpers for the multi-tenant suites. Seeded in
# backend/db/seeds/003_local_snapshot.sql (tail). Modules keep their own
# `client`; these helpers use a module-level httpx client only for login.

import httpx
import pytest

API_URL = os.environ.get("INTEGRATION_API_URL", "http://localhost:9001")

SCHOOL_A_ID = "5cae0000-0000-0000-0000-000000000001"
SCHOOL_B_ID = "5cae0000-0000-0000-0000-000000000002"

DIRECTOR_A = "director.a@saferide.test"
COORDINATOR_A = "coordinator.a@saferide.test"
DIRECTOR_B = "director.b@saferide.test"
PROVIDER = "provider@kuumbai.test"
DRIVER_B_PIN = "7391"
TEST_PASSWORD = "Test1234"

_token_cache: dict[str, str] = {}


def login(email: str, password: str = TEST_PASSWORD) -> dict[str, str]:
    """Bearer headers for a seeded identity; one login per account per run
    (the per-account limiter is 10/5min — see frontend/tests/e2e/helpers.ts)."""
    if email not in _token_cache:
        response = httpx.post(
            f"{API_URL}/api/auth/login", json={"email": email, "password": password}
        )
        response.raise_for_status()
        _token_cache[email] = response.json()["token"]
    return {"Authorization": f"Bearer {_token_cache[email]}"}


def school_headers(headers: dict[str, str], school_id: str) -> dict[str, str]:
    """The same identity acting inside one named school (U5's X-School-Id)."""
    return {**headers, "X-School-Id": school_id}


@pytest.fixture(scope="session")
def director_a_headers() -> dict[str, str]:
    return login(DIRECTOR_A)


@pytest.fixture(scope="session")
def coordinator_a_headers() -> dict[str, str]:
    return login(COORDINATOR_A)


@pytest.fixture(scope="session")
def director_b_headers() -> dict[str, str]:
    return login(DIRECTOR_B)


@pytest.fixture(scope="session")
def provider_headers() -> dict[str, str]:
    # Until U10 lands, the provider signs in with password only; afterwards the
    # fixture completes the second-factor step too.
    return login(PROVIDER)


from contextlib import contextmanager


@contextmanager
def temp_school(
    name: str,
    *,
    director_email: str = DIRECTOR_A,
    lat: float | None = None,
    lng: float | None = None,
):
    """A throwaway school plus an active director membership, via SQL.

    School creation has no staff API surface any more (it arrives with the
    provider console, U10), so isolation suites provision their sandbox school
    directly. Yields the school id; the exit sweeps everything the test may
    have left inside the school — its own rows first, then the membership and
    the school row. Seeded schools are never touched.
    """
    with psycopg.connect(DSN, autocommit=True) as pg:
        school_id = str(
            pg.execute(
                "insert into live_schools (name, lat, lng) values (%s, %s, %s) returning id",
                (name, lat, lng),
            ).fetchone()[0]
        )
        user_id = str(
            pg.execute(
                "select id from app_users where email = %s", (director_email,)
            ).fetchone()[0]
        )
        pg.execute(
            "insert into school_memberships (user_id, school_id, role, state, accepted_at) "
            "values (%s, %s, 'director', 'active', now())",
            (user_id, school_id),
        )
    try:
        yield school_id
    finally:
        with psycopg.connect(DSN, autocommit=True) as pg:
            pg.execute(
                "delete from live_student_absences where student_id in "
                "(select id from live_students where school_id = %s)",
                (school_id,),
            )
            for table in (
                "live_runs", "live_students", "live_routes", "live_buses",
                "live_fleet_plans", "live_incidents", "live_admin_audit",
                "school_memberships",
            ):
                pg.execute(
                    f"delete from {table} where school_id = %s", (school_id,)  # noqa: S608
                )
            pg.execute("delete from live_schools where id = %s", (school_id,))


@pytest.fixture(scope="session")
def in_process_db():
    """Point the app's process-global connection pool at the suite's DSN.

    backend/.env carries the compose-internal host (``db``), which does not
    resolve from the host machine; in-process tests that exercise real app
    code (TestClient, the seam) need the pool on the host-mapped port.
    """
    from app.core import db as app_db
    from app.core.config import get_settings

    settings = get_settings()
    original = settings.database_url
    app_db.close_pool()
    settings.database_url = DSN
    yield
    app_db.close_pool()
    settings.database_url = original
