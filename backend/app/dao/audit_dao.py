"""Shared attribution writer for the school-facing audit (U9).

Every staff and provider write goes through :func:`record_audit`, inside the
same transaction as the write it describes. The row carries who (actor id,
name, email), as-what (``actor_kind``: a provider stepping in stays
``provider`` and keeps the ``support_session_id``), where (``school_id``)
and what (``action`` from migration 013's fixed vocabulary, plus
``resource_type``/``resource_id``/``detail``).

School-facing *display* strings are computed server-side and mask provider
identity as "SafeRide" (R25): use :func:`masked_display_sql` in list/report
queries and :func:`actor_display` for in-hand actor dicts. The audit row
itself always keeps the real name — the school-side reader (U8) filters
provider rows out entirely, the provider-side reader (U10) sees everything.

``detail`` must never carry passwords, temporary passwords, tokens, or child
details (names, addresses); counts and ids only.
"""

import uuid
from typing import Any

from psycopg import Connection
from psycopg.types.json import Jsonb

# Mirror of migration 013's live_admin_audit_action_check, kept in lockstep
# by tests/core/test_audit_actions.py.
ALLOWED_ACTIONS = frozenset({
    "plan-applied", "plan-restored", "pin-map-viewed",
    "staff-created", "staff-role-offered", "staff-offer-accepted",
    "staff-offer-declined", "staff-offer-cancelled", "staff-role-removed",
    "staff-password-reset",
    "school-created", "school-updated",
    "bus-created", "bus-updated", "bus-deleted",
    "driver-created", "driver-updated", "driver-deleted",
    "student-created", "student-updated", "student-deleted", "students-imported",
    "route-created", "route-updated", "route-deleted",
    "run-created", "run-updated", "run-deleted", "run-force-closed",
    "absence-marked", "absence-cleared",
    "incident-acknowledged", "incident-deleted",
    "plan-drafted", "plan-updated", "plan-discarded",
    "slot-in-accepted", "slot-in-dismissed",
    "broadcast-sent",
    "parent-updated", "parent-deleted", "parent-link-declined",
    "provider-step-in", "provider-step-out",
    "provider-account-created", "provider-account-removed", "provider-totp-reset",
})

PROVIDER_DISPLAY = "SafeRide"


def _is_provider_actor(actor: dict[str, Any] | None, scope: Any = None) -> bool:
    if scope is not None and getattr(scope, "actor_kind", None) == "provider":
        return True
    return bool(actor and actor.get("provider"))


def actor_display(actor: dict[str, Any] | None, scope: Any = None) -> str:
    """The school-facing name for an actor: their name, or "SafeRide"."""
    if _is_provider_actor(actor, scope):
        return PROVIDER_DISPLAY
    if not actor:
        return "unknown"
    return actor.get("full_name") or actor.get("email") or "unknown"


def masked_display_sql(user_alias: str, provider_alias: str) -> str:
    """SQL CASE for a display column: join ``app_users`` as ``user_alias`` and
    ``provider_accounts`` as ``provider_alias`` on the actor id first."""
    return (
        f"case when {provider_alias}.user_id is not null then '{PROVIDER_DISPLAY}' "
        f"else coalesce({user_alias}.full_name, {user_alias}.email) end"
    )


def record_audit(
    conn: Connection,
    *,
    action: str,
    actor: dict[str, Any] | None,
    scope: Any = None,
    school_id: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> str:
    """Insert one audit row on the caller's connection; returns the row id.

    ``scope`` (a SchoolScope) wins for school, kind and step-in attribution;
    without one, both derive from the enriched actor dict, so pre-conversion
    call sites attribute correctly too. Unknown ``action`` values fail fast —
    the database CHECK would reject them anyway, but a ValueError names the
    bug before a transaction is burned.
    """
    if action not in ALLOWED_ACTIONS:
        raise ValueError(f"audit action {action!r} is not in migration 013's vocabulary")

    provider_actor = _is_provider_actor(actor, scope)
    actor_kind = "provider" if provider_actor else "staff"

    effective_school = school_id
    if scope is not None and getattr(scope, "school_id", None):
        effective_school = scope.school_id

    support_session_id = None
    if provider_actor:
        if scope is not None and getattr(scope, "support_session_id", None):
            support_session_id = scope.support_session_id
        elif actor and actor.get("support_session"):
            support_session_id = actor["support_session"]["id"]

    actor = actor or {}
    # The id is minted client-side, NOT via RETURNING: PostgreSQL applies the
    # table's SELECT policy to INSERT..RETURNING rows, so a read-back would
    # 42501 on any connection whose GUC does not cover the row's school —
    # exactly the GUC-less provider writes the split policy admits on purpose.
    audit_id = str(uuid.uuid4())
    conn.execute(
        """
        insert into live_admin_audit
            (id, actor_id, actor_name, actor_email, actor_kind, support_session_id,
             action, school_id, resource_type, resource_id, detail)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            audit_id,
            actor.get("id"),
            actor.get("full_name") or actor.get("email") or "unknown",
            actor.get("email") or "unknown",
            actor_kind,
            support_session_id,
            action,
            effective_school,
            resource_type,
            str(resource_id) if resource_id is not None else None,
            Jsonb(detail or {}),
        ),
    )
    return audit_id
