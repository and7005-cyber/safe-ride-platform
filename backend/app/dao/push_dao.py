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

    def register_fcm_token(self, user_id: str, token: str, user_agent: str | None) -> None:
        with get_connection() as conn:
            conn.execute(
                """
                insert into live_fcm_tokens (user_id, token, user_agent)
                values (%s, %s, %s)
                on conflict (token) do update set
                    user_id = excluded.user_id, user_agent = excluded.user_agent
                """,
                (user_id, token, user_agent),
            )

    def unregister_fcm_token(self, user_id: str, token: str) -> None:
        with get_connection() as conn:
            conn.execute(
                "delete from live_fcm_tokens where token = %s and user_id = %s",
                (token, user_id),
            )

    def delete_fcm_token(self, token: str) -> None:
        """Drop a token FCM reported as dead, whoever owns it."""
        with get_connection() as conn:
            conn.execute("delete from live_fcm_tokens where token = %s", (token,))

    def fcm_tokens_for_users(self, user_ids: list[str]) -> list[dict]:
        if not user_ids:
            return []
        with get_connection() as conn:
            rows = conn.execute(
                "select user_id, token from live_fcm_tokens where user_id = any(%s)",
                (user_ids,),
            ).fetchall()
        return [dict(row) for row in rows]

    def web_push_subscriptions_for_users(self, user_ids: list[str]) -> list[dict]:
        if not user_ids:
            return []
        with get_connection() as conn:
            rows = conn.execute(
                """
                select user_id, endpoint, p256dh, auth
                from live_push_subscriptions
                where user_id = any(%s)
                """,
                (user_ids,),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_web_push_subscription(self, endpoint: str) -> None:
        """Drop a subscription the push service reported as gone (404/410)."""
        with get_connection() as conn:
            conn.execute(
                "delete from live_push_subscriptions where endpoint = %s", (endpoint,)
            )

    def active_run_for_driver(self, driver_id: str) -> dict | None:
        """Today's active run for the driver's bus, if any.

        Mirrors RunDao.find_active_run_today: anything not completed counts
        (an admin marking a run 'delayed' must not mute its notifications).
        """
        with get_connection() as conn:
            row = conn.execute(
                """
                select r.* from live_runs r
                join live_buses b on b.id = r.bus_id
                where b.driver_id = %s
                  and r.date = (now() at time zone 'Africa/Nairobi')::date
                  and r.status <> 'completed'
                order by r.created_at desc
                limit 1
                """,
                (driver_id,),
            ).fetchone()
        return dict(row) if row else None

    def bus_name(self, bus_id: str) -> str | None:
        with get_connection() as conn:
            row = conn.execute("select name from live_buses where id = %s", (bus_id,)).fetchone()
        return row["name"] if row else None

    def parents_of_students(self, student_ids: list[str]) -> list[dict]:
        """Resolve (parent_id, student_id, student_name) pairs for students."""
        if not student_ids:
            return []
        with get_connection() as conn:
            rows = conn.execute(
                """
                select ps.parent_id, ps.student_id, s.name as student_name
                from live_parent_students ps
                join live_students s on s.id = ps.student_id
                where ps.student_id = any(%s)
                """,
                (student_ids,),
            ).fetchall()
        return [dict(row) for row in rows]

    def parents_of_bus(self, bus_id: str) -> list[dict]:
        """Resolve (parent_id, student_id, student_name) pairs for a bus."""
        with get_connection() as conn:
            rows = conn.execute(
                """
                select ps.parent_id, ps.student_id, s.name as student_name
                from live_parent_students ps
                join live_students s on s.id = ps.student_id
                where s.bus_id = %s
                """,
                (bus_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def students_on_run(self, run_id: str, include_absent: bool = False) -> list[dict]:
        """Students with a seat on the run's stop roster."""
        with get_connection() as conn:
            rows = conn.execute(
                """
                select distinct s.id, s.name, s.status
                from run_stops rs
                join live_students s on s.id = rs.student_id
                where rs.run_id = %s
                """,
                (run_id,),
            ).fetchall()
        students = [dict(row) for row in rows]
        if include_absent:
            return students
        return [s for s in students if s["status"] != "absent"]

    def remaining_student_stops(self, run_id: str, stops_completed: int) -> list[dict]:
        """Upcoming (not yet reached) student stops for a run, with coordinates."""
        with get_connection() as conn:
            rows = conn.execute(
                """
                select rs.stop_order, rs.lat, rs.lng, rs.student_id, s.name as student_name,
                       s.status as student_status
                from run_stops rs
                join live_students s on s.id = rs.student_id
                where rs.run_id = %s
                  and rs.stop_order > %s
                  and rs.is_school_gate = false
                  and rs.student_id is not null
                  and rs.lat is not null
                  and rs.lng is not null
                """,
                (run_id, stops_completed),
            ).fetchall()
        return [dict(row) for row in rows]

    def insert_notification(
        self,
        user_id: str,
        type: str,
        title: str,
        body: str,
        student_id: str | None = None,
        run_id: str | None = None,
        bus_id: str | None = None,
    ) -> dict | None:
        """Insert a feed row. Returns None when the run-scoped dedup suppressed it."""
        with get_connection() as conn:
            row = conn.execute(
                """
                insert into live_notifications (user_id, student_id, run_id, bus_id, type, title, body)
                values (%s, %s, %s, %s, %s, %s, %s)
                on conflict do nothing
                returning id, user_id, student_id, run_id, bus_id, type, title, body, read, created_at
                """,
                (user_id, student_id, run_id, bus_id, type, title, body),
            ).fetchone()
        return dict(row) if row else None

    def list_notifications(self, user_id: str, limit: int = 50) -> list[dict]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                select id, student_id, run_id, bus_id, type, title, body, read, created_at
                from live_notifications
                where user_id = %s
                order by created_at desc
                limit %s
                """,
                (user_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_notifications_read(self, user_id: str) -> None:
        with get_connection() as conn:
            conn.execute(
                "update live_notifications set read = true where user_id = %s and read = false",
                (user_id,),
            )

    def unread_count(self, user_id: str) -> int:
        with get_connection() as conn:
            row = conn.execute(
                "select count(*) as count from live_notifications where user_id = %s and read = false",
                (user_id,),
            ).fetchone()
        return int(row["count"]) if row else 0
