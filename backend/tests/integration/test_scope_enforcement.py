"""End-to-end enforcement of the tenancy permission guard (U5).

Runs the real ``create_app()`` in-process (TestClient) against the local
database, with four tiny test-only routes mounted behind the new guards —
the production routers convert to these guards in U6/U7, so this file pins
the dependency behavior itself: header resolution, the 404-not-403 contract,
role limits, immediate revocation, provider step-in, the temporary-password
allowlist, scoped response headers, and GUC visibility inside endpoints and
background tasks.
"""

import os
import uuid

import psycopg
import pytest

from conftest import (
    COORDINATOR_A,
    DIRECTOR_B,
    DSN,
    PROVIDER,
    SCHOOL_A_ID,
    SCHOOL_B_ID,
    TEST_PASSWORD,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="needs the local stack; set RUN_INTEGRATION=1",
)

DRIVER_A_PIN = "0322"
PARENT_EMAIL = "and7005@gmail.com"

_bg_guc_seen: list[str | None] = []


@pytest.fixture(scope="module")
def client(in_process_db):
    from fastapi import BackgroundTasks, Depends
    from fastapi.testclient import TestClient

    from app.core.db import get_connection
    from app.core.permissions import (
        require_director,
        require_driver_scope,
        require_parent_scope,
        require_staff,
    )
    from app.core.scope import ParentScope, SchoolScope
    from app.main import create_app

    app = create_app()

    def observed_guc(scope=None) -> str | None:
        # U7: school-surface routes thread their scope explicitly (the strict
        # seam refuses an implicit checkout there); the parent route still
        # exercises the context-var fallback, which ParentScope keeps.
        with (get_connection(scope) if scope is not None else get_connection()) as conn:
            return conn.execute(
                "select current_setting('saferide.school_ids', true) as v"
            ).fetchone()["v"]

    @app.get("/t/staff")
    def staff_route(scope: SchoolScope = Depends(require_staff)):
        return {
            "school": scope.school_id,
            "role": scope.role,
            "actor": scope.actor_kind,
            "guc": observed_guc(scope),
        }

    @app.get("/t/staff-implicit")
    def staff_implicit_route(scope: SchoolScope = Depends(require_staff)):
        # The strict default (U7): an implicitly-scoped checkout inside a
        # SchoolScope request is a programming error, not a fallback.
        try:
            observed_guc()
        except RuntimeError as error:
            return {"raised": str(error)}
        return {"raised": None}

    @app.delete("/t/record")
    def delete_route(scope: SchoolScope = Depends(require_director)):
        return {"school": scope.school_id, "role": scope.role}

    @app.get("/t/driver")
    def driver_route(scope: SchoolScope = Depends(require_driver_scope)):
        return {
            "school": scope.school_id,
            "actor": scope.actor_kind,
            "guc": observed_guc(scope),
        }

    @app.get("/t/parent")
    def parent_route(scope: ParentScope = Depends(require_parent_scope)):
        return {"schools": list(scope.school_ids), "guc": observed_guc()}

    @app.get("/t/background")
    def background_route(
        background_tasks: BackgroundTasks,
        scope: SchoolScope = Depends(require_staff),
    ):
        # U7: background work threads the request scope explicitly — the
        # production dispatch pattern (notify_route_changes, notify_*).
        background_tasks.add_task(lambda: _bg_guc_seen.append(observed_guc(scope)))
        return {"school": scope.school_id}

    return TestClient(app)


def tok(client, email: str, password: str = TEST_PASSWORD) -> str:
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


