"""Provider-console data access (U10): accounts, step-in, health, audit.

Boundary rules (pinned by ``tests/integration/test_provider_dao_imports.py``):
this module imports no other DAO (``audit_dao`` excepted — it is the shared
attribution writer) and its SQL never touches a school-owned table. School
aggregates come exclusively through migration 013's ``SECURITY DEFINER``
functions (``provider_school_health``, ``provider_audit_rows``) — the one
named, reviewable RLS bypass — and the only ``live_schools`` reads are the
school's own row for creation and step-in existence checks. Everything here
runs on the deliberately scope-free global connection: provider, user and
session rows are platform tables, and the console works outside any school.

Every mutating method is one transaction; audit rows land inside it.
"""

from typing import Any

from app.core.db import get_global_connection
from app.core.errors import ConflictError, NotFoundError
from app.core.scope import SchoolScope
from app.dao.audit_dao import record_audit

LAST_PROVIDER_MESSAGE = "The platform must keep at least one provider account"
SUPPORT_SESSION_HOURS = 4


class ProviderDao:
    # --- login-time reads ----------------------------------------------------

    def get_provider_login_user(self, email: str) -> dict[str, Any] | None:
        """The password-step read: the identity joined to its ACTIVE provider
        row (removed providers fall through to the ordinary login)."""
        with get_global_connection() as conn:
            row = conn.execute(
                """
                select u.id, u.email, u.password_hash, u.full_name,
                       u.must_change_password, u.temporary_password_expires_at,
                       u.disabled_at, r.role,
                       p.totp_salt, p.totp_enrolled_at, p.totp_last_step,
                       p.totp_pepper_key
                from app_users u
                join provider_accounts p
                    on p.user_id = u.id and p.removed_at is null
                left join app_user_roles r on r.user_id = u.id
                where lower(u.email) = lower(%s)
                """,
                (email,),
            ).fetchone()
        return dict(row) if row else None

    def get_provider_verify_state(self, user_id: str) -> dict[str, Any] | None:
        """The code-step re-check: disabled/removed/enrolment state NOW, not
        as of the password step. ``provider_active`` is False once removed."""
        with get_global_connection() as conn:
            row = conn.execute(
                """
                select u.id, u.email, u.full_name, u.must_change_password,
                       u.disabled_at, r.role,
                       p.user_id is not null as provider_active,
                       p.totp_salt, p.totp_enrolled_at, p.totp_last_step,
                       p.totp_pepper_key
                from app_users u
                left join provider_accounts p
                    on p.user_id = u.id and p.removed_at is null
                left join app_user_roles r on r.user_id = u.id
                where u.id = %s
                """,
                (user_id,),
            ).fetchone()
        return dict(row) if row else None

    def set_totp_last_step(self, user_id: str, step: int) -> None:
        """Persist the accepted step — the replay guard's memory."""
        with get_global_connection() as conn:
            conn.execute(
                """
                update provider_accounts
                set totp_last_step = greatest(coalesce(totp_last_step, -1), %s)
                where user_id = %s and removed_at is null
                """,
                (step, user_id),
            )

    # --- enrolment -----------------------------------------------------------

    def start_enrolment(self, user_id: str, salt: str, pepper_key: str) -> None:
        """A fresh salt (old secret dead) + the running pepper's key id.
        Refused while enrolled — reset-totp is the way back."""
        with get_global_connection() as conn:
            row = conn.execute(
                """
                update provider_accounts
                set totp_salt = %s, totp_pepper_key = %s, totp_last_step = null
                where user_id = %s and removed_at is null
                    and totp_enrolled_at is null
                returning user_id
                """,
                (salt, pepper_key, user_id),
            ).fetchone()
            if row is None:
                exists = conn.execute(
                    "select 1 from provider_accounts "
                    "where user_id = %s and removed_at is null",
                    (user_id,),
                ).fetchone()
                if exists:
                    raise ConflictError("Second factor is already enrolled")
                raise NotFoundError("Provider account not found")

    def confirm_enrolment(self, user_id: str, step: int, session_id: str) -> None:
        """First valid code: enrolment stamped, the step recorded, and the
        calling session marked code-verified — lifting the 409 allowlist."""
        with get_global_connection() as conn:
            row = conn.execute(
                """
                update provider_accounts
                set totp_enrolled_at = now(), totp_last_step = %s
                where user_id = %s and removed_at is null
                    and totp_enrolled_at is null
                returning user_id
                """,
                (step, user_id),
            ).fetchone()
            if row is None:
                raise ConflictError("Second factor is already enrolled")
            conn.execute(
                "update auth_sessions set totp_verified_at = now() where id = %s",
                (session_id,),
            )

    # --- schools -------------------------------------------------------------

    def list_schools_with_health(self) -> list[dict[str, Any]]:
        """Counts and setup state only — never a roster (R24). The SECURITY
        DEFINER function is the single sanctioned cross-school aggregate."""
        with get_global_connection() as conn:
            rows = conn.execute("select * from provider_school_health()").fetchall()
        return [dict(row) for row in rows]

    def list_school_codes(self, prefix: str) -> list[str]:
        with get_global_connection() as conn:
            rows = conn.execute(
                "select code from live_schools where code like %s",
                (f"{prefix}-%",),
            ).fetchall()
        return [row["code"] for row in rows]

    def create_school(
        self,
        *,
        name: str,
        lat: float | None,
        lng: float | None,
        morning_bell: str | None,
        afternoon_bell: str | None,
        code: str,
        actor: dict[str, Any],
    ) -> dict[str, Any]:
        """The school row with its non-editable code, audited (school-created).
        A code collision raises 23505 for the service's retry loop."""
        with get_global_connection() as conn:
            row = conn.execute(
                """
                insert into live_schools
                    (name, lat, lng, morning_bell, afternoon_bell, code)
                values (%s, %s, %s, %s, %s, %s)
                returning id, name, code, lat, lng, morning_bell, afternoon_bell
                """,
                (name, lat, lng, morning_bell, afternoon_bell, code),
            ).fetchone()
            record_audit(
                conn, action="school-created", actor=actor,
                school_id=str(row["id"]),
                resource_type="school", resource_id=row["id"],
                detail={"code": code},
            )
        return dict(row)

    def get_school(self, school_id: str) -> dict[str, Any] | None:
        with get_global_connection() as conn:
            row = conn.execute(
                "select id, name, code from live_schools where id = %s",
                (school_id,),
            ).fetchone()
        return dict(row) if row else None

    # --- step-in / step-out --------------------------------------------------

    def expire_stale_support_sessions(self, provider_user_id: str) -> None:
        """Lazy janitor (U10): a support session past the four-hour hard
        lifetime is closed as 'expired' the next time its provider shows up.
        ``get_session_user`` already hides such sessions from scope
        resolution, so this only settles the record."""
        with get_global_connection() as conn:
            rows = conn.execute(
                """
                update provider_support_sessions
                set ended_at = started_at + make_interval(hours => %s),
                    end_cause = 'expired'
                where provider_user_id = %s and ended_at is null
                    and started_at <= now() - make_interval(hours => %s)
                returning id
                """,
                (SUPPORT_SESSION_HOURS, provider_user_id, SUPPORT_SESSION_HOURS),
            ).fetchall()
            if rows:
                conn.execute(
                    "update auth_sessions set support_session_id = null "
                    "where support_session_id = any(%s)",
                    ([row["id"] for row in rows],),
                )

    def step_in(
        self,
        *,
        actor: dict[str, Any],
        session_id: str,
        school_id: str,
        reason: str,
        ip: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        """One transaction: supersede any live session of this provider, open
        the new one with its provenance (IP, user agent, auth session), point
        the calling session at it, audit (AE26 — the step-in itself is the
        audited act, writes or not)."""
        with get_global_connection() as conn:
            school = conn.execute(
                "select id, name, code from live_schools where id = %s",
                (school_id,),
            ).fetchone()
            if school is None:
                raise NotFoundError("School not found")
            superseded = conn.execute(
                """
                update provider_support_sessions
                set ended_at = now(), end_cause = 'superseded'
                where provider_user_id = %s and ended_at is null
                returning id
                """,
                (actor["id"],),
            ).fetchall()
            if superseded:
                conn.execute(
                    "update auth_sessions set support_session_id = null "
                    "where support_session_id = any(%s)",
                    ([row["id"] for row in superseded],),
                )
            row = conn.execute(
                """
                insert into provider_support_sessions
                    (provider_user_id, school_id, reason, auth_session_id,
                     ip, user_agent)
                values (%s, %s, %s, %s, %s, %s)
                returning id, started_at
                """,
                (actor["id"], school_id, reason, session_id, ip, user_agent),
            ).fetchone()
            conn.execute(
                "update auth_sessions set support_session_id = %s where id = %s",
                (row["id"], session_id),
            )
            record_audit(
                conn, action="provider-step-in", actor=actor,
                scope=SchoolScope(
                    user_id=str(actor["id"]), school_id=str(school["id"]),
                    role="director", actor_kind="provider",
                    support_session_id=str(row["id"]),
                ),
                resource_type="support-session", resource_id=row["id"],
                detail={"reason": reason},
            )
        return {
            "id": str(row["id"]),
            "started_at": row["started_at"],
            "school": dict(school),
        }

    def end_support_session(
        self, *, actor: dict[str, Any], support_session_id: str, cause: str
    ) -> bool:
        """End one support session (step-out or logout); idempotent — an
        already-ended session writes nothing and returns False."""
        with get_global_connection() as conn:
            row = conn.execute(
                """
                update provider_support_sessions
                set ended_at = now(), end_cause = %s
                where id = %s and ended_at is null
                returning school_id
                """,
                (cause, support_session_id),
            ).fetchone()
            if row is None:
                return False
            conn.execute(
                "update auth_sessions set support_session_id = null "
                "where support_session_id = %s",
                (support_session_id,),
            )
            record_audit(
                conn, action="provider-step-out", actor=actor,
                scope=SchoolScope(
                    user_id=str(actor["id"]), school_id=str(row["school_id"]),
                    role="director", actor_kind="provider",
                    support_session_id=str(support_session_id),
                ),
                resource_type="support-session",
                resource_id=support_session_id,
                detail={"cause": cause},
            )
        return True

    def find_support_for_token(self, token_hash: str) -> dict[str, Any] | None:
        """The logout hook's lookup: the calling session's live support
        session plus enough identity to write the step-out audit row."""
        with get_global_connection() as conn:
            row = conn.execute(
                """
                select ss.id as support_session_id, u.id, u.email, u.full_name
                from auth_sessions s
                join provider_support_sessions ss on ss.id = s.support_session_id
                join app_users u on u.id = s.user_id
                where s.token_hash = %s and ss.ended_at is null
                """,
                (token_hash,),
            ).fetchone()
        return dict(row) if row else None

    # --- provider accounts ---------------------------------------------------

    def list_accounts(self) -> list[dict[str, Any]]:
        with get_global_connection() as conn:
            rows = conn.execute(
                """
                select u.id as user_id, u.email, u.full_name,
                       p.totp_enrolled_at is not null as totp_enrolled,
                       p.created_at
                from provider_accounts p
                join app_users u on u.id = p.user_id
                where p.removed_at is null
                order by p.created_at asc, u.email asc
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def email_taken(self, email: str) -> bool:
        """Providers never piggyback an existing identity (R19): any match —
        parent, staff, driver, disabled, removed — refuses the creation."""
        with get_global_connection() as conn:
            row = conn.execute(
                "select 1 from app_users where lower(email) = lower(%s)",
                (email,),
            ).fetchone()
        return row is not None

    def create_account(
        self,
        *,
        email: str,
        full_name: str,
        password_hash: str,
        totp_salt: str,
        temporary_password_ttl_hours: int,
        actor: dict[str, Any],
    ) -> dict[str, Any]:
        """New identity + provider row, one transaction (R19). The temporary
        password arrives hashed; enrolment starts at the account's first
        login. No legacy role row — provider authority IS the provider row."""
        with get_global_connection() as conn:
            row = conn.execute(
                """
                insert into app_users
                    (email, password_hash, full_name, must_change_password,
                     temporary_password_expires_at)
                values (%s, %s, %s, true, now() + make_interval(hours => %s))
                returning id, email, full_name
                """,
                (email, password_hash, full_name, temporary_password_ttl_hours),
            ).fetchone()
            conn.execute(
                """
                insert into provider_accounts (user_id, totp_salt, created_by)
                values (%s, %s, %s)
                """,
                (row["id"], totp_salt, actor["id"]),
            )
            record_audit(
                conn, action="provider-account-created", actor=actor,
                resource_type="provider-account", resource_id=row["id"],
                detail={"user_id": str(row["id"])},
            )
        return dict(row)

    def remove_account(self, user_id: str, *, actor: dict[str, Any]) -> None:
        """End a provider account (AE24/AE17), one transaction: the last
        active one is locked and refused; the row is marked removed (the
        identity itself is NOT disabled), every session revoked and every
        pre-auth token voided — access ends on the next request."""
        with get_global_connection() as conn:
            active = conn.execute(
                """
                select user_id from provider_accounts
                where removed_at is null
                order by user_id for update
                """
            ).fetchall()
            target = [row for row in active if str(row["user_id"]) == str(user_id)]
            if not target:
                raise NotFoundError("Provider account not found")
            if len(active) == 1:
                raise ConflictError(LAST_PROVIDER_MESSAGE)
            conn.execute(
                "update provider_accounts set removed_at = now() "
                "where user_id = %s and removed_at is null",
                (user_id,),
            )
            conn.execute(
                "update auth_sessions set revoked_at = now() "
                "where user_id = %s and revoked_at is null",
                (user_id,),
            )
            conn.execute(
                "delete from auth_preauth_tokens where user_id = %s", (user_id,)
            )
            record_audit(
                conn, action="provider-account-removed", actor=actor,
                resource_type="provider-account", resource_id=user_id,
                detail={"user_id": str(user_id)},
            )

    def reset_totp(
        self, user_id: str, *, salt: str, pepper_key: str, actor: dict[str, Any]
    ) -> None:
        """Peer reset (R20): enrolment cleared, salt regenerated (old secret
        dead), running pepper key stamped. The target's next login lands in
        the enrolment flow; their live sessions fall under the 409 allowlist
        on their very next request."""
        with get_global_connection() as conn:
            row = conn.execute(
                """
                update provider_accounts
                set totp_enrolled_at = null, totp_last_step = null,
                    totp_salt = %s, totp_pepper_key = %s
                where user_id = %s and removed_at is null
                returning user_id
                """,
                (salt, pepper_key, user_id),
            ).fetchone()
            if row is None:
                raise NotFoundError("Provider account not found")
            record_audit(
                conn, action="provider-totp-reset", actor=actor,
                resource_type="provider-account", resource_id=user_id,
                detail={"user_id": str(user_id)},
            )

    # --- audit reader --------------------------------------------------------

    def list_audit(
        self, school_id: str | None, support_session_id: str | None
    ) -> list[dict[str, Any]]:
        """The provider-side reader (R25): real identities, unmasked, through
        the SECURITY DEFINER function (RLS-proof by design)."""
        with get_global_connection() as conn:
            rows = conn.execute(
                "select * from provider_audit_rows(%s, %s)",
                (school_id, support_session_id),
            ).fetchall()
        return [dict(row) for row in rows]
