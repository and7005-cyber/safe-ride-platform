"""The provider surface end-to-end (U10).

Runs the real ``create_app()`` in-process (TestClient) against the local
database. Covers the plan's scenarios: the two-step login (AE19) with the
voided-token distinct code, pre-auth token hygiene (expiry, single use,
password-change void, never a bearer), the enrolment flow and its allowlist,
step-up freshness, account lifecycle (AE24/AE17), step-in/step-out/supersede/
4-hour expiry with audit (AE29/AE26), the stepped-in director surface writing
provider-kind audit rows (AE9/R24), school health semantics, and school
creation with generated codes (AE15 provider branch).

State discipline: the seeded provider ends the module UNENROLLED with its
original ``totp_salt`` (module guard snapshot/restore); every school, account,
session, support session, pre-auth token and audit row a test creates is
removed in a finally block. Parallel suites share this database, so every
assertion is scoped to ids created here.
"""

import base64
import datetime as dt
import os
import re
import uuid

import psycopg
import pytest
from psycopg.rows import dict_row

from conftest import (
    DIRECTOR_A,
    DSN,
    PROVIDER,
    SCHOOL_A_ID,
    SCHOOL_B_ID,
    TEST_PASSWORD,
    purge_accounts,
    temp_school,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="needs the local stack; set RUN_INTEGRATION=1",
)

SEED_SALT = "5eedab1e5a17c0ffee00000000000001"


@pytest.fixture(scope="module")
def client(in_process_db):
    from fastapi.testclient import TestClient

    from app.main import create_app

    return TestClient(create_app())


@pytest.fixture(autouse=True)
def _reset_limiters():
    """The code-step and login budgets are module-global and this suite's
    volume (many uncached logins, deliberate wrong codes) would trip them."""
    from app.api import auth as auth_api
    from app.api import provider as provider_api

    auth_api.totp_ip_limiter.reset()
    auth_api.login_ip_limiter.reset()
    auth_api.login_account_limiter.reset()
    auth_api.change_password_account_limiter.reset()
    provider_api.stepup_account_limiter.reset()
    yield


def db():
    return psycopg.connect(DSN, autocommit=True, row_factory=dict_row)


def user_id_of(email: str) -> str:
    with db() as conn:
        return str(
            conn.execute(
                "select id from app_users where email = %s", (email,)
            ).fetchone()["id"]
        )


