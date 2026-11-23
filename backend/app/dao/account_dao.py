import uuid
from typing import Any

from psycopg import errors as pg_errors

from app.core.db import get_connection, get_global_connection
from app.core.errors import AccountRefusedError, ConflictError, NotFoundError
from app.core.scope import ParentScope, SchoolScope
from app.dao.audit_dao import record_audit
from app.dao.student_live_dao import _ConnParentLinks, link_account_to_matching_students
from app.dao.verification_sql import unverified_sql, verified_sql

# Neutral wording for a shared parent (R33): names no other school, discloses
# nothing beyond "shared" — which the school already knows from its own card.
PARENT_SHARED_MESSAGE = (
    "This parent is shared with another school and cannot be changed here"
)

# Every temporary password — staff created or reset (U8), parent reset by the
# office (hardening U9) — dies unused after this long (R6).
TEMPORARY_PASSWORD_TTL_HOURS = 72

# The office parent reset's refusal (hardening plan, Key Technical Decisions):
# one neutral sentence whatever the reason — a membership of any role at any
# school (drivers sign in by password too), or a provider account. It never
# names the school or the role.
NOT_RESETTABLE_HERE_CODE = "not-resettable-here"
NOT_RESETTABLE_HERE_MESSAGE = "This account cannot be reset from the Parents page"

# "Confirm and reset" on an Awaiting-signup slot (execution decisions,
# Release 1b; DL12): the one outcome for every slot with nothing behind it —
# no account, an already verified or grandfathered one, a disabled one, or
# one the Parents page may not reset — so the page never tells the office
# which addresses hold an account.
NOTHING_TO_CONFIRM_CODE = "nothing-to-confirm"
NOTHING_TO_CONFIRM_MESSAGE = "Nothing to confirm yet. Ask the parent to sign up with this email."

# The office's parent EMAIL change on an identity holding other access (the
# same predicate as the reset's refusal): a new address hands over the
# recovery channel, so once mail is on, Forgot password would let one
# school's office sign in as a driver, staff member or provider. Name and
# phone edits stay allowed. One neutral sentence, never the school or role.
NOT_EDITABLE_HERE_CODE = "not-editable-here"
NOT_EDITABLE_HERE_MESSAGE = "This account's email cannot be changed from the Parents page"

# Holds access the Parents page must never reach: a membership row of ANY
# role at ANY school that is not removed (offered rows included), or a live
# provider account. Drivers are memberships and sign in by password, so "no
# staff membership" alone would let one school's office sign in as another
# school's driver.
_HOLDS_OTHER_ACCESS_SQL = (
    "(exists (select 1 from school_memberships m"
    " where m.user_id = {u}.id and m.removed_at is null)"
    " or exists (select 1 from provider_accounts p"
    " where p.user_id = {u}.id and p.removed_at is null))"
)


# Driver PINs (hardening U10). A PIN is unique within its school only: the
# office's refusal names no other school and never says whether the PIN
# exists anywhere else. A PIN holder drives at ONE school (execution
# decisions, Release 1b): giving a PIN to an identity with an active driver
# membership elsewhere is refused, because one PIN binds one school.
PIN_IN_USE_MESSAGE = "That PIN is already in use at this school"
DRIVER_ELSEWHERE_MESSAGE = (
    "This driver already drives for another school. "
    "A driver can sign in by PIN at one school only."
)

_ACTIVE_DRIVER_SQL = (
    "m.role = 'driver' and m.state = 'active' and m.removed_at is null"
)


def _assert_pin_assignable(
    conn, school_id: str, user_id: str | None, pin_hashes: tuple[str, str]
) -> None:
    """Before a PIN is written for ``user_id`` (None: a driver being created)
    at ``school_id``: refuse an identity that drives elsewhere, then refuse
    the PIN when any OTHER active driver at this school holds it in either
    form — the school-salted hash or the legacy global one — so a salted and
    a legacy hash of one PIN never coexist at a school. The global unique
    index on app_users.pin_hash stays the race-proof backstop (same
    message, see account_service._map_unique_violation)."""
    salted, legacy = pin_hashes
    if user_id is not None:
        elsewhere = conn.execute(
            "select 1 from school_memberships m "
            f"where m.user_id = %s and m.school_id <> %s and {_ACTIVE_DRIVER_SQL} limit 1",
            (user_id, school_id),
        ).fetchone()
        if elsewhere:
            raise ConflictError(DRIVER_ELSEWHERE_MESSAGE)
    taken = conn.execute(
        "select 1 from school_memberships m join app_users u on u.id = m.user_id "
        f"where m.school_id = %s and {_ACTIVE_DRIVER_SQL} "
        "and u.pin_hash in (%s, %s) and u.id is distinct from %s limit 1",
        (school_id, salted, legacy, user_id),
    ).fetchone()
    if taken:
        raise ConflictError(PIN_IN_USE_MESSAGE)


