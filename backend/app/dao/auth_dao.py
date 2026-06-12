from typing import Any

from app.core.db import get_connection


class AuthDao:
    def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        with get_connection() as conn:
            row = conn.execute(
                """
                select u.id, u.email, u.password_hash, u.full_name, u.phone,
                       u.pin_hash, r.role
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
        """Resolve a live session to its user+role; slide expiry forward."""
        with get_connection() as conn:
            row = conn.execute(
                """
                select s.id as session_id, u.id, u.email, u.full_name, u.phone, r.role
                from auth_sessions s
                join app_users u on u.id = s.user_id
                left join app_user_roles r on r.user_id = u.id
                where s.token_hash = %s
                    and s.revoked_at is null
                    and s.expires_at > now()
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
        """Single-use: mark used and return the user_id if valid and unused."""
        with get_connection() as conn:
            row = conn.execute(
                """
                update password_reset_tokens
                set used_at = now()
                where token_hash = %s
                    and used_at is null
                    and expires_at > now()
                returning user_id
                """,
                (token_hash,),
            ).fetchone()
        return dict(row) if row else None

    def update_password(self, user_id: str, password_hash: str) -> None:
        with get_connection() as conn:
            conn.execute(
                "update app_users set password_hash = %s where id = %s",
                (password_hash, user_id),
            )
