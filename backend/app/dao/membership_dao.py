"""School membership data access — the staff surface's access-key rows (U8).

``school_memberships`` rows are access control, not school data (they are not
in ``SCHOOL_OWNED_TABLES``), but every staff-surface method still takes the
request's :class:`SchoolScope` and opens ``get_connection(scope)``: the audit
row written in the same transaction IS school-stamped, so the GUC must be
armed for it (and the strict seam refuses an implicit checkout inside a
school request anyway). The two offer-answer methods run on the auth surface
(no school header, no scope) and deliberately use the global connection.

Every mutating method is one transaction: the ``with get_connection(...)``
block commits on clean exit and rolls back whole on any raise.
"""

import uuid
from typing import Any

from app.core.db import get_connection, get_global_connection
from app.core.errors import ConflictError, NotFoundError
from app.core.scope import STAFF_ROLES, SchoolScope
from app.dao.audit_dao import masked_display_sql, record_audit

LAST_DIRECTOR_MESSAGE = "A school must keep at least one director"
TEMPORARY_PASSWORD_TTL_HOURS = 72


def _offer_uuid_or_404(offer_id: str) -> str:
    """Auth-surface offer ids arrive as raw path text; a malformed id names
    nothing (the not-found contract), never a 500."""
    try:
        return str(uuid.UUID(str(offer_id).strip()))
    except (ValueError, AttributeError) as error:
        raise NotFoundError("Offer not found") from error


