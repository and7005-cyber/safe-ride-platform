from typing import Any

from psycopg.types.json import Jsonb

from app.core.db import get_connection
from app.core.errors import ConflictError, ForbiddenError, UnauthorizedError
from app.core.security import hash_session_token

PARENT_NOTIFICATION_TEMPLATES = {
    "passenger_boarded": (
        "child_confirmed_on_van",
        "SafeRide: Your child has been confirmed on the van.",
    ),
    "passenger_dropped": (
        "child_dropped_off_home",
        "SafeRide: Your child has been confirmed dropped off at home.",
    ),
    "passenger_not_present": (
        "child_not_boarded",
        "SafeRide: Your child was expected but was not confirmed by the driver. Please contact the school.",
    ),
}


class DriverDao:
    def list_active_drivers(self) -> list[dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                select id, school_id, full_name, pin_hash
                from drivers
                where active = true
                order by full_name asc
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def create_driver_session(self, school_id: str, driver_id: str, token_hash: str) -> None:
        with get_connection() as conn:
            conn.execute(
                """
                insert into driver_sessions (school_id, driver_id, token_hash, expires_at)
                values (%s, %s, %s, now() + interval '16 hours')
                """,
                (school_id, driver_id, token_hash),
            )

    def get_session(self, session_token: str) -> dict[str, Any] | None:
        with get_connection() as conn:
            return self._get_session(conn, session_token)

    def list_trips_for_today(self, session_token: str, service_date: str) -> list[dict[str, Any]]:
        with get_connection() as conn:
            session = self._get_session(conn, session_token)
            if not session:
                raise UnauthorizedError("Driver session is invalid or expired")

            rows = conn.execute(
                """
                select
                    t.id,
                    t.name,
                    t.scheduled_start as "scheduledStart",
                    t.session,
                    t.status,
                    b.label as "busLabel"
                from trips t
                left join buses b
                    on b.id = t.bus_id
                    and b.school_id = t.school_id
                where t.driver_id = %s
                    and t.school_id = %s
                    and t.service_date = %s
                order by t.scheduled_start asc
                """,
                (session["driver_id"], session["school_id"], service_date),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_trip_passengers(self, session_token: str, trip_id: str) -> list[dict[str, Any]]:
        with get_connection() as conn:
            session = self._get_session(conn, session_token)
            if not session:
                raise UnauthorizedError("Driver session is invalid or expired")
            if not self._get_assigned_trip(conn, session["school_id"], session["driver_id"], trip_id):
                raise ForbiddenError("Trip is not assigned to this driver")

            rows = conn.execute(
                """
                select
                    tp.id,
                    coalesce(s.full_name, sp.full_name, 'Passenger ' || tp.sequence_position) as name,
                    tp.sequence_position as "sequencePosition",
                    tp.status
                from trip_passengers tp
                left join students s
                    on s.id = tp.student_id
                    and s.school_id = tp.school_id
                left join staff_passengers sp
                    on sp.id = tp.staff_passenger_id
                    and sp.school_id = tp.school_id
                where tp.trip_id = %s
                    and tp.school_id = %s
                    and tp.status not in ('absent_admin', 'alternative_transport')
                order by tp.sequence_position asc
                """,
                (trip_id, session["school_id"]),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_assigned_trip(self, school_id: str, driver_id: str, trip_id: str) -> dict[str, Any] | None:
        with get_connection() as conn:
            return self._get_assigned_trip(conn, school_id, driver_id, trip_id)

    def get_trip_passenger(self, school_id: str, trip_id: str, trip_passenger_id: str) -> dict[str, Any] | None:
        with get_connection() as conn:
            return self._get_trip_passenger(conn, school_id, trip_id, trip_passenger_id)

    def insert_trip_event(
        self,
        school_id: str,
        trip_id: str,
        trip_passenger_id: str | None,
        event_type: str,
        driver_id: str,
        occurred_at,
        metadata: dict,
    ) -> str:
        with get_connection() as conn:
            return self._insert_trip_event(conn, school_id, trip_id, trip_passenger_id, event_type, driver_id, occurred_at, metadata)

    def apply_trip_event(
        self,
        event_id: str,
        event_type: str,
        trip_id: str,
        school_id: str,
        trip_passenger_id: str | None,
        occurred_at,
    ) -> None:
        with get_connection() as conn:
            self._apply_trip_event(conn, event_id, event_type, trip_id, school_id, trip_passenger_id, occurred_at)

    def enqueue_parent_notifications(
        self,
        event_id: str,
        school_id: str,
        trip_passenger_id: str,
        event_type: str,
        metadata: dict,
    ) -> None:
        with get_connection() as conn:
            self._enqueue_parent_notifications(conn, event_id, school_id, trip_passenger_id, event_type, metadata)

    def record_trip_event_atomic(
        self,
        school_id: str,
        trip_id: str,
        trip_passenger_id: str | None,
        event_type: str,
        driver_id: str,
        occurred_at,
        metadata: dict,
    ) -> str:
        with get_connection() as conn:
            event_id = self._insert_trip_event(conn, school_id, trip_id, trip_passenger_id, event_type, driver_id, occurred_at, metadata)
            applied = self._apply_trip_event(conn, event_id, event_type, trip_id, school_id, trip_passenger_id, occurred_at)
            if not applied:
                raise ConflictError("Driver event could not be applied to current trip state")
            if event_type in PARENT_NOTIFICATION_TEMPLATES and trip_passenger_id:
                self._enqueue_parent_notifications(conn, event_id, school_id, trip_passenger_id, event_type, metadata)
            return event_id

    def _get_session(self, conn, session_token: str) -> dict[str, Any] | None:
        row = conn.execute(
            """
            select ds.driver_id, ds.school_id
            from driver_sessions ds
            join drivers d
                on d.id = ds.driver_id
                and d.school_id = ds.school_id
            where d.active = true
                and ds.token_hash = %s
                and ds.revoked_at is null
                and ds.expires_at > now()
            """,
            (hash_session_token(session_token),),
        ).fetchone()
        return dict(row) if row else None

    def _get_assigned_trip(self, conn, school_id: str, driver_id: str, trip_id: str) -> dict[str, Any] | None:
        row = conn.execute(
            """
            select id, school_id, driver_id, status
            from trips
            where id = %s
                and school_id = %s
                and driver_id = %s
            """,
            (trip_id, school_id, driver_id),
        ).fetchone()
        return dict(row) if row else None

    def _get_trip_passenger(self, conn, school_id: str, trip_id: str, trip_passenger_id: str) -> dict[str, Any] | None:
        row = conn.execute(
            """
            select id, status, student_id
            from trip_passengers
            where id = %s
                and school_id = %s
                and trip_id = %s
            """,
            (trip_passenger_id, school_id, trip_id),
        ).fetchone()
        return dict(row) if row else None

    def _insert_trip_event(
        self,
        conn,
        school_id: str,
        trip_id: str,
        trip_passenger_id: str | None,
        event_type: str,
        driver_id: str,
        occurred_at,
        metadata: dict,
    ) -> str:
        row = conn.execute(
            """
            insert into trip_events (
                school_id,
                trip_id,
                trip_passenger_id,
                event_type,
                created_by_role,
                created_by_id,
                occurred_at,
                metadata
            )
            values (%s, %s, %s, %s::event_type, 'driver', %s, coalesce(%s::timestamptz, now()), %s)
            returning id
            """,
            (
                school_id,
                trip_id,
                trip_passenger_id,
                event_type,
                driver_id,
                occurred_at,
                Jsonb(metadata or {}),
            ),
        ).fetchone()
        return row["id"]

    def _apply_trip_event(
        self,
        conn,
        event_id: str,
        event_type: str,
        trip_id: str,
        school_id: str,
        trip_passenger_id: str | None,
        occurred_at,
    ) -> bool:
        row = conn.execute(
            """
            select occurred_at
            from trip_events
            where id = %s and school_id = %s
            """,
            (event_id, school_id),
        ).fetchone()
        effective_occurred_at = occurred_at or row["occurred_at"]

        if event_type == "passenger_boarded":
            cursor = conn.execute(
                """
                update trip_passengers
                set status = 'boarded', actual_pickup_time = %s::timestamptz
                where id = %s
                    and school_id = %s
                    and trip_id = %s
                    and status = 'pending'
                """,
                (effective_occurred_at, trip_passenger_id, school_id, trip_id),
            )
            return cursor.rowcount == 1
        elif event_type == "passenger_dropped":
            cursor = conn.execute(
                """
                update trip_passengers
                set status = 'dropped', actual_dropoff_time = %s::timestamptz
                where id = %s
                    and school_id = %s
                    and trip_id = %s
                    and status = 'boarded'
                """,
                (effective_occurred_at, trip_passenger_id, school_id, trip_id),
            )
            return cursor.rowcount == 1
        elif event_type == "passenger_not_present":
            cursor = conn.execute(
                """
                update trip_passengers
                set status = 'absent_driver'
                where id = %s
                    and school_id = %s
                    and trip_id = %s
                    and status = 'pending'
                """,
                (trip_passenger_id, school_id, trip_id),
            )
            return cursor.rowcount == 1
        elif event_type == "trip_started":
            cursor = conn.execute(
                """
                update trips
                set status = 'active', started_at = %s::timestamptz
                where id = %s and school_id = %s and status = 'scheduled'
                """,
                (effective_occurred_at, trip_id, school_id),
            )
            return cursor.rowcount == 1
        elif event_type == "trip_ended":
            cursor = conn.execute(
                """
                update trips
                set status = 'completed', ended_at = %s::timestamptz
                where id = %s
                    and school_id = %s
                    and status in ('active', 'issue_reported', 'delayed')
                """,
                (effective_occurred_at, trip_id, school_id),
            )
            return cursor.rowcount == 1
        elif event_type == "issue_reported":
            cursor = conn.execute(
                """
                update trips
                set status = 'issue_reported'
                where id = %s
                    and school_id = %s
                    and status in ('active', 'delayed', 'issue_reported')
                """,
                (trip_id, school_id),
            )
            return cursor.rowcount == 1
        return False

    def _enqueue_parent_notifications(
        self,
        conn,
        event_id: str,
        school_id: str,
        trip_passenger_id: str,
        event_type: str,
        metadata: dict,
    ) -> None:
        template = PARENT_NOTIFICATION_TEMPLATES.get(event_type)
        if not template:
            return

        template_key, body = template
        payload = dict(metadata or {})
        payload["body"] = body
        conn.execute(
            """
            insert into notification_outbox (
                school_id,
                trip_event_id,
                recipient_kind,
                recipient_phone,
                channel,
                template_key,
                payload
            )
            select
                %s,
                %s,
                'parent',
                parent_phones.recipient_phone,
                'sms',
                %s,
                %s
            from (
                select distinct recipient_phone
                from (
                    select pc.contact_1_phone as recipient_phone
                    from trip_passengers tp
                    join parent_contacts pc
                        on pc.student_id = tp.student_id
                        and pc.school_id = tp.school_id
                    where tp.id = %s
                        and tp.school_id = %s
                        and tp.passenger_type = 'student'
                    union all
                    select pc.contact_2_phone as recipient_phone
                    from trip_passengers tp
                    join parent_contacts pc
                        on pc.student_id = tp.student_id
                        and pc.school_id = tp.school_id
                    where tp.id = %s
                        and tp.school_id = %s
                        and tp.passenger_type = 'student'
                ) contact_phones
                where recipient_phone is not null
            ) parent_phones
            where not exists (
                select 1
                from notification_outbox existing_outbox
                join trip_events existing_events
                    on existing_events.id = existing_outbox.trip_event_id
                    and existing_events.school_id = existing_outbox.school_id
                where existing_outbox.school_id = %s
                    and existing_events.trip_passenger_id = %s
                    and existing_outbox.template_key = %s
                    and existing_outbox.recipient_kind = 'parent'
                    and existing_outbox.channel = 'sms'
                    and existing_outbox.recipient_phone = parent_phones.recipient_phone
            )
            """,
            (
                school_id,
                event_id,
                template_key,
                Jsonb(payload),
                trip_passenger_id,
                school_id,
                trip_passenger_id,
                school_id,
                school_id,
                trip_passenger_id,
                template_key,
            ),
        )
