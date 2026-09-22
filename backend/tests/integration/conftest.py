"""Shared integration-test helpers.

Kept deliberately small: these tests drive the product through its own API on
purpose, and anything that goes around it hides a contract rather than checking
one. `purge_run` is the one sanctioned exception, and only for teardown — see
its docstring.
"""

import os
import uuid

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

    run_stops, run_absences and run_participation all cascade on the run row,
    as do the 016 run children (run_positions, run_exceptions,
    run_exception_events); a driver_action_keys row keeps its school and
    loses its run reference (SET NULL) so a replay still short-circuits.
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
                "insert into live_schools (name, lat, lng, code) "
                "values (%s, %s, %s, %s) returning id",
                (name, lat, lng, f"IT-{uuid.uuid4().hex[:6].upper()}"),
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
                # 016 run children before the run (they cascade anyway; the
                # explicit order keeps the sweep independent of the FK shape),
                # the key table before the school row it references.
                "run_exception_events", "run_exceptions", "run_positions",
                "live_runs", "live_students", "live_routes", "live_buses",
                "live_fleet_plans", "live_incidents", "live_admin_audit",
                "driver_action_keys", "school_memberships",
            ):
                pg.execute(
                    f"delete from {table} where school_id = %s", (school_id,)  # noqa: S608
                )
            pg.execute("delete from live_schools where id = %s", (school_id,))


def purge_accounts(*user_ids: str | None) -> None:
    """Remove throwaway accounts out-of-band. **Teardown only** — the purge_run
    precedent. Needed since U6 for parent accounts: account deletion became
    school-scoped (a parent is deletable only while linked at the active
    school), so a teardown that already removed the students has no product
    path left to the account — sessions, roles, links and notifications all
    cascade with the user row."""
    ids = [str(u) for u in user_ids if u]
    if not ids:
        return
    with psycopg.connect(DSN, autocommit=True) as pg:
        pg.execute("delete from app_users where id = any(%s::uuid[])", (ids,))


SANDBOX_PASSWORD = "SandboxPass1!"


def _password_hash(password: str) -> str:
    """Mirror app.core.security.hash_password's stored format (pbkdf2_sha256,
    200k iterations) so a direct-SQL account can log in through the real API."""
    import base64
    import hashlib
    import uuid as _uuid

    salt = _uuid.uuid4().hex
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000
    )
    return f"pbkdf2_sha256$200000${salt}${base64.b64encode(digest).decode('ascii')}"


@contextmanager
def school_sandbox(
    name: str, *, lat: float | None = None, lng: float | None = None,
    morning_bell: str | None = None, afternoon_bell: str | None = None,
):
    """A throwaway school PLUS a throwaway admin whose ONLY membership is that
    school — the post-U6 replacement for the retired POST /api/fleet/schools
    the legacy suites used to provision their isolated world.

    The admin carries the legacy 'admin' role (so the pre-U7 container's
    role guards accept it) AND an active director membership at the sandbox
    school (so the U6/U7 staff guards resolve it too); being their single
    membership, the header fallback lands there and no X-School-Id is
    needed anywhere in the suite. Yields ``{"id", "name", "lat", "lng",
    "email", "password"}``; the exit sweeps everything the suite may have
    left inside the school, then the membership, school and admin account.
    Seeded rows are never touched.
    """
    import uuid as _uuid

    marker = _uuid.uuid4().hex[:8]
    email = f"it-sandbox-admin-{marker}@test.local"
    with psycopg.connect(DSN, autocommit=True) as pg:
        school_id = str(
            pg.execute(
                "insert into live_schools (name, lat, lng, morning_bell, afternoon_bell, code) "
                "values (%s, %s, %s, %s, %s, %s) returning id",
                (name, lat, lng, morning_bell, afternoon_bell, f"IT-{marker.upper()}"),
            ).fetchone()[0]
        )
        admin_id = str(
            pg.execute(
                "insert into app_users (email, password_hash, full_name) "
                "values (%s, %s, %s) returning id",
                (email, _password_hash(SANDBOX_PASSWORD), f"IT Sandbox Admin {marker}"),
            ).fetchone()[0]
        )
        pg.execute(
            "insert into app_user_roles (user_id, role) values (%s, 'admin')",
            (admin_id,),
        )
        pg.execute(
            "insert into school_memberships (user_id, school_id, role, state, accepted_at) "
            "values (%s, %s, 'director', 'active', now())",
            (admin_id, school_id),
        )
    try:
        yield {
            "id": school_id, "name": name, "lat": lat, "lng": lng,
            "email": email, "password": SANDBOX_PASSWORD, "admin_id": admin_id,
        }
    finally:
        with psycopg.connect(DSN, autocommit=True) as pg:
            pg.execute(
                "delete from live_student_absences where student_id in "
                "(select id from live_students where school_id = %s)",
                (school_id,),
            )
            for table in (
                # Same order as temp_school: 016 run children, the run, the
                # rest, then the key table before the school row.
                "run_exception_events", "run_exceptions", "run_positions",
                "live_runs", "live_students", "live_routes", "live_buses",
                "live_fleet_plans", "live_incidents", "live_admin_audit",
                "driver_action_keys", "school_memberships",
            ):
                pg.execute(
                    f"delete from {table} where school_id = %s", (school_id,)  # noqa: S608
                )
            pg.execute("delete from live_schools where id = %s", (school_id,))
            pg.execute("delete from app_users where id = %s", (admin_id,))


