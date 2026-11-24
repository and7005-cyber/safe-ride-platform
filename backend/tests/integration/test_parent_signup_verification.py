"""Sign-up verification gating on the live stack (hardening U8; AE4, AE6, AE7).

A new parent reaches a child only after proving the inbox from their OWN
signed-in session: sign-up links nothing, raises no office incident and
leaves the Parents page slot "Awaiting signup"; the office-side student sync
skips the unverified account; verification runs the linking rule and its
incident. Accounts made before Release 1b (``email_verification_required``
false) are grandfathered. An office email change makes the account unverified
again and ends everything bound to the old address, with the links intact.

Drives the API container (the RLS-bound runtime role) through its own routes;
the local simulated transport's dev routes stand in for the inbox. Two
throwaway schools via conftest.temp_school (students, incidents and audit
rows swept on exit); every account is fresh and purged in a finally. The
``parent-email-changed`` audit rows are deleted explicitly: the 013/016
double-apply rehearsals validate existing rows against older CHECK lists.
"""

import os
import uuid

import httpx
import psycopg
import pytest
from psycopg.rows import dict_row

from conftest import (
    API_URL,
    DIRECTOR_A,
    DSN,
    login,
    purge_accounts,
    school_headers,
    signup_unverified_parent,
    temp_school,
    verification_token_from,
    verify_parent,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="needs the local stack; set RUN_INTEGRATION=1",
)

PASSWORD = "VerifyPass1!"


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=API_URL, timeout=20) as c:
        yield c


@pytest.fixture(scope="module")
def schools():
    with temp_school("IT Verify School A") as school_a, \
            temp_school("IT Verify School B") as school_b:
        yield {"a": school_a, "b": school_b}


@pytest.fixture(scope="module")
def staff(schools):
    base = login(DIRECTOR_A)
    return {key: school_headers(base, school_id) for key, school_id in schools.items()}


def db():
    return psycopg.connect(DSN, autocommit=True, row_factory=dict_row)


def fresh_email(tag: str) -> str:
    return f"it-verify-{tag}-{uuid.uuid4().hex[:8]}@saferide.test"


def create_student(client, headers, name, email, email2=None) -> str:
    payload = {
        "name": name, "grade": "G2", "parent_name": "IT Verify Parent",
        "parent_phone": "+254711000002", "parent_email": email,
    }
    if email2:
        payload["parent2_name"] = "IT Verify Parent Two"
        payload["parent2_email"] = email2
    resp = client.post("/api/students", json=payload, headers=headers)
    assert resp.status_code == 200, resp.text
    return str(resp.json()["id"])


def links_of(parent_id: str) -> list[dict]:
    with db() as conn:
        return conn.execute(
            "select student_id::text, school_id::text, status from live_parent_students "
            "where parent_id = %s order by student_id",
            (parent_id,),
        ).fetchall()


def incidents_naming(school_id: str, email: str) -> list[dict]:
    with db() as conn:
        return conn.execute(
            "select description from live_incidents where school_id = %s "
            "and description like %s",
            (school_id, f"%{email}%"),
        ).fetchall()


