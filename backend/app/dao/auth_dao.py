from typing import Any

from app.core.db import get_connection, get_global_connection
from app.dao.audit_dao import masked_display_sql


class AuthDao:
    def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        with get_connection() as conn:
            row = conn.execute(
                """
                select u.id, u.email, u.password_hash, u.full_name, u.phone,
                       u.pin_hash, u.must_change_password,
                       u.temporary_password_expires_at, u.disabled_at, r.role
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
                """
                select u.id, u.email, u.full_name, u.phone, r.role
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

    def create_user(
        self, email: str, password_hash: str, full_name: str, role: str, phone: str | None = None
    ) -> dict[str, Any]:
        with get_connection() as conn:
            row = conn.execute(
                """
                insert into app_users (email, password_hash, full_name, phone)
                values (%s, %s, %s, %s)
                returning id, email, full_name
                """,
                (email, password_hash, full_name, phone),
            ).fetchone()
            conn.execute(
                "insert into app_user_roles (user_id, role) values (%s, %s)",
                (row["id"], role),
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
                                'id', ss.id::text, 'school_id', ss.school_id::text)
                        from provider_support_sessions ss
                        where ss.id = s.support_session_id
                          and ss.ended_at is null
                          and ss.started_at > now() - interval '4 hours'
                       ) as support_session,
                       (select coalesce(array_agg(distinct st.school_id::text), '{{}}')
                        from live_parent_students ps
                        join live_students st on st.id = ps.student_id
                        where ps.parent_id = u.id and ps.status = 'accepted'
                          and st.school_id is not null
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

    def create_reset_token(self, user_id: str, token_hash: str, ttl_minutes: int = 60) -> None:
        with get_connection() as conn:
            conn.execute(
                """
                insert into password_reset_tokens (user_id, token_hash, expires_at)
                values (%s, %s, now() + make_interval(mins => %s))
                """,
                (user_id, token_hash, ttl_minutes),
            )

    def consume_reset_token(self, token_hash: str) -> dict[str, Any] | None:
        """Single-use: mark used and return the user_id if valid and unused.
        A disabled account's tokens are dead on arrival (R13: `disable_user`
        voids them, and this join refuses any that slip through a race)."""
        with get_connection() as conn:
            row = conn.execute(
                """
                update password_reset_tokens t
                set used_at = now()
                from app_users u
                where u.id = t.user_id
                    and t.token_hash = %s
                    and t.used_at is null
                    and t.expires_at > now()
                    and u.disabled_at is null
                returning t.user_id
                """,
                (token_hash,),
            ).fetchone()
        return dict(row) if row else None

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
        outstanding reset and pre-auth token voided — one transaction. Used by
        both change-password and the forgot-password reset; session handling
        stays with the caller (change keeps its own session, reset keeps none).
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
