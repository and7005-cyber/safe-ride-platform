from typing import Any

from app.core.db import get_connection


class NotificationDao:
    def recover_stale_claims(self) -> None:
        with get_connection() as conn:
            conn.execute(
                """
                update notification_outbox
                set
                    status = case when attempts >= 3 then 'failed'::notification_status else 'pending'::notification_status end,
                    claimed_at = null,
                    last_error = case
                        when attempts >= 3 then coalesce(last_error, 'Notification claim timed out after maximum attempts')
                        else last_error
                    end
                where status = 'processing'
                    and claimed_at < now() - interval '5 minutes'
                """
            )

    def list_pending_messages(self, limit: int) -> list[dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                select id, channel, recipient_phone, payload, attempts
                from notification_outbox
                where status = 'pending'
                    and attempts < 3
                order by created_at asc
                limit %s
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def claim_message(self, message_id: str, attempts: int) -> dict[str, Any] | None:
        current_attempts = max(attempts - 1, 0)
        with get_connection() as conn:
            row = conn.execute(
                """
                update notification_outbox
                set
                    status = 'processing',
                    attempts = %s,
                    claimed_at = now(),
                    last_error = null
                where id = %s
                    and status = 'pending'
                    and attempts = %s
                    and attempts < 3
                returning id, channel, recipient_phone, payload, attempts
                """,
                (attempts, message_id, current_attempts),
            ).fetchone()
        return dict(row) if row else None

    def mark_sent(self, message_id: str) -> None:
        with get_connection() as conn:
            conn.execute(
                """
                update notification_outbox
                set
                    status = 'sent',
                    sent_at = now(),
                    claimed_at = null,
                    last_error = null
                where id = %s
                """,
                (message_id,),
            )

    def mark_skipped(self, message_id: str, reason: str) -> None:
        with get_connection() as conn:
            conn.execute(
                """
                update notification_outbox
                set
                    status = 'skipped',
                    claimed_at = null,
                    last_error = %s
                where id = %s
                """,
                (reason, message_id),
            )

    def mark_failed_or_retry(self, message_id: str, attempts: int, error: str) -> None:
        with get_connection() as conn:
            conn.execute(
                """
                update notification_outbox
                set
                    status = case when %s >= 3 then 'failed'::notification_status else 'pending'::notification_status end,
                    claimed_at = null,
                    last_error = %s
                where id = %s
                """,
                (attempts, error, message_id),
            )