class MembershipDao:
    # --- lookups -------------------------------------------------------------

    def find_user_by_email(self, email: str) -> dict[str, Any] | None:
        """Platform-wide identity lookup for staff creation: id, name, and the
        two flags that silence the offer (provider account, disabled). A
        user-table read inside a staff request → global connection (U7 rule)."""
        with get_global_connection() as conn:
            row = conn.execute(
                """
                select u.id, u.full_name, u.disabled_at,
                       exists (select 1 from provider_accounts p
                               where p.user_id = u.id and p.removed_at is null
                       ) as is_provider
                from app_users u
                where lower(u.email) = lower(%s)
                """,
                (email,),
            ).fetchone()
        return dict(row) if row else None

    def list_staff(self, scope: SchoolScope) -> dict[str, list[dict[str, Any]]]:
        """Active director/coordinator members plus open offers at the scope
        school (drivers live on their own page, R5). Members carry
        ``password_changed_at``; offers carry the offering person and date."""
        with get_connection(scope) as conn:
            members = conn.execute(
                """
                select m.id as membership_id, m.role,
                       u.id as user_id, u.full_name, u.email, u.password_changed_at
                from school_memberships m
                join app_users u on u.id = m.user_id
                where m.school_id = %s and m.role = any(%s)
                    and m.state = 'active' and m.removed_at is null
                order by u.full_name asc nulls last, u.email asc
                """,
                (scope.school_id, list(STAFF_ROLES)),
            ).fetchall()
            offers = conn.execute(
                f"""
                select m.id, m.role, m.created_at,
                       u.id as user_id, u.full_name, u.email,
                       {masked_display_sql("ob", "op")} as offered_by_name
                from school_memberships m
                join app_users u on u.id = m.user_id
                left join app_users ob on ob.id = m.offered_by
                left join provider_accounts op
                    on op.user_id = ob.id and op.removed_at is null
                where m.school_id = %s and m.role = any(%s)
                    and m.state = 'offered' and m.removed_at is null
                order by m.created_at asc
                """,
                (scope.school_id, list(STAFF_ROLES)),
            ).fetchall()
        return {
            "members": [dict(r) for r in members],
            "offers": [dict(r) for r in offers],
        }

    def active_staff_roles(self, scope: SchoolScope, user_id: str) -> list[str]:
        with get_connection(scope) as conn:
            rows = conn.execute(
                """
                select role from school_memberships
                where user_id = %s and school_id = %s and role = any(%s)
                    and state = 'active' and removed_at is null
                """,
                (user_id, scope.school_id, list(STAFF_ROLES)),
            ).fetchall()
        return sorted(r["role"] for r in rows)

    # --- staff creation ------------------------------------------------------

    def create_staff_user(
        self, scope: SchoolScope, email: str, password_hash: str,
        full_name: str | None, role: str, *, actor: dict,
    ) -> dict[str, Any]:
        """New identity + active membership + audit, one transaction (R6).

        The temporary password is server-generated by the service; here only
        its hash arrives. ``must_change_password`` locks the session to the
        change screen (R30) and the 72-hour expiry kills the credential if it
        is never used. NO legacy ``app_user_roles`` row: staff authority is
        the membership now — the legacy 'admin' role marks pre-tenancy
        identities only."""
        with get_connection(scope) as conn:
            row = conn.execute(
                """
                insert into app_users
                    (email, password_hash, full_name, must_change_password,
                     temporary_password_expires_at)
                values (%s, %s, %s, true,
                        now() + make_interval(hours => %s))
                returning id, email, full_name
                """,
                (email, password_hash, full_name, TEMPORARY_PASSWORD_TTL_HOURS),
            ).fetchone()
            conn.execute(
                """
                insert into school_memberships
                    (user_id, school_id, role, state, accepted_at)
                values (%s, %s, %s, 'active', now())
                """,
                (row["id"], scope.school_id, role),
            )
            record_audit(
                conn, action="staff-created", actor=actor, scope=scope,
                resource_type="staff", resource_id=row["id"],
                detail={"role": role},
            )
        return dict(row)

    def offer_role(
        self, scope: SchoolScope, user_id: str, role: str, *, actor: dict
    ) -> dict[str, Any]:
        """Offer ``role`` at the scope school to an existing identity (R6).

        An active membership with that role already here → 409; an open offer
        for the same role → returned idempotently (no new row, no new audit).
        The partial unique index (user_id, school_id, role) where removed_at
        is null is the race-proof backstop."""
        with get_connection(scope) as conn:
            active = conn.execute(
                """
                select 1 from school_memberships
                where user_id = %s and school_id = %s and role = %s
                    and state = 'active' and removed_at is null
                """,
                (user_id, scope.school_id, role),
            ).fetchone()
            if active:
                raise ConflictError("Already a member")
            existing = conn.execute(
                """
                select id from school_memberships
                where user_id = %s and school_id = %s and role = %s
                    and state = 'offered' and removed_at is null
                """,
                (user_id, scope.school_id, role),
            ).fetchone()
            if existing:
                return {"offer_id": str(existing["id"]), "created": False}
            row = conn.execute(
                """
                insert into school_memberships
                    (user_id, school_id, role, state, offered_by)
                values (%s, %s, %s, 'offered', %s)
                returning id
                """,
                (user_id, scope.school_id, role, scope.user_id),
            ).fetchone()
            record_audit(
                conn, action="staff-role-offered", actor=actor, scope=scope,
                resource_type="staff-offer", resource_id=row["id"],
                detail={"role": role, "user_id": str(user_id)},
            )
        return {"offer_id": str(row["id"]), "created": True}

    # --- removal (last-director lock) ---------------------------------------

    def remove_staff(self, scope: SchoolScope, user_id: str, *, actor: dict) -> None:
        """End the target's active staff membership(s) at this school (R10).

        Lock order: the school's ACTIVE director rows first (``order by id
        for update`` — the apply_restore convention), then the target's own
        staff rows, so concurrent removals serialise and can never race the
        school down to zero directors (AE4). Offered rows never count.
        Self-removal follows the same rule.

        Sessions are revoked only when the target keeps no other active
        membership, no parent role and no provider account — otherwise scope
        resolution drops this school on their very next request anyway (U5)."""
        with get_connection(scope) as conn:
            directors = conn.execute(
                """
                select id, user_id from school_memberships
                where school_id = %s and role = 'director'
                    and state = 'active' and removed_at is null
                order by id for update
                """,
                (scope.school_id,),
            ).fetchall()
            targets = conn.execute(
                """
                select id, role from school_memberships
                where user_id = %s and school_id = %s and role = any(%s)
                    and state = 'active' and removed_at is null
                order by id for update
                """,
                (user_id, scope.school_id, list(STAFF_ROLES)),
            ).fetchall()
            if not targets:
                raise NotFoundError("Staff member not found")
            removes_director = any(t["role"] == "director" for t in targets)
            if removes_director:
                remaining = [d for d in directors if str(d["user_id"]) != str(user_id)]
                if not remaining:
                    raise ConflictError(LAST_DIRECTOR_MESSAGE)
            conn.execute(
                "update school_memberships set removed_at = now() where id = any(%s)",
                ([t["id"] for t in targets],),
            )
            holds_more = conn.execute(
                """
                select
                  exists (select 1 from school_memberships
                          where user_id = %(id)s and state = 'active'
                            and removed_at is null) as memberships,
                  exists (select 1 from provider_accounts
                          where user_id = %(id)s and removed_at is null) as provider,
                  exists (select 1 from app_user_roles
                          where user_id = %(id)s and role = 'parent') as parent_role
                """,
                {"id": user_id},
            ).fetchone()
            if not any(holds_more.values()):
                conn.execute(
                    "update auth_sessions set revoked_at = now() "
                    "where user_id = %s and revoked_at is null",
                    (user_id,),
                )
            record_audit(
                conn, action="staff-role-removed", actor=actor, scope=scope,
                resource_type="staff", resource_id=user_id,
                detail={"roles": sorted(t["role"] for t in targets)},
            )

    def cancel_offer(self, scope: SchoolScope, offer_id: str, *, actor: dict) -> None:
        offer_id = _offer_uuid_or_404(offer_id)
        with get_connection(scope) as conn:
            row = conn.execute(
                """
                update school_memberships set removed_at = now()
                where id = %s and school_id = %s and role = any(%s)
                    and state = 'offered' and removed_at is null
                returning role
                """,
                (offer_id, scope.school_id, list(STAFF_ROLES)),
            ).fetchone()
            if not row:
                raise NotFoundError("Offer not found")
            record_audit(
                conn, action="staff-offer-cancelled", actor=actor, scope=scope,
                resource_type="staff-offer", resource_id=offer_id,
                detail={"role": row["role"]},
            )

    # --- staff password reset ------------------------------------------------

    def reset_staff_password(
        self, scope: SchoolScope, user_id: str, password_hash: str, *, actor: dict
    ) -> list[str]:
        """Replace the target's password with a temporary one (AE23), one
        transaction: flag + 72-hour expiry, every session revoked, and every
        outstanding reset and pre-auth token voided — the old credentials and
        recovery paths all die together. Returns the target's staff roles."""
        with get_connection(scope) as conn:
            roles = [
                r["role"]
                for r in conn.execute(
                    """
                    select role from school_memberships
                    where user_id = %s and school_id = %s and role = any(%s)
                        and state = 'active' and removed_at is null
                    """,
                    (user_id, scope.school_id, list(STAFF_ROLES)),
                ).fetchall()
            ]
            if not roles:
                raise NotFoundError("Staff member not found")
            conn.execute(
                """
                update app_users
                set password_hash = %s, must_change_password = true,
                    temporary_password_expires_at = now() + make_interval(hours => %s),
                    password_changed_at = null
                where id = %s
                """,
                (password_hash, TEMPORARY_PASSWORD_TTL_HOURS, user_id),
            )
            conn.execute(
                "update auth_sessions set revoked_at = now() "
                "where user_id = %s and revoked_at is null",
                (user_id,),
            )
            conn.execute(
                "update password_reset_tokens set used_at = now() "
                "where user_id = %s and used_at is null",
                (user_id,),
            )
            conn.execute(
                "delete from auth_preauth_tokens where user_id = %s", (user_id,)
            )
            record_audit(
                conn, action="staff-password-reset", actor=actor, scope=scope,
                resource_type="staff", resource_id=user_id,
                detail={"roles": sorted(roles)},
            )
        return sorted(roles)

    # --- the account's own offer answers (auth surface, no school header) ----

    def accept_offer(self, offer_id: str, *, actor: dict) -> dict[str, Any]:
        """Activate an offer belonging to the CALLING user (AE15). Runs on the
        auth surface: no scope exists, memberships are access-control rows —
        global connection; the audit is stamped with the offer's own school."""
        offer_id = _offer_uuid_or_404(offer_id)
        with get_global_connection() as conn:
            row = conn.execute(
                """
                update school_memberships
                set state = 'active', accepted_at = now()
                where id = %s and user_id = %s
                    and state = 'offered' and removed_at is null
                returning school_id, role
                """,
                (offer_id, actor["id"]),
            ).fetchone()
            if not row:
                raise NotFoundError("Offer not found")
            # Staff-kind audit rows must land inside an armed school GUC
            # (migration 015's split policy) — arm it from the offer's own
            # school; transaction-local, so nothing leaks past this call.
            conn.execute(
                "select set_config('saferide.school_ids', %s, true)",
                (str(row["school_id"]),),
            )
            record_audit(
                conn, action="staff-offer-accepted", actor=actor,
                school_id=str(row["school_id"]),
                resource_type="staff-offer", resource_id=offer_id,
                detail={"role": row["role"]},
            )
        return {"school_id": str(row["school_id"]), "role": row["role"]}

    def decline_offer(self, offer_id: str, *, actor: dict) -> dict[str, Any]:
        offer_id = _offer_uuid_or_404(offer_id)
        with get_global_connection() as conn:
            row = conn.execute(
                """
                update school_memberships set removed_at = now()
                where id = %s and user_id = %s
                    and state = 'offered' and removed_at is null
                returning school_id, role
                """,
                (offer_id, actor["id"]),
            ).fetchone()
            if not row:
                raise NotFoundError("Offer not found")
            # Staff-kind audit rows must land inside an armed school GUC
            # (migration 015's split policy) — arm it from the offer's own
            # school; transaction-local, so nothing leaks past this call.
            conn.execute(
                "select set_config('saferide.school_ids', %s, true)",
                (str(row["school_id"]),),
            )
            record_audit(
                conn, action="staff-offer-declined", actor=actor,
                school_id=str(row["school_id"]),
                resource_type="staff-offer", resource_id=offer_id,
                detail={"role": row["role"]},
            )
        return {"school_id": str(row["school_id"]), "role": row["role"]}
