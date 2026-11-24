"""Reset by email and the emailed tokens on the live stack (hardening U7; AE1, AE2).

Drives the API container through its own routes, with the local simulated
transport standing in for SES: every mail that carries a link records it for
the local-only dev routes (``/api/auth/dev/last-reset-link`` and
``/api/auth/dev/last-verification-link``), and the token rows are read from
the database to prove what was — and was not — written.

Budget-aware: the forgot-password IP net is five a minute and does not scale
on the local stack, so this file asks it twice; the existing-account and cap
journeys reach their reset links through sign-up on an existing address
instead. One test runs the real app in-process to swap in a failing
transport.

Every account is a fresh ``@saferide.test`` address, purged in a finally.
"""

import logging
import os
import uuid

import httpx
import psycopg
import pytest
from psycopg.rows import dict_row

from conftest import (
    API_URL,
    DSN,
    purge_accounts,
    reset_token_from,
    signup_unverified_parent,
    verification_token_from,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="needs the local stack; set RUN_INTEGRATION=1",
)

PASSWORD = "MailPass1!"
FORGOT_MESSAGE = "If an account exists for this address, a reset link has been sent"


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=API_URL, timeout=20) as c:
        yield c


def db():
    return psycopg.connect(DSN, autocommit=True, row_factory=dict_row)


def fresh_email(tag: str) -> str:
    return f"it-mail-{tag}-{uuid.uuid4().hex[:8]}@saferide.test"


def tokens_of(user_id: str) -> list[dict]:
    with db() as conn:
        return conn.execute(
            "select token_hash, purpose, used_at, expires_at - created_at as ttl "
            "from password_reset_tokens where user_id = %s order by created_at",
            (user_id,),
        ).fetchall()


def token_row(raw: str) -> dict | None:
    from app.core.security import hash_session_token

    with db() as conn:
        return conn.execute(
            "select user_id, purpose, used_at from password_reset_tokens where token_hash = %s",
            (hash_session_token(raw),),
        ).fetchone()


def last_reset_link(client) -> str | None:
    return client.get("/api/auth/dev/last-reset-link").json()["link"]


def test_forgot_sends_one_single_use_link_and_reset_signs_in(client):
    """AE1, plus: a reset token never verifies and is left unused; a reset
    ends every session, drops the push rows, verifies the address and
    answers a session; the next sign-in re-attaches a browser's push
    subscription while the revoked session cannot."""
    email = fresh_email("ae1")
    parent = signup_unverified_parent(client, email, PASSWORD, "IT Mail AE1")
    try:
        endpoint = f"https://push.saferide.test/it-{uuid.uuid4().hex[:10]}"
        subscribed = client.post(
            "/api/push/subscribe", headers=parent["headers"],
            json={"endpoint": endpoint, "p256dh": "k", "auth": "a"},
        )
        assert subscribed.status_code == 200, subscribed.text

        answer = client.post("/api/auth/forgot-password", json={"email": email})
        assert answer.status_code == 200, answer.text
        assert answer.json()["message"] == FORGOT_MESSAGE

        rows = tokens_of(parent["id"])
        assert [r["purpose"] for r in rows] == ["verify", "reset"]
        reset_row = rows[-1]
        assert reset_row["ttl"].total_seconds() == 30 * 60
        raw = reset_token_from(client)
        assert token_row(raw)["purpose"] == "reset"

        # A reset token presented to verify is refused and left unused.
        wrong = client.post(
            "/api/auth/verify-email", json={"token": raw}, headers=parent["headers"]
        )
        assert wrong.status_code == 400
        assert wrong.json()["detail"]["code"] == "link-invalid"
        assert token_row(raw)["used_at"] is None

        reset = client.post("/api/auth/reset-password", json={"token": raw, "password": "MailPass2!"})
        assert reset.status_code == 200, reset.text
        body = reset.json()
        assert body["ok"] is True and body["token"]
        assert body["user"]["id"] == parent["id"]
        assert body["user"]["emailVerified"] is True

        # Single use.
        again = client.post("/api/auth/reset-password", json={"token": raw, "password": "MailPass3!"})
        assert again.status_code == 400

        # The old session is gone; the push row with it; the address verified.
        assert client.get("/api/auth/me", headers=parent["headers"]).status_code == 401
        with db() as conn:
            assert conn.execute(
                "select count(*) as n from live_push_subscriptions where user_id = %s",
                (parent["id"],),
            ).fetchone()["n"] == 0
            assert conn.execute(
                "select email_verified_at from app_users where id = %s", (parent["id"],)
            ).fetchone()["email_verified_at"] is not None
        new_headers = {"Authorization": f"Bearer {body['token']}"}
        me = client.get("/api/auth/me", headers=new_headers)
        assert me.status_code == 200 and me.json()["emailVerified"] is True

        # A device whose session was revoked cannot re-attach; the browser
        # signed in again re-posts its subscription (lib/push.ts) and the row
        # comes back.
        assert client.post(
            "/api/push/subscribe", headers=parent["headers"],
            json={"endpoint": endpoint, "p256dh": "k", "auth": "a"},
        ).status_code == 401
        assert client.post(
            "/api/push/subscribe", headers=new_headers,
            json={"endpoint": endpoint, "p256dh": "k", "auth": "a"},
        ).status_code == 200
        with db() as conn:
            assert conn.execute(
                "select user_id from live_push_subscriptions where endpoint = %s", (endpoint,)
            ).fetchone()["user_id"] == uuid.UUID(parent["id"])
            conn.execute("delete from live_push_subscriptions where endpoint = %s", (endpoint,))
    finally:
        purge_accounts(parent["id"])


