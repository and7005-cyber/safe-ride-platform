"""The office parent reset, Confirm and reset, and the self-service password
change (hardening U9; AE3).

Runs the real ``create_app()`` in-process (TestClient) against the local
database, like test_staff_accounts.py: the accounts router, the shared
temporary-password helper and the auth changes under test live in this
working tree. The accounts service's mail goes to a recording simulated
transport, so the notice mail is checked without SES.

Families are synthetic and throwaway (``@saferide.test`` addresses, students
inserted at the seeded schools and deleted afterwards); seeded accounts are
never reset. Every audit row written with 017's actions
(``parent-password-reset``, ``parent-email-confirmed``) is deleted in the
finally blocks: the 013/016 double-apply rehearsals validate existing rows
against older CHECK lists.
"""

import datetime as dt
import os
import uuid

import psycopg
import pytest
from psycopg.rows import dict_row

from conftest import (
    API_URL,
    COORDINATOR_A,
    DIRECTOR_A,
    DIRECTOR_B,
    DRIVER_B_PIN,
    DSN,
    SCHOOL_A_ID,
    SCHOOL_B_ID,
    TEST_PASSWORD,
    _password_hash,
    pin_login_body,
    purge_accounts,
    school_headers,
    signup_unverified_parent,
    verify_parent,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="needs the local stack; set RUN_INTEGRATION=1",
)

PARENT_PASSWORD = "ParentPass1!"
NOT_RESETTABLE = "This account cannot be reset from the Parents page"
NOTHING_TO_CONFIRM = "Nothing to confirm yet. Ask the parent to sign up with this email."
RELAYED_ALPHABET = set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")
SCHOOL_B_NAME = "IT Second School"


@pytest.fixture(scope="module")
def client(in_process_db):
    from fastapi.testclient import TestClient

    from app.main import create_app

    return TestClient(create_app())


@pytest.fixture(scope="module")
def mail():
    """The accounts service's mail, recorded (the notice is office-sent)."""
    from app.api import accounts as accounts_api
    from app.services.mail_service import MailService, SimulatedTransport

    transport = SimulatedTransport(record_links=True)
    previous = accounts_api.service._mail
    accounts_api.service._mail = MailService(transport)
    yield transport
    accounts_api.service._mail = previous


@pytest.fixture(autouse=True)
def _reset_limiters():
    """The auth budgets are module-global; this suite signs the same
    throwaway accounts in many times from one address."""
    from app.api import auth as auth_api

    for limiter in (
        auth_api.login_account_limiter,
        auth_api.login_ip_limiter,
        auth_api.change_password_account_limiter,
        auth_api.pin_ip_limiter,
        auth_api.signup_ip_limiter,
        auth_api.verify_ip_limiter,
    ):
        limiter.reset()
    yield


# --- helpers ------------------------------------------------------------------------

_tokens: dict[str, str] = {}


def db():
    return psycopg.connect(DSN, autocommit=True, row_factory=dict_row)


def tok(client, email: str) -> str:
    """One cached login per seeded staff identity."""
    if email not in _tokens:
        resp = client.post("/api/auth/login", json={"email": email, "password": TEST_PASSWORD})
        assert resp.status_code == 200, resp.text
        _tokens[email] = resp.json()["token"]
    return _tokens[email]