def _is_uuid(value) -> bool:
    try:
        uuid.UUID(str(value).strip())
    except (ValueError, AttributeError):
        return False
    return True


def _parent_uuid_or_404(parent_id: str) -> str:
    """Raw path text names nothing when it is not a uuid (the not-found
    contract), never a 500."""
    try:
        return str(uuid.UUID(str(parent_id).strip()))
    except (ValueError, AttributeError) as error:
        raise NotFoundError("Parent not found") from error


def issue_temporary_password(
    conn, user_id, password_hash: str, *, delete_push: bool
) -> dict[str, int]:
    """THE office-issued temporary password (staff reset, U8; parent reset
    and Confirm and reset, hardening U9), on the caller's transaction:

    - the hash replaced, ``must_change_password`` set, a 72-hour expiry, and
      ``password_changed_at`` cleared (the Staff page's "—");
    - every session revoked, every emailed token (both purposes) voided and
      every pre-auth token deleted — the old credential and every recovery
      path die together, and a second reset kills the first's password;
    - with ``delete_push``, every push registration deleted: a parent reset
      usually follows a lost phone, which must stop receiving child notices
      (the parent's own browser re-attaches at its next sign-in).

    Writes no audit row: each caller records its own action. Returns counts
    for that row — never the password, never an address."""
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
    sessions = conn.execute(
        "update auth_sessions set revoked_at = now() "
        "where user_id = %s and revoked_at is null",
        (user_id,),
    ).rowcount
    tokens = conn.execute(
        "update password_reset_tokens set used_at = now() "
        "where user_id = %s and used_at is null",
        (user_id,),
    ).rowcount
    conn.execute("delete from auth_preauth_tokens where user_id = %s", (user_id,))
    push = 0
    if delete_push:
        push = conn.execute(
            "delete from live_push_subscriptions where user_id = %s", (user_id,)
        ).rowcount or 0
        push += conn.execute(
            "delete from live_fcm_tokens where user_id = %s", (user_id,)
        ).rowcount or 0
    return {
        "sessions_revoked": sessions or 0,
        "tokens_voided": tokens or 0,
        "push_registrations_removed": push,
    }


# A link's school, for "is this parent shared with another school?". The link
# row's own stamp first: under the runtime role's row security (015) another
# school's students are invisible, so a student join alone never saw the
# other school — a shared parent read as unshared and stayed editable from
# either school (found in hardening U9). A link without a stamp falls back
# to its student's school, and one whose school cannot be seen counts as
# elsewhere (frozen, the safe side). The caller left-joins the link's student.
def _link_school_sql(link: str, student: str) -> str:
    return f"coalesce({link}.school_id, {student}.school_id)"


