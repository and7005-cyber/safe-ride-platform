from typing import Any

from app.core.db import get_connection


class ParentDao:
    def get_parent_link(self, token: str) -> dict[str, Any] | None:
        with get_connection() as conn:
            row = conn.execute(
                """
                select id, school_id, student_id
                from parent_links
                where token = %s
                    and revoked_at is null
                """,
                (token,),
            ).fetchone()
        return dict(row) if row else None

    def get_active_trip_for_student_today(self, school_id: str, student_id: str) -> dict[str, Any] | None:
        with get_connection() as conn:
            row = conn.execute(
                """
                select
                    t.id,
                    t.name,
                    t.session,
                    t.service_date,
                    t.scheduled_start,
                    t.status
                from trips t
                join trip_passengers tp
                    on tp.trip_id = t.id
                    and tp.school_id = t.school_id
                    and tp.student_id = %s
                where t.school_id = %s
                    and t.service_date = (now() at time zone 'Africa/Nairobi')::date
                    and t.status in ('scheduled', 'active', 'delayed', 'issue_reported')
                order by case t.status
                    when 'active' then 1
                    when 'delayed' then 2
                    when 'issue_reported' then 3
                    when 'scheduled' then 4
                    else 5
                end,
                t.scheduled_start desc
                limit 1
                """,
                (student_id, school_id),
            ).fetchone()
        return dict(row) if row else None

    def list_parent_progress_passengers(self, school_id: str, trip_id: str) -> list[dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                select
                    tp.id,
                    tp.student_id,
                    s.full_name as student_name,
                    coalesce(s.home_location_note, s.home_address, 'Stop ' || tp.sequence_position) as location_label,
                    tp.sequence_position,
                    tp.estimated_minutes_from_start,
                    tp.status
                from trip_passengers tp
                left join students s
                    on s.id = tp.student_id
                    and s.school_id = tp.school_id
                where tp.trip_id = %s
                    and tp.school_id = %s
                order by tp.sequence_position asc
                """,
                (trip_id, school_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def upsert_push_subscription(self, school_id: str, parent_link_id: str, endpoint: str, p256dh: str, auth: str) -> None:
        with get_connection() as conn:
            conn.execute(
                """
                insert into push_subscriptions (school_id, parent_link_id, endpoint, p256dh, auth)
                values (%s, %s, %s, %s, %s)
                on conflict (parent_link_id, endpoint) do update set
                    p256dh = excluded.p256dh,
                    auth = excluded.auth
                """,
                (school_id, parent_link_id, endpoint, p256dh, auth),
            )