def staff(client, email: str, school_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {tok(client, email)}", "X-School-Id": school_id}


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def login(client, email: str, password: str):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def user_id_of(email: str) -> str:
    with db() as conn:
        return str(
            conn.execute("select id from app_users where email = %s", (email,)).fetchone()["id"]
        )


def fresh_email(tag: str) -> str:
    return f"it-u9-{tag}-{uuid.uuid4().hex[:8]}@saferide.test"


class Family:
    """A throwaway parent account with students at the seeded schools. The
    account is made in SQL (the reset's subject is the office path, not the
    sign-up), as a verified-by-default (pre-1b) parent; ``teardown`` removes
    the students, the account (sessions, links, memberships and push rows
    cascade), the incidents the linking rule raised, and the audit rows."""

    def __init__(self, tag: str):
        self.email = fresh_email(tag)
        self.students: list[str] = []
        with db() as conn:
            self.id = str(
                conn.execute(
                    "insert into app_users (email, password_hash, full_name) "
                    "values (%s, %s, %s) returning id",
                    (self.email, _password_hash(PARENT_PASSWORD), f"IT U9 {tag}"),
                ).fetchone()["id"]
            )
            conn.execute(
                "insert into app_user_roles (user_id, role) values (%s, 'parent')", (self.id,)
            )

    def child_at(self, school_id: str, *, status: str = "accepted") -> str:
        with db() as conn:
            student_id = str(
                conn.execute(
                    "insert into live_students (name, school_id, parent_name, parent_email) "
                    "values (%s, %s, 'IT U9 Parent', %s) returning id",
                    (f"IT U9 Child {uuid.uuid4().hex[:6]}", school_id, self.email),
                ).fetchone()["id"]
            )
            conn.execute(
                "insert into live_parent_students (parent_id, student_id, status, school_id) "
                "values (%s, %s, %s, %s)",
                (self.id, student_id, status, school_id),
            )
        self.students.append(student_id)
        return student_id

    def teardown(self) -> None:
        purge_family(self.id, self.email, self.students)


def slot_student(school_id: str, email: str) -> str:
    """A student whose Parent 1 slot carries ``email`` — no link."""
    with db() as conn:
        return str(
            conn.execute(
                "insert into live_students (name, school_id, parent_name, parent_email) "
                "values (%s, %s, 'IT U9 Slot Parent', %s) returning id",
                (f"IT U9 Slot Child {uuid.uuid4().hex[:6]}", school_id, email),
            ).fetchone()["id"]
        )


def purge_family(user_id: str | None, email: str | None, students: list[str]) -> None:
    with db() as conn:
        if students:
            conn.execute(
                "delete from live_students where id = any(%s::uuid[])", (students,)
            )
        if email:
            # The office incidents the linking rule raised name the address.
            conn.execute(
                "delete from live_incidents where description like %s", (f"%{email}%",)
            )
        if user_id:
            conn.execute(
                "delete from live_admin_audit where resource_id = %s", (str(user_id),)
            )
    purge_accounts(user_id)


def audit_rows(user_id: str, action: str) -> list[dict]:
    with db() as conn:
        return conn.execute(
            "select actor_id, actor_kind, school_id, detail, detail::text as detail_text "
            "from live_admin_audit where resource_id = %s and action = %s order by created_at",
            (str(user_id), action),
        ).fetchall()


def push_rows(user_id: str) -> int:
    with db() as conn:
        return conn.execute(
            "select count(*) as n from live_push_subscriptions where user_id = %s", (user_id,)
        ).fetchone()["n"]


def subscribe_push(client, token: str) -> None:
    endpoint = f"https://push.saferide.test/it-u9-{uuid.uuid4().hex[:10]}"
    resp = client.post(
        "/api/push/subscribe", headers=bearer(token),
        json={"endpoint": endpoint, "p256dh": "k", "auth": "a"},
    )
    assert resp.status_code == 200, resp.text


def reset(client, parent_id: str, staff_email: str, school_id: str):
    return client.post(
        f"/api/accounts/parents/{parent_id}/reset-password",
        headers=staff(client, staff_email, school_id),
    )


def grouped(password: str) -> str:
    """How a parent types what they heard: lower case, in groups of four."""
    return " ".join(password[i:i + 4] for i in range(0, len(password), 4)).lower()


def listed(client, staff_email: str, school_id: str, email: str) -> dict:
    rows = client.get("/api/accounts/parents", headers=staff(client, staff_email, school_id))
    assert rows.status_code == 200, rows.text
    return next(r for r in rows.json() if r["email"].lower() == email.lower())


# --- AE3: a coordinator resets a parent ------------------------------------------


def test_ae3_coordinator_reset_forced_change_and_everything_old_gone(client, mail):
    from app.core.security import create_session_token, hash_session_token
    from app.dao.auth_dao import AuthDao

    family = Family("ae3")
    try:
        family.child_at(SCHOOL_A_ID)
        first = login(client, family.email, PARENT_PASSWORD).json()["token"]
        second = login(client, family.email, PARENT_PASSWORD).json()["token"]
        subscribe_push(client, first)
        assert push_rows(family.id) == 1
        raw_reset = create_session_token()
        AuthDao().create_reset_token(family.id, hash_session_token(raw_reset))
        sent_before = len(mail.sent)

        resp = reset(client, family.id, COORDINATOR_A, SCHOOL_A_ID)
        assert resp.status_code == 200, resp.text
        assert set(resp.json()) == {"ok", "temporaryPassword"}
        temporary = resp.json()["temporaryPassword"]
        assert len(temporary) == 16 and set(temporary) <= RELAYED_ALPHABET

        # Earlier sessions, push rows, the old password and the emailed
        # reset link are all gone.
        for token in (first, second):
            assert client.get("/api/auth/me", headers=bearer(token)).status_code == 401
        assert push_rows(family.id) == 0
        assert login(client, family.email, PARENT_PASSWORD).status_code == 401
        assert client.post(
            "/api/auth/reset-password", json={"token": raw_reset, "password": "Sneaky123"}
        ).status_code == 400

        # The neutral notice went to the parent — no password, no link.
        notices = list(mail.sent)[sent_before:]
        assert [(m.kind, m.to) for m in notices] == [("office-reset", family.email)]
        assert temporary not in notices[0].text and notices[0].link is None

        # The audit row names the coordinator; counts only.
        (row,) = audit_rows(family.id, "parent-password-reset")
        assert str(row["actor_id"]) == user_id_of(COORDINATOR_A)
        assert row["actor_kind"] == "staff" and str(row["school_id"]) == SCHOOL_A_ID
        assert row["detail"] == {
            "sessions_revoked": 2, "tokens_voided": 1, "push_registrations_removed": 1,
        }
        assert temporary not in row["detail_text"] and family.email not in row["detail_text"]

        # The Parents page shows the temporary password and its expiry.
        shown = listed(client, COORDINATOR_A, SCHOOL_A_ID, family.email)
        assert shown["mustChangePassword"] is True and shown["linkAccepted"] is True
        expires = dt.datetime.fromisoformat(shown["temporaryPasswordExpiresAt"])
        left = expires - dt.datetime.now(dt.timezone.utc)
        assert dt.timedelta(hours=71) < left <= dt.timedelta(hours=72)

        # The parent signs in with it as heard (any case, grouped), is held on
        # the change screen, changes it, and the temporary password is dead.
        relog = login(client, family.email, grouped(temporary))
        assert relog.status_code == 200, relog.text
        assert relog.json()["user"]["mustChangePassword"] is True
        session = relog.json()["token"]
        held = client.get("/api/parent-portal/children", headers=bearer(session))
        assert held.status_code == 409
        assert held.json()["detail"]["code"] == "password-change-required"
        changed = client.post(
            "/api/auth/change-password", headers=bearer(session),
            json={"currentPassword": grouped(temporary), "newPassword": "ParentPass2!"},
        )
        assert changed.status_code == 200, changed.text
        assert client.get("/api/parent-portal/children", headers=bearer(session)).status_code == 200
        assert login(client, family.email, temporary).status_code == 401
        assert login(client, family.email, "ParentPass2!").status_code == 200
        shown = listed(client, COORDINATOR_A, SCHOOL_A_ID, family.email)
        assert shown["mustChangePassword"] is False
        assert shown["temporaryPasswordExpiresAt"] is None
    finally:
        family.teardown()


def test_a_parent_linked_at_two_schools_resets_from_either_and_the_latest_wins(client, mail):
    family = Family("two-schools")
    try:
        family.child_at(SCHOOL_A_ID)
        family.child_at(SCHOOL_B_ID)
        # The Shared row at A still offers the reset (an accepted link here).
        row_a = listed(client, DIRECTOR_A, SCHOOL_A_ID, family.email)
        assert row_a["shared"] is True and row_a["linkAccepted"] is True

        from_b = reset(client, family.id, DIRECTOR_B, SCHOOL_B_ID)
        assert from_b.status_code == 200, from_b.text
        first = from_b.json()["temporaryPassword"]
        from_a = reset(client, family.id, COORDINATOR_A, SCHOOL_A_ID)
        assert from_a.status_code == 200, from_a.text
        second = from_a.json()["temporaryPassword"]
        assert first != second

        # A second reset before the first is used invalidates the first.
        assert login(client, family.email, first).status_code == 401
        assert login(client, family.email, second).status_code == 200

        rows = audit_rows(family.id, "parent-password-reset")
        assert [str(r["school_id"]) for r in rows] == [SCHOOL_B_ID, SCHOOL_A_ID]
    finally:
        family.teardown()


def test_a_shared_row_reads_shared_under_row_security_and_still_resets():
    """Against the API container, which connects as the runtime role (row
    security on, 015) — the in-process client connects as the owner and sees
    every school. Another school's students are invisible to school A there,
    so Shared and the edit freeze must come from the link rows' own school
    stamp (found while building U9: both read through a student join)."""
    import httpx

    from conftest import login as container_login

    family = Family("shared-rls")
    try:
        family.child_at(SCHOOL_A_ID)
        family.child_at(SCHOOL_B_ID)
        headers = school_headers(container_login(DIRECTOR_A), SCHOOL_A_ID)
        with httpx.Client(base_url=API_URL, timeout=20) as api:
            rows = api.get("/api/accounts/parents", headers=headers)
            assert rows.status_code == 200, rows.text
            row = next(r for r in rows.json() if r["id"] == family.id)
            assert row["shared"] is True and row["linkAccepted"] is True
            edit = api.put(
                f"/api/accounts/parents/{family.id}", headers=headers,
                json={"full_name": "IT U9 Hijack", "email": family.email, "phone": None},
            )
            assert edit.status_code == 409, edit.text
            # The office reset stays available on a Shared row (DL12).
            reset_resp = api.post(
                f"/api/accounts/parents/{family.id}/reset-password", headers=headers
            )
            assert reset_resp.status_code == 200, reset_resp.text
    finally:
        family.teardown()


@pytest.mark.parametrize(
    "access",
    [
        "driver-elsewhere",
        "director-elsewhere",
        "coordinator-here",
        "offered-elsewhere",
        "provider",
    ],
)
def test_an_identity_with_any_other_access_is_refused_with_the_neutral_code(client, access):
    family = Family(f"refused-{access}")
    try:
        family.child_at(SCHOOL_A_ID)
        with db() as conn:
            if access == "provider":
                conn.execute(
                    "insert into provider_accounts (user_id, totp_salt) values (%s, %s)",
                    (family.id, uuid.uuid4().hex),
                )
            else:
                role, school, state = {
                    "driver-elsewhere": ("driver", SCHOOL_B_ID, "active"),
                    "director-elsewhere": ("director", SCHOOL_B_ID, "active"),
                    "coordinator-here": ("coordinator", SCHOOL_A_ID, "active"),
                    "offered-elsewhere": ("coordinator", SCHOOL_B_ID, "offered"),
                }[access]
                conn.execute(
                    "insert into school_memberships (user_id, school_id, role, state, accepted_at) "
                    "values (%s, %s, %s, %s, case when %s = 'active' then now() end)",
                    (family.id, school, role, state, state),
                )
        resp = reset(client, family.id, COORDINATOR_A, SCHOOL_A_ID)
        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"] == {"code": "not-resettable-here", "message": NOT_RESETTABLE}
        # Neutral: no school, no role named.
        assert SCHOOL_B_NAME not in resp.text and "driver" not in resp.text.lower()
        # Nothing changed and nothing was recorded.
        assert login(client, family.email, PARENT_PASSWORD).status_code == 200
        assert audit_rows(family.id, "parent-password-reset") == []
    finally:
        family.teardown()


def test_a_removed_membership_no_longer_blocks_the_reset(client, mail):
    family = Family("ex-driver")
    try:
        family.child_at(SCHOOL_A_ID)
        with db() as conn:
            conn.execute(
                "insert into school_memberships "
                "(user_id, school_id, role, state, accepted_at, removed_at) "
                "values (%s, %s, 'driver', 'active', now(), now())",
                (family.id, SCHOOL_B_ID),
            )
        assert reset(client, family.id, COORDINATOR_A, SCHOOL_A_ID).status_code == 200
    finally:
        family.teardown()


def test_no_accepted_link_here_is_not_found(client):
    pending = Family("pending-here")
    elsewhere = Family("only-at-b")
    try:
        pending.child_at(SCHOOL_A_ID, status="pending")
        elsewhere.child_at(SCHOOL_B_ID)
        for target in (pending.id, elsewhere.id, str(uuid.uuid4()), "not-a-uuid",
                       user_id_of(DIRECTOR_B)):
            resp = reset(client, target, COORDINATOR_A, SCHOOL_A_ID)
            assert resp.status_code == 404, (target, resp.text)
        # The pending-only row says so up front.
        assert listed(client, COORDINATOR_A, SCHOOL_A_ID, pending.email)["linkAccepted"] is False
        assert login(client, pending.email, PARENT_PASSWORD).status_code == 200
    finally:
        pending.teardown()
        elsewhere.teardown()


# --- Confirm and reset on an Awaiting-signup slot -----------------------------------


def test_confirm_and_reset_verifies_links_issues_a_password_and_audits(client, mail):
    email = fresh_email("confirm")
    students = [slot_student(SCHOOL_A_ID, email)]
    parent = None
    try:
        parent = signup_unverified_parent(client, email, PARENT_PASSWORD, "IT U9 Confirm")
        subscribe_push(client, parent["token"])
        # Unverified: the slot still reads Awaiting signup.
        slot = listed(client, COORDINATOR_A, SCHOOL_A_ID, email)
        assert slot["status"] == "pending" and slot["id"] is None
        sent_before = len(mail.sent)

        resp = client.post(
            "/api/accounts/parents/slots/confirm",
            headers=staff(client, COORDINATOR_A, SCHOOL_A_ID),
            json={"email": email.upper()},
        )
        assert resp.status_code == 200, resp.text
        temporary = resp.json()["temporaryPassword"]
        assert len(temporary) == 16 and set(temporary) <= RELAYED_ALPHABET

        with db() as conn:
            account = conn.execute(
                "select email_verified_at, must_change_password from app_users where id = %s",
                (parent["id"],),
            ).fetchone()
            links = conn.execute(
                "select status, school_id from live_parent_students where parent_id = %s",
                (parent["id"],),
            ).fetchall()
            incidents = conn.execute(
                "select count(*) as n from live_incidents where school_id = %s "
                "and description like %s",
                (SCHOOL_A_ID, f"%{email}%"),
            ).fetchone()["n"]
        assert account["email_verified_at"] is not None and account["must_change_password"]
        # The linking rule ran: one school matches, so the link is accepted,
        # with the office incident every new link raises.
        assert [(link["status"], str(link["school_id"])) for link in links] == [
            ("accepted", SCHOOL_A_ID)
        ]
        assert incidents == 1

        # The creator's password and session are dead; push rows are gone.
        assert client.get("/api/auth/me", headers=parent["headers"]).status_code == 401
        assert login(client, email, PARENT_PASSWORD).status_code == 401
        assert push_rows(parent["id"]) == 0

        notices = list(mail.sent)[sent_before:]
        assert [(m.kind, m.to) for m in notices] == [("office-reset", email)]

        (confirmed,) = audit_rows(parent["id"], "parent-email-confirmed")
        (issued,) = audit_rows(parent["id"], "parent-password-reset")
        for row in (confirmed, issued):
            assert str(row["actor_id"]) == user_id_of(COORDINATOR_A)
            assert str(row["school_id"]) == SCHOOL_A_ID
            assert temporary not in row["detail_text"] and email not in row["detail_text"]
        assert issued["detail"]["via"] == "confirm"
        assert issued["detail"]["sessions_revoked"] == 1
        assert issued["detail"]["push_registrations_removed"] == 1

        # Registered now, holding a temporary password; the parent signs in
        # with it and is held on the change screen.
        row = listed(client, COORDINATOR_A, SCHOOL_A_ID, email)
        assert row["status"] == "registered" and row["mustChangePassword"] is True
        relog = login(client, email, grouped(temporary))
        assert relog.status_code == 200, relog.text
        assert relog.json()["user"]["mustChangePassword"] is True
        assert relog.json()["user"]["emailVerified"] is True
    finally:
        purge_family(parent["id"] if parent else None, email, students)


def test_confirm_and_reset_refusals(client, mail):
    from app.core.security import verify_password

    no_account = fresh_email("no-account")
    verified_email = fresh_email("verified")
    driver_email = fresh_email("unverified-driver")
    grandfathered = Family("grandfathered")
    students = [
        slot_student(SCHOOL_A_ID, no_account),
        slot_student(SCHOOL_A_ID, verified_email),
        slot_student(SCHOOL_A_ID, driver_email),
        slot_student(SCHOOL_A_ID, grandfathered.email),
    ]
    elsewhere_email = fresh_email("slot-at-b")
    students.append(slot_student(SCHOOL_B_ID, elsewhere_email))
    created: list[str] = []
    try:
        verified = signup_unverified_parent(client, verified_email, PARENT_PASSWORD, "IT U9 V")
        created.append(verified["id"])
        verify_parent(client, verified)
        driver = signup_unverified_parent(client, driver_email, PARENT_PASSWORD, "IT U9 D")
        created.append(driver["id"])
        with db() as conn:
            conn.execute(
                "insert into school_memberships (user_id, school_id, role, state, accepted_at) "
                "values (%s, %s, 'driver', 'active', now())",
                (driver["id"], SCHOOL_B_ID),
            )
        sent_before = len(mail.sent)

        def confirm(email: str):
            return client.post(
                "/api/accounts/parents/slots/confirm",
                headers=staff(client, COORDINATOR_A, SCHOOL_A_ID),
                json={"email": email},
            )

        # Not on this school's students (a slot of school B): not found.
        assert confirm(elsewhere_email).status_code == 404
        # No account, verified, grandfathered, or one holding other access:
        # the same neutral outcome.
        for email in (no_account, verified_email, grandfathered.email, driver_email):
            resp = confirm(email)
            assert resp.status_code == 409, (email, resp.text)
            assert resp.json()["detail"] == {
                "code": "nothing-to-confirm", "message": NOTHING_TO_CONFIRM,
            }
        assert list(mail.sent)[sent_before:] == []
        with db() as conn:
            rows = conn.execute(
                "select id, password_hash, email_verified_at from app_users "
                "where id = any(%s::uuid[])",
                ([driver["id"], verified["id"], grandfathered.id],),
            ).fetchall()
            audits = conn.execute(
                "select count(*) as n from live_admin_audit where resource_id = any(%s) "
                "and action in ('parent-email-confirmed', 'parent-password-reset')",
                ([driver["id"], verified["id"], grandfathered.id],),
            ).fetchone()["n"]
        assert audits == 0
        for row in rows:
            assert verify_password(PARENT_PASSWORD, row["password_hash"])
        unverified_driver = next(r for r in rows if str(r["id"]) == driver["id"])
        assert unverified_driver["email_verified_at"] is None
    finally:
        purge_family(created[0] if created else None, verified_email, [])
        purge_family(created[1] if len(created) > 1 else None, driver_email, [])
        grandfathered.teardown()
        purge_family(None, no_account, students)


# --- self-service change -----------------------------------------------------------


def test_self_service_change_needs_the_current_password_and_keeps_this_session(client):
    family = Family("self-change")
    try:
        here = login(client, family.email, PARENT_PASSWORD).json()["token"]
        other = login(client, family.email, PARENT_PASSWORD).json()["token"]
        subscribe_push(client, here)
        me = client.get("/api/auth/me", headers=bearer(here)).json()
        assert me["signedInWithPin"] is False

        wrong = client.post(
            "/api/auth/change-password", headers=bearer(here),
            json={"currentPassword": "not-it", "newPassword": "ParentPass3!"},
        )
        assert wrong.status_code == 400
        assert client.get("/api/auth/me", headers=bearer(other)).status_code == 200

        ok = client.post(
            "/api/auth/change-password", headers=bearer(here),
            json={"currentPassword": PARENT_PASSWORD, "newPassword": "ParentPass3!"},
        )
        assert ok.status_code == 200, ok.text
        # This session stays; the other device is signed out; push is kept.
        assert client.get("/api/auth/me", headers=bearer(here)).status_code == 200
        assert client.get("/api/auth/me", headers=bearer(other)).status_code == 401
        assert push_rows(family.id) == 1
        assert login(client, family.email, "ParentPass3!").status_code == 200
    finally:
        family.teardown()


def test_a_pin_session_cannot_change_the_password(client):
    from app.core.security import hash_session_token

    # Hardening U10: the PIN signs in with the school the phone is bound to.
    by_pin = client.post("/api/auth/pin-login", json=pin_login_body(DRIVER_B_PIN, SCHOOL_B_ID))
    assert by_pin.status_code == 200, by_pin.text
    token = by_pin.json()["token"]
    try:
        assert by_pin.json()["user"]["signedInWithPin"] is True
        me = client.get("/api/auth/me", headers=bearer(token))
        assert me.status_code == 200 and me.json()["signedInWithPin"] is True
        refused = client.post(
            "/api/auth/change-password", headers=bearer(token),
            json={"currentPassword": TEST_PASSWORD, "newPassword": "Whatever12"},
        )
        assert refused.status_code == 403
        assert refused.json()["detail"]["code"] == "pin-session"
        # The session itself is untouched.
        assert client.get("/api/auth/me", headers=bearer(token)).status_code == 200
    finally:
        with db() as conn:
            conn.execute(
                "delete from auth_sessions where token_hash = %s", (hash_session_token(token),)
            )
