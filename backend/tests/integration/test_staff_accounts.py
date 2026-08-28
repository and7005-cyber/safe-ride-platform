"""Staff accounts, offers, passwords and disabling (U8).

Runs the real ``create_app()`` in-process (TestClient) against the local
database — the staff router, membership DAO and auth extensions under test
live in this working tree, not in the docker API container. Covers the plan's
scenarios: AE16 (temporary password → forced change), AE15 (offer → accept /
decline / cancel), AE4 (last-director lock, offers don't count, concurrent
removals serialised), AE23 (director resets a coordinator's password),
expired temporary passwords, the provider/disabled anti-enumeration answer,
``disable_user`` as the single disable path, the parent-only signup rule and
auto-link hygiene.

Every row a test creates is removed in a finally block (or by the
``temp_school`` sweep); seeded rows are never deleted or left modified.
"""

import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest
from psycopg.rows import dict_row

from conftest import (
    COORDINATOR_A,
    DIRECTOR_A,
    DIRECTOR_B,
    DSN,
    PROVIDER,
    SCHOOL_A_ID,
    TEST_PASSWORD,
    purge_accounts,
    temp_school,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="needs the local stack; set RUN_INTEGRATION=1",
)

DIRECTOR_A_NAME = "Dora Director"
PARENT_SIGNUP_PASSWORD = "ParentPass1!"


@pytest.fixture(scope="module")
def client(in_process_db):
    from fastapi.testclient import TestClient

    from app.main import create_app

    return TestClient(create_app())


@pytest.fixture(autouse=True)
def _reset_write_limiters():
    """The staff-create and change-password budgets are per acting account and
    module-global; reset them so this suite's own volume never trips them."""
    from app.api import auth as auth_api
    from app.api import staff as staff_api

    staff_api.staff_create_limiter.reset()
    auth_api.change_password_account_limiter.reset()
    yield


_tokens: dict[str, str] = {}


def tok(client, email: str) -> str:
    """One cached login per seeded identity (the per-account login budget)."""
    if email not in _tokens:
        resp = client.post(
            "/api/auth/login", json={"email": email, "password": TEST_PASSWORD}
        )
        assert resp.status_code == 200, resp.text
        _tokens[email] = resp.json()["token"]
    return _tokens[email]


def login(client, email: str, password: str):
    """An UNCACHED login for throwaway identities and failure assertions."""
    return client.post("/api/auth/login", json={"email": email, "password": password})


