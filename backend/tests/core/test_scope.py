"""Pure tests for the tenancy scope model and access-key resolution (U5).

`resolve_school_scope` / `resolve_driver_scope` / `resolve_parent_scope` are
pure functions over the enriched session-user dict, so the whole permission
matrix is testable without a database: header validation, wrong-school 404 vs
wrong-role 403, offered rows never granting access, header fallback rules,
and the provider step-in gate.
"""

import dataclasses

import pytest

from app.core.scope import (
    ParentScope,
    ProviderScope,
    SchoolScope,
    ScopeError,
    guc_value,
    is_password_change_exempt,
    resolve_driver_scope,
    resolve_parent_scope,
    resolve_school_scope,
)

SCHOOL_A = "5cae0000-0000-0000-0000-000000000001"
SCHOOL_B = "5cae0000-0000-0000-0000-000000000002"


def member(school, role, state="active"):
    return {
        "school_id": school,
        "role": role,
        "state": state,
        "school_name": "S",
        "school_code": None,
    }


def user(memberships=(), provider=None, support=None, last=None, role=None):
    return {
        "id": "u-1",
        "email": "x@test",
        "full_name": "X",
        "role": role,
        "memberships": list(memberships),
        "provider": provider,
        "support_session": support,
        "parent_school_ids": [],
        "last_school_id": last,
    }


def resolve(u, header, roles=("director", "coordinator"), required=False):
    return resolve_school_scope(u, header, roles, header_required=required)


def expect(status, fn):
    with pytest.raises(ScopeError) as e:
        fn()
    assert e.value.status_code == status
    return e.value


# --- scope objects -----------------------------------------------------------


def test_scopes_are_frozen_and_expose_school_ids():
    s = SchoolScope(user_id="u", school_id=SCHOOL_A, role="director")
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.school_id = SCHOOL_B
    assert s.school_ids == (SCHOOL_A,)
    assert s.actor_kind == "staff"
    p = ParentScope(user_id="u", school_ids=(SCHOOL_A, SCHOOL_B))
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.school_ids = ()
    assert ProviderScope(user_id="u").school_ids == ()


def test_guc_value_joins_school_ids():
    assert guc_value(None) == ""
    assert guc_value(SchoolScope(user_id="u", school_id=SCHOOL_A, role="director")) == SCHOOL_A
    assert guc_value(ParentScope(user_id="u", school_ids=(SCHOOL_A, SCHOOL_B))) == (
        f"{SCHOOL_A},{SCHOOL_B}"
    )
    assert guc_value(ProviderScope(user_id="u")) == ""


# --- header validation and the not-found contract ---------------------------


def test_malformed_header_is_400():
    u = user([member(SCHOOL_A, "director")])
    expect(400, lambda: resolve(u, "not-a-uuid"))


def test_foreign_school_header_is_404_not_403():
    u = user([member(SCHOOL_A, "director")])
    expect(404, lambda: resolve(u, SCHOOL_B))


def test_wrong_role_at_own_school_is_403():
    u = user([member(SCHOOL_A, "coordinator")])
    expect(403, lambda: resolve(u, SCHOOL_A, roles=("director",)))


def test_matching_membership_yields_scope():
    u = user([member(SCHOOL_A, "coordinator")])
    scope = resolve(u, SCHOOL_A)
    assert scope == SchoolScope(user_id="u-1", school_id=SCHOOL_A, role="coordinator")


def test_offered_membership_never_yields_a_scope():
    u = user([member(SCHOOL_B, "director", state="offered")])
    expect(404, lambda: resolve(u, SCHOOL_B))


def test_director_wins_when_both_staff_roles_exist_at_one_school():
    u = user([member(SCHOOL_A, "coordinator"), member(SCHOOL_A, "director")])
    assert resolve(u, SCHOOL_A).role == "director"


# --- header fallback (compatibility window) ---------------------------------


def test_missing_header_is_400_when_required():
    u = user([member(SCHOOL_A, "director")])
    expect(400, lambda: resolve(u, None, required=True))