def test_forgot_for_an_unknown_address_sends_nothing_and_answers_the_same(client):
    """AE2."""
    before = last_reset_link(client)
    email = fresh_email("ae2-nobody")
    answer = client.post("/api/auth/forgot-password", json={"email": email})
    assert answer.status_code == 200
    assert answer.json()["message"] == FORGOT_MESSAGE
    assert last_reset_link(client) == before
    with db() as conn:
        assert conn.execute(
            "select count(*) as n from app_users where lower(email) = lower(%s)", (email,)
        ).fetchone()["n"] == 0


def test_a_verify_token_cannot_reset_a_password(client):
    email = fresh_email("purpose")
    parent = signup_unverified_parent(client, email, PASSWORD, "IT Mail Purpose")
    try:
        raw = parent["verify_token"]
        refused = client.post("/api/auth/reset-password", json={"token": raw, "password": "Hijack123"})
        assert refused.status_code == 400
        row = token_row(raw)
        assert row["purpose"] == "verify" and row["used_at"] is None
        # The password is unchanged and the link still verifies its owner.
        assert client.post(
            "/api/auth/login", json={"email": email, "password": PASSWORD}
        ).status_code == 200
        assert client.post(
            "/api/auth/verify-email", json={"token": raw}, headers=parent["headers"]
        ).status_code == 200
    finally:
        purge_accounts(parent["id"])


def test_signup_on_an_existing_address_creates_nothing_and_mails_a_reset_link(client):
    email = fresh_email("existing")
    control_email = fresh_email("control")
    parent = signup_unverified_parent(client, email, PASSWORD, "IT Mail Existing")
    try:
        fresh = client.post(
            "/api/auth/signup",
            json={"email": control_email, "password": PASSWORD,
                  "full_name": "IT Mail Control", "role": "parent"},
        )
        again = client.post(
            "/api/auth/signup",
            json={"email": email.upper(), "password": "Other123!",
                  "full_name": "IT Mail Squatter", "role": "parent"},
        )
        # The identical answer, and no session either way.
        assert again.status_code == fresh.status_code == 200
        assert again.json() == fresh.json()
        assert "token" not in again.json()
        with db() as conn:
            assert conn.execute(
                "select count(*) as n from app_users where lower(email) = lower(%s)", (email,)
            ).fetchone()["n"] == 1
            assert conn.execute(
                "select full_name from app_users where id = %s", (parent["id"],)
            ).fetchone()["full_name"] == "IT Mail Existing"
        # The existing-account mail carries a working reset link for the
        # real account.
        raw = reset_token_from(client)
        assert token_row(raw)["user_id"] == uuid.UUID(parent["id"])
        assert client.post(
            "/api/auth/reset-password", json={"token": raw, "password": "MailPass9!"}
        ).status_code == 200
    finally:
        purge_accounts(parent["id"])
        with db() as conn:
            conn.execute("delete from app_users where email = %s", (control_email,))


