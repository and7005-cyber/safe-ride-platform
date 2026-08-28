"""Scope-resolving FastAPI dependencies: the tenancy permission guard (U5).

These wrap the pure resolution in ``app.core.scope`` with I/O: the enriched
session user comes from ``get_current_user``, the resolved scope is published
in the request context var (async dependencies run in the request task, so a
sync endpoint executed in the threadpool — and its DAO calls — inherit it),
and a staff switch persists ``last_school_id`` for header fallback.

Guards:
- ``require_staff``    — director or coordinator inside the header school
- ``require_director`` — director only (deletes, staff management)
- ``require_driver_scope`` — driver surface; school derived, header refused
- ``require_parent_scope`` — parent surface; accepted-link school set
- ``require_provider``     — provider console (outside any school)
"""

from collections.abc import Awaitable, Callable

from fastapi import Depends, Header, HTTPException, Request

from app.core.auth import get_current_user
from app.core.config import get_settings
from app.core.scope import (
    STAFF_ROLES,
    ParentScope,
    ProviderScope,
    SchoolScope,
    Scope,
    ScopeError,
    resolve_driver_scope,
    resolve_parent_scope,
    resolve_provider_scope,
    resolve_school_scope,
    set_current_scope,
)
from app.dao.auth_dao import AuthDao

_auth_dao = AuthDao()

SchoolHeader = Header(default=None, alias="X-School-Id")


def _publish(request: Request, scope: Scope) -> Scope:
    set_current_scope(scope)
    request.state.scope = scope  # for the response middleware's log line
    return scope


def _remember_school(user: dict, scope: SchoolScope) -> None:
    if scope.actor_kind == "staff" and user.get("last_school_id") != scope.school_id:
        _auth_dao.set_session_school(user["session_id"], scope.school_id)


def require_school(
    *roles: str,
) -> Callable[..., Awaitable[SchoolScope]]:
    """Staff-surface guard: resolve the header school against an active
    membership with one of ``roles`` (or the provider's live step-in)."""
    allowed = tuple(roles)

    async def dependency(
        request: Request,
        user: dict = Depends(get_current_user),
        x_school_id: str | None = SchoolHeader,
    ) -> SchoolScope:
        try:
            scope = resolve_school_scope(
                user,
                x_school_id,
                allowed,
                header_required=get_settings().scope_header_required,
            )
        except ScopeError as error:
            raise HTTPException(error.status_code, error.detail) from error
        _remember_school(user, scope)
        _publish(request, scope)
        return scope

    return dependency


require_staff = require_school(*STAFF_ROLES)
require_director = require_school("director")


async def require_driver_scope(
    request: Request,
    user: dict = Depends(get_current_user),
    x_school_id: str | None = SchoolHeader,
) -> SchoolScope:
    try:
        scope = resolve_driver_scope(user, x_school_id)
    except ScopeError as error:
        raise HTTPException(error.status_code, error.detail) from error
    _publish(request, scope)
    return scope


async def require_parent_scope(
    request: Request, user: dict = Depends(get_current_user)
) -> ParentScope:
    try:
        scope = resolve_parent_scope(user)
    except ScopeError as error:
        raise HTTPException(error.status_code, error.detail) from error
    _publish(request, scope)
    return scope


async def require_provider(
    request: Request, user: dict = Depends(get_current_user)
) -> ProviderScope:
    try:
        scope = resolve_provider_scope(user)
    except ScopeError as error:
        raise HTTPException(error.status_code, error.detail) from error
    _publish(request, scope)
    return scope