def hdr(token: str, school: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if school is not None:
        headers["X-School-Id"] = school
    return headers


def provider_tok(client) -> str:
    """U10: provider sign-in is two-step (password → pre-auth → code). This
    suite exercises the staff surface under a step-in, not the login flow
    (test_provider.py owns that), so enrolment is arranged in SQL; callers
    restore the seeded UNENROLLED state with ``restore_provider_enrolment``
    in their finally block."""
    import app.api.auth as auth_api
    from app.core.config import get_settings
    from app.core.totp import current_step, derive_secret, pepper_key, totp_code

    auth_api.totp_ip_limiter.reset()
    pepper = get_settings().totp_pepper
    with db() as conn:
        salt = conn.execute(
            """
            update provider_accounts
            set totp_enrolled_at = coalesce(totp_enrolled_at, now()),
                totp_pepper_key = %s, totp_last_step = null
            where user_id = %s
            returning totp_salt
            """,
            (pepper_key(pepper), user_id_of(PROVIDER)),
        ).fetchone()["totp_salt"]
    start = client.post(
        "/api/auth/login", json={"email": PROVIDER, "password": TEST_PASSWORD}
    )
    assert start.status_code == 200, start.text
    code = totp_code(derive_secret(pepper, salt), current_step())
    resp = client.post(
        "/api/auth/totp", json={"token": start.json()["preauth"], "code": code}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


def restore_provider_enrolment(token: str | None = None) -> None:
    """Back to the seed: unenrolled, original salt untouched; the test's own
    provider session removed so nothing lingers on the shared identity."""
    from app.core.security import hash_session_token

    with db() as conn:
        conn.execute(
            "update provider_accounts set totp_enrolled_at = null, "
            "totp_last_step = null, totp_pepper_key = null where user_id = %s",
            (user_id_of(PROVIDER),),
        )
        conn.execute(
            "delete from auth_preauth_tokens where user_id = %s",
            (user_id_of(PROVIDER),),
        )
        if token:
            conn.execute(
                "delete from auth_sessions where token_hash = %s",
                (hash_session_token(token),),
            )


def db():
    return psycopg.connect(DSN, autocommit=True, row_factory=dict_row)


def user_id_of(email: str) -> str:
    with db() as conn:
        return str(
            conn.execute(
                "select id from app_users where email = %s", (email,)
            ).fetchone()["id"]
        )


def unique_email(tag: str) -> str:
    return f"it-u8-{tag}-{uuid.uuid4().hex[:8]}@test.local"


def purge_audit(*resource_ids: str | None) -> None:
    ids = [str(r) for r in resource_ids if r]
    if not ids:
        return
    with db() as conn:
        conn.execute("delete from live_admin_audit where resource_id = any(%s)", (ids,))


def signup_parent(client, tag: str) -> tuple[str, str, str]:
    """A throwaway parent account via the real signup; (email, user_id, token)."""
    email = unique_email(tag)
    resp = client.post(
        "/api/auth/signup",
        json={
            "email": email, "password": PARENT_SIGNUP_PASSWORD,
            "full_name": f"IT U8 {tag}", "role": "parent",
        },
    )
    assert resp.status_code == 200, resp.text
    return email, str(resp.json()["user"]["id"]), resp.json()["token"]


def create_staff(client, school_id: str, email: str, role: str, full_name: str | None = None):
    return client.post(
        "/api/staff",
        headers=hdr(tok(client, DIRECTOR_A), school_id),
        json={"email": email, "fullName": full_name, "role": role},
    )


def offer_row(user_id: str, school_id: str, role: str) -> dict | None:
    with db() as conn:
        return conn.execute(
            "select id, state, offered_by, removed_at, accepted_at from school_memberships "
            "where user_id = %s and school_id = %s and role = %s "
            "order by created_at desc limit 1",
            (user_id, school_id, role),
        ).fetchone()


# --- AE16: create staff → temporary password → forced first change ------------


def test_create_staff_temporary_password_first_login_flow(client):
    email = unique_email("coord")
    created_id = None
    try:
        resp = create_staff(client, SCHOOL_A_ID, email, "coordinator", "Temp Coordinator")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "created"
        assert body["role"] == "coordinator"
        temp_password = body["temporaryPassword"]
        assert len(temp_password) >= 12
        created_id = body["userId"]

        with db() as conn:
            row = conn.execute(
                "select must_change_password, temporary_password_expires_at, "
                "password_changed_at from app_users where id = %s",
                (created_id,),
            ).fetchone()
            assert row["must_change_password"] is True
            assert row["temporary_password_expires_at"] is not None
            assert row["password_changed_at"] is None
            membership = conn.execute(
                "select role, state from school_memberships "
                "where user_id = %s and school_id = %s and removed_at is null",
                (created_id, SCHOOL_A_ID),
            ).fetchall()
            assert [(m["role"], m["state"]) for m in membership] == [("coordinator", "active")]
            # Staff authority is the membership: NO legacy app_user_roles row.
            assert conn.execute(
                "select 1 from app_user_roles where user_id = %s", (created_id,)
            ).fetchone() is None
            audits = conn.execute(
                "select action, actor_id, school_id, detail::text as detail "
                "from live_admin_audit where resource_id = %s",
                (created_id,),
            ).fetchall()
            assert [a["action"] for a in audits] == ["staff-created"]
            assert str(audits[0]["actor_id"]) == user_id_of(DIRECTOR_A)
            assert str(audits[0]["school_id"]) == SCHOOL_A_ID
            # The temporary password is revealed once in the response — never
            # in the audit detail, in any spelling.
            assert temp_password not in audits[0]["detail"]
            assert "password" not in audits[0]["detail"].lower()

        first = login(client, email, temp_password)
        assert first.status_code == 200, first.text
        assert first.json()["user"]["mustChangePassword"] is True
        token = first.json()["token"]
        second_token = login(client, email, temp_password).json()["token"]

        # Everything but me/change-password/logout answers 409 (R30).
        for call in (
            lambda: client.get("/api/students", headers=hdr(token, SCHOOL_A_ID)),
            lambda: client.get("/api/staff", headers=hdr(token, SCHOOL_A_ID)),
        ):
            resp = call()
            assert resp.status_code == 409
            assert resp.json()["detail"]["code"] == "password-change-required"
        assert client.get("/api/auth/me", headers=hdr(token)).status_code == 200

        # The change ALWAYS requires the correct current password — the forced
        # first change included.
        for wrong in ("", "definitely-not-it"):
            resp = client.post(
                "/api/auth/change-password", headers=hdr(token),
                json={"currentPassword": wrong, "newPassword": "FreshPass9"},
            )
            assert resp.status_code == 400
            assert "Current password is incorrect" in resp.text

        resp = client.post(
            "/api/auth/change-password", headers=hdr(token),
            json={"currentPassword": temp_password, "newPassword": "FreshPass9"},
        )
        assert resp.status_code == 200, resp.text

        # The console opens on the calling session; the OTHER session is gone.
        assert client.get("/api/students", headers=hdr(token, SCHOOL_A_ID)).status_code == 200
        assert client.get("/api/auth/me", headers=hdr(second_token)).status_code == 401
        # The temporary password is dead for everyone; the new one works.
        assert login(client, email, temp_password).status_code == 401
        assert login(client, email, "FreshPass9").status_code == 200
        with db() as conn:
            row = conn.execute(
                "select must_change_password, temporary_password_expires_at, "
                "password_changed_at from app_users where id = %s",
                (created_id,),
            ).fetchone()
            assert row["must_change_password"] is False
            assert row["temporary_password_expires_at"] is None
            assert row["password_changed_at"] is not None
    finally:
        purge_audit(created_id)
        purge_accounts(created_id)


def test_staff_list_members_and_offers(client):
    member_email = unique_email("listmember")
    offer_email, offer_user_id, _ = signup_parent(client, "listoffer")
    member_id = None
    offer_id = None
    try:
        member_id = create_staff(client, SCHOOL_A_ID, member_email, "coordinator").json()["userId"]
        assert create_staff(client, SCHOOL_A_ID, offer_email, "coordinator").json()["status"] == "offered"
        offer_id = str(offer_row(offer_user_id, SCHOOL_A_ID, "coordinator")["id"])

        # Coordinators may view the page too (require_staff).
        resp = client.get("/api/staff", headers=hdr(tok(client, COORDINATOR_A), SCHOOL_A_ID))
        assert resp.status_code == 200, resp.text
        data = resp.json()

        by_email = {m["email"]: m for m in data["members"]}
        assert by_email[DIRECTOR_A]["role"] == "director"
        assert by_email[COORDINATOR_A]["role"] == "coordinator"
        # Drivers are never in the staff list.
        assert "francis@saferide.test" not in by_email
        mine = by_email[member_email]
        assert mine["state"] == "active"
        assert mine["passwordSetOn"] is None  # temporary password never set by the user

        offers = {o["id"]: o for o in data["offers"]}
        assert offer_id in offers
        entry = offers[offer_id]
        assert entry["email"] == offer_email
        assert entry["role"] == "coordinator"
        assert entry["offeredBy"] == DIRECTOR_A_NAME
        assert entry["offeredAt"]
    finally:
        with db() as conn:
            conn.execute(
                "delete from school_memberships where user_id = any(%s::uuid[])",
                ([offer_user_id],),
            )
        purge_audit(member_id, offer_id)
        purge_accounts(member_id, offer_user_id)


# --- AE15: offers to existing accounts ----------------------------------------


def test_offer_existing_account_and_accept(client):
    email, parent_id, parent_token = signup_parent(client, "accept")
    offer_id = None
    try:
        with db() as conn:
            hash_before = conn.execute(
                "select password_hash from app_users where id = %s", (parent_id,)
            ).fetchone()["password_hash"]
            school = conn.execute(
                "select name, code from live_schools where id = %s", (SCHOOL_A_ID,)
            ).fetchone()

        resp = create_staff(client, SCHOOL_A_ID, email, "coordinator")
        assert resp.status_code == 200
        assert resp.json()["status"] == "offered"
        assert "temporaryPassword" not in resp.json()

        row = offer_row(parent_id, SCHOOL_A_ID, "coordinator")
        assert row["state"] == "offered"
        assert str(row["offered_by"]) == user_id_of(DIRECTOR_A)
        offer_id = str(row["id"])

        # Idempotent repeat: same row, no duplicate, single audit entry.
        assert create_staff(client, SCHOOL_A_ID, email, "coordinator").json()["status"] == "offered"
        with db() as conn:
            count = conn.execute(
                "select count(*) as n from school_memberships "
                "where user_id = %s and school_id = %s and role = 'coordinator' "
                "and removed_at is null",
                (parent_id, SCHOOL_A_ID),
            ).fetchone()["n"]
            assert count == 1
            offered_audits = conn.execute(
                "select count(*) as n from live_admin_audit "
                "where resource_id = %s and action = 'staff-role-offered'",
                (offer_id,),
            ).fetchone()["n"]
            assert offered_audits == 1
            # The offer changed no credentials.
            assert conn.execute(
                "select password_hash from app_users where id = %s", (parent_id,)
            ).fetchone()["password_hash"] == hash_before

        # /me lists the offer with school name, code, role, offerer and date.
        me = client.get("/api/auth/me", headers=hdr(parent_token)).json()
        assert [m["schoolId"] for m in me["memberships"]] == []
        offers = me["pendingOffers"]
        assert len(offers) == 1
        assert offers[0]["id"] == offer_id
        assert offers[0]["schoolName"] == school["name"]
        assert offers[0]["schoolCode"] == school["code"]
        assert offers[0]["role"] == "coordinator"
        assert offers[0]["offeredBy"] == DIRECTOR_A_NAME
        assert offers[0]["offeredAt"]

        # Someone else's offer does not exist for another caller.
        foreign = client.post(
            f"/api/auth/offers/{offer_id}/accept",
            headers=hdr(tok(client, DIRECTOR_B)),
        )
        assert foreign.status_code == 404

        accept = client.post(f"/api/auth/offers/{offer_id}/accept", headers=hdr(parent_token))
        assert accept.status_code == 200, accept.text
        me = client.get("/api/auth/me", headers=hdr(parent_token)).json()
        assert me["pendingOffers"] == []
        assert [(m["schoolId"], m["role"]) for m in me["memberships"]] == [
            (SCHOOL_A_ID, "coordinator")
        ]
        # The membership works immediately on the staff surface.
        assert client.get("/api/staff", headers=hdr(parent_token, SCHOOL_A_ID)).status_code == 200
        row = offer_row(parent_id, SCHOOL_A_ID, "coordinator")
        assert row["state"] == "active"
        assert row["accepted_at"] is not None
        with db() as conn:
            actions = [
                a["action"]
                for a in conn.execute(
                    "select action from live_admin_audit where resource_id = %s "
                    "order by created_at",
                    (offer_id,),
                ).fetchall()
            ]
        assert actions == ["staff-role-offered", "staff-offer-accepted"]

        # A second accept finds nothing offered any more.
        assert client.post(
            f"/api/auth/offers/{offer_id}/accept", headers=hdr(parent_token)
        ).status_code == 404
    finally:
        with db() as conn:
            conn.execute("delete from school_memberships where user_id = %s", (parent_id,))
        purge_audit(offer_id, parent_id)
        purge_accounts(parent_id)


def test_offer_decline_and_director_cancel(client):
    email, parent_id, parent_token = signup_parent(client, "decline")
    first_offer = None
    second_offer = None
    try:
        assert create_staff(client, SCHOOL_A_ID, email, "coordinator").json()["status"] == "offered"
        first_offer = str(offer_row(parent_id, SCHOOL_A_ID, "coordinator")["id"])

        decline = client.post(
            f"/api/auth/offers/{first_offer}/decline", headers=hdr(parent_token)
        )
        assert decline.status_code == 200
        me = client.get("/api/auth/me", headers=hdr(parent_token)).json()
        assert me["pendingOffers"] == []
        row = offer_row(parent_id, SCHOOL_A_ID, "coordinator")
        assert row["state"] == "offered" and row["removed_at"] is not None
        # Declining again finds nothing.
        assert client.post(
            f"/api/auth/offers/{first_offer}/decline", headers=hdr(parent_token)
        ).status_code == 404

        # A re-offer after a decline is a NEW offer row.
        assert create_staff(client, SCHOOL_A_ID, email, "coordinator").json()["status"] == "offered"
        second_offer = str(offer_row(parent_id, SCHOOL_A_ID, "coordinator")["id"])
        assert second_offer != first_offer

        # The director cancels it from the staff surface.
        cancel = client.delete(
            f"/api/staff/offers/{second_offer}",
            headers=hdr(tok(client, DIRECTOR_A), SCHOOL_A_ID),
        )
        assert cancel.status_code == 204
        row = offer_row(parent_id, SCHOOL_A_ID, "coordinator")
        assert row["removed_at"] is not None
        assert client.get("/api/auth/me", headers=hdr(parent_token)).json()["pendingOffers"] == []
        # Cancelling twice finds nothing.
        assert client.delete(
            f"/api/staff/offers/{second_offer}",
            headers=hdr(tok(client, DIRECTOR_A), SCHOOL_A_ID),
        ).status_code == 404

        with db() as conn:
            actions = {
                a["resource_id"]: a["action"]
                for a in conn.execute(
                    "select resource_id, action from live_admin_audit "
                    "where resource_id = any(%s) and action like 'staff-offer-%%'",
                    ([first_offer, second_offer],),
                ).fetchall()
            }
        assert actions[first_offer] == "staff-offer-declined"
        assert actions[second_offer] == "staff-offer-cancelled"
    finally:
        with db() as conn:
            conn.execute("delete from school_memberships where user_id = %s", (parent_id,))
        purge_audit(first_offer, second_offer, parent_id)
        purge_accounts(parent_id)


def test_provider_made_offers_display_as_saferide(client):
    """R25: a provider-created offer (the U10 create-school branch writes one)
    never shows the provider's name school-side — 'SafeRide' everywhere."""
    email, parent_id, parent_token = signup_parent(client, "masked")
    offer_id = None
    try:
        with db() as conn:
            offer_id = str(
                conn.execute(
                    "insert into school_memberships (user_id, school_id, role, state, offered_by) "
                    "values (%s, %s, 'coordinator', 'offered', %s) returning id",
                    (parent_id, SCHOOL_A_ID, user_id_of(PROVIDER)),
                ).fetchone()["id"]
            )
        me = client.get("/api/auth/me", headers=hdr(parent_token)).json()
        assert [o["offeredBy"] for o in me["pendingOffers"]] == ["SafeRide"]
        staff = client.get(
            "/api/staff", headers=hdr(tok(client, DIRECTOR_A), SCHOOL_A_ID)
        ).json()
        entry = next(o for o in staff["offers"] if o["id"] == offer_id)
        assert entry["offeredBy"] == "SafeRide"
    finally:
        if offer_id:
            with db() as conn:
                conn.execute("delete from school_memberships where id = %s", (offer_id,))
        purge_accounts(parent_id)


def test_offer_conflicts_and_bad_roles(client):
    # An active member with that role at this school → 409, nothing written.
    resp = create_staff(client, SCHOOL_A_ID, COORDINATOR_A, "coordinator")
    assert resp.status_code == 409
    assert "Already a member" in resp.text
    with db() as conn:
        count = conn.execute(
            "select count(*) as n from school_memberships "
            "where user_id = %s and school_id = %s and role = 'coordinator'",
            (user_id_of(COORDINATOR_A), SCHOOL_A_ID),
        ).fetchone()["n"]
        assert count == 1  # the seeded membership only
    # Staff roles only on this surface.
    assert create_staff(client, SCHOOL_A_ID, unique_email("badrole"), "driver").status_code == 400
    assert create_staff(client, SCHOOL_A_ID, unique_email("badrole"), "admin").status_code == 400
    # Coordinators cannot manage staff.
    resp = client.post(
        "/api/staff",
        headers=hdr(tok(client, COORDINATOR_A), SCHOOL_A_ID),
        json={"email": unique_email("coordattempt"), "role": "coordinator"},
    )
    assert resp.status_code == 403


# --- AE4: the last-director lock ----------------------------------------------


def test_last_director_lock(client):
    director_a_id = user_id_of(DIRECTOR_A)
    offer_email, offer_user_id, _ = signup_parent(client, "lockoffer")
    dir2_id = None
    dir3_id = None
    try:
        with temp_school("IT U8 Lock School") as school_id:
            headers = hdr(tok(client, DIRECTOR_A), school_id)

            # Sole director self-removal → 409.
            resp = client.delete(f"/api/staff/{director_a_id}", headers=headers)
            assert resp.status_code == 409
            assert "director" in resp.text.lower()

            # A pending director OFFER does not count as a director.
            assert client.post(
                "/api/staff", headers=headers,
                json={"email": offer_email, "role": "director"},
            ).json()["status"] == "offered"
            resp = client.delete(f"/api/staff/{director_a_id}", headers=headers)
            assert resp.status_code == 409

            # Removing a NON-last director works.
            dir2_id = client.post(
                "/api/staff", headers=headers,
                json={"email": unique_email("dir2"), "role": "director"},
            ).json()["userId"]
            assert client.delete(f"/api/staff/{dir2_id}", headers=headers).status_code == 204
            with db() as conn:
                assert conn.execute(
                    "select removed_at from school_memberships "
                    "where user_id = %s and school_id = %s",
                    (dir2_id, school_id),
                ).fetchone()["removed_at"] is not None

            # With a second ACTIVE director, self-removal succeeds…
            dir3_id = client.post(
                "/api/staff", headers=headers,
                json={"email": unique_email("dir3"), "role": "director"},
            ).json()["userId"]
            assert client.delete(f"/api/staff/{director_a_id}", headers=headers).status_code == 204
            # …and the school no longer exists for the removed director,
            # while their OTHER school keeps working on the same session.
            assert client.get("/api/staff", headers=headers).status_code == 404
            assert client.get(
                "/api/staff", headers=hdr(tok(client, DIRECTOR_A), SCHOOL_A_ID)
            ).status_code == 200
            with db() as conn:
                assert conn.execute(
                    "select removed_at from school_memberships "
                    "where user_id = %s and school_id = %s and role = 'director'",
                    (director_a_id, SCHOOL_A_ID),
                ).fetchone()["removed_at"] is None
    finally:
        purge_audit(director_a_id, dir2_id, dir3_id, offer_user_id)
        purge_accounts(dir2_id, dir3_id, offer_user_id)


def test_concurrent_removals_leave_at_least_one_director(client):
    director_a_id = user_id_of(DIRECTOR_A)
    director_b_id = user_id_of(DIRECTOR_B)
    try:
        with temp_school("IT U8 Race School") as school_id:
            with db() as conn:
                conn.execute(
                    "insert into school_memberships (user_id, school_id, role, state, accepted_at) "
                    "values (%s, %s, 'director', 'active', now())",
                    (director_b_id, school_id),
                )
            a_token = tok(client, DIRECTOR_A)
            b_token = tok(client, DIRECTOR_B)

            def remove(actor_token: str, target_id: str) -> int:
                return client.delete(
                    f"/api/staff/{target_id}", headers=hdr(actor_token, school_id)
                ).status_code

            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [
                    pool.submit(remove, a_token, director_b_id),
                    pool.submit(remove, b_token, director_a_id),
                ]
                statuses = sorted(f.result() for f in futures)

            # The FOR UPDATE lock serialises them: exactly one wins.
            assert statuses == [204, 409]
            with db() as conn:
                remaining = conn.execute(
                    "select count(*) as n from school_memberships "
                    "where school_id = %s and role = 'director' "
                    "and state = 'active' and removed_at is null",
                    (school_id,),
                ).fetchone()["n"]
            assert remaining == 1
    finally:
        purge_audit(director_a_id, director_b_id)


def test_remove_staff_revokes_sessions_when_nothing_is_left(client):
    email = unique_email("removed")
    created_id = None
    try:
        created = create_staff(client, SCHOOL_A_ID, email, "coordinator")
        created_id = created.json()["userId"]
        token = login(client, email, created.json()["temporaryPassword"]).json()["token"]
        assert client.get("/api/auth/me", headers=hdr(token)).status_code == 200

        # An unknown target answers 404 (no membership here).
        assert client.delete(
            f"/api/staff/{uuid.uuid4()}", headers=hdr(tok(client, DIRECTOR_A), SCHOOL_A_ID)
        ).status_code == 404

        resp = client.delete(
            f"/api/staff/{created_id}", headers=hdr(tok(client, DIRECTOR_A), SCHOOL_A_ID)
        )
        assert resp.status_code == 204
        # No other membership, no parent role → the session dies with the role.
        assert client.get("/api/auth/me", headers=hdr(token)).status_code == 401
        with db() as conn:
            rows = conn.execute(
                "select removed_at from school_memberships where user_id = %s",
                (created_id,),
            ).fetchall()
            assert rows and all(r["removed_at"] is not None for r in rows)
            actions = [
                a["action"]
                for a in conn.execute(
                    "select action from live_admin_audit where resource_id = %s "
                    "order by created_at",
                    (created_id,),
                ).fetchall()
            ]
            assert actions == ["staff-created", "staff-role-removed"]
    finally:
        purge_audit(created_id)
        purge_accounts(created_id)


# --- AE23: director resets a staff password -----------------------------------


def test_director_resets_coordinator_password(client):
    from app.core.security import create_session_token, hash_session_token
    from app.dao.auth_dao import AuthDao

    email = unique_email("reset")
    created_id = None
    support_id = None
    provider_token = None
    try:
        created = create_staff(client, SCHOOL_A_ID, email, "coordinator")
        created_id = created.json()["userId"]
        first_temp = created.json()["temporaryPassword"]
        token = login(client, email, first_temp).json()["token"]
        assert client.post(
            "/api/auth/change-password", headers=hdr(token),
            json={"currentPassword": first_temp, "newPassword": "WorkingPass1"},
        ).status_code == 200
        assert client.get("/api/students", headers=hdr(token, SCHOOL_A_ID)).status_code == 200

        # Outstanding recovery paths to be voided by the reset.
        raw_reset = create_session_token()
        AuthDao().create_reset_token(created_id, hash_session_token(raw_reset))
        with db() as conn:
            conn.execute(
                "insert into auth_preauth_tokens (user_id, token_hash, expires_at) "
                "values (%s, %s, now() + interval '5 minutes')",
                (created_id, f"it-u8-preauth-{uuid.uuid4().hex}"),
            )

        # A target with no membership at this school does not exist (404).
        assert client.post(
            f"/api/staff/{user_id_of(DIRECTOR_B)}/reset-password",
            headers=hdr(tok(client, DIRECTOR_A), SCHOOL_A_ID),
        ).status_code == 404

        resp = client.post(
            f"/api/staff/{created_id}/reset-password",
            headers=hdr(tok(client, DIRECTOR_A), SCHOOL_A_ID),
        )
        assert resp.status_code == 200, resp.text
        second_temp = resp.json()["temporaryPassword"]
        assert len(second_temp) >= 12
        assert second_temp != first_temp

        # The open session is rejected on its next call; old password is dead.
        assert client.get("/api/students", headers=hdr(token, SCHOOL_A_ID)).status_code == 401
        assert login(client, email, "WorkingPass1").status_code == 401
        # The outstanding reset link and pre-auth token died with it.
        assert client.post(
            "/api/auth/reset-password",
            json={"token": raw_reset, "password": "Sneaky123"},
        ).status_code == 400
        with db() as conn:
            assert conn.execute(
                "select count(*) as n from auth_preauth_tokens where user_id = %s",
                (created_id,),
            ).fetchone()["n"] == 0

        # The new temporary password signs in and forces a change.
        relog = login(client, email, second_temp)
        assert relog.status_code == 200
        assert relog.json()["user"]["mustChangePassword"] is True
        new_token = relog.json()["token"]
        assert client.get("/api/students", headers=hdr(new_token, SCHOOL_A_ID)).status_code == 409
        assert client.post(
            "/api/auth/change-password", headers=hdr(new_token),
            json={"currentPassword": second_temp, "newPassword": "WorkingPass2"},
        ).status_code == 200
        assert client.get("/api/students", headers=hdr(new_token, SCHOOL_A_ID)).status_code == 200

        # Audited with the actor; role and ids only — never the password.
        with db() as conn:
            audit = conn.execute(
                "select actor_id, actor_kind, school_id, detail::text as detail "
                "from live_admin_audit "
                "where resource_id = %s and action = 'staff-password-reset' "
                "order by created_at",
                (created_id,),
            ).fetchall()
            assert len(audit) == 1
            assert str(audit[0]["actor_id"]) == user_id_of(DIRECTOR_A)
            assert audit[0]["actor_kind"] == "staff"
            assert str(audit[0]["school_id"]) == SCHOOL_A_ID
            assert "coordinator" in audit[0]["detail"]
            assert second_temp not in audit[0]["detail"]
            assert "password" not in audit[0]["detail"].lower()

        # A stepped-in provider's scope qualifies as director automatically.
        provider_token = provider_tok(client)
        provider_id = user_id_of(PROVIDER)
        with db() as conn:
            support_id = conn.execute(
                "insert into provider_support_sessions (provider_user_id, school_id, reason) "
                "values (%s, %s, 'IT U8 staff reset') returning id",
                (provider_id, SCHOOL_A_ID),
            ).fetchone()["id"]
            conn.execute(
                "update auth_sessions set support_session_id = %s where token_hash = %s",
                (support_id, hash_session_token(provider_token)),
            )
        resp = client.post(
            f"/api/staff/{created_id}/reset-password",
            headers=hdr(provider_token, SCHOOL_A_ID),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["temporaryPassword"]
        with db() as conn:
            kinds = [
                a["actor_kind"]
                for a in conn.execute(
                    "select actor_kind from live_admin_audit "
                    "where resource_id = %s and action = 'staff-password-reset' "
                    "order by created_at",
                    (created_id,),
                ).fetchall()
            ]
            assert kinds == ["staff", "provider"]
    finally:
        if support_id:
            with db() as conn:
                conn.execute(
                    "update auth_sessions set support_session_id = null "
                    "where support_session_id = %s",
                    (support_id,),
                )
                conn.execute(
                    "delete from provider_support_sessions where id = %s", (support_id,)
                )
        if provider_token:
            restore_provider_enrolment(provider_token)
        purge_audit(created_id)
        purge_accounts(created_id)


# --- Temporary password expiry ------------------------------------------------


def test_expired_temporary_password_fails_with_the_generic_message(client):
    email = unique_email("expired")
    created_id = None
    try:
        created = create_staff(client, SCHOOL_A_ID, email, "coordinator")
        temp_password = created.json()["temporaryPassword"]
        created_id = created.json()["userId"]
        with db() as conn:
            conn.execute(
                "update app_users set temporary_password_expires_at = now() - interval '1 hour' "
                "where id = %s",
                (created_id,),
            )
        resp = login(client, email, temp_password)
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Invalid email or password"
    finally:
        purge_audit(created_id)
        purge_accounts(created_id)


# --- Anti-enumeration: provider and disabled emails ---------------------------


def test_provider_and_disabled_emails_answer_offered_and_create_nothing(client):
    from app.dao.auth_dao import AuthDao

    provider_id = user_id_of(PROVIDER)
    disabled_email, disabled_id, _ = signup_parent(client, "disabled")
    real_email, real_id, _ = signup_parent(client, "realoffer")
    real_offer_id = None
    try:
        AuthDao().disable_user(disabled_id)

        provider_resp = create_staff(client, SCHOOL_A_ID, PROVIDER, "coordinator")
        disabled_resp = create_staff(client, SCHOOL_A_ID, disabled_email, "coordinator")
        real_resp = create_staff(client, SCHOOL_A_ID, real_email, "coordinator")
        assert provider_resp.status_code == disabled_resp.status_code == real_resp.status_code == 200

        # All three answers are byte-for-byte the same shape (email aside).
        def strip(body: dict) -> dict:
            return {k: v for k, v in body.items() if k != "email"}

        assert strip(provider_resp.json()) == strip(disabled_resp.json()) == strip(real_resp.json())
        assert provider_resp.json()["status"] == "offered"
        assert "temporaryPassword" not in provider_resp.json()

        with db() as conn:
            for silent_id in (provider_id, disabled_id):
                assert conn.execute(
                    "select count(*) as n from school_memberships "
                    "where user_id = %s and school_id = %s",
                    (silent_id, SCHOOL_A_ID),
                ).fetchone()["n"] == 0
                assert conn.execute(
                    "select count(*) as n from live_admin_audit "
                    "where action = 'staff-role-offered' and detail->>'user_id' = %s",
                    (str(silent_id),),
                ).fetchone()["n"] == 0
            # …while the real account genuinely got its offer row.
            real_offer = offer_row(real_id, SCHOOL_A_ID, "coordinator")
            assert real_offer is not None and real_offer["state"] == "offered"
            real_offer_id = str(real_offer["id"])
    finally:
        with db() as conn:
            conn.execute(
                "delete from school_memberships where user_id = any(%s::uuid[])",
                ([real_id, disabled_id],),
            )
        purge_audit(real_offer_id, real_id, disabled_id)
        purge_accounts(disabled_id, real_id)


# --- disable_user is the single disable path ----------------------------------


def test_disable_user_kills_pin_sessions_and_reset_tokens(client):
    from app.core.security import create_session_token, hash_session_token
    from app.dao.auth_dao import AuthDao

    email = unique_email("driver")
    pin = "941562"
    driver_id = None
    try:
        created = client.post(
            "/api/accounts/drivers",
            headers=hdr(tok(client, DIRECTOR_A), SCHOOL_A_ID),
            json={
                "full_name": "IT U8 Driver", "email": email,
                "password": "DriverPass1", "phone": "0712000111", "pin": pin,
            },
        )
        assert created.status_code == 200, created.text
        driver_id = str(created.json()["id"])

        # Working before the disable: PIN and password both sign in.
        assert client.post("/api/auth/pin-login", json={"pin": pin}).status_code == 200
        session_token = login(client, email, "DriverPass1").json()["token"]
        assert client.get("/api/auth/me", headers=hdr(session_token)).status_code == 200
        raw_reset = create_session_token()
        AuthDao().create_reset_token(driver_id, hash_session_token(raw_reset))

        AuthDao().disable_user(driver_id)

        with db() as conn:
            assert conn.execute(
                "select disabled_at from app_users where id = %s", (driver_id,)
            ).fetchone()["disabled_at"] is not None
        # The PIN no longer resolves, the live session is dead, the password
        # is refused with the generic message, and the reset link is void.
        assert client.post("/api/auth/pin-login", json={"pin": pin}).status_code == 401
        assert client.get("/api/auth/me", headers=hdr(session_token)).status_code == 401
        assert login(client, email, "DriverPass1").status_code == 401
        assert client.post(
            "/api/auth/reset-password",
            json={"token": raw_reset, "password": "Sneaky123"},
        ).status_code == 400
        # The staff surface is unaffected by a disabled driver.
        assert client.get(
            "/api/staff", headers=hdr(tok(client, DIRECTOR_A), SCHOOL_A_ID)
        ).status_code == 200
    finally:
        purge_audit(driver_id)
        purge_accounts(driver_id)


# --- Signup rule and auto-link hygiene ----------------------------------------


def test_signup_offers_parent_only(client):
    resp = client.post(
        "/api/auth/signup",
        json={
            "email": unique_email("driver-signup"), "password": "DriverPass1",
            "full_name": "IT U8 No Driver", "role": "driver",
        },
    )
    assert resp.status_code == 400
    assert "Role must be parent" in resp.text

    email, parent_id, _ = signup_parent(client, "signup-ok")
    try:
        with db() as conn:
            role = conn.execute(
                "select role from app_user_roles where user_id = %s", (parent_id,)
            ).fetchone()["role"]
        assert role == "parent"
    finally:
        purge_accounts(parent_id)


def test_provider_and_disabled_emails_never_auto_link_as_parents(client):
    disabled_email, disabled_id, _ = signup_parent(client, "nolink-disabled")
    control_email, control_id, _ = signup_parent(client, "nolink-control")
    provider_parent_email = unique_email("nolink-provider")
    provider_parent_id = None
    student_ids: list[str] = []
    try:
        from app.dao.auth_dao import AuthDao

        AuthDao().disable_user(disabled_id)
        # A parent-role account that ALSO holds a provider account — the
        # provider predicate, independent of the seeded Kuumbai identity.
        signup = client.post(
            "/api/auth/signup",
            json={
                "email": provider_parent_email, "password": PARENT_SIGNUP_PASSWORD,
                "full_name": "IT U8 Provider Parent", "role": "parent",
            },
        )
        assert signup.status_code == 200
        provider_parent_id = str(signup.json()["user"]["id"])
        with db() as conn:
            conn.execute(
                "insert into provider_accounts (user_id, totp_salt) values (%s, %s)",
                (provider_parent_id, uuid.uuid4().hex),
            )

        headers = hdr(tok(client, DIRECTOR_A), SCHOOL_A_ID)

        def make_student(name: str, email1: str, email2: str | None) -> str:
            resp = client.post(
                "/api/students", headers=headers,
                json={
                    "name": name, "parent_name": "IT U8 Slot Parent",
                    "parent_phone": "0712000112", "parent_email": email1,
                    "parent2_email": email2,
                },
            )
            assert resp.status_code == 200, resp.text
            student_ids.append(str(resp.json()["id"]))
            return student_ids[-1]

        # The literal seeded provider email, and a provider-holding parent.
        s1 = make_student("IT U8 NoLink One", PROVIDER, provider_parent_email)
        # A disabled account's email, plus a healthy control account.
        s2 = make_student("IT U8 NoLink Two", disabled_email, control_email)

        with db() as conn:
            s1_links = conn.execute(
                "select count(*) as n from live_parent_students where student_id = %s",
                (s1,),
            ).fetchone()["n"]
            s2_links = conn.execute(
                "select parent_id from live_parent_students where student_id = %s",
                (s2,),
            ).fetchall()
        assert s1_links == 0
        assert [str(r["parent_id"]) for r in s2_links] == [control_id]
    finally:
        for student_id in student_ids:
            client.delete(
                f"/api/students/{student_id}",
                headers=hdr(tok(client, DIRECTOR_A), SCHOOL_A_ID),
            )
        purge_audit(*student_ids)
        purge_accounts(disabled_id, control_id, provider_parent_id)