# --- a moving phone (GPS plan U12) ------------------------------------------
# The plausibility safeguard judges every fix against the run's previous one:
# a teleport, a repeated coordinate or accuracy, a fix on a stop's pin is
# flagged and clears no check. A suite that posts fixes milliseconds apart
# from kilometres away would flag its own taps, so the GPS suites mint their
# fixes through one Phone that behaves like a phone on a bus: capture times
# in the past that advance by the travel time between consecutive fixes,
# coordinates that never repeat unless the same fix is re-sent (the client's
# 15 s cache window, U6/U7). Tests that want a flag build the fix by hand.

from datetime import datetime, timedelta, timezone
from math import asin, cos, radians, sin, sqrt

NAIROBI_OFFSET = timezone(timedelta(hours=3))
METRE = 1 / 111_195.0  # degrees of latitude per metre


def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lng1, lat2, lng2 = map(radians, (a[0], a[1], b[0], b[1]))
    h = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lng2 - lng1) / 2) ** 2
    return 2 * 6_371_000.0 * asin(sqrt(h))


class Phone:
    """Mints plausible consecutive fixes for one driver at a time.

    ``begin_run`` (called by the suites' start helpers) rewinds the clock to
    an hour ago so a run's fixes never reach the clock-skew tolerance, and
    re-stamps a start fix minted a moment earlier so the run's first fix is
    also its earliest. ``fix`` advances the clock by the distance from the
    last fix at ``SPEED_MPS`` (under the 40 m/s cap; a kilometre takes over
    30 s), cycles a non-integer accuracy the way a phone's varies, defaults
    the coordinates to the run's home then a few metres on from the last fix,
    and nudges an explicit coordinate that repeats the last one by a few
    decimetres east. An explicit ``captured_at`` is sent as given.
    """

    SPEED_MPS = 30.0
    MIN_GAP_S = 3.0
    DRIFT_M = 5.0
    NUDGE_M = 0.3
    ACCURACIES = tuple(12.25 + 0.5 * n for n in range(40))

    def __init__(self, home: tuple[float, float]):
        self.home = home
        self.minted: set[str] = set()
        self.mints = 0
        self.begin_run(None)

    def begin_run(self, fix_body) -> None:
        self.clock = datetime.now(timezone.utc) - timedelta(hours=1)
        self.at: tuple[float, float] | None = None
        self.last_accuracy: float | None = None
        if (
            isinstance(fix_body, dict)
            and fix_body.get("captured_at") in self.minted
            and fix_body.get("lat") is not None
        ):
            self.at = (fix_body["lat"], fix_body["lng"])
            self.last_accuracy = fix_body.get("accuracy_m")
            self.clock += timedelta(seconds=self.MIN_GAP_S)
            fix_body["captured_at"] = self._stamp()

    def _stamp(self) -> str:
        stamp = self.clock.astimezone(NAIROBI_OFFSET).isoformat(timespec="milliseconds")
        self.minted.add(stamp)
        return stamp

    def _next_accuracy(self) -> float:
        for _ in range(len(self.ACCURACIES)):
            value = self.ACCURACIES[self.mints % len(self.ACCURACIES)]
            self.mints += 1
            if value != self.last_accuracy:
                return value
        return self.ACCURACIES[0]

    def fix(self, lat=None, lng=None, accuracy=None, captured_at=None, **extra) -> dict:
        if lat is None and lng is None:
            if self.at is None:
                lat, lng = self.home
            else:
                lat, lng = self.at[0] + self.DRIFT_M * METRE, self.at[1]
        else:
            lat = self.home[0] if lat is None else lat
            lng = self.home[1] if lng is None else lng
            if self.at is not None and (lat, lng) == self.at:
                lng = lng + self.NUDGE_M * METRE
        if accuracy is None:
            accuracy = self._next_accuracy()
        if captured_at is None:
            travel = haversine_m(self.at, (lat, lng)) if self.at is not None else 0.0
            self.clock += timedelta(seconds=max(self.MIN_GAP_S, travel / self.SPEED_MPS))
            captured_at = self._stamp()
        self.at = (lat, lng)
        self.last_accuracy = accuracy
        return {"lat": lat, "lng": lng, "accuracy_m": accuracy, "captured_at": captured_at, **extra}

    def jump(self, lat: float, lng: float, accuracy=None, *, after_s: float) -> dict:
        """An implausible move on purpose: a fix at exactly (lat, lng) —
        no nudge — captured ``after_s`` after the last one, whatever the
        distance. The U12 tests build their teleports with this."""
        self.clock += timedelta(seconds=after_s)
        captured_at = self._stamp()
        if accuracy is None:
            accuracy = self._next_accuracy()
        self.at = (lat, lng)
        self.last_accuracy = accuracy
        return {"lat": lat, "lng": lng, "accuracy_m": accuracy, "captured_at": captured_at}


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
