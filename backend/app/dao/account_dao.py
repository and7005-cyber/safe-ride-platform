from typing import Any

from app.core.db import get_connection, get_global_connection
from app.core.errors import ConflictError, NotFoundError
from app.core.scope import ParentScope, SchoolScope
from app.dao.audit_dao import record_audit
from app.dao.student_live_dao import _ConnParentLinks, link_account_to_matching_students
from app.dao.verification_sql import verified_sql

# Neutral wording for a shared parent (R33): names no other school, discloses
# nothing beyond "shared" — which the school already knows from its own card.
PARENT_SHARED_MESSAGE = (
    "This parent is shared with another school and cannot be changed here"
)


def _assert_parent_editable_here(conn, parent_id: str, school_id: str) -> None:
    """The school-side powers over a parent account (U6/R33): the parent must
    be linked to one of THIS school's students (else they do not exist here —
    404), and any link (any status) to another school freezes the account for
    this school (409, neutral message)."""
    linked_here = conn.execute(
        "select 1 from live_parent_students ps "
        "join live_students s on s.id = ps.student_id "
        "where ps.parent_id = %s and s.school_id = %s limit 1",
        (parent_id, school_id),
    ).fetchone()
    if not linked_here:
        raise NotFoundError("Parent not found")
    elsewhere = conn.execute(
        "select 1 from live_parent_students ps "
        "join live_students s on s.id = ps.student_id "
        "where ps.parent_id = %s and s.school_id is not null and s.school_id <> %s "
        "limit 1",
        (parent_id, school_id),
    ).fetchone()
    if elsewhere:
        raise ConflictError(PARENT_SHARED_MESSAGE)


def _restart_verification(
    conn, user_id, verify_token_hash: str | None, verify_ttl_minutes: int
) -> dict[str, int]:
    """An office email change (execution decisions, Release 1b): the new
    address is unproven, so the account is unverified again (D1: required
    true, verified_at null) and everything bound to the old address ends —
    every emailed token (both purposes), pre-auth tokens, every session and
    every push registration. One fresh verification token is written for the
    new address. Links are left in place. Returns counts for the audit row
    (never addresses)."""
    conn.execute(
        "update app_users set email_verification_required = true, "
        "email_verified_at = null where id = %s",
        (user_id,),
    )
    tokens = conn.execute(
        "update password_reset_tokens set used_at = now() "
        "where user_id = %s and used_at is null",
        (user_id,),
    ).rowcount
    conn.execute("delete from auth_preauth_tokens where user_id = %s", (user_id,))
    sessions = conn.execute(
        "update auth_sessions set revoked_at = now() "
        "where user_id = %s and revoked_at is null",
        (user_id,),
    ).rowcount
    push = conn.execute(
        "delete from live_push_subscriptions where user_id = %s", (user_id,)
    ).rowcount
    push += conn.execute("delete from live_fcm_tokens where user_id = %s", (user_id,)).rowcount
    if verify_token_hash:
        conn.execute(
            """
            insert into password_reset_tokens (user_id, token_hash, purpose, expires_at)
            values (%s, %s, 'verify', now() + make_interval(mins => %s))
            """,
            (user_id, verify_token_hash, verify_ttl_minutes),
        )
    return {
        "tokens_voided": tokens or 0,
        "sessions_revoked": sessions or 0,
        "push_registrations_removed": push or 0,
    }


