from app.core.db import get_connection


class PushDao:
    def subscribe(self, user_id: str, endpoint: str, p256dh: str | None, auth: str | None, user_agent: str | None) -> None:
        with get_connection() as conn:
            conn.execute(
                """
                insert into live_push_subscriptions (user_id, endpoint, p256dh, auth, user_agent)
                values (%s, %s, %s, %s, %s)
                on conflict (endpoint) do update set
                    user_id = excluded.user_id, p256dh = excluded.p256dh,
                    auth = excluded.auth, user_agent = excluded.user_agent
                """,
                (user_id, endpoint, p256dh, auth, user_agent),
            )

    def unsubscribe(self, user_id: str, endpoint: str) -> None:
        with get_connection() as conn:
            conn.execute(
                "delete from live_push_subscriptions where endpoint = %s and user_id = %s",
                (endpoint, user_id),
            )