def test_missing_header_falls_back_to_the_only_membership():
    u = user([member(SCHOOL_A, "director")])
    assert resolve(u, None).school_id == SCHOOL_A


def test_missing_header_uses_last_school_across_two_memberships():
    u = user([member(SCHOOL_A, "director"), member(SCHOOL_B, "director")], last=SCHOOL_B)
    assert resolve(u, None).school_id == SCHOOL_B


def test_missing_header_with_ambiguous_memberships_is_400():
    u = user([member(SCHOOL_A, "director"), member(SCHOOL_B, "director")])
    expect(400, lambda: resolve(u, None))


def test_missing_header_with_no_memberships_is_403():
    expect(403, lambda: resolve(user(), None))


def test_fallback_ignores_memberships_outside_the_allowed_roles():
    u = user([member(SCHOOL_A, "driver"), member(SCHOOL_B, "coordinator")])
    assert resolve(u, None).school_id == SCHOOL_B


# --- provider step-in --------------------------------------------------------


def test_provider_without_step_in_is_403():
    u = user(provider={"totp_enrolled": True})
    expect(403, lambda: resolve(u, SCHOOL_A))


def test_provider_step_in_grants_director_scope_at_that_school_only():
    u = user(
        provider={"totp_enrolled": True},
        support={"id": "ss-1", "school_id": SCHOOL_A},
    )
    scope = resolve(u, SCHOOL_A)
    assert scope.actor_kind == "provider"
    assert scope.role == "director"
    assert scope.support_session_id == "ss-1"
    expect(403, lambda: resolve(u, SCHOOL_B))


def test_provider_fallback_uses_the_step_in_school():
    u = user(provider={"totp_enrolled": True}, support={"id": "ss-1", "school_id": SCHOOL_A})
    assert resolve(u, None).school_id == SCHOOL_A


# --- driver scope (derived server-side) --------------------------------------


def test_driver_route_rejects_a_school_header():
    u = user([member(SCHOOL_A, "driver"), member(SCHOOL_A, "coordinator")])
    expect(403, lambda: resolve_driver_scope(u, SCHOOL_A))


def test_driver_scope_derives_from_the_driver_membership():
    u = user([member(SCHOOL_A, "coordinator"), member(SCHOOL_A, "driver")])
    scope = resolve_driver_scope(u, None)
    assert scope.actor_kind == "driver"
    assert scope.role == "driver"
    assert scope.school_id == SCHOOL_A


def test_driver_scope_prefers_last_school_when_driving_for_two():
    u = user([member(SCHOOL_A, "driver"), member(SCHOOL_B, "driver")], last=SCHOOL_B)
    assert resolve_driver_scope(u, None).school_id == SCHOOL_B


def test_driver_scope_without_a_driver_membership_is_403():
    u = user([member(SCHOOL_A, "coordinator")])
    expect(403, lambda: resolve_driver_scope(u, None))


# --- parent scope ------------------------------------------------------------


def test_parent_scope_carries_the_accepted_link_school_set():
    u = user(role="parent")
    u["parent_school_ids"] = [SCHOOL_B, SCHOOL_A]
    scope = resolve_parent_scope(u)
    assert scope == ParentScope(user_id="u-1", school_ids=(SCHOOL_A, SCHOOL_B))


def test_parent_scope_requires_the_parent_role():
    expect(403, lambda: resolve_parent_scope(user(role="driver")))


def test_parent_with_no_links_sees_an_empty_school_set():
    assert resolve_parent_scope(user(role="parent")).school_ids == ()


# --- temporary-password allowlist -------------------------------------------


def test_password_change_exemptions_cover_exactly_the_three_paths():
    for path in ("/api/auth/me", "/api/auth/logout", "/api/auth/change-password"):
        assert is_password_change_exempt(path)
    for path in ("/api/push/subscribe", "/api/live/students", "/api/auth/login2"):
        assert not is_password_change_exempt(path)
