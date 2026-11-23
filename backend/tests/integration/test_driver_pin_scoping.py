"""Driver sign-in bound to the school; school-salted PINs; the per-school lock
(hardening U10; AE8, AE9, AE10).

Runs the real ``create_app()`` in-process (TestClient) against the local
database, like test_parent_reset.py: the sign-in, the lookup and the Drivers
page rules under test live in this working tree, and the in-process client
gives the lock a known source (``testclient``, or the address passed to a
second client).

Every school here is a throwaway (``temp_school``) and every driver a
throwaway account, purged at the end. Two schools share one PIN, one driver
holding the LEGACY global hash (written directly, as the pre-1b code did) and
one the salted hash (written by the Drivers page). The one
``driver-code-regenerated`` audit row is deleted after its test: the 013/016
double-apply rehearsals validate existing audit rows against older CHECK
lists.
"""

import datetime as dt
import logging
import os
import uuid
from contextlib import ExitStack

import psycopg
import pytest
from psycopg.rows import dict_row

from conftest import (
    COORDINATOR_A,
    DIRECTOR_A,
    DSN,
    SCHOOL_A_ID,
    TEST_PASSWORD,
    _password_hash,
    driver_pin,
    pin_login_body,
    purge_accounts,
    temp_school,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="needs the local stack; set RUN_INTEGRATION=1",
)

IN_USE = "That PIN is already in use at this school"
ELSEWHERE = (
    "This driver already drives for another school. "
    "A driver can sign in by PIN at one school only."
)
OTHER_SOURCE = ("203.0.113.9", 50000)  # TEST-NET-3: another phone's address


@pytest.fixture(scope="module")
def client(in_process_db):
    from fastapi.testclient import TestClient

    from app.main import create_app

    return TestClient(create_app())