class AccountDao:
    # --- drivers -----------------------------------------------------------

    def list_drivers(self, scope: SchoolScope) -> list[dict[str, Any]]:
        # Membership is the access key (U6): only drivers with an ACTIVE
        # driver membership at the scope school, with any bus assignment
        # resolved against this school's own buses.
        with get_connection(scope) as conn:
            rows = conn.execute(
                """
                select u.id, u.full_name, u.email, u.phone,
                       (u.pin_hash is not null) as has_pin,
                       b.name as assigned_bus, u.created_at
                from app_users u
                join school_memberships m on m.user_id = u.id
                    and m.school_id = %s and m.role = 'driver'
                    and m.state = 'active' and m.removed_at is null
                left join live_buses b on b.driver_id = u.id and b.school_id = %s
                order by u.full_name asc
                """,
                (scope.school_id, scope.school_id),
            ).fetchall()
        return [dict(r) for r in rows]

    def create_driver(
        self, scope: SchoolScope, email, password_hash, full_name, phone, pin_hash,
        *, actor: dict,
    ) -> dict[str, Any]:
        with get_connection(scope) as conn:
            row = conn.execute(
                "insert into app_users (email, password_hash, full_name, phone, pin_hash) "
                "values (%s, %s, %s, %s, %s) returning id, full_name, email, phone",
                (email, password_hash, full_name, phone, pin_hash),
            ).fetchone()
            # Legacy role row kept through the transition window: PIN login
            # resolves drivers via app_user_roles (no school context), and the
            # Release 3 rollback path reads it too.
            conn.execute(
                "insert into app_user_roles (user_id, role) values (%s, 'driver')", (row["id"],)
            )
            # The access key itself (R28): an active driver membership at the
            # creating school, in the same transaction as the identity.
            conn.execute(
                "insert into school_memberships (user_id, school_id, role, state, accepted_at) "
                "values (%s, %s, 'driver', 'active', now())",
                (row["id"], scope.school_id),
            )
            record_audit(
                conn, action="driver-created", actor=actor, scope=scope,
                resource_type="driver", resource_id=row["id"], detail={},
            )
        return dict(row)

    def update_driver(
        self, scope: SchoolScope, driver_id, full_name, email, phone, pin_hash,
        *, actor: dict,
    ) -> dict[str, Any]:
        membership_predicate = (
            "exists (select 1 from school_memberships m "
            "where m.user_id = app_users.id and m.school_id = %s "
            "and m.role = 'driver' and m.state = 'active' and m.removed_at is null)"
        )
        with get_connection(scope) as conn:
            if pin_hash is not None:
                row = conn.execute(
                    "update app_users set full_name=%s, email=%s, phone=%s, pin_hash=%s "
                    f"where id=%s and {membership_predicate} "
                    "returning id, full_name, email, phone",
                    (full_name, email, phone, pin_hash, driver_id, scope.school_id),
                ).fetchone()
            else:
                row = conn.execute(
                    "update app_users set full_name=%s, email=%s, phone=%s "
                    f"where id=%s and {membership_predicate} "
                    "returning id, full_name, email, phone",
                    (full_name, email, phone, driver_id, scope.school_id),
                ).fetchone()
            if not row:
                # No active driver membership here: this driver does not exist
                # for the caller's school (R3's not-found contract).
                raise NotFoundError("Driver not found")
            record_audit(
                conn, action="driver-updated", actor=actor, scope=scope,
                resource_type="driver", resource_id=driver_id, detail={},
            )
        return dict(row)

    def delete_driver(self, scope: SchoolScope, driver_id: str, *, actor: dict) -> None:
        """Remove the driver FROM THIS SCHOOL (U6): end the membership, clear
        any bus assignment here, and delete the identity only when it holds
        nothing else — another membership, a provider account or parent links
        keep the account alive (the person exists beyond this school)."""
        with get_connection(scope) as conn:
            # Lock this school's affected bus rows first (bus -> membership,
            # the same cross-table order the assignment paths take).
            conn.execute(
                "select id from live_buses where driver_id = %s and school_id = %s "
                "order by id for update",
                (driver_id, scope.school_id),
            ).fetchall()
            removed = conn.execute(
                "update school_memberships set removed_at = now() "
                "where user_id = %s and school_id = %s and role = 'driver' "
                "and removed_at is null returning id",
                (driver_id, scope.school_id),
            ).fetchall()
            if not removed:
                raise NotFoundError("Driver not found")
            conn.execute(
                "update live_buses set driver_id = null "
                "where driver_id = %s and school_id = %s",
                (driver_id, scope.school_id),
            )
            holds_more = conn.execute(
                """
                select
                  exists (select 1 from school_memberships
                          where user_id = %(id)s and removed_at is null) as memberships,
                  exists (select 1 from provider_accounts
                          where user_id = %(id)s) as provider,
                  exists (select 1 from live_parent_students
                          where parent_id = %(id)s) as parent_links,
                  exists (select 1 from app_user_roles
                          where user_id = %(id)s and role = 'parent') as parent_role
                """,
                {"id": driver_id},
            ).fetchone()
            identity_deleted = not any(holds_more.values())
            if identity_deleted:
                conn.execute("delete from app_users where id = %s", (driver_id,))
            record_audit(
                conn, action="driver-deleted", actor=actor, scope=scope,
                resource_type="driver", resource_id=driver_id,
                detail={"identity_deleted": identity_deleted},
            )

    # --- parents -----------------------------------------------------------

    def list_parents(self, scope: SchoolScope) -> list[dict[str, Any]]:
        with get_connection(scope) as conn:
            # Registered parents: linked (any status) to one of THIS school's
            # students, showing ONLY the children at this school (R33/AE18).
            registered = conn.execute(
                """
                select u.id, u.full_name, u.email, u.phone, u.created_at,
                       coalesce(array_agg(s.name order by s.name)
                                filter (where s.name is not null), '{}') as students,
                       exists (
                           select 1 from live_parent_students po
                           join live_students so on so.id = po.student_id
                           where po.parent_id = u.id
                             and so.school_id is distinct from %s
                       ) as shared
                from app_users u
                join app_user_roles r on r.user_id = u.id and r.role = 'parent'
                join live_parent_students ps on ps.parent_id = u.id
                join live_students s on s.id = ps.student_id and s.school_id = %s
                group by u.id
                order by u.full_name asc
                """,
                (scope.school_id, scope.school_id),
            ).fetchall()
            # `shared` (AE18's UI half): a link — any status — to a child of
            # another school freezes this parent here; the page disables Edit
            # up front instead of leaving the server 409 as the only signal.
            result = [
                {**dict(r), "status": "registered", "students": list(r["students"])}
                for r in registered
            ]
            # Pending parents: an email in either parent slot of THIS school's
            # students with no matching account (R11 — Parent 2 shows up as
            # pending too). Slot email only, no account name/phone to leak.
            # An account still waiting for its verification click does not
            # count as a match (hardening U8): its slot keeps reading
            # "Awaiting signup" until the owner verifies.
            pending = conn.execute(
                f"""
                with slots as (
                    select parent_email as email, parent_name as pname, name
                    from live_students
                    where parent_email is not null and school_id = %s
                    union all
                    select parent2_email, parent2_name, name
                    from live_students
                    where parent2_email is not null and school_id = %s
                )
                select email as parent_email, pname as parent_name,
                       coalesce(array_agg(name) filter (where name is not null), '{{}}') as students
                from slots
                where lower(email) not in (
                        select lower(u.email) from app_users u
                        join app_user_roles r on r.user_id = u.id and r.role = 'parent'
                        where {verified_sql("u")}
                    )
                group by email, pname
                order by pname asc
                """,
                (scope.school_id, scope.school_id),
            ).fetchall()
            for p in pending:
                result.append({
                    "id": None,
                    "full_name": p["parent_name"],
                    "email": p["parent_email"],
                    "phone": None,
                    "status": "pending",
                    "students": list(p["students"]),
                    "created_at": None,
                    "shared": False,
                })
        return result

    def update_parent(
        self, scope: SchoolScope, parent_id, full_name, email, phone, *, actor: dict,
        verify_token_hash: str | None = None, verify_ttl_minutes: int = 24 * 60,
    ) -> dict[str, Any]:
        """The office's edit of a parent account. A changed email address
        (case-insensitively) restarts verification in the same transaction
        (hardening Release 1b): see ``_restart_verification``. Returns the
        row plus ``email_changed`` and ``previous_email`` — internal fields
        the service strips after sending the two mails."""
        with get_connection(scope) as conn:
            _assert_parent_editable_here(conn, parent_id, scope.school_id)
            before = conn.execute(
                "select email from app_users where id = %s for update", (parent_id,)
            ).fetchone()
            if not before:
                raise NotFoundError("Parent not found")
            row = conn.execute(
                "update app_users set full_name=%s, email=%s, phone=%s where id=%s "
                "returning id, full_name, email, phone",
                (full_name, email, phone, parent_id),
            ).fetchone()
            if not row:
                raise NotFoundError("Parent not found")
            record_audit(
                conn, action="parent-updated", actor=actor, scope=scope,
                resource_type="parent", resource_id=parent_id, detail={},
            )
            email_changed = (
                str(before["email"]).strip().lower() != str(row["email"]).strip().lower()
            )
            if email_changed:
                counts = _restart_verification(
                    conn, parent_id, verify_token_hash, verify_ttl_minutes
                )
                record_audit(
                    conn, action="parent-email-changed", actor=actor, scope=scope,
                    resource_type="parent", resource_id=parent_id, detail=counts,
                )
        return {
            **dict(row),
            "email_changed": email_changed,
            "previous_email": before["email"] if email_changed else None,
        }

    def delete_parent(self, scope: SchoolScope, parent_id: str, *, actor: dict) -> None:
        with get_connection(scope) as conn:
            _assert_parent_editable_here(conn, parent_id, scope.school_id)
            conn.execute("delete from app_users where id = %s", (parent_id,))
            record_audit(
                conn, action="parent-deleted", actor=actor, scope=scope,
                resource_type="parent", resource_id=parent_id, detail={},
            )

    # --- unscoped account plumbing (signup path) ----------------------------

    def email_exists(self, email: str) -> bool:
        # Platform-wide user-table read (an email in use ANYWHERE refuses):
        # called inside staff-scoped requests too, so it takes the global
        # connection — the U7 rule for auth/user-table access.
        with get_global_connection() as conn:
            row = conn.execute(
                "select 1 from app_users where lower(email) = lower(%s)", (email,)
            ).fetchone()
        return bool(row)

    def link_parent_to_matching_students(self, parent_id, email: str) -> int:
        """Link a (new) parent account to every student carrying its email in
        either parent slot (R11/R31), honouring the per-student link cap.
        Returns the number of links created. Since Release 1b this runs when
        a new account VERIFIES its address (verify-email, or an emailed reset
        that verifies it), never at sign-up, and refuses an unverified one.

        Matching reads through migration 013's SECURITY DEFINER
        ``parent_signup_matches`` — the platform-wide scan has no scope of its
        own, and the DAO raises rather than degrading to zero matches if the
        function is missing. Matches grouped by school decide the link status
        (one school → ``accepted``, more → all ``pending`` — U11); the links
        and the per-school incidents are then created under a constructed
        ``ParentScope`` of the matched schools, passed explicitly to
        ``get_connection`` so the writes pass the database backstop.

        A verified address proves the inbox, not the parenthood, so every
        school with created links also gets an admin-visible incident
        (school-stamped, no bus/student stamp, so it never reaches a parent
        feed): the office knows its families and can catch an email claimed
        by the wrong person before it matters.
        """
        with get_global_connection() as conn:
            eligible = conn.execute(
                f"""
                select 1 from app_users u
                where u.id = %s and u.disabled_at is null
                  and {verified_sql("u")}
                  and not exists (select 1 from provider_accounts p
                                  where p.user_id = u.id and p.removed_at is null)
                """,
                (parent_id,),
            ).fetchone()
            if not eligible:
                # U8 hygiene: a provider or disabled identity is never linked
                # as a parent, even if its email lands in a student slot — nor
                # (hardening U8) an account whose address is not verified yet.
                return 0
            matches = _ConnParentLinks(conn).signup_matches(email)
        if not matches:
            return 0
        matched_schools = tuple(
            sorted({str(m["school_id"]) for m in matches if m["school_id"] is not None})
        )
        scope = ParentScope(user_id=str(parent_id), school_ids=matched_schools)
        with get_connection(scope) as conn:
            created = link_account_to_matching_students(_ConnParentLinks(conn), parent_id, email)
            by_school: dict[str, list[dict]] = {}
            for link in created:
                if link["school_id"] is not None:
                    by_school.setdefault(str(link["school_id"]), []).append(link)
            for school_id, links in sorted(by_school.items()):
                names = [
                    r["name"]
                    for r in conn.execute(
                        "select name from live_students where id = any(%s) order by name",
                        ([link["student_id"] for link in links],),
                    ).fetchall()
                ]
                if links[0]["status"] == "accepted":
                    description = (
                        f"Parent signup auto-linked: {email} now tracks "
                        f"{', '.join(names)} — verify this is the child's parent."
                    )
                else:
                    description = (
                        f"Parent signup: {email} matches {', '.join(names)} — "
                        "the link is awaiting the parent's confirmation. "
                        "Verify this is the child's parent."
                    )
                conn.execute(
                    "insert into live_incidents (type, description, school_id) "
                    "values ('other', %s, %s)",
                    (description, school_id),
                )
            return len(created)