def hdr(token: str, school: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if school is not None:
        headers["X-School-Id"] = school
    return headers


def db():
    return psycopg.connect(DSN, autocommit=True)


def user_id_of(email: str) -> str:
    with db() as conn:
        return str(
            conn.execute(
                "select id from app_users where email = %s", (email,)
            ).fetchone()[0]
        )


# --- header resolution and the not-found contract (AE1, AE20) ----------------


def test_wrong_school_header_is_404_and_own_school_works(client):
    token = tok(client, COORDINATOR_A)
    assert client.get("/t/staff", headers=hdr(token, SCHOOL_B_ID)).status_code == 404
    resp = client.get("/t/staff", headers=hdr(token, SCHOOL_A_ID))
    assert resp.status_code == 200
    body = resp.json()
    assert body["school"] == SCHOOL_A_ID
    assert body["role"] == "coordinator"
    assert body["guc"] == SCHOOL_A_ID


def test_malformed_header_is_400(client):
    token = tok(client, COORDINATOR_A)
    assert client.get("/t/staff", headers=hdr(token, "not-a-uuid")).status_code == 400


def test_missing_header_falls_back_to_the_only_membership(client):
    token = tok(client, DIRECTOR_B)
    resp = client.get("/t/staff", headers=hdr(token))
    assert resp.status_code == 200
    assert resp.json()["school"] == SCHOOL_B_ID


def test_missing_header_is_400_once_the_flag_flips(client, monkeypatch):
    from app.core.config import get_settings

    token = tok(client, DIRECTOR_B)
    monkeypatch.setattr(get_settings(), "scope_header_required", True)
    assert client.get("/t/staff", headers=hdr(token)).status_code == 400
    assert client.get("/t/staff", headers=hdr(token, SCHOOL_B_ID)).status_code == 200


def test_scoped_responses_are_uncacheable_and_header_variant(client):
    token = tok(client, COORDINATOR_A)
    resp = client.get("/t/staff", headers=hdr(token, SCHOOL_A_ID))
    assert resp.headers["Cache-Control"] == "no-store"
    assert "X-School-Id" in resp.headers["Vary"]
    login = client.post(
        "/api/auth/login", json={"email": COORDINATOR_A, "password": TEST_PASSWORD}
    )
    assert login.headers.get("Cache-Control") != "no-store"  # unscoped route


# --- role limits (AE2) -------------------------------------------------------


def test_coordinator_cannot_delete_but_director_can(client):
    coordinator = tok(client, COORDINATOR_A)
    resp = client.delete("/t/record", headers=hdr(coordinator, SCHOOL_A_ID))
    assert resp.status_code == 403
    director = tok(client, "director.a@saferide.test")
    resp = client.delete("/t/record", headers=hdr(director, SCHOOL_A_ID))
    assert resp.status_code == 200
    assert resp.json()["role"] == "director"


# --- one token, two schools; revocation (AE13, AE5) --------------------------


def test_alternating_headers_switch_scope_and_removal_ends_access(client):
    director_a = user_id_of("director.a@saferide.test")
    with db() as conn:
        conn.execute(
            "insert into school_memberships (user_id, school_id, role, state) "
            "values (%s, %s, 'director', 'active')",
            (director_a, SCHOOL_B_ID),
        )
    try:
        token = tok(client, "director.a@saferide.test")
        for i in range(50):
            school = SCHOOL_A_ID if i % 2 == 0 else SCHOOL_B_ID
            body = client.get("/t/staff", headers=hdr(token, school)).json()
            assert body["school"] == school
            assert body["guc"] == school
    finally:
        with db() as conn:
            conn.execute(
                "delete from school_memberships where user_id = %s and school_id = %s",
                (director_a, SCHOOL_B_ID),
            )
    # Same live session, membership gone: that school no longer exists for
    # them, while the remaining school keeps working.
    assert client.get("/t/staff", headers=hdr(token, SCHOOL_B_ID)).status_code == 404
    assert client.get("/t/staff", headers=hdr(token, SCHOOL_A_ID)).status_code == 200


def test_an_offered_membership_never_yields_a_scope(client):
    coordinator = user_id_of(COORDINATOR_A)
    with db() as conn:
        conn.execute(
            "insert into school_memberships (user_id, school_id, role, state) "
            "values (%s, %s, 'coordinator', 'offered')",
            (coordinator, SCHOOL_B_ID),
        )
    try:
        token = tok(client, COORDINATOR_A)
        assert client.get("/t/staff", headers=hdr(token, SCHOOL_B_ID)).status_code == 404
        me = client.get("/api/auth/me", headers=hdr(token)).json()
        assert [o["schoolId"] for o in me["pendingOffers"]] == [SCHOOL_B_ID]
        assert SCHOOL_B_ID not in [m["schoolId"] for m in me["memberships"]]
    finally:
        with db() as conn:
            conn.execute(
                "delete from school_memberships "
                "where user_id = %s and school_id = %s and state = 'offered'",
                (coordinator, SCHOOL_B_ID),
            )


def test_staff_switch_persists_last_school_on_the_session(client):
    from app.core.security import hash_session_token

    token = tok(client, COORDINATOR_A)
    assert client.get("/t/staff", headers=hdr(token, SCHOOL_A_ID)).status_code == 200
    with db() as conn:
        last = conn.execute(
            "select last_school_id from auth_sessions where token_hash = %s",
            (hash_session_token(token),),
        ).fetchone()[0]
    assert str(last) == SCHOOL_A_ID
    me = client.get("/api/auth/me", headers=hdr(token)).json()
    assert me["activeSchoolId"] == SCHOOL_A_ID


# --- driver and parent surfaces ----------------------------------------------


def test_driver_scope_is_derived_and_a_header_is_refused(client):
    resp = client.post("/api/auth/pin-login", json={"pin": DRIVER_A_PIN})
    assert resp.status_code == 200, resp.text
    token = resp.json()["token"]
    body = client.get("/t/driver", headers=hdr(token)).json()
    assert body == {"school": SCHOOL_A_ID, "actor": "driver", "guc": SCHOOL_A_ID}
    assert client.get("/t/driver", headers=hdr(token, SCHOOL_A_ID)).status_code == 403


def test_parent_scope_carries_accepted_link_schools_and_staff_routes_hide(client):
    token = tok(client, PARENT_EMAIL)
    body = client.get("/t/parent", headers=hdr(token)).json()
    # The seeded parent has accepted links at both schools (the F5 flow).
    assert body["schools"] == [SCHOOL_A_ID, SCHOOL_B_ID]
    assert body["guc"] == f"{SCHOOL_A_ID},{SCHOOL_B_ID}"
    # A parent on the staff surface: no memberships → 403 without a header,
    # and a named school they cannot access does not exist for them → 404.
    assert client.get("/t/staff", headers=hdr(token)).status_code == 403
    assert client.get("/t/staff", headers=hdr(token, SCHOOL_A_ID)).status_code == 404


# --- provider step-in gate ---------------------------------------------------


def test_provider_needs_an_active_step_in_for_that_school(client):
    from app.core.security import hash_session_token

    token = tok(client, PROVIDER)
    assert client.get("/t/staff", headers=hdr(token, SCHOOL_A_ID)).status_code == 403

    provider_id = user_id_of(PROVIDER)
    with db() as conn:
        support_id = conn.execute(
            "insert into provider_support_sessions (provider_user_id, school_id, reason) "
            "values (%s, %s, 'IT enforcement test') returning id",
            (provider_id, SCHOOL_A_ID),
        ).fetchone()[0]
        conn.execute(
            "update auth_sessions set support_session_id = %s where token_hash = %s",
            (support_id, hash_session_token(token)),
        )
    try:
        body = client.get("/t/staff", headers=hdr(token, SCHOOL_A_ID)).json()
        assert body["role"] == "director"
        assert body["actor"] == "provider"
        assert body["guc"] == SCHOOL_A_ID
        # Step-in is per school: another school's header stays 403.
        assert client.get("/t/staff", headers=hdr(token, SCHOOL_B_ID)).status_code == 403
        with db() as conn:
            conn.execute(
                "update provider_support_sessions set ended_at = now(), "
                "end_cause = 'step-out' where id = %s",
                (support_id,),
            )
        # Ending the session revokes access on the very next request.
        assert client.get("/t/staff", headers=hdr(token, SCHOOL_A_ID)).status_code == 403
    finally:
        with db() as conn:
            conn.execute(
                "update auth_sessions set support_session_id = null "
                "where support_session_id = %s",
                (support_id,),
            )
            conn.execute(
                "delete from provider_support_sessions where id = %s", (support_id,)
            )


# --- temporary passwords (R30) -----------------------------------------------


def test_temporary_password_locks_everything_but_the_three_paths(client):
    coordinator = user_id_of(COORDINATOR_A)
    with db() as conn:
        conn.execute(
            "update app_users set must_change_password = true where id = %s",
            (coordinator,),
        )
    try:
        login = client.post(
            "/api/auth/login", json={"email": COORDINATOR_A, "password": TEST_PASSWORD}
        )
        assert login.status_code == 200
        assert login.json()["user"]["mustChangePassword"] is True
        token = login.json()["token"]

        for call in (
            lambda: client.get("/t/staff", headers=hdr(token, SCHOOL_A_ID)),
            lambda: client.get("/api/students", headers=hdr(token)),
            lambda: client.post("/api/push/subscribe", headers=hdr(token), json={}),
        ):
            resp = call()
            assert resp.status_code == 409
            assert resp.json()["detail"]["code"] == "password-change-required"

        me = client.get("/api/auth/me", headers=hdr(token))
        assert me.status_code == 200
        assert me.json()["mustChangePassword"] is True
        assert client.post("/api/auth/logout", headers=hdr(token)).status_code == 200
    finally:
        with db() as conn:
            conn.execute(
                "update app_users set must_change_password = false where id = %s",
                (coordinator,),
            )


# --- disabled identities (R13) -----------------------------------------------


def test_a_disabled_identity_loses_its_live_session_and_cannot_log_in(client):
    coordinator = user_id_of(COORDINATOR_A)
    token = tok(client, COORDINATOR_A)
    with db() as conn:
        conn.execute(
            "update app_users set disabled_at = now() where id = %s", (coordinator,)
        )
    try:
        assert client.get("/api/auth/me", headers=hdr(token)).status_code == 401
        login = client.post(
            "/api/auth/login", json={"email": COORDINATOR_A, "password": TEST_PASSWORD}
        )
        assert login.status_code == 401
    finally:
        with db() as conn:
            conn.execute(
                "update app_users set disabled_at = null where id = %s", (coordinator,)
            )


# --- seam: background tasks inherit the request scope ------------------------


def test_background_tasks_see_the_request_guc(client):
    token = tok(client, COORDINATOR_A)
    _bg_guc_seen.clear()
    resp = client.get("/t/background", headers=hdr(token, SCHOOL_A_ID))
    assert resp.status_code == 200
    assert _bg_guc_seen == [SCHOOL_A_ID]


def test_implicit_checkout_inside_a_school_request_is_refused(client):
    """U7's strict default, proven through a real request: a school-scoped
    endpoint that opens a connection without threading its scope raises —
    the missed-conversion detector, not a fallback."""
    token = tok(client, COORDINATOR_A)
    body = client.get("/t/staff-implicit", headers=hdr(token, SCHOOL_A_ID)).json()
    assert body["raised"] is not None
    assert "without an explicit scope" in body["raised"]
