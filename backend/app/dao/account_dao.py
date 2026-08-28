from typing import Any

from app.core.db import get_connection
from app.core.errors import ConflictError, NotFoundError
from app.core.scope import SchoolScope
from app.dao.audit_dao import record_audit
from app.dao.student_live_dao import _ConnParentLinks, link_account_to_matching_students

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
                                filter (where s.name is not null), '{}') as students
                from app_users u
                join app_user_roles r on r.user_id = u.id and r.role = 'parent'
                join live_parent_students ps on ps.parent_id = u.id
                join live_students s on s.id = ps.student_id and s.school_id = %s
                group by u.id
                order by u.full_name asc
                """,
                (scope.school_id,),
            ).fetchall()
            result = [
                {**dict(r), "status": "registered", "students": list(r["students"])}
                for r in registered
            ]
            # Pending parents: an email in either parent slot of THIS school's
            # students with no matching account (R11 — Parent 2 shows up as
            # pending too). Slot email only, no account name/phone to leak.
            pending = conn.execute(
                """
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
                       coalesce(array_agg(name) filter (where name is not null), '{}') as students
                from slots
                where lower(email) not in (
                        select lower(email) from app_users u
                        join app_user_roles r on r.user_id = u.id and r.role = 'parent'
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
                })
        return result

    def update_parent(
        self, scope: SchoolScope, parent_id, full_name, email, phone, *, actor: dict
    ) -> dict[str, Any]:
        with get_connection(scope) as conn:
            _assert_parent_editable_here(conn, parent_id, scope.school_id)
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
        return dict(row)

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
        with get_connection() as conn:
            row = conn.execute(
                "select 1 from app_users where lower(email) = lower(%s)", (email,)
            ).fetchone()
        return bool(row)

    def link_parent_to_matching_students(self, parent_id, email: str) -> int:
        """Link a (new) parent account to every student carrying its email in
        either parent slot (R11), honouring the per-student link cap. Returns
        the number of links created.

        Signup emails are self-asserted and unverified, so every auto-link
        also raises an admin-visible incident (no bus/student stamp, so it
        never reaches a parent feed): the office knows its families and can
        catch an email claimed by the wrong person before it matters.
        """
        with get_connection() as conn:
            created = link_account_to_matching_students(_ConnParentLinks(conn), parent_id, email)
            if created:
                names = [
                    r["name"]
                    for r in conn.execute(
                        """
                        select s.name from live_students s
                        join live_parent_students ps on ps.student_id = s.id
                        where ps.parent_id = %s order by s.name
                        """,
                        (parent_id,),
                    ).fetchall()
                ]
                conn.execute(
                    "insert into live_incidents (type, description) values ('other', %s)",
                    (
                        f"Parent signup auto-linked: {email} now tracks "
                        f"{', '.join(names)} — verify this is the child's parent.",
                    ),
                )
            return created
