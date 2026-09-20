"""Tenancy request scope: frozen access keys, pure resolution, context var (U5).

A request's *access key* is resolved once, from the session user and the
``X-School-Id`` header, into a frozen scope object:

- ``SchoolScope`` — staff/driver/provider acting inside one school,
- ``ParentScope`` — a parent's accepted-link school set (cross-school surface),
- ``ProviderScope`` — the provider console outside any school.

Resolution is pure (no I/O) so the whole permission matrix is unit-testable;
the FastAPI dependencies in ``app.core.permissions`` feed it the enriched user
dict from ``AuthDao.get_session_user`` and publish the result in a context
var, which the connection seam (``app.core.db``) turns into the
transaction-local ``saferide.school_ids`` GUC.

The not-found contract: a school the caller cannot access behaves as if it
did not exist (404); the wrong role at a school they *do* belong to is 403.
"""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field

logger = logging.getLogger("saferide.scope")

STAFF_ROLES = ("director", "coordinator")
_ROLE_PRIORITY = {"director": 0, "coordinator": 1, "driver": 2}

# Paths a must-change-password session may still call (R30): see who they
# are, change the password, or leave. Everything else answers 409.
PASSWORD_CHANGE_EXEMPT_PATHS = frozenset(
    {"/api/auth/me", "/api/auth/logout", "/api/auth/change-password"}
)

# Paths an UNENROLLED provider's session may still call (U10/R20): the same
# allowlist mechanism as must-change-password. Until the second factor is
# confirmed, everything else — every provider and school route included —
# answers 409 `totp-enrolment-required`.
TOTP_ENROLMENT_EXEMPT_PATHS = frozenset(
    {
        "/api/auth/me",
        "/api/auth/logout",
        "/api/auth/change-password",
        "/api/provider/totp/enrol",
        "/api/provider/totp/confirm",
    }
)


class ScopeError(Exception):
    """Resolution failure carrying the HTTP status the guard should raise."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class SchoolScope:
    """One actor working inside one school for this request."""

    user_id: str
    school_id: str
    role: str  # membership role granting access: director|coordinator|driver
    actor_kind: str = "staff"  # staff | driver | provider (step-in)
    support_session_id: str | None = None

    @property
    def school_ids(self) -> tuple[str, ...]:
        return (self.school_id,)


@dataclass(frozen=True)
class ParentScope:
    """A parent's cross-school surface: every school with an accepted link."""

    user_id: str
    school_ids: tuple[str, ...] = ()
    actor_kind: str = field(default="parent", init=False)


@dataclass(frozen=True)
class ProviderScope:
    """The provider console outside any school (lists, health, audit)."""

    user_id: str
    actor_kind: str = field(default="provider", init=False)

    @property
    def school_ids(self) -> tuple[str, ...]:
        return ()


Scope = SchoolScope | ParentScope | ProviderScope

_current_scope: ContextVar[Scope | None] = ContextVar("saferide_scope", default=None)


def current_scope() -> Scope | None:
    return _current_scope.get()


def set_current_scope(scope: Scope | None) -> None:
    _current_scope.set(scope)


def guc_value(scope: Scope | None) -> str:
    """The ``saferide.school_ids`` value for a scope: comma-joined uuids."""
    return ",".join(scope.school_ids) if scope is not None else ""


def is_password_change_exempt(path: str) -> bool:
    return path in PASSWORD_CHANGE_EXEMPT_PATHS


def is_totp_enrolment_exempt(path: str) -> bool:
    return path in TOTP_ENROLMENT_EXEMPT_PATHS


# --- pure resolution ---------------------------------------------------------


def _validated_school_id(header: str) -> str:
    try:
        return str(uuid.UUID(header.strip()))
    except (ValueError, AttributeError) as error:
        raise ScopeError(400, "X-School-Id must be a school id") from error


def _active_memberships(user: dict) -> list[dict]:
    return [m for m in user.get("memberships") or [] if m.get("state") == "active"]


def _best_role(rows: list[dict]) -> str:
    return min((m["role"] for m in rows), key=lambda r: _ROLE_PRIORITY.get(r, 99))


def _fallback_school(
    user: dict, memberships: list[dict], allowed_roles: tuple[str, ...]
) -> str:
    """No header sent (compatibility window): derive the school server-side."""
    if user.get("provider") is not None:
        support = user.get("support_session")
        if support:
            return support["school_id"]
        raise ScopeError(403, "An active support session is required")
    candidates = {m["school_id"] for m in memberships if m["role"] in allowed_roles}
    if not candidates:
        raise ScopeError(403, "You do not have access to this resource")
    if len(candidates) == 1:
        return next(iter(candidates))
    last = user.get("last_school_id")
    if last and str(last) in candidates:
        return str(last)
    raise ScopeError(400, "X-School-Id must name which of your schools to work in")


def resolve_school_scope(
    user: dict,
    header_school: str | None,
    allowed_roles: tuple[str, ...],
    *,
    header_required: bool,
) -> SchoolScope:
    """Resolve a staff-surface request to its school access key.

    ``user`` is the enriched session dict (active+offered memberships with
    removed rows already excluded, provider state, the live step-in session,
    ``last_school_id``).
    """
    memberships = _active_memberships(user)
    if header_school is not None:
        school_id = _validated_school_id(header_school)
    elif header_required:
        raise ScopeError(400, "X-School-Id header is required")
    else:
        school_id = _fallback_school(user, memberships, allowed_roles)

    if user.get("provider") is not None:
        support = user.get("support_session")
        if support and support["school_id"] == school_id:
            return SchoolScope(
                user_id=str(user["id"]),
                school_id=school_id,
                role="director",
                actor_kind="provider",
                support_session_id=str(support["id"]),
            )
        raise ScopeError(403, "An active support session is required for this school")

    at_school = [m for m in memberships if m["school_id"] == school_id]
    if not at_school:
        # Not a member: the school does not exist for this caller.
        raise ScopeError(404, "Not found")
    allowed = [m for m in at_school if m["role"] in allowed_roles]
    if not allowed:
        raise ScopeError(403, "You do not have access to this resource")
    role = _best_role(allowed)
    return SchoolScope(
        user_id=str(user["id"]),
        school_id=school_id,
        role=role,
        actor_kind="driver" if role == "driver" else "staff",
    )


def resolve_driver_scope(user: dict, header_school: str | None) -> SchoolScope:
    """Driver routes derive their school server-side; a header is refused so
    the staff surface can never drive driver endpoints by accident."""
    if header_school is not None:
        raise ScopeError(403, "Driver routes derive their school from the account")
    driving = [m for m in _active_memberships(user) if m["role"] == "driver"]
    if not driving:
        raise ScopeError(403, "You do not have access to this resource")
    schools = {m["school_id"] for m in driving}
    school_id = next(iter(schools))
    if len(schools) > 1:
        last = user.get("last_school_id")
        # The driver app has no school picker; stay deterministic.
        school_id = str(last) if last and str(last) in schools else sorted(schools)[0]
    return SchoolScope(
        user_id=str(user["id"]), school_id=school_id, role="driver", actor_kind="driver"
    )


def resolve_parent_scope(user: dict) -> ParentScope:
    if user.get("role") != "parent":
        raise ScopeError(403, "You do not have access to this resource")
    schools = tuple(sorted(str(s) for s in user.get("parent_school_ids") or []))
    return ParentScope(user_id=str(user["id"]), school_ids=schools)


def resolve_provider_scope(user: dict) -> ProviderScope:
    if user.get("provider") is None:
        raise ScopeError(403, "You do not have access to this resource")
    return ProviderScope(user_id=str(user["id"]))
