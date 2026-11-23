from typing import Any

from app.core.db import get_connection, get_global_connection
from app.dao.audit_dao import masked_display_sql
from app.dao.verification_sql import verified_sql

# Token purposes on password_reset_tokens (migration 017): consumption is
# purpose-scoped, so a verification token can never reset a password and a
# reset token never verifies an address.
TOKEN_PURPOSES = ("reset", "verify")


class AuthDao:
    def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        with get_connection() as conn:
            row = conn.execute(
                f"""
                select u.id, u.email, u.password_hash, u.full_name, u.phone,
                       u.pin_hash, u.must_change_password,
                       u.temporary_password_expires_at, u.disabled_at, r.role,
                       u.email_verification_required, u.email_verified_at,
                       {verified_sql("u")} as email_verified
                from app_users u
                left join app_user_roles r on r.user_id = u.id
                where lower(u.email) = lower(%s)
                """,
                (email,),
            ).fetchone()
        return dict(row) if row else None

    def get_user_by_id(self, user_id: str) -> dict[str, Any] | None:
        with get_connection() as conn:
            row = conn.execute(
                f"""
                select u.id, u.email, u.full_name, u.phone, r.role,
                       u.must_change_password,
                       {verified_sql("u")} as email_verified,
                       exists (select 1 from provider_accounts p
                               where p.user_id = u.id and p.removed_at is null
                       ) as is_provider
                from app_users u
                left join app_user_roles r on r.user_id = u.id
                where u.id = %s
                """,
                (user_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_driver_pin_users(self) -> list[dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                select u.id, u.email, u.full_name, u.pin_hash
                from app_users u
                join app_user_roles r on r.user_id = u.id
                where r.role = 'driver' and u.pin_hash is not null
                    and u.disabled_at is null
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def create_unverified_parent(
        self,
        email: str,
        password_hash: str,
        full_name: str,
        verify_token_hash: str,
        verify_ttl_minutes: int,
    ) -> dict[str, Any]:
        """Release 1b sign-up (U8): the account, its parent role and its
        verification token in ONE transaction. The account starts unverified
        (``email_verification_required`` true, ``email_verified_at`` null —
        D1): it links nothing, raises no office incident and stays off the
        Parents page until its owner verifies from a signed-in session."""
        with get_global_connection() as conn:
            row = conn.execute(
                """
                insert into app_users
                    (email, password_hash, full_name, email_verification_required)
                values (%s, %s, %s, true)
                returning id, email, full_name
                """,
                (email, password_hash, full_name),
            ).fetchone()
            conn.execute(
                "insert into app_user_roles (user_id, role) values (%s, 'parent')",
                (row["id"],),
            )
            conn.execute(
                """
                insert into password_reset_tokens (user_id, token_hash, purpose, expires_at)
                values (%s, %s, 'verify', now() + make_interval(mins => %s))
                """,
                (row["id"], verify_token_hash, verify_ttl_minutes),
            )
        return dict(row)

    def create_session(self, user_id: str, token_hash: str, ttl_hours: int = 16) -> None:
        with get_connection() as conn:
            conn.execute(
                """
                insert into auth_sessions (user_id, token_hash, expires_at)
                values (%s, %s, now() + make_interval(hours => %s))
                """,
                (user_id, token_hash, ttl_hours),
            )

    def get_session_user(self, token_hash: str) -> dict[str, Any] | None:
        """Resolve a live session to its enriched user; slide expiry forward.

        Carries everything scope resolution needs (U5): active+offered
        memberships (removed rows excluded), provider state, the live step-in
        session (unexpired, ≤4h), the parent's accepted-link school set,
        ``must_change_password`` and ``last_school_id``. A disabled identity
        resolves to no session at all (R13: removal ends access at once).
        """
        with get_connection() as conn:
            row = conn.execute(
                f"""
                select s.id as session_id, s.last_school_id, s.totp_verified_at,
                       u.id, u.email, u.full_name, u.phone, u.must_change_password,
                       {verified_sql("u")} as email_verified,
                       r.role,
                       (select coalesce(jsonb_agg(jsonb_build_object(
                                'id', m.id::text,
                                'school_id', m.school_id::text, 'role', m.role,
                                'state', m.state, 'school_name', sc.name,
                                'school_code', sc.code,
                                'created_at', m.created_at,
                                'offered_by_name',
                                    (select {masked_display_sql("ob", "op")}
                                     from app_users ob
                                     left join provider_accounts op
                                        on op.user_id = ob.id and op.removed_at is null
                                     where ob.id = m.offered_by))
                                order by m.created_at), '[]'::jsonb)
                        from school_memberships m
                        join live_schools sc on sc.id = m.school_id
                        where m.user_id = u.id and m.removed_at is null
                       ) as memberships,
                       (select jsonb_build_object(
                                'totp_enrolled', p.totp_enrolled_at is not null)
                        from provider_accounts p
                        where p.user_id = u.id and p.removed_at is null
                       ) as provider,
                       (select jsonb_build_object(
                                'id', ss.id::text, 'school_id', ss.school_id::text,
                                'reason', ss.reason, 'started_at', ss.started_at,
                                'school_name', ssc.name, 'school_code', ssc.code)
                        from provider_support_sessions ss
                        join live_schools ssc on ssc.id = ss.school_id
                        where ss.id = s.support_session_id
                          and ss.ended_at is null
                          and ss.started_at > now() - interval '4 hours'
                       ) as support_session,
                       -- From the link rows' own stamp, never a live_students
                       -- join: scope derivation runs on a GUC-less connection,
                       -- and under RLS (015) a student join would see nothing —
                       -- the access key cannot depend on tables it unlocks.
                       (select coalesce(array_agg(distinct ps.school_id::text), '{{}}')
                        from live_parent_students ps
                        where ps.parent_id = u.id and ps.status = 'accepted'
                          and ps.school_id is not null
                       ) as parent_school_ids
                from auth_sessions s
                join app_users u on u.id = s.user_id
                left join app_user_roles r on r.user_id = u.id
                where s.token_hash = %s
                    and s.revoked_at is null
                    and s.expires_at > now()
                    and u.disabled_at is null
                """,
                (token_hash,),
            ).fetchone()
            if not row:
                return None
            conn.execute(
                "update auth_sessions set expires_at = now() + interval '16 hours' where id = %s",
                (row["session_id"],),
            )
        return dict(row)

    def set_session_school(self, session_id: str, school_id: str) -> None:
        """Remember the session's active school for header fallback (R16).

        Runs inside the staff guard BEFORE the scope is published (checked in
        U7), and sessions are a non-school table either way — the global
        connection keeps this safe under the strict seam by construction."""
        with get_global_connection() as conn:
            conn.execute(
                "update auth_sessions set last_school_id = %s where id = %s",
                (school_id, session_id),
            )

    def revoke_session(self, token_hash: str) -> None:
        with get_connection() as conn:
            conn.execute(
                "update auth_sessions set revoked_at = now() where token_hash = %s and revoked_at is null",
                (token_hash,),
            )

    def revoke_all_sessions(self, user_id: str) -> None:
        with get_connection() as conn:
            conn.execute(
                "update auth_sessions set revoked_at = now() where user_id = %s and revoked_at is null",
                (user_id,),
            )

    # --- provider pre-auth tokens (U10) --------------------------------------
    # The bridge between the password step and the code step: five-minute,
    # single-use, attempt-counted rows in ``auth_preauth_tokens``. They are a
    # separate table from sessions on purpose — a pre-auth token can never be
    # presented as a bearer session. ``set_user_password`` and ``disable_user``
    # above already void them alongside reset tokens.

    def create_preauth_token(
        self, user_id: str, token_hash: str, ttl_minutes: int = 5
    ) -> None:
        with get_global_connection() as conn:
            conn.execute(
                """
                insert into auth_preauth_tokens (user_id, token_hash, expires_at)
                values (%s, %s, now() + make_interval(mins => %s))
                """,
                (user_id, token_hash, ttl_minutes),
            )

    def get_preauth_token(self, token_hash: str) -> dict[str, Any] | None:
        """The raw row, whatever its state — the service tells an exhausted
        (voided) token apart from a merely wrong code by ``attempts``."""
        with get_global_connection() as conn:
            row = conn.execute(
                """
                select id, user_id, expires_at, used_at, attempts,
                       expires_at <= now() as expired
                from auth_preauth_tokens
                where token_hash = %s
                """,
                (token_hash,),
            ).fetchone()
        return dict(row) if row else None

    def record_preauth_failure(
        self, token_id: str, max_attempts: int
    ) -> dict[str, Any]:
        """One wrong code: bump the counter and, atomically, void the token on
        the ``max_attempts``-th failure. Returns {'attempts', 'voided'}."""
        with get_global_connection() as conn:
            row = conn.execute(
                """
                update auth_preauth_tokens
                set attempts = attempts + 1,
                    used_at = case
                        when attempts + 1 >= %s then coalesce(used_at, now())
                        else used_at
                    end
                where id = %s
                returning attempts, used_at is not null as voided
                """,
                (max_attempts, token_id),
            ).fetchone()
        return dict(row) if row else {"attempts": max_attempts, "voided": True}

    def consume_preauth_token(self, token_id: str) -> bool:
        """Single-use: returns False when a concurrent request already won."""
        with get_global_connection() as conn:
            row = conn.execute(
                """
                update auth_preauth_tokens set used_at = now()
                where id = %s and used_at is null and expires_at > now()
                returning id
                """,
                (token_id,),
            ).fetchone()
        return row is not None

    def create_totp_session(
        self, user_id: str, token_hash: str, ttl_hours: int = 16
    ) -> None:
        """A session born code-verified: ``totp_verified_at`` stamped at
        creation (the step-up freshness clock starts here)."""
        with get_global_connection() as conn:
            conn.execute(
                """
                insert into auth_sessions
                    (user_id, token_hash, expires_at, totp_verified_at)
                values (%s, %s, now() + make_interval(hours => %s), now())
                """,
                (user_id, token_hash, ttl_hours),
            )

    def refresh_session_totp(self, session_id: str) -> None:
        """A fresh code presented mid-session (step-up) restarts the clock."""
        with get_global_connection() as conn:
            conn.execute(
                "update auth_sessions set totp_verified_at = now() where id = %s",
                (session_id,),
            )

    # --- emailed tokens: reset (30 min) and verify (24 h) — U7 ---------------

    def create_reset_token(
        self,
        user_id: str,
        token_hash: str,
        purpose: str = "reset",
        ttl_minutes: int = 30,
        *,
        cap_per_hour: int | None = None,
        void_earlier: bool = False,
    ) -> bool:
        """Write one token row; returns whether it was written.

        ``cap_per_hour`` is the per-recipient mail cap: the account's row is
        locked first (concurrent requests for one account queue here), then
        the rows of BOTH purposes created in the last hour are counted, and
        at or over the cap nothing is written. ``void_earlier`` (the
        verification resend) marks every earlier unused token of the same
        purpose used, in the same transaction, so only the newest link works.
        """
        if purpose not in TOKEN_PURPOSES:
            raise ValueError(f"unknown token purpose {purpose!r}")
        with get_global_connection() as conn:
            if cap_per_hour is not None:
                conn.execute(
                    "select 1 from app_users where id = %s for update", (user_id,)
                ).fetchone()
                recent = conn.execute(
                    "select count(*) as n from password_reset_tokens "
                    "where user_id = %s and created_at > now() - interval '1 hour'",
                    (user_id,),
                ).fetchone()["n"]
                if recent >= cap_per_hour:
                    return False
            if void_earlier:
                conn.execute(
                    "update password_reset_tokens set used_at = now() "
                    "where user_id = %s and purpose = %s and used_at is null",
                    (user_id, purpose),
                )
            conn.execute(
                """
                insert into password_reset_tokens (user_id, token_hash, purpose, expires_at)
                values (%s, %s, %s, now() + make_interval(mins => %s))
                """,
                (user_id, token_hash, purpose, ttl_minutes),
            )
        return True

    def consume_reset_token(
        self, token_hash: str, purpose: str = "reset"
    ) -> dict[str, Any] | None:
        """Single-use: mark used and return the user_id if valid, unused AND
        of ``purpose``. A token of the other purpose matches nothing and is
        left untouched — a verification link never resets a password, and a
        reset link never verifies an address. A disabled account's tokens are
        dead on arrival (R13: `disable_user` voids them, and this join refuses
        any that slip through a race)."""
        with get_global_connection() as conn:
            row = conn.execute(
                """
                update password_reset_tokens t
                set used_at = now()
                from app_users u
                where u.id = t.user_id
                    and t.token_hash = %s
                    and t.purpose = %s
                    and t.used_at is null
                    and t.expires_at > now()
                    and u.disabled_at is null
                returning t.user_id
                """,
                (token_hash, purpose),
            ).fetchone()
        return dict(row) if row else None

    def live_verify_token_owner(self, token_hash: str) -> str | None:
        """The owner of a LIVE (unused, unexpired) verification token, without
        consuming it — the verify endpoint refuses another account's token
        and must leave it working for its owner."""
        with get_global_connection() as conn:
            row = conn.execute(
                """
                select t.user_id from password_reset_tokens t
                join app_users u on u.id = t.user_id and u.disabled_at is null
                where t.token_hash = %s and t.purpose = 'verify'
                  and t.used_at is null and t.expires_at > now()
                """,
                (token_hash,),
            ).fetchone()
        return str(row["user_id"]) if row else None

    def consume_verify_token(self, token_hash: str, user_id: str) -> bool:
        """Single-use, owner-bound: consumes only the caller's own live
        verification token."""
        with get_global_connection() as conn:
            row = conn.execute(
                """
                update password_reset_tokens set used_at = now()
                where token_hash = %s and purpose = 'verify' and user_id = %s
                  and used_at is null and expires_at > now()
                returning id
                """,
                (token_hash, user_id),
            ).fetchone()
        return row is not None

    def mark_email_verified(self, user_id: str) -> dict[str, Any] | None:
        """Stamp ``email_verified_at`` when it is still null. Returns
        ``{"email", "was_unverified"}`` when a stamp was written —
        ``was_unverified`` is true only for an account that was waiting for
        verification (the transition that runs the parent-link rule) — or
        None when the address was already verified."""
        with get_global_connection() as conn:
            row = conn.execute(
                """
                update app_users set email_verified_at = now()
                where id = %s and email_verified_at is null
                returning email, email_verification_required as was_unverified
                """,
                (user_id,),
            ).fetchone()
        return dict(row) if row else None

    def delete_push_registrations(self, user_id: str) -> int:
        """Every push registration of the account (web push subscriptions and
        FCM tokens): an emailed or office reset usually follows a lost phone,
        which must stop receiving child notices. The parent's own browser
        re-posts its subscription at the next sign-in (lib/push.ts)."""
        with get_global_connection() as conn:
            web = conn.execute(
                "delete from live_push_subscriptions where user_id = %s", (user_id,)
            ).rowcount
            fcm = conn.execute(
                "delete from live_fcm_tokens where user_id = %s", (user_id,)
            ).rowcount
        return (web or 0) + (fcm or 0)

    def get_user_credentials(self, user_id: str) -> dict[str, Any] | None:
        """id + password hash for the change-password current-password check.
        User-table read reachable from any authenticated surface → global."""
        with get_global_connection() as conn:
            row = conn.execute(
                "select id, email, password_hash from app_users where id = %s",
                (user_id,),
            ).fetchone()
        return dict(row) if row else None

    def set_user_password(self, user_id: str, password_hash: str) -> None:
        """THE user-set-a-password write (U8): new hash, ``password_changed_at``
        stamped, the temporary-password flag and expiry cleared, and every
        outstanding emailed token (both purposes, reset and verify) and
        pre-auth token voided — one transaction. Used by both change-password
        and the emailed reset; session handling stays with the caller (change
        keeps its own session, the emailed reset issues a fresh one).
        """
        with get_global_connection() as conn:
            conn.execute(
                """
                update app_users
                set password_hash = %s, password_changed_at = now(),
                    must_change_password = false,
                    temporary_password_expires_at = null
                where id = %s
                """,
                (password_hash, user_id),
            )
            conn.execute(
                "update password_reset_tokens set used_at = now() "
                "where user_id = %s and used_at is null",
                (user_id,),
            )
            conn.execute(
                "delete from auth_preauth_tokens where user_id = %s", (user_id,)
            )

    def revoke_other_sessions(self, user_id: str, keep_session_id: str) -> None:
        """End every session but the calling one (change-password, R29)."""
        with get_global_connection() as conn:
            conn.execute(
                "update auth_sessions set revoked_at = now() "
                "where user_id = %s and id <> %s and revoked_at is null",
                (user_id, keep_session_id),
            )

    def disable_user(self, user_id: str) -> None:
        """THE single disable path (R13), one transaction: the account stops
        resolving (login, PIN list, session lookup and push recipients all
        carry ``disabled_at is null``), every live session is revoked, and
        every outstanding reset and pre-auth token is voided."""
        with get_global_connection() as conn:
            conn.execute(
                "update app_users set disabled_at = now() "
                "where id = %s and disabled_at is null",
                (user_id,),
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