def hdr(token: str, school: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if school is not None:
        headers["X-School-Id"] = school
    return headers


def _pepper() -> str:
    from app.core.config import get_settings

    return get_settings().totp_pepper


def provider_row() -> dict:
    with db() as conn:
        return conn.execute(
            "select totp_salt, totp_pepper_key, totp_enrolled_at, totp_last_step, "
            "removed_at from provider_accounts where user_id = %s",
            (user_id_of(PROVIDER),),
        ).fetchone()


def enrol_via_sql(user_id: str | None = None) -> None:
    """Arrange 'enrolled' directly — the API enrolment flow has its own test."""
    from app.core.totp import pepper_key

    with db() as conn:
        conn.execute(
            "update provider_accounts set totp_enrolled_at = now(), "
            "totp_pepper_key = %s, totp_last_step = null where user_id = %s",
            (pepper_key(_pepper()), user_id or user_id_of(PROVIDER)),
        )


def code_now(salt: str | None = None) -> str:
    from app.core.totp import current_step, derive_secret, totp_code

    salt = salt or provider_row()["totp_salt"]
    return totp_code(derive_secret(_pepper(), salt), current_step())


def wrong_code(salt: str | None = None) -> str:
    """A code outside the whole acceptance window — never accidentally right."""
    from app.core.totp import current_step, derive_secret, totp_code

    salt = salt or provider_row()["totp_salt"]
    secret = derive_secret(_pepper(), salt)
    step = current_step()
    valid = {totp_code(secret, s) for s in range(step - 2, step + 3)}
    for i in range(10):
        candidate = str(i) * 6  # 000000, 111111, …
        if candidate not in valid:
            return candidate
    raise AssertionError("could not find a wrong code")


def clear_replay_guard() -> None:
    """Forget the last accepted step so a test can present "a fresh code"
    inside the same 30-second window the login just used (the guard would —
    correctly — refuse a same-step reuse otherwise)."""
    with db() as conn:
        conn.execute(
            "update provider_accounts set totp_last_step = null where user_id = %s",
            (user_id_of(PROVIDER),),
        )


def login_provider(client, password: str = TEST_PASSWORD) -> dict:
    resp = client.post(
        "/api/auth/login", json={"email": PROVIDER, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def provider_session(client) -> str:
    """A full two-step sign-in for the (already enrolled) seeded provider.
    Clears ``totp_last_step`` first so back-to-back logins inside one
    30-second step never trip the replay guard."""
    with db() as conn:
        conn.execute(
            "update provider_accounts set totp_last_step = null where user_id = %s",
            (user_id_of(PROVIDER),),
        )
    body = login_provider(client)
    assert body.get("totpRequired") is True
    resp = client.post(
        "/api/auth/totp", json={"token": body["preauth"], "code": code_now()}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


def age_session_totp(token: str, minutes: int) -> None:
    from app.core.security import hash_session_token

    with db() as conn:
        conn.execute(
            "update auth_sessions set totp_verified_at = now() - make_interval(mins => %s) "
            "where token_hash = %s",
            (minutes, hash_session_token(token)),
        )


def purge_school(school_id: str | None) -> None:
    """Sweep a school this suite created through the API (empty of fleet/roster
    rows by construction): audit, memberships, then the row itself."""
    if not school_id:
        return
    with db() as conn:
        conn.execute(
            "delete from live_admin_audit where school_id = %s", (school_id,)
        )
        conn.execute(
            "delete from school_memberships where school_id = %s", (school_id,)
        )
        conn.execute("delete from live_schools where id = %s", (school_id,))


def purge_support_sessions_for_school(school_id: str) -> None:
    with db() as conn:
        conn.execute(
            "update auth_sessions set support_session_id = null "
            "where support_session_id in "
            "(select id from provider_support_sessions where school_id = %s)",
            (school_id,),
        )
        conn.execute(
            "delete from provider_support_sessions where school_id = %s",
            (school_id,),
        )


@pytest.fixture(scope="module", autouse=True)
def seeded_provider_guard():
    """Snapshot the seeded provider row, start the module deterministically
    UNENROLLED, and afterwards restore the row exactly and sweep every
    session, support session, pre-auth token and audit row the suite added."""
    provider_id = user_id_of(PROVIDER)
    with db() as conn:
        snapshot = conn.execute(
            "select totp_salt, totp_pepper_key, totp_enrolled_at, totp_last_step "
            "from provider_accounts where user_id = %s",
            (provider_id,),
        ).fetchone()
        session_ids = [
            str(r["id"])
            for r in conn.execute(
                "select id from auth_sessions where user_id = %s", (provider_id,)
            ).fetchall()
        ]
        support_ids = [
            str(r["id"])
            for r in conn.execute(
                "select id from provider_support_sessions where provider_user_id = %s",
                (provider_id,),
            ).fetchall()
        ]
        started = conn.execute("select now() as t").fetchone()["t"]
        conn.execute(
            "update provider_accounts set totp_enrolled_at = null, "
            "totp_last_step = null, totp_pepper_key = null where user_id = %s",
            (provider_id,),
        )
    yield
    with db() as conn:
        conn.execute(
            "update provider_accounts set totp_salt = %s, totp_pepper_key = %s, "
            "totp_enrolled_at = %s, totp_last_step = %s, removed_at = null "
            "where user_id = %s",
            (
                snapshot["totp_salt"], snapshot["totp_pepper_key"],
                snapshot["totp_enrolled_at"], snapshot["totp_last_step"],
                provider_id,
            ),
        )
        conn.execute(
            "delete from auth_preauth_tokens where user_id = %s", (provider_id,)
        )
        conn.execute(
            "update auth_sessions set support_session_id = null "
            "where support_session_id in "
            "(select id from provider_support_sessions "
            " where provider_user_id = %s and not (id = any(%s::uuid[])))",
            (provider_id, support_ids),
        )
        conn.execute(
            "delete from provider_support_sessions "
            "where provider_user_id = %s and not (id = any(%s::uuid[]))",
            (provider_id, support_ids),
        )
        conn.execute(
            "delete from auth_sessions "
            "where user_id = %s and not (id = any(%s::uuid[]))",
            (provider_id, session_ids),
        )
        conn.execute(
            "delete from live_admin_audit "
            "where actor_id = %s and created_at >= %s",
            (provider_id, started),
        )


# --- AE19: the two-step login -------------------------------------------------


def test_password_alone_yields_a_preauth_token_never_a_session(client):
    enrol_via_sql()
    try:
        body = login_provider(client)
        assert body.get("totpRequired") is True
        assert "token" not in body and "user" not in body
        preauth = body["preauth"]

        # The pre-auth token is NOT a bearer session, anywhere.
        assert client.get("/api/auth/me", headers=hdr(preauth)).status_code == 401
        assert (
            client.get("/api/provider/schools", headers=hdr(preauth)).status_code
            == 401
        )
    finally:
        with db() as conn:
            conn.execute(
                "delete from auth_preauth_tokens where user_id = %s",
                (user_id_of(PROVIDER),),
            )


def test_wrong_codes_then_voided_token_then_fresh_login_succeeds(client):
    from app.core.security import hash_session_token

    enrol_via_sql()
    session_token = None
    try:
        preauth = login_provider(client)["preauth"]
        bad = wrong_code()

        for attempt in range(4):
            resp = client.post(
                "/api/auth/totp", json={"token": preauth, "code": bad}
            )
            assert resp.status_code == 401, resp.text
            assert resp.json()["detail"] == "Invalid code"  # plain wrong-code

        fifth = client.post("/api/auth/totp", json={"token": preauth, "code": bad})
        assert fifth.status_code == 401
        assert fifth.json()["detail"]["code"] == "preauth-voided"  # distinct

        # Voided means voided: even the RIGHT code is refused now.
        dead = client.post(
            "/api/auth/totp", json={"token": preauth, "code": code_now()}
        )
        assert dead.status_code == 401
        assert dead.json()["detail"]["code"] == "preauth-voided"

        # The next attempt starts over at the password — and then works.
        session_token = provider_session(client)
        me = client.get("/api/auth/me", headers=hdr(session_token))
        assert me.status_code == 200
        assert me.json()["provider"]["totpEnrolled"] is True
        assert me.json()["provider"]["totpVerifiedAt"] is not None
        with db() as conn:
            stamped = conn.execute(
                "select totp_verified_at from auth_sessions where token_hash = %s",
                (hash_session_token(session_token),),
            ).fetchone()["totp_verified_at"]
        assert stamped is not None
    finally:
        with db() as conn:
            conn.execute(
                "delete from auth_preauth_tokens where user_id = %s",
                (user_id_of(PROVIDER),),
            )


def test_preauth_token_expires_and_is_single_use(client):
    enrol_via_sql()
    try:
        # Expiry: five minutes, aged in SQL.
        preauth = login_provider(client)["preauth"]
        from app.core.security import hash_session_token

        with db() as conn:
            conn.execute(
                "update auth_preauth_tokens set expires_at = now() - interval '1 minute' "
                "where token_hash = %s",
                (hash_session_token(preauth),),
            )
        resp = client.post(
            "/api/auth/totp", json={"token": preauth, "code": code_now()}
        )
        assert resp.status_code == 401
        assert "expired" in resp.json()["detail"].lower()

        # Single use: a consumed token never issues a second session.
        with db() as conn:
            conn.execute(
                "update provider_accounts set totp_last_step = null where user_id = %s",
                (user_id_of(PROVIDER),),
            )
        second = login_provider(client)["preauth"]
        ok = client.post(
            "/api/auth/totp", json={"token": second, "code": code_now()}
        )
        assert ok.status_code == 200
        with db() as conn:
            conn.execute(
                "update provider_accounts set totp_last_step = null where user_id = %s",
                (user_id_of(PROVIDER),),
            )
        replay = client.post(
            "/api/auth/totp", json={"token": second, "code": code_now()}
        )
        assert replay.status_code == 401
    finally:
        with db() as conn:
            conn.execute(
                "delete from auth_preauth_tokens where user_id = %s",
                (user_id_of(PROVIDER),),
            )


def test_password_change_voids_outstanding_preauth_tokens(client):
    enrol_via_sql()
    changed = False
    session = None
    try:
        session = provider_session(client)
        pending = login_provider(client)["preauth"]

        resp = client.post(
            "/api/auth/change-password", headers=hdr(session),
            json={"currentPassword": TEST_PASSWORD, "newPassword": "TempChange1!"},
        )
        assert resp.status_code == 200, resp.text
        changed = True

        with db() as conn:
            conn.execute(
                "update provider_accounts set totp_last_step = null where user_id = %s",
                (user_id_of(PROVIDER),),
            )
        resp = client.post(
            "/api/auth/totp", json={"token": pending, "code": code_now()}
        )
        assert resp.status_code == 401  # voided by the password change (U8 seam)
    finally:
        if changed and session:
            restore = client.post(
                "/api/auth/change-password", headers=hdr(session),
                json={"currentPassword": "TempChange1!", "newPassword": TEST_PASSWORD},
            )
            assert restore.status_code == 200, restore.text
        with db() as conn:
            conn.execute(
                "delete from auth_preauth_tokens where user_id = %s",
                (user_id_of(PROVIDER),),
            )


# --- Enrolment: the bootstrap flow (AE19 enrolment branch) --------------------


def test_enrolment_flow_lifts_the_allowlist_and_reenrol_is_409(client):
    provider_id = user_id_of(PROVIDER)
    with db() as conn:  # deterministic start: unenrolled, seed salt
        conn.execute(
            "update provider_accounts set totp_enrolled_at = null, "
            "totp_last_step = null, totp_pepper_key = null, totp_salt = %s "
            "where user_id = %s",
            (SEED_SALT, provider_id),
        )
    try:
        body = login_provider(client)
        assert body.get("totpEnrolmentRequired") is True
        assert "token" not in body

        # Accepted WITHOUT a code — but the session is enrolment-locked.
        resp = client.post("/api/auth/totp", json={"token": body["preauth"]})
        assert resp.status_code == 200, resp.text
        token = resp.json()["token"]

        for call in (
            lambda: client.get("/api/provider/schools", headers=hdr(token)),
            lambda: client.get("/api/provider/accounts", headers=hdr(token)),
            lambda: client.get("/api/students", headers=hdr(token, SCHOOL_A_ID)),
            lambda: client.post("/api/provider/step-out", headers=hdr(token)),
        ):
            locked = call()
            assert locked.status_code == 409
            assert locked.json()["detail"]["code"] == "totp-enrolment-required"

        me = client.get("/api/auth/me", headers=hdr(token))
        assert me.status_code == 200
        assert me.json()["provider"]["totpEnrolled"] is False

        # Enrol: fresh salt, secret + URI revealed exactly once.
        enrol = client.post("/api/provider/totp/enrol", headers=hdr(token))
        assert enrol.status_code == 200, enrol.text
        secret_b32 = enrol.json()["secret"]
        assert enrol.json()["uri"].startswith("otpauth://totp/SafeRide:")
        assert secret_b32 in enrol.json()["uri"]
        row = provider_row()
        assert row["totp_salt"] != SEED_SALT  # regenerated
        assert row["totp_pepper_key"] is not None

        secret = base64.b32decode(secret_b32 + "=" * (-len(secret_b32) % 8))
        from app.core.totp import current_step, totp_code

        bad = client.post(
            "/api/provider/totp/confirm", headers=hdr(token),
            json={"code": wrong_code()},
        )
        assert bad.status_code == 401

        confirm = client.post(
            "/api/provider/totp/confirm", headers=hdr(token),
            json={"code": totp_code(secret, current_step())},
        )
        assert confirm.status_code == 200, confirm.text

        # The restriction lifts on the SAME session, code-verified.
        assert client.get("/api/provider/schools", headers=hdr(token)).status_code == 200
        me = client.get("/api/auth/me", headers=hdr(token)).json()
        assert me["provider"]["totpEnrolled"] is True
        assert me["provider"]["totpVerifiedAt"] is not None

        # Enrolled: enrol and confirm both answer 409 now.
        assert client.post("/api/provider/totp/enrol", headers=hdr(token)).status_code == 409
        assert client.post(
            "/api/provider/totp/confirm", headers=hdr(token),
            json={"code": totp_code(secret, current_step() + 1)},
        ).status_code == 409
    finally:
        with db() as conn:
            conn.execute(
                "update provider_accounts set totp_enrolled_at = null, "
                "totp_last_step = null, totp_pepper_key = null, totp_salt = %s "
                "where user_id = %s",
                (SEED_SALT, provider_id),
            )
            conn.execute(
                "delete from auth_preauth_tokens where user_id = %s", (provider_id,)
            )


# --- Step-up freshness (15 minutes) -------------------------------------------


def test_stale_code_requires_step_up_and_a_fresh_code_restarts_the_clock(client):
    from app.core.security import hash_session_token

    enrol_via_sql()
    token = provider_session(client)
    support_id = None
    try:
        age_session_totp(token, 20)
        me = client.get("/api/auth/me", headers=hdr(token)).json()
        stale = dt.datetime.fromisoformat(me["provider"]["totpVerifiedAt"])
        assert dt.datetime.now(dt.timezone.utc) - stale > dt.timedelta(minutes=15)

        resp = client.post(
            "/api/provider/step-in", headers=hdr(token),
            json={"schoolId": SCHOOL_A_ID, "reason": "IT U10 stale clock"},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"]["code"] == "totp-step-up-required"

        # Account creation rides the same gate (AE step-up scenario).
        resp = client.post(
            "/api/provider/accounts", headers=hdr(token),
            json={"email": "it-u10-never-created@test.local"},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"]["code"] == "totp-step-up-required"

        # A fresh code both authorises the action and restarts the clock.
        clear_replay_guard()  # the login's code step is still the current one
        resp = client.post(
            "/api/provider/step-in", headers=hdr(token),
            json={
                "schoolId": SCHOOL_A_ID,
                "reason": "IT U10 step-up",
                "code": code_now(),
            },
        )
        assert resp.status_code == 200, resp.text
        support_id = resp.json()["supportSessionId"]
        with db() as conn:
            verified = conn.execute(
                "select totp_verified_at from auth_sessions where token_hash = %s",
                (hash_session_token(token),),
            ).fetchone()["totp_verified_at"]
        assert dt.datetime.now(dt.timezone.utc) - verified < dt.timedelta(minutes=1)

        assert client.post("/api/provider/step-out", headers=hdr(token)).status_code == 200
    finally:
        if support_id:
            with db() as conn:
                conn.execute(
                    "update auth_sessions set support_session_id = null "
                    "where support_session_id = %s", (support_id,)
                )
                conn.execute(
                    "delete from provider_support_sessions where id = %s",
                    (support_id,),
                )


# --- AE24 / AE17: account lifecycle -------------------------------------------


def test_account_create_remove_last_provider_lock_and_dead_peer_session(client):
    enrol_via_sql()
    token = provider_session(client)
    peer_email = f"it-u10-peer-{uuid.uuid4().hex[:8]}@kuumbai.test"
    peer_id = None
    try:
        # Existing identity → 409 (providers never piggyback).
        clash = client.post(
            "/api/provider/accounts", headers=hdr(token),
            json={"email": DIRECTOR_A, "fullName": "Never"},
        )
        assert clash.status_code == 409

        created = client.post(
            "/api/provider/accounts", headers=hdr(token),
            json={"email": peer_email, "fullName": "IT U10 Peer"},
        )
        assert created.status_code == 200, created.text
        body = created.json()
        peer_id = body["userId"]
        temp_password = body["temporaryPassword"]
        assert len(temp_password) >= 12

        listing = client.get("/api/provider/accounts", headers=hdr(token)).json()
        mine = [a for a in listing if a["userId"] == peer_id]
        assert len(mine) == 1
        assert mine[0]["totpEnrolled"] is False and mine[0]["createdAt"]

        with db() as conn:
            audit = conn.execute(
                "select actor_kind, school_id, detail::text as detail "
                "from live_admin_audit "
                "where action = 'provider-account-created' and resource_id = %s",
                (peer_id,),
            ).fetchall()
        assert len(audit) == 1
        assert audit[0]["actor_kind"] == "provider"
        assert audit[0]["school_id"] is None  # provider-global: no school
        assert temp_password not in audit[0]["detail"]
        assert peer_email not in audit[0]["detail"]  # ids only, never emails

        # The peer signs in (two-step; unenrolled → restricted session).
        start = client.post(
            "/api/auth/login", json={"email": peer_email, "password": temp_password}
        )
        assert start.status_code == 200, start.text
        assert start.json().get("totpEnrolmentRequired") is True
        peer_session = client.post(
            "/api/auth/totp", json={"token": start.json()["preauth"]}
        )
        assert peer_session.status_code == 200
        peer_token = peer_session.json()["token"]
        assert client.get("/api/auth/me", headers=hdr(peer_token)).status_code == 200

        # AE17: removal ends the peer's signed-in session on the next request.
        removed = client.request(
            "DELETE", f"/api/provider/accounts/{peer_id}", headers=hdr(token)
        )
        assert removed.status_code == 204, removed.text
        assert client.get("/api/auth/me", headers=hdr(peer_token)).status_code == 401
        with db() as conn:
            row = conn.execute(
                "select removed_at from provider_accounts where user_id = %s",
                (peer_id,),
            ).fetchone()
            assert row["removed_at"] is not None
            audit = conn.execute(
                "select count(*) as n from live_admin_audit "
                "where action = 'provider-account-removed' and resource_id = %s",
                (peer_id,),
            ).fetchone()
            assert audit["n"] == 1

        # AE24: the last active provider cannot be removed.
        last = client.request(
            "DELETE",
            f"/api/provider/accounts/{user_id_of(PROVIDER)}",
            headers=hdr(token),
        )
        assert last.status_code == 409
        assert "at least one provider" in last.json()["detail"]
        assert provider_row()["removed_at"] is None

        # A removed target answers 404, not a second removal.
        again = client.request(
            "DELETE", f"/api/provider/accounts/{peer_id}", headers=hdr(token)
        )
        assert again.status_code == 404
    finally:
        if peer_id:
            with db() as conn:
                conn.execute(
                    "delete from live_admin_audit where resource_id = %s",
                    (str(peer_id),),
                )
        purge_accounts(peer_id)


def test_peer_totp_reset_forces_reenrolment_with_a_new_salt(client):
    enrol_via_sql()
    token = provider_session(client)
    peer_email = f"it-u10-reset-{uuid.uuid4().hex[:8]}@kuumbai.test"
    peer_id = None
    try:
        created = client.post(
            "/api/provider/accounts", headers=hdr(token),
            json={"email": peer_email, "fullName": "IT U10 Resettee"},
        )
        assert created.status_code == 200, created.text
        peer_id = created.json()["userId"]
        temp_password = created.json()["temporaryPassword"]
        enrol_via_sql(peer_id)  # pretend the peer completed enrolment
        with db() as conn:
            before = conn.execute(
                "select totp_salt from provider_accounts where user_id = %s",
                (peer_id,),
            ).fetchone()["totp_salt"]

        resp = client.post(
            f"/api/provider/accounts/{peer_id}/reset-totp", headers=hdr(token)
        )
        assert resp.status_code == 200, resp.text

        with db() as conn:
            row = conn.execute(
                "select totp_salt, totp_enrolled_at, totp_last_step, totp_pepper_key "
                "from provider_accounts where user_id = %s",
                (peer_id,),
            ).fetchone()
        assert row["totp_enrolled_at"] is None and row["totp_last_step"] is None
        assert row["totp_salt"] != before  # regenerated: the old secret is dead
        assert row["totp_pepper_key"] is not None
        with db() as conn:
            audit = conn.execute(
                "select count(*) as n from live_admin_audit "
                "where action = 'provider-totp-reset' and resource_id = %s",
                (peer_id,),
            ).fetchone()
        assert audit["n"] == 1

        # Their next login lands in the enrolment flow.
        start = client.post(
            "/api/auth/login", json={"email": peer_email, "password": temp_password}
        )
        assert start.status_code == 200
        assert start.json().get("totpEnrolmentRequired") is True
    finally:
        if peer_id:
            with db() as conn:
                conn.execute(
                    "delete from live_admin_audit where resource_id = %s",
                    (str(peer_id),),
                )
        purge_accounts(peer_id)


# --- AE29 / AE26: step-in lifecycle and audit ---------------------------------


def test_step_in_validations_lifecycle_and_audit(client):
    from app.core.security import hash_session_token

    enrol_via_sql()
    token = provider_session(client)
    created_support: list[str] = []
    try:
        no_reason = client.post(
            "/api/provider/step-in", headers=hdr(token),
            json={"schoolId": SCHOOL_A_ID},
        )
        assert no_reason.status_code == 400
        long_reason = client.post(
            "/api/provider/step-in", headers=hdr(token),
            json={"schoolId": SCHOOL_A_ID, "reason": "x" * 600},
        )
        assert long_reason.status_code == 400
        ghost = client.post(
            "/api/provider/step-in", headers=hdr(token),
            json={"schoolId": str(uuid.uuid4()), "reason": "IT U10 ghost"},
        )
        assert ghost.status_code == 404

        first = client.post(
            "/api/provider/step-in", headers=hdr(token),
            json={"schoolId": SCHOOL_A_ID, "reason": "IT U10 first"},
        )
        assert first.status_code == 200, first.text
        first_id = first.json()["supportSessionId"]
        created_support.append(first_id)
        assert first.json()["school"]["id"] == SCHOOL_A_ID
        assert first.json()["school"]["code"]

        # /me shows the live step-in (banner data).
        me = client.get("/api/auth/me", headers=hdr(token)).json()
        assert me["supportSession"]["id"] == first_id
        assert me["supportSession"]["schoolId"] == SCHOOL_A_ID
        assert me["supportSession"]["reason"] == "IT U10 first"

        # AE26: step-in with NO writes still left its audit row.
        with db() as conn:
            row = conn.execute(
                "select actor_kind, school_id, support_session_id, "
                "detail->>'reason' as reason from live_admin_audit "
                "where action = 'provider-step-in' and support_session_id = %s",
                (first_id,),
            ).fetchone()
        assert row is not None
        assert row["actor_kind"] == "provider"
        assert str(row["school_id"]) == SCHOOL_A_ID
        assert row["reason"] == "IT U10 first"
        with db() as conn:
            provenance = conn.execute(
                "select ip, user_agent, auth_session_id "
                "from provider_support_sessions where id = %s",
                (first_id,),
            ).fetchone()
        assert provenance["ip"] and provenance["user_agent"]
        assert provenance["auth_session_id"] is not None

        # The provider-side audit reader sees it, unmasked, by session filter.
        reader = client.get(
            f"/api/provider/audit?support_session_id={first_id}",
            headers=hdr(token),
        )
        assert reader.status_code == 200
        entries = reader.json()
        assert [e["action"] for e in entries] == ["provider-step-in"]
        assert entries[0]["actorName"] == "Kaya Provider"  # real name (R25)
        assert entries[0]["actorKind"] == "provider"

        # A second step-in supersedes the first.
        second = client.post(
            "/api/provider/step-in", headers=hdr(token),
            json={"schoolId": SCHOOL_B_ID, "reason": "IT U10 second"},
        )
        assert second.status_code == 200, second.text
        second_id = second.json()["supportSessionId"]
        created_support.append(second_id)
        with db() as conn:
            first_row = conn.execute(
                "select ended_at, end_cause from provider_support_sessions where id = %s",
                (first_id,),
            ).fetchone()
        assert first_row["end_cause"] == "superseded"
        assert first_row["ended_at"] is not None
        me = client.get("/api/auth/me", headers=hdr(token)).json()
        assert me["supportSession"]["id"] == second_id

        # Step-out ends it with its own cause and audits.
        out = client.post("/api/provider/step-out", headers=hdr(token))
        assert out.status_code == 200
        with db() as conn:
            second_row = conn.execute(
                "select ended_at, end_cause from provider_support_sessions where id = %s",
                (second_id,),
            ).fetchone()
            out_audit = conn.execute(
                "select count(*) as n from live_admin_audit "
                "where action = 'provider-step-out' and support_session_id = %s",
                (second_id,),
            ).fetchone()
        assert second_row["end_cause"] == "step-out"
        assert out_audit["n"] == 1
        assert client.get("/api/auth/me", headers=hdr(token)).json()["supportSession"] is None

        # Logout ends a live step-in with cause 'logout'.
        third = client.post(
            "/api/provider/step-in", headers=hdr(token),
            json={"schoolId": SCHOOL_A_ID, "reason": "IT U10 logout"},
        )
        assert third.status_code == 200
        third_id = third.json()["supportSessionId"]
        created_support.append(third_id)
        assert client.post("/api/auth/logout", headers=hdr(token)).status_code == 200
        with db() as conn:
            third_row = conn.execute(
                "select end_cause from provider_support_sessions where id = %s",
                (third_id,),
            ).fetchone()
        assert third_row["end_cause"] == "logout"

        # Four-hour hard lifetime: settled lazily on the next provider request.
        token = provider_session(client)
        fourth = client.post(
            "/api/provider/step-in", headers=hdr(token),
            json={"schoolId": SCHOOL_A_ID, "reason": "IT U10 expiry"},
        )
        assert fourth.status_code == 200
        fourth_id = fourth.json()["supportSessionId"]
        created_support.append(fourth_id)
        with db() as conn:
            conn.execute(
                "update provider_support_sessions set started_at = now() - interval '5 hours' "
                "where id = %s",
                (fourth_id,),
            )
        assert client.get("/api/provider/schools", headers=hdr(token)).status_code == 200
        with db() as conn:
            fourth_row = conn.execute(
                "select started_at, ended_at, end_cause "
                "from provider_support_sessions where id = %s",
                (fourth_id,),
            ).fetchone()
        assert fourth_row["end_cause"] == "expired"
        assert fourth_row["ended_at"] == fourth_row["started_at"] + dt.timedelta(hours=4)
    finally:
        with db() as conn:
            for support_id in created_support:
                conn.execute(
                    "update auth_sessions set support_session_id = null "
                    "where support_session_id = %s", (support_id,)
                )
                conn.execute(
                    "delete from live_admin_audit where support_session_id = %s",
                    (support_id,),
                )
                conn.execute(
                    "delete from provider_support_sessions where id = %s",
                    (support_id,),
                )


# --- AE9 / R24: the school surface needs a step-in; health carries counts -----


def test_provider_school_access_gated_by_step_in_and_health_has_no_names(client):
    enrol_via_sql()
    token = provider_session(client)
    with temp_school("IT U10 Step School") as school_id:
        try:
            # Without a step-in the school surface is forbidden…
            assert client.get(
                "/api/students", headers=hdr(token, school_id)
            ).status_code == 403

            stepped = client.post(
                "/api/provider/step-in", headers=hdr(token),
                json={"schoolId": school_id, "reason": "IT U10 surface"},
            )
            assert stepped.status_code == 200, stepped.text

            # …with one, the full director surface at THAT school works.
            assert client.get(
                "/api/students", headers=hdr(token, school_id)
            ).status_code == 200
            bus = client.post(
                "/api/fleet/buses", headers=hdr(token, school_id),
                json={"name": "IT U10 Provider Bus"},
            )
            assert bus.status_code == 200, bus.text
            bus_id = bus.json()["id"]
            deleted = client.delete(  # delete is director-only: scope qualifies
                f"/api/fleet/buses/{bus_id}", headers=hdr(token, school_id)
            )
            assert deleted.status_code == 200, deleted.text

            with db() as conn:
                kinds = conn.execute(
                    "select action, actor_kind, support_session_id "
                    "from live_admin_audit where school_id = %s "
                    "and action in ('bus-created', 'bus-deleted') order by created_at",
                    (school_id,),
                ).fetchall()
            assert [k["action"] for k in kinds] == ["bus-created", "bus-deleted"]
            assert all(k["actor_kind"] == "provider" for k in kinds)
            assert all(k["support_session_id"] is not None for k in kinds)

            # Health rows: counts and state only — never a roster (R24).
            health = client.get("/api/provider/schools", headers=hdr(token))
            assert health.status_code == 200
            mine = [r for r in health.json() if r["schoolId"] == school_id]
            assert len(mine) == 1
            assert set(mine[0]) == {
                "schoolId", "name", "code", "setupState", "students",
                "buses", "drivers", "runsToday", "lastStaffWrite",
            }
            assert mine[0]["students"] == 0 and mine[0]["setupState"] == "setup"

            assert client.post(
                "/api/provider/step-out", headers=hdr(token)
            ).status_code == 200
        finally:
            purge_support_sessions_for_school(school_id)


def test_health_latest_staff_write_ignores_pin_map_and_provider_rows(client):
    enrol_via_sql()
    token = provider_session(client)
    with temp_school("IT U10 Health School") as school_id:
        def stage(action: str, kind: str, hours_ago: int):
            with db() as conn:
                return conn.execute(
                    """
                    insert into live_admin_audit
                        (actor_name, actor_email, actor_kind, action, school_id,
                         detail, created_at)
                    values ('IT Stage', 'it-stage@test.local', %s, %s, %s,
                            '{}'::jsonb, now() - make_interval(hours => %s))
                    returning created_at
                    """,
                    (kind, action, school_id, hours_ago),
                ).fetchone()["created_at"]

        staff_write = stage("student-created", "staff", 3)
        stage("pin-map-viewed", "staff", 2)  # later, but never counts
        stage("bus-created", "provider", 1)  # later, but provider-kind

        health = client.get("/api/provider/schools", headers=hdr(token))
        assert health.status_code == 200
        mine = [r for r in health.json() if r["schoolId"] == school_id]
        assert len(mine) == 1
        # The later pin-map view and the provider write are both ignored:
        # "latest staff write" is the 3-hour-old student-created row.
        assert dt.datetime.fromisoformat(mine[0]["lastStaffWrite"]) == staff_write


# --- AE15 (provider branch): school creation ----------------------------------


def test_create_school_generates_codes_and_provisions_the_first_director(client):
    enrol_via_sql()
    token = provider_session(client)
    age_session_totp(token, 20)  # DECISION: creation is NOT step-up-gated
    director_email = f"it-u10-director-{uuid.uuid4().hex[:8]}@test.local"
    first_school = second_school = director_id = None
    try:
        assert client.post(
            "/api/provider/schools", headers=hdr(token),
            json={"name": "", "directorEmail": director_email},
        ).status_code == 400
        assert client.post(
            "/api/provider/schools", headers=hdr(token),
            json={"name": "Msingi Bora Academy", "directorEmail": "not-an-email"},
        ).status_code == 400
        assert client.post(
            "/api/provider/schools", headers=hdr(token),
            json={
                "name": "Msingi Bora Academy",
                "directorEmail": director_email,
                "morningBell": "25:99",
            },
        ).status_code == 400

        created = client.post(
            "/api/provider/schools", headers=hdr(token),
            json={
                "name": "Msingi Bora Academy",
                "directorEmail": director_email,
                "directorName": "First Director",
                "lat": -1.3, "lng": 36.8, "morningBell": "07:30",
            },
        )
        assert created.status_code == 200, created.text
        body = created.json()
        first_school = body["school"]["id"]
        assert re.fullmatch(r"MSI-\d{3}", body["school"]["code"])
        assert body["director"]["status"] == "created"
        director_id = body["director"]["userId"]
        temp_password = body["director"]["temporaryPassword"]
        assert len(temp_password) >= 12

        with db() as conn:
            membership = conn.execute(
                "select role, state from school_memberships "
                "where user_id = %s and school_id = %s and removed_at is null",
                (director_id, first_school),
            ).fetchall()
            assert [(m["role"], m["state"]) for m in membership] == [
                ("director", "active")
            ]
            audit = conn.execute(
                "select action, actor_kind from live_admin_audit "
                "where school_id = %s order by created_at",
                (first_school,),
            ).fetchall()
            assert [a["action"] for a in audit] == ["school-created", "staff-created"]
            assert all(a["actor_kind"] == "provider" for a in audit)
            secrets_check = conn.execute(
                "select detail::text as detail from live_admin_audit "
                "where school_id = %s",
                (first_school,),
            ).fetchall()
            assert all(temp_password not in r["detail"] for r in secrets_check)

        # An existing NON-provider email → the offered contract, no password;
        # and the second code is unique.
        second = client.post(
            "/api/provider/schools", headers=hdr(token),
            json={"name": "Msingi Bora Academy", "directorEmail": DIRECTOR_A},
        )
        assert second.status_code == 200, second.text
        second_school = second.json()["school"]["id"]
        assert second.json()["director"]["status"] == "offered"
        assert "temporaryPassword" not in second.json()["director"]
        assert re.fullmatch(r"MSI-\d{3}", second.json()["school"]["code"])
        assert second.json()["school"]["code"] != body["school"]["code"]
        with db() as conn:
            offer = conn.execute(
                "select state from school_memberships "
                "where user_id = %s and school_id = %s and removed_at is null",
                (user_id_of(DIRECTOR_A), second_school),
            ).fetchall()
        assert [o["state"] for o in offer] == ["offered"]
    finally:
        purge_school(first_school)
        purge_school(second_school)
        purge_accounts(director_id)