def _linked_elsewhere(conn, parent_id: str, school_id: str) -> bool:
    """Any link (any status) of the parent's whose school is not ``school_id``
    — read from the link's own stamp, see ``_link_school_sql``."""
    return conn.execute(
        "select 1 from live_parent_students ps "
        "left join live_students s on s.id = ps.student_id "
        f"where ps.parent_id = %s and {_link_school_sql('ps', 's')} is distinct from %s "
        "limit 1",
        (parent_id, school_id),
    ).fetchone() is not None


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
    if _linked_elsewhere(conn, parent_id, school_id):
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
        self, scope: SchoolScope, email, password_hash, full_name, phone,
        pin_hashes: tuple[str, str] | None,
        *, actor: dict,
    ) -> dict[str, Any]:
        """``pin_hashes`` is (school-salted, legacy) for a new PIN, or None:
        the salted form is stored, both are checked against this school's
        drivers first (hardening U10)."""
        pin_hash = pin_hashes[0] if pin_hashes else None
        with get_connection(scope) as conn:
            if pin_hashes:
                _assert_pin_assignable(conn, scope.school_id, None, pin_hashes)
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
        self, scope: SchoolScope, driver_id, full_name, email, phone,
        pin_hashes: tuple[str, str] | None,
        *, actor: dict,
    ) -> dict[str, Any]:
        membership_predicate = (
            "exists (select 1 from school_memberships m "
            "where m.user_id = app_users.id and m.school_id = %s "
            "and m.role = 'driver' and m.state = 'active' and m.removed_at is null)"
        )
        pin_hash = pin_hashes[0] if pin_hashes else None
        with get_connection(scope) as conn:
            if pin_hashes:
                is_driver_here = conn.execute(
                    "select 1 from school_memberships m "
                    f"where m.user_id = %s and m.school_id = %s and {_ACTIVE_DRIVER_SQL}",
                    (str(driver_id), scope.school_id),
                ).fetchone() if _is_uuid(driver_id) else None
                if not is_driver_here:
                    raise NotFoundError("Driver not found")
                _assert_pin_assignable(conn, scope.school_id, str(driver_id), pin_hashes)
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

    # --- the driver code (hardening U10) --------------------------------------

    def regenerate_driver_code(self, scope: SchoolScope, *, actor: dict) -> str:
        """Replace the scope school's driver code with a fresh one from the
        database generator (017's ``saferide_new_driver_code()``), audited as
        ``driver-code-regenerated`` with ids only — never the code. A
        (40-bit, practically impossible) collision on the unique index draws
        again."""
        with get_connection(scope) as conn:
            row = None
            for _ in range(3):
                try:
                    with conn.transaction():
                        row = conn.execute(
                            "update live_schools set driver_code = saferide_new_driver_code() "
                            "where id = %s returning driver_code",
                            (scope.school_id,),
                        ).fetchone()
                    break
                except pg_errors.UniqueViolation:
                    continue
            if not row:
                raise NotFoundError("School not found")
            record_audit(
                conn, action="driver-code-regenerated", actor=actor, scope=scope,
                resource_type="school", resource_id=scope.school_id, detail={},
            )
        return row["driver_code"]

    # --- parents -----------------------------------------------------------

    def list_parents(self, scope: SchoolScope) -> list[dict[str, Any]]:
        with get_connection(scope) as conn:
            # Registered parents: linked (any status) to one of THIS school's
            # students, showing ONLY the children at this school (R33/AE18).
            registered = conn.execute(
                """
                select u.id, u.full_name, u.email, u.phone, u.created_at,
                       u.must_change_password, u.temporary_password_expires_at,
                       coalesce(array_agg(s.name order by s.name)
                                filter (where s.name is not null), '{}') as students,
                       bool_or(ps.status = 'accepted') as link_accepted,
                       exists (
                           select 1 from live_parent_students po
                           left join live_students so on so.id = po.student_id
                           where po.parent_id = u.id
                             and """ + _link_school_sql("po", "so") + """ is distinct from %s
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
            # another school freezes this parent's details here; the page
            # disables Edit up front instead of leaving the server 409 as the
            # only signal. Read from the link's own school stamp: another
            # school's students are invisible under row security. The office reset (hardening U9) stays available on
            # a shared row: it needs an ACCEPTED link here (`linkAccepted`),
            # and the temporary-password state feeds the row's chip.
            result = []
            for r in registered:
                row = dict(r)
                expires_at = row.pop("temporary_password_expires_at")
                must_change = bool(row.pop("must_change_password"))
                link_accepted = bool(row.pop("link_accepted"))
                result.append({
                    **row,
                    "status": "registered",
                    "students": list(r["students"]),
                    "linkAccepted": link_accepted,
                    "mustChangePassword": must_change,
                    "temporaryPasswordExpiresAt": (
                        expires_at.isoformat() if must_change and expires_at else None
                    ),
                })
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
                    "linkAccepted": False,
                    "mustChangePassword": False,
                    "temporaryPasswordExpiresAt": None,
                })
        return result

    def update_parent(
        self, scope: SchoolScope, parent_id, full_name, email, phone, *, actor: dict,
        verify_token_hash: str | None = None, verify_ttl_minutes: int = 24 * 60,
    ) -> dict[str, Any]:
        """The office's edit of a parent account. A changed email address
        (case-insensitively) restarts verification in the same transaction
        (hardening Release 1b): see ``_restart_verification``. An identity
        holding other access (a membership anywhere, a provider account) keeps
        its email: changing it answers the neutral ``not-editable-here`` 409,
        while name and phone still save. Returns the row plus
        ``email_changed`` and ``previous_email`` — internal fields the service
        strips after sending the two mails."""
        with get_connection(scope) as conn:
            _assert_parent_editable_here(conn, parent_id, scope.school_id)
            before = conn.execute(
                f"select u.email, {_HOLDS_OTHER_ACCESS_SQL.format(u='u')} as holds_other "
                "from app_users u where u.id = %s for update of u",
                (parent_id,),
            ).fetchone()
            if not before:
                raise NotFoundError("Parent not found")
            if before["holds_other"] and (
                str(before["email"]).strip().lower() != str(email).strip().lower()
            ):
                raise AccountRefusedError(
                    NOT_EDITABLE_HERE_MESSAGE, code=NOT_EDITABLE_HERE_CODE
                )
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

    # --- office parent reset and Confirm and reset (hardening U9) ------------

    def reset_parent_password(
        self, scope: SchoolScope, parent_id: str, password_hash: str, *, actor: dict
    ) -> dict[str, Any]:
        """The office's parent reset, one transaction. Either staff role, at
        any school where the parent holds an ACCEPTED link to one of its
        students — a family linked at two schools is reachable from each, so
        a Shared row resets too (accepted cross-tenant trust; the parent is
        mailed a notice). Never an identity holding anything beyond the
        parent role: a membership of any role at any school, or a provider
        account, answers the neutral 409. No accepted link here, a non-parent
        or a disabled account → 404 (it does not exist for this school).

        Returns the address the service mails the notice to."""
        parent_id = _parent_uuid_or_404(parent_id)
        with get_connection(scope) as conn:
            target = conn.execute(
                f"""
                select u.id, u.email, {_HOLDS_OTHER_ACCESS_SQL.format(u="u")} as holds_other
                from app_users u
                join app_user_roles r on r.user_id = u.id and r.role = 'parent'
                where u.id = %s and u.disabled_at is null
                  and exists (
                      select 1 from live_parent_students ps
                      join live_students s on s.id = ps.student_id
                      where ps.parent_id = u.id and ps.status = 'accepted'
                        and s.school_id = %s
                  )
                for update of u
                """,
                (parent_id, scope.school_id),
            ).fetchone()
            if not target:
                raise NotFoundError("Parent not found")
            if target["holds_other"]:
                raise AccountRefusedError(
                    NOT_RESETTABLE_HERE_MESSAGE, code=NOT_RESETTABLE_HERE_CODE
                )
            counts = issue_temporary_password(
                conn, parent_id, password_hash, delete_push=True
            )
            record_audit(
                conn, action="parent-password-reset", actor=actor, scope=scope,
                resource_type="parent", resource_id=parent_id, detail=counts,
            )
        return {"id": str(target["id"]), "email": target["email"]}

    def confirm_parent_slot(
        self, scope: SchoolScope, email: str, password_hash: str, *, actor: dict
    ) -> dict[str, Any]:
        """"Confirm and reset" on an Awaiting-signup slot (execution
        decisions, Release 1b), one transaction: the slot email must be on
        one of THIS school's students (else 404) and belong to an UNVERIFIED
        parent account (D1) that the Parents page may reset — no membership
        anywhere, no provider account, not disabled — and that reaches no
        other school: its address on no other school's slot, no link at
        another school; otherwise the neutral ``nothing-to-confirm`` 409. The
        account is verified and given a temporary password through the
        shared helper, because confirming an
        account someone else may have created must not leave the creator's
        password working.

        Two audit rows: ``parent-email-confirmed`` for the verification and
        ``parent-password-reset`` (``via: confirm``) for the password, so a
        reader of either action sees every office-issued credential. Counts
        only. The linking rule runs after the commit (the service): it reads
        the verified state through its own connections."""
        with get_connection(scope) as conn:
            on_slot = conn.execute(
                """
                select 1 from live_students
                where school_id = %s
                  and (lower(parent_email) = lower(%s) or lower(parent2_email) = lower(%s))
                limit 1
                """,
                (scope.school_id, email, email),
            ).fetchone()
            if not on_slot:
                raise NotFoundError("Parent not found")
            target = conn.execute(
                f"""
                select u.id, u.email from app_users u
                join app_user_roles r on r.user_id = u.id and r.role = 'parent'
                where lower(u.email) = lower(%s) and u.disabled_at is null
                  and {unverified_sql("u")}
                  and not {_HOLDS_OTHER_ACCESS_SQL.format(u="u")}
                for update of u
                """,
                (email,),
            ).fetchone()
            if not target:
                raise AccountRefusedError(
                    NOTHING_TO_CONFIRM_MESSAGE, code=NOTHING_TO_CONFIRM_CODE
                )
            parent_id = str(target["id"])
            # This office writes its own slots, so "the email is on one of
            # our students" proves nothing about another school's child. An
            # address also carried on another school's slot (the platform-
            # wide linking would reach that child), or an account already
            # linked at another school (that school changed its email, say),
            # verifies through its own email link — never through one
            # school's office. Same neutral answer; nothing written.
            elsewhere_slot = any(
                str(match["school_id"]) != str(scope.school_id)
                for match in _ConnParentLinks(conn).signup_matches(target["email"])
            )
            if elsewhere_slot or _linked_elsewhere(conn, parent_id, scope.school_id):
                raise AccountRefusedError(
                    NOTHING_TO_CONFIRM_MESSAGE, code=NOTHING_TO_CONFIRM_CODE
                )
            conn.execute(
                "update app_users set email_verified_at = now() where id = %s",
                (parent_id,),
            )
            counts = issue_temporary_password(
                conn, parent_id, password_hash, delete_push=True
            )
            record_audit(
                conn, action="parent-email-confirmed", actor=actor, scope=scope,
                resource_type="parent", resource_id=parent_id, detail={},
            )
            record_audit(
                conn, action="parent-password-reset", actor=actor, scope=scope,
                resource_type="parent", resource_id=parent_id,
                detail={**counts, "via": "confirm"},
            )
        return {"id": parent_id, "email": target["email"]}

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