def parents_page(client, headers) -> list[dict]:
    resp = client.get("/api/accounts/parents", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def rows_for(page: list[dict], email: str) -> list[dict]:
    return [p for p in page if (p.get("email") or "").lower() == email.lower()]


def me(client, headers) -> dict:
    resp = client.get("/api/auth/me", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


# AE4 -----------------------------------------------------------------------------


def test_signup_links_nothing_until_the_owner_verifies(client, schools, staff):
    marker = uuid.uuid4().hex[:6]
    email = fresh_email("ae4")
    kid = f"IT Verify Kid {marker}"
    parent = None
    try:
        create_student(client, staff["a"], kid, email)
        parent = signup_unverified_parent(client, email, PASSWORD, f"IT Verify {marker}")

        # Account and token exist; nothing else happened.
        with db() as conn:
            account = conn.execute(
                "select email_verification_required, email_verified_at from app_users "
                "where id = %s",
                (parent["id"],),
            ).fetchone()
            tokens = conn.execute(
                "select purpose, used_at from password_reset_tokens where user_id = %s",
                (parent["id"],),
            ).fetchall()
        assert account["email_verification_required"] is True
        assert account["email_verified_at"] is None
        assert [(t["purpose"], t["used_at"]) for t in tokens] == [("verify", None)]
        assert links_of(parent["id"]) == []
        assert incidents_naming(schools["a"], email) == []
        assert me(client, parent["headers"])["emailVerified"] is False
        assert client.get(
            "/api/parent-portal/children", headers=parent["headers"]
        ).json() == []
        # The Parents page: no registered row, the slot still awaiting signup.
        page = rows_for(parents_page(client, staff["a"]), email)
        assert [p["status"] for p in page] == ["pending"]
        assert page[0]["students"] == [kid]

        # Verifying from the owner's session links the child and tells the office.
        verify_parent(client, parent)
        assert [l["status"] for l in links_of(parent["id"])] == ["accepted"]
        (incident,) = incidents_naming(schools["a"], email)
        assert "auto-linked" in incident["description"]
        assert me(client, parent["headers"])["emailVerified"] is True
        names = {c["name"] for c in client.get(
            "/api/parent-portal/children", headers=parent["headers"]).json()}
        assert names == {kid}
        page = rows_for(parents_page(client, staff["a"]), email)
        assert [p["status"] for p in page] == ["registered"]
    finally:
        purge_accounts(parent and parent["id"])


def test_a_token_without_a_session_or_from_another_account_links_nothing(client, schools, staff):
    marker = uuid.uuid4().hex[:6]
    email = fresh_email("owner")
    other_email = fresh_email("other")
    owner = other = None
    try:
        create_student(client, staff["a"], f"IT Verify Owned {marker}", email)
        owner = signup_unverified_parent(client, email, PASSWORD, f"IT Owner {marker}")
        other = signup_unverified_parent(client, other_email, PASSWORD, f"IT Other {marker}")

        signed_out = client.post("/api/auth/verify-email", json={"token": owner["verify_token"]})
        assert signed_out.status_code == 400
        assert signed_out.json()["detail"] == {
            "code": "sign-in-required",
            "message": "Sign in to the account you are verifying",
        }
        wrong = client.post(
            "/api/auth/verify-email", json={"token": owner["verify_token"]},
            headers=other["headers"],
        )
        assert wrong.status_code == 400
        assert wrong.json()["detail"]["code"] == "wrong-account"
        assert wrong.json()["detail"]["message"] == "Sign in to the account you are verifying"
        assert links_of(owner["id"]) == [] and links_of(other["id"]) == []
        assert incidents_naming(schools["a"], email) == []
        assert me(client, other["headers"])["emailVerified"] is False

        # The token was left working for its owner.
        verify_parent(client, owner)
        assert len(links_of(owner["id"])) == 1
    finally:
        purge_accounts(owner and owner["id"], other and other["id"])


# AE6 ------------------------------------------------------------------------------


def seeded_parent_email() -> str:
    """The demo seed's parent: a pre-1b account with linked children, so
    grandfathered (D1). Read from the database rather than spelled out here —
    the seed's address belongs to a real person and stays out of new source."""
    with db() as conn:
        row = conn.execute(
            "select u.email from app_users u"
            " where not u.email_verification_required"
            " and exists (select 1 from live_parent_students ps where ps.parent_id = u.id)"
            " order by u.created_at, u.email limit 1"
        ).fetchone()
    assert row, "the demo seed's parent is missing: run scripts/reset-local-db.sh"
    return row["email"]


def test_a_grandfathered_account_signs_in_and_sees_children_with_no_verification_state(client):
    seeded = seeded_parent_email()
    with db() as conn:
        account = conn.execute(
            "select email_verification_required from app_users where email = %s",
            (seeded,),
        ).fetchone()
    assert account["email_verification_required"] is False
    headers = login(seeded)
    assert me(client, headers)["emailVerified"] is True
    children = client.get("/api/parent-portal/children", headers=headers)
    assert children.status_code == 200 and len(children.json()) > 0


# AE7 ------------------------------------------------------------------------------


def test_a_verified_account_matching_two_schools_gets_two_pending_cards(client, schools, staff):
    marker = uuid.uuid4().hex[:6]
    email = fresh_email("ae7")
    parent = None
    try:
        create_student(client, staff["a"], f"IT Verify TwoA {marker}", email)
        create_student(client, staff["b"], f"IT Verify TwoB {marker}", email)
        parent = signup_unverified_parent(client, email, PASSWORD, f"IT Two {marker}")
        assert links_of(parent["id"]) == []
        verify_parent(client, parent)
        assert sorted(l["status"] for l in links_of(parent["id"])) == ["pending", "pending"]
        cards = client.get("/api/parent-portal/pending", headers=parent["headers"]).json()
        assert sorted(c["schoolId"] for c in cards) == sorted(schools.values())
        assert client.get("/api/parent-portal/children", headers=parent["headers"]).json() == []
    finally:
        purge_accounts(parent and parent["id"])


# The office-side sync ---------------------------------------------------------------


def test_the_office_sync_skips_an_unverified_account_until_it_verifies(client, schools, staff):
    marker = uuid.uuid4().hex[:6]
    email = fresh_email("sync")
    parent = None
    try:
        parent = signup_unverified_parent(client, email, PASSWORD, f"IT Sync {marker}")
        # The office types the unverified account's address into a slot.
        first = create_student(client, staff["a"], f"IT Verify SyncOne {marker}", "x@saferide.test")
        resp = client.put(
            f"/api/students/{first}", headers=staff["a"],
            json={"name": f"IT Verify SyncOne {marker}", "grade": "G2",
                  "parent_name": "IT Verify Parent", "parent_phone": "+254711000002",
                  "parent_email": email},
        )
        assert resp.status_code == 200, resp.text
        assert links_of(parent["id"]) == []
        assert rows_for(parents_page(client, staff["a"]), email)[0]["status"] == "pending"

        # Verification links it; the next office write links a second child.
        verify_parent(client, parent)
        assert [l["student_id"] for l in links_of(parent["id"])] == [first]
        second = create_student(client, staff["a"], f"IT Verify SyncTwo {marker}", email)
        assert sorted(l["student_id"] for l in links_of(parent["id"])) == sorted([first, second])
        assert {l["status"] for l in links_of(parent["id"])} == {"accepted"}
    finally:
        purge_accounts(parent and parent["id"])


def test_two_unverified_signups_for_parent_one_and_two_link_nothing(client, schools, staff):
    marker = uuid.uuid4().hex[:6]
    p1_email, p2_email = fresh_email("p1"), fresh_email("p2")
    p1 = p2 = None
    try:
        p1 = signup_unverified_parent(client, p1_email, PASSWORD, f"IT P1 {marker}")
        p2 = signup_unverified_parent(client, p2_email, PASSWORD, f"IT P2 {marker}")
        create_student(client, staff["a"], f"IT Verify Both {marker}", p1_email, p2_email)
        assert links_of(p1["id"]) == [] and links_of(p2["id"]) == []
        page = parents_page(client, staff["a"])
        assert [p["status"] for p in rows_for(page, p1_email)] == ["pending"]
        assert [p["status"] for p in rows_for(page, p2_email)] == ["pending"]
        assert incidents_naming(schools["a"], p1_email) == []
        assert incidents_naming(schools["a"], p2_email) == []
    finally:
        purge_accounts(p1 and p1["id"], p2 and p2["id"])


# The office changes a parent account's email ----------------------------------------


def test_an_office_email_change_restarts_verification_and_keeps_the_links(client, schools, staff):
    from app.core.security import hash_session_token

    marker = uuid.uuid4().hex[:6]
    old_email = fresh_email("before")
    new_email = fresh_email("after")
    kid = f"IT Verify Change {marker}"
    endpoint = f"https://push.saferide.test/it-{uuid.uuid4().hex[:10]}"
    parent = None
    try:
        student_id = create_student(client, staff["a"], kid, old_email)
        parent = signup_unverified_parent(client, old_email, PASSWORD, f"IT Change {marker}")
        verify_parent(client, parent)
        assert client.post(
            "/api/push/subscribe", headers=parent["headers"],
            json={"endpoint": endpoint, "p256dh": "k", "auth": "a"},
        ).status_code == 200
        # An outstanding reset link bound to the old address.
        with db() as conn:
            conn.execute(
                "insert into password_reset_tokens (user_id, token_hash, purpose, expires_at) "
                "values (%s, %s, 'reset', now() + interval '30 minutes')",
                (parent["id"], f"it-verify-reset-{marker}"),
            )

        changed = client.put(
            f"/api/accounts/parents/{parent['id']}", headers=staff["a"],
            json={"full_name": f"IT Change {marker}", "email": new_email, "phone": None},
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["email"] == new_email
        assert "previous_email" not in changed.json()

        with db() as conn:
            account = conn.execute(
                "select email_verification_required, email_verified_at from app_users "
                "where id = %s",
                (parent["id"],),
            ).fetchone()
            live_tokens = conn.execute(
                "select purpose, token_hash from password_reset_tokens "
                "where user_id = %s and used_at is null",
                (parent["id"],),
            ).fetchall()
            push_rows = conn.execute(
                "select count(*) as n from live_push_subscriptions where user_id = %s",
                (parent["id"],),
            ).fetchone()["n"]
            audit = conn.execute(
                "select detail from live_admin_audit where action = 'parent-email-changed' "
                "and resource_id = %s",
                (parent["id"],),
            ).fetchall()
        assert account["email_verification_required"] is True
        assert account["email_verified_at"] is None
        # Every old token voided; exactly the new verification token is live,
        # and it is the one the mail to the new address carries.
        assert [t["purpose"] for t in live_tokens] == ["verify"]
        assert live_tokens[0]["token_hash"] == hash_session_token(verification_token_from(client))
        assert push_rows == 0
        assert client.get("/api/auth/me", headers=parent["headers"]).status_code == 401
        # The links are untouched.
        assert [(l["student_id"], l["status"]) for l in links_of(parent["id"])] == [
            (student_id, "accepted")
        ]
        # One audit row, counts only — no address anywhere in it.
        (row,) = audit
        detail = row["detail"]
        assert detail["sessions_revoked"] >= 1
        assert detail["push_registrations_removed"] == 1
        assert detail["tokens_voided"] >= 1
        assert old_email not in str(detail) and new_email not in str(detail)

        # Signing in with the new address shows the verify state again.
        relog = client.post("/api/auth/login", json={"email": new_email, "password": PASSWORD})
        assert relog.status_code == 200
        assert relog.json()["user"]["emailVerified"] is False
    finally:
        with db() as conn:
            conn.execute("delete from live_push_subscriptions where endpoint = %s", (endpoint,))
            if parent:
                conn.execute(
                    "delete from live_admin_audit where action = 'parent-email-changed' "
                    "and resource_id = %s",
                    (parent["id"],),
                )
        purge_accounts(parent and parent["id"])