def test_the_per_recipient_cap_stops_the_fourth_mail_in_an_hour(client):
    email = fresh_email("cap")
    parent = signup_unverified_parent(client, email, PASSWORD, "IT Mail Cap")  # 1: verify
    try:
        resent = client.post("/api/auth/resend-verification", headers=parent["headers"])  # 2
        assert resent.status_code == 200, resent.text
        signup_again = {"email": email, "password": PASSWORD, "full_name": "x", "role": "parent"}
        assert client.post("/api/auth/signup", json=signup_again).status_code == 200  # 3: reset
        third = last_reset_link(client)
        assert len(tokens_of(parent["id"])) == 3
        capped = client.post("/api/auth/signup", json=signup_again)  # 4: over the cap
        assert capped.status_code == 200
        assert capped.json()["ok"] is True
        assert len(tokens_of(parent["id"])) == 3
        assert last_reset_link(client) == third
        # The resend invalidated the sign-up's link; the resent one is live.
        rows = tokens_of(parent["id"])
        verify_rows = [r for r in rows if r["purpose"] == "verify"]
        assert verify_rows[0]["used_at"] is not None and verify_rows[1]["used_at"] is None
        assert verify_rows[1]["ttl"].total_seconds() == 24 * 3600
    finally:
        purge_accounts(parent["id"])


def test_a_provider_reset_by_email_answers_no_session(client):
    email = fresh_email("provider")
    parent = signup_unverified_parent(client, email, PASSWORD, "IT Mail Provider")
    try:
        with db() as conn:
            conn.execute(
                "insert into provider_accounts (user_id, totp_salt) values (%s, %s)",
                (parent["id"], uuid.uuid4().hex),
            )
        signup_again = {"email": email, "password": PASSWORD, "full_name": "x", "role": "parent"}
        assert client.post("/api/auth/signup", json=signup_again).status_code == 200
        raw = reset_token_from(client)
        reset = client.post("/api/auth/reset-password", json={"token": raw, "password": "MailPass5!"})
        assert reset.status_code == 200, reset.text
        # The second factor still stands between a mailbox and a session.
        assert reset.json() == {"ok": True}
    finally:
        with db() as conn:
            conn.execute("delete from provider_accounts where user_id = %s", (parent["id"],))
        purge_accounts(parent["id"])


def test_a_failing_transport_keeps_the_token_logs_and_answers_ok(in_process_db, monkeypatch, caplog):
    """In-process (the real app on the local database) so a raising transport
    can stand in for SES: the token row stays, one ``mail-send-failed`` line
    without the address, and the route still answers the neutral message."""
    from fastapi.testclient import TestClient

    from app.api import auth as auth_api
    from app.main import create_app
    from app.services.mail_service import MailService

    class Broken:
        def send(self, message):
            raise RuntimeError(f"SES unavailable for {message.to}")

    local = TestClient(create_app())
    email = fresh_email("broken")
    parent = signup_unverified_parent(local, email, PASSWORD, "IT Mail Broken")
    try:
        auth_api.forgot_ip_limiter.reset()
        auth_api.forgot_account_limiter.reset()
        monkeypatch.setattr(auth_api.service, "_mail", MailService(Broken()))
        with caplog.at_level(logging.ERROR, logger="saferide.mail"):
            answer = local.post("/api/auth/forgot-password", json={"email": email})
        assert answer.status_code == 200
        assert answer.json()["message"] == FORGOT_MESSAGE
        rows = tokens_of(parent["id"])
        assert [r["purpose"] for r in rows] == ["verify", "reset"]
        assert rows[-1]["used_at"] is None
        failures = [r for r in caplog.records if r.getMessage().startswith("mail-send-failed")]
        assert len(failures) == 1
        line = failures[0].getMessage()
        assert line.startswith("mail-send-failed kind=reset request_id=")
        assert email not in line and failures[0].exc_info is None
    finally:
        purge_accounts(parent["id"])