@pytest.fixture(scope="module")
def other_client(in_process_db):
    """Another source: the lock is keyed on (school, source)."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    return TestClient(create_app(), client=OTHER_SOURCE)


@pytest.fixture(autouse=True)
def _reset_limiters():
    """The in-process nets are module-global; this suite signs in and fails
    from one address many times. The database lock is what is under test."""
    from app.api import auth as auth_api

    for limiter in (
        auth_api.login_account_limiter,
        auth_api.login_ip_limiter,
        auth_api.pin_ip_limiter,
        auth_api.school_lookup_ip_limiter,
    ):
        limiter.reset()
    yield


# --- helpers ------------------------------------------------------------------------

_tokens: dict[str, str] = {}
_created: list[str] = []


def db():
    return psycopg.connect(DSN, autocommit=True, row_factory=dict_row)


def pepper() -> str:
    from app.core.config import get_settings

    return get_settings().pin_pepper


def staff(client, email: str, school_id: str) -> dict[str, str]:
    if email not in _tokens:
        resp = client.post("/api/auth/login", json={"email": email, "password": TEST_PASSWORD})
        assert resp.status_code == 200, resp.text
        _tokens[email] = resp.json()["token"]
    return {"Authorization": f"Bearer {_tokens[email]}", "X-School-Id": school_id}


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def create_driver(client, school_id: str, pin: str | None, marker: str):
    return client.post(
        "/api/accounts/drivers",
        json={
            "full_name": f"IT U10 Driver {marker}",
            "email": f"it-u10-{marker}-{uuid.uuid4().hex[:6]}@saferide.test",
            "password": "test1234.", "pin": pin,
        },
        headers=staff(client, DIRECTOR_A, school_id),
    )


def new_driver(client, school_id: str, pin: str | None, marker: str) -> str:
    resp = create_driver(client, school_id, pin, marker)
    assert resp.status_code == 200, resp.text
    _created.append(str(resp.json()["id"]))
    return str(resp.json()["id"])


def legacy_driver(school_id: str, pin: str, marker: str) -> str:
    """A driver exactly as the pre-1b code left it: the GLOBAL legacy hash."""
    from app.core.security import hash_pin_hmac

    with db() as conn:
        user_id = str(
            conn.execute(
                "insert into app_users (email, password_hash, full_name, pin_hash) "
                "values (%s, %s, %s, %s) returning id",
                (
                    f"it-u10-legacy-{marker}-{uuid.uuid4().hex[:6]}@saferide.test",
                    _password_hash("test1234."), f"IT U10 Legacy {marker}",
                    hash_pin_hmac(pin, pepper()),
                ),
            ).fetchone()["id"]
        )
        conn.execute(
            "insert into app_user_roles (user_id, role) values (%s, 'driver')", (user_id,)
        )
        conn.execute(
            "insert into school_memberships (user_id, school_id, role, state, accepted_at) "
            "values (%s, %s, 'driver', 'active', now())",
            (user_id, school_id),
        )
    _created.append(user_id)
    return user_id


def pin_hash_of(user_id: str) -> str | None:
    with db() as conn:
        return conn.execute(
            "select pin_hash from app_users where id = %s", (user_id,)
        ).fetchone()["pin_hash"]


def school_row(school_id: str) -> dict:
    with db() as conn:
        return conn.execute(
            "select id, name, code, driver_code from live_schools where id = %s", (school_id,)
        ).fetchone()


def sign_in(client, pin: str, school_id: str):
    return client.post("/api/auth/pin-login", json=pin_login_body(pin, school_id))


def signed_in_as(client, pin: str, school_id: str) -> str:
    resp = sign_in(client, pin, school_id)
    assert resp.status_code == 200, resp.text
    return str(resp.json()["user"]["id"])


def fail_rows(school_id: str, source: str, n: int, minutes_ago: float) -> None:
    with db() as conn:
        conn.execute(
            "insert into pin_login_failures (school_id, source, attempted_at) "
            "select %s, %s, now() - make_interval(secs => %s) from generate_series(1, %s)",
            (school_id, source, minutes_ago * 60, n),
        )


@pytest.fixture(scope="module", autouse=True)
def _purge_drivers():
    yield
    purge_accounts(*_created)


@pytest.fixture(scope="module")
def schools():
    """Three throwaway schools, director A at each."""
    with ExitStack() as stack:
        ids = [
            stack.enter_context(temp_school(f"IT U10 School {n} {uuid.uuid4().hex[:4]}"))
            for n in (1, 2, 3)
        ]
        yield ids


# --- AE8: no school, no sign-in -------------------------------------------------------


def test_a_pin_without_a_school_is_refused_and_counted_in_the_log(client, schools, caplog):
    school = schools[0]
    pin = driver_pin()
    driver = new_driver(client, school, pin, "ae8")
    with caplog.at_level(logging.INFO, logger="saferide.auth"):
        refused = client.post("/api/auth/pin-login", json={"pin": pin})
    assert refused.status_code == 401
    assert refused.json()["detail"]["code"] == "school-required"
    assert any(
        r.name == "saferide.auth" and r.getMessage() == "school-required" for r in caplog.records
    )

    ok = sign_in(client, pin, school)
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert str(body["user"]["id"]) == driver
    assert body["user"]["signedInWithPin"] is True
    # AE10's server half: the session names the school, and /me carries the
    # driver membership a phone without a binding adopts.
    me = client.get("/api/auth/me", headers=bearer(body["token"])).json()
    assert me["activeSchoolId"] == school
    assert {"schoolId": school, "role": "driver"}.items() <= next(
        m for m in me["memberships"] if m["role"] == "driver"
    ).items()


def test_an_unknown_school_is_an_invalid_pin(client):
    resp = sign_in(client, driver_pin(), str(uuid.uuid4()))
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "invalid-pin"
    resp = client.post("/api/auth/pin-login", json={"pin": "0322", "schoolId": "GFA-001"})
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "invalid-pin"


# --- AE9: two schools, one PIN — legacy at one, salted at the other -----------------------


def test_two_schools_share_a_pin_and_each_driver_signs_in_only_at_their_own(client, schools):
    from app.core.security import hash_pin_hmac, hash_pin_v2

    s1, s2, s3 = schools
    pin = driver_pin()
    legacy = legacy_driver(s1, pin, "ae9")
    assert pin_hash_of(legacy) == hash_pin_hmac(pin, pepper())

    # The Drivers page at school 2 sets the same PIN: allowed, stored salted.
    salted = new_driver(client, s2, pin, "ae9-salted")
    assert pin_hash_of(salted) == hash_pin_v2(s2, pin, pepper())

    # Twice at one school is refused, whichever form the holder has —
    # the legacy holder at school 1, the salted one at school 2.
    for school in (s1, s2):
        refused = create_driver(client, school, pin, "ae9-twice")
        assert refused.status_code == 409, refused.text
        assert refused.json()["detail"] == IN_USE
    # A re-issue to that PIN for another driver at the school is refused too.
    other = new_driver(client, s1, driver_pin(), "ae9-other")
    reissue = client.put(
        f"/api/accounts/drivers/{other}",
        json={"full_name": "IT U10 Other", "email": f"it-u10-other-{other[:8]}@saferide.test",
              "pin": pin},
        headers=staff(client, DIRECTOR_A, s1),
    )
    assert reissue.status_code == 409 and reissue.json()["detail"] == IN_USE

    # A third school may use it.
    third = new_driver(client, s3, pin, "ae9-third")

    # Each school's sign-in finds its own driver — never another school's.
    assert signed_in_as(client, pin, s2) == salted
    assert signed_in_as(client, pin, s3) == third
    assert pin_hash_of(legacy) == hash_pin_hmac(pin, pepper())  # untouched so far

    # The legacy driver signs in at their own school, and the row is
    # rewritten to the salted form in that sign-in's transaction.
    assert signed_in_as(client, pin, s1) == legacy
    upgraded = pin_hash_of(legacy)
    assert upgraded == hash_pin_v2(s1, pin, pepper()) and upgraded.startswith("v2:k1:")
    # The next sign-in matches the salted hash directly.
    assert signed_in_as(client, pin, s1) == legacy
    assert pin_hash_of(legacy) == upgraded


def test_legacy_verification_is_confined_to_the_bound_schools_drivers(client, schools):
    from app.core.security import hash_pin_hmac

    # Throwaway schools only: a failure row at a seeded school would count
    # towards that school's lock for every later suite from this address.
    s1, s2, s3 = schools
    pin = driver_pin()
    legacy = legacy_driver(s1, pin, "confined")
    for school in (s2, s3):
        resp = sign_in(client, pin, school)
        assert resp.status_code == 401 and resp.json()["detail"]["code"] == "invalid-pin"
    assert pin_hash_of(legacy) == hash_pin_hmac(pin, pepper())


@pytest.mark.parametrize("state", ["removed-membership", "disabled"])
def test_a_removed_membership_or_a_disabled_driver_never_matches(client, schools, state):
    school = schools[1]
    pin = driver_pin()
    driver = new_driver(client, school, pin, state)
    with db() as conn:
        if state == "disabled":
            conn.execute("update app_users set disabled_at = now() where id = %s", (driver,))
        else:
            conn.execute(
                "update school_memberships set removed_at = now() "
                "where user_id = %s and school_id = %s", (driver, school),
            )
    resp = sign_in(client, pin, school)
    assert resp.status_code == 401 and resp.json()["detail"]["code"] == "invalid-pin"


# --- the Drivers page's rules -------------------------------------------------------------


@pytest.mark.parametrize(
    "pin, message",
    [
        ("48291", "PIN must be 6 digits"),
        ("0322", "PIN must be 6 digits"),
        ("123456", "That PIN is too easy to guess"),
        ("654321", "That PIN is too easy to guess"),
        ("111111", "That PIN is too easy to guess"),
        ("121212", "That PIN is too easy to guess"),
        ("123123", "That PIN is too easy to guess"),
        ("112233", "That PIN is too easy to guess"),
    ],
)
def test_creation_refuses_short_easy_and_denylisted_pins(client, schools, pin, message):
    resp = create_driver(client, schools[2], pin, "rules")
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == message


def test_a_pin_holder_drives_at_one_school_only(client, schools):
    s1, s2, _ = schools
    driver = new_driver(client, s1, driver_pin(), "one-school")
    # Data the pre-1b code could hold: an active driver membership elsewhere.
    with db() as conn:
        conn.execute(
            "insert into school_memberships (user_id, school_id, role, state, accepted_at) "
            "values (%s, %s, 'driver', 'active', now())",
            (driver, s2),
        )
    email = f"it-u10-one-{driver[:8]}@saferide.test"
    for school in (s1, s2):
        refused = client.put(
            f"/api/accounts/drivers/{driver}",
            json={"full_name": "IT U10 One", "email": email, "pin": driver_pin()},
            headers=staff(client, DIRECTOR_A, school),
        )
        assert refused.status_code == 409, refused.text
        assert refused.json()["detail"] == ELSEWHERE
    # Editing without setting a PIN is not refused.
    edited = client.put(
        f"/api/accounts/drivers/{driver}",
        json={"full_name": "IT U10 One", "email": email},
        headers=staff(client, DIRECTOR_A, s1),
    )
    assert edited.status_code == 200, edited.text


# --- the per-school lock (D3) ---------------------------------------------------------------


def test_the_lock_is_keyed_on_school_and_source_and_lifts_after_the_window(
    client, other_client, schools, caplog
):
    from app.api import auth as auth_api

    s3 = schools[2]
    with temp_school(f"IT U10 Lock {uuid.uuid4().hex[:4]}") as locked:
        code = school_row(locked)["code"]
        pin = driver_pin()
        driver = new_driver(client, locked, pin, "lock")
        elsewhere_pin = driver_pin()
        elsewhere = new_driver(client, s3, elsewhere_pin, "lock-elsewhere")

        # Twenty earlier failures from this source, ten minutes ago, then the
        # twenty-first through the API: an ordinary wrong PIN.
        fail_rows(locked, "testclient", 20, minutes_ago=10)
        wrong = next(p for p in iter(driver_pin, None) if p != pin)
        with caplog.at_level(logging.INFO, logger="saferide.auth"):
            first = sign_in(client, wrong, locked)
            assert first.status_code == 401
            assert first.json()["detail"]["code"] == "invalid-pin"

            # The twenty-second attempt is paused — the right PIN included.
            paused = sign_in(client, pin, locked)
        assert paused.status_code == 429, paused.text
        assert paused.json()["detail"]["code"] == "school-pin-locked"
        retry_after = int(paused.headers["Retry-After"])
        assert 285 <= retry_after <= 301  # the oldest counted failure leaves in ~5 min
        messages = [r.getMessage() for r in caplog.records if r.name == "saferide.auth"]
        assert f"pin-failure school={code}" in messages
        assert f"pin-locked school={code}" in messages
        blob = " ".join(messages)
        for secret in (pin, wrong, school_row(locked)["driver_code"], "testclient", locked):
            assert secret not in blob
        with db() as conn:
            failures = conn.execute(
                "select count(*) as n from pin_login_failures "
                "where school_id = %s and source = 'testclient'", (locked,),
            ).fetchone()["n"]
        assert failures == 21  # the refused attempt wrote no row

        # Another source at the same school, and another school from this
        # source, sign in normally.
        auth_api.pin_ip_limiter.reset()
        assert signed_in_as(other_client, pin, locked) == driver
        assert signed_in_as(client, elsewhere_pin, s3) == elsewhere

        # The window passes: the lock lifts.
        with db() as conn:
            conn.execute(
                "update pin_login_failures set attempted_at = attempted_at - interval '16 minutes' "
                "where school_id = %s", (locked,),
            )
        assert signed_in_as(client, pin, locked) == driver


# --- the driver code: lookup, normalisation, regeneration ---------------------------------


def test_the_lookup_answers_id_and_name_for_any_spelling_of_the_code(client, schools):
    from app.core.security import RELAYED_CODE_ALPHABET

    school = schools[0]
    # A code with a 0 and a 1 in it, so the look-alike letters are exercised.
    import secrets

    middle = "".join(secrets.choice(RELAYED_CODE_ALPHABET) for _ in range(6))
    code = f"0{middle}1"
    with db() as conn:
        conn.execute("update live_schools set driver_code = %s where id = %s", (code, school))
    row = school_row(school)
    typed_forms = [
        code,
        code.lower(),
        f"{code[:4]}-{code[4:]}",
        f" {code[:4].lower()} {code[4:].lower()} ",
        "O" + code[1:-1] + "I",
        "o" + code[1:-1].lower() + "l",
    ]
    for typed in typed_forms:
        resp = client.get(f"/api/auth/schools/{typed}")
        assert resp.status_code == 200, (typed, resp.text)
        assert resp.json() == {"schoolId": school, "name": row["name"]}

    for unknown in (row["code"], "GFA-001", "ZZZZZZZZ", "SHORT", f"{code}X"):
        assert client.get(f"/api/auth/schools/{unknown}").status_code == 404


def test_regenerating_the_code_keeps_bound_phones_and_kills_the_old_link(client, schools):
    school = schools[1]
    before = school_row(school)["driver_code"]
    pin = driver_pin()
    driver = new_driver(client, school, pin, "regen")
    session = sign_in(client, pin, school).json()["token"]

    # Director only (DL7): a coordinator is refused and nothing changes.
    coordinator = client.post(
        "/api/accounts/school/driver-code/regenerate",
        headers=staff(client, COORDINATOR_A, SCHOOL_A_ID),
    )
    assert coordinator.status_code == 403

    try:
        resp = client.post(
            "/api/accounts/school/driver-code/regenerate",
            headers=staff(client, DIRECTOR_A, school),
        )
        assert resp.status_code == 200, resp.text
        after = resp.json()["driverCode"]
        assert after != before and school_row(school)["driver_code"] == after
        assert client.get(f"/api/auth/schools/{before}").status_code == 404
        assert client.get(f"/api/auth/schools/{after}").json()["schoolId"] == school
        # A phone already set up stores the school id: it keeps signing in,
        # and a live session is untouched.
        assert signed_in_as(client, pin, school) == driver
        assert client.get("/api/auth/me", headers=bearer(session)).status_code == 200
        with db() as conn:
            (audit,) = conn.execute(
                "select resource_type, resource_id, detail from live_admin_audit "
                "where action = 'driver-code-regenerated' and school_id = %s", (school,),
            ).fetchall()
        assert audit["resource_type"] == "school" and str(audit["resource_id"]) == school
        assert before not in str(audit["detail"]) and after not in str(audit["detail"])
    finally:
        with db() as conn:
            conn.execute(
                "delete from live_admin_audit where action = 'driver-code-regenerated' "
                "and school_id = %s", (school,),
            )


def test_the_failure_rows_carry_no_more_than_school_source_and_time(client, schools):
    school = schools[0]
    sign_in(client, driver_pin(), school)
    with db() as conn:
        columns = [
            r["column_name"]
            for r in conn.execute(
                "select column_name from information_schema.columns "
                "where table_name = 'pin_login_failures' order by ordinal_position"
            ).fetchall()
        ]
        latest = conn.execute(
            "select source, attempted_at from pin_login_failures where school_id = %s "
            "order by attempted_at desc limit 1", (school,),
        ).fetchone()
    assert columns == ["school_id", "source", "attempted_at"]
    assert latest["source"] == "testclient"
    assert latest["attempted_at"] > dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1)
