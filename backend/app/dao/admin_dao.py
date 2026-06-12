from typing import Any

from psycopg.types.json import Jsonb

from app.core.db import get_connection


class AdminDao:
    def list_active_trips(self, school_id: str) -> list[dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                select t.id, t.name, t.service_date, t.scheduled_start, t.status, b.label as bus_label
                from trips t
                left join buses b on b.id = t.bus_id and b.school_id = t.school_id
                where t.school_id = %s and t.status in ('active', 'delayed', 'issue_reported')
                order by t.scheduled_start asc
                """,
                (school_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_students(self, school_id: str) -> list[dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                select id, full_name, grade_level, home_address, home_location_note
                from students
                where school_id = %s and active = true
                order by full_name asc
                """,
                (school_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_student_directory(self, school_id: str) -> list[dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                select
                    s.id,
                    s.full_name,
                    s.grade_level,
                    s.home_address,
                    s.home_location_note,
                    pc.contact_1_name as parent_name,
                    pc.contact_1_phone as parent_phone,
                    coalesce(
                        array_remove(array_agg(distinct t.name order by t.name), null),
                        array[]::text[]
                    ) as route_names,
                    to_char(
                        min(t.scheduled_start + (tp.estimated_minutes_from_start * interval '1 minute')),
                        'HH24:MI'
                    ) as pickup_time,
                    coalesce(max(tp.status::text), 'pending') as status
                from students s
                left join parent_contacts pc
                    on pc.student_id = s.id and pc.school_id = s.school_id
                left join trip_passengers tp
                    on tp.student_id = s.id
                    and tp.school_id = s.school_id
                    and tp.passenger_type = 'student'
                left join trips t
                    on t.id = tp.trip_id and t.school_id = s.school_id
                where s.school_id = %s and s.active = true
                group by
                    s.id,
                    s.full_name,
                    s.grade_level,
                    s.home_address,
                    s.home_location_note,
                    pc.contact_1_name,
                    pc.contact_1_phone
                order by s.full_name asc
                """,
                (school_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_buses(self, school_id: str) -> list[dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                select id, label, registration_number
                from buses
                where school_id = %s and active = true
                order by label asc
                """,
                (school_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_drivers(self, school_id: str) -> list[dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                select
                    d.id,
                    d.full_name,
                    d.phone,
                    d.default_bus_id,
                    b.label as default_bus_label
                from drivers d
                left join buses b on b.id = d.default_bus_id and b.school_id = d.school_id
                where d.school_id = %s and d.active = true
                order by d.full_name asc
                """,
                (school_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def bus_belongs_to_school(self, school_id: str, bus_id: str) -> bool:
        with get_connection() as conn:
            row = conn.execute(
                """
                select 1
                from buses
                where id = %s and school_id = %s and active = true
                """,
                (bus_id, school_id),
            ).fetchone()
        return row is not None

    def list_trips(self, school_id: str) -> list[dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                select
                    t.id,
                    t.name,
                    t.service_date,
                    t.scheduled_start,
                    t.status,
                    b.label as bus_label
                from trips t
                left join buses b on b.id = t.bus_id and b.school_id = t.school_id
                where t.school_id = %s
                order by t.service_date desc, t.scheduled_start asc
                """,
                (school_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_completed_trips(self, school_id: str) -> list[dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                select id, name, service_date, status
                from trips
                where school_id = %s and status = 'completed'
                order by service_date desc
                """,
                (school_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_driver_alerts(self, school_id: str) -> list[dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                select
                    te.id,
                    coalesce(te.metadata->>'title', 'Driver Alert') as title,
                    coalesce(te.metadata->>'message', '') as message,
                    coalesce(te.metadata->>'badge', 'New') as badge,
                    te.occurred_at,
                    coalesce(d.full_name, 'Unassigned driver') as driver_name,
                    coalesce(b.label, 'Unassigned bus') as bus_label
                from trip_events te
                join trips t
                    on t.id = te.trip_id and t.school_id = te.school_id
                left join drivers d
                    on d.id = coalesce(te.created_by_id, t.driver_id)
                    and d.school_id = te.school_id
                left join buses b
                    on b.id = t.bus_id and b.school_id = te.school_id
                where te.school_id = %s
                    and (
                        te.metadata->>'admin_alert' = 'true'
                        or te.event_type = 'issue_reported'
                    )
                order by te.occurred_at desc, te.id asc
                limit 50
                """,
                (school_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_bus(self, input_data) -> dict[str, Any]:
        with get_connection() as conn:
            row = conn.execute(
                """
                insert into buses (school_id, label, registration_number)
                values (%s, %s, %s)
                returning *
                """,
                (input_data.school_id, input_data.label, input_data.registration_number),
            ).fetchone()
        return dict(row)

    def create_student(self, input_data) -> dict[str, Any]:
        with get_connection() as conn:
            row = conn.execute(
                """
                insert into students (school_id, full_name, grade_level, home_address, home_location_note)
                values (%s, %s, %s, %s, %s)
                returning *
                """,
                (
                    input_data.school_id,
                    input_data.full_name,
                    input_data.grade_level,
                    input_data.home_address,
                    input_data.home_location_note,
                ),
            ).fetchone()
        return dict(row)

    def create_driver(self, input_data, pin_hash: str) -> dict[str, Any]:
        with get_connection() as conn:
            row = conn.execute(
                """
                insert into drivers (school_id, full_name, phone, default_bus_id, pin_hash)
                values (%s, %s, %s, %s, %s)
                returning id, school_id, full_name, phone, default_bus_id
                """,
                (
                    input_data.school_id,
                    input_data.full_name,
                    input_data.phone,
                    input_data.default_bus_id,
                    pin_hash,
                ),
            ).fetchone()
        return dict(row)

    def upsert_parent_contact(self, input_data) -> dict[str, Any]:
        with get_connection() as conn:
            row = conn.execute(
                """
                insert into parent_contacts (
                    school_id,
                    student_id,
                    contact_1_name,
                    contact_1_phone,
                    contact_1_relationship,
                    contact_2_name,
                    contact_2_phone,
                    contact_2_relationship
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s)
                on conflict (student_id) do update set
                    school_id = excluded.school_id,
                    contact_1_name = excluded.contact_1_name,
                    contact_1_phone = excluded.contact_1_phone,
                    contact_1_relationship = excluded.contact_1_relationship,
                    contact_2_name = excluded.contact_2_name,
                    contact_2_phone = excluded.contact_2_phone,
                    contact_2_relationship = excluded.contact_2_relationship
                returning *
                """,
                (
                    input_data.school_id,
                    input_data.student_id,
                    input_data.contact_1_name,
                    input_data.contact_1_phone,
                    input_data.contact_1_relationship,
                    input_data.contact_2_name,
                    input_data.contact_2_phone,
                    input_data.contact_2_relationship,
                ),
            ).fetchone()
        return dict(row)

    def create_parent_link(self, input_data) -> dict[str, Any]:
        with get_connection() as conn:
            row = conn.execute(
                """
                insert into parent_links (school_id, student_id, token)
                values (%s, %s, %s)
                returning *
                """,
                (input_data.school_id, input_data.student_id, input_data.token),
            ).fetchone()
        return dict(row)

    def create_trip(self, input_data) -> dict[str, Any]:
        with get_connection() as conn:
            row = conn.execute(
                """
                insert into trips (
                    school_id,
                    bus_id,
                    driver_id,
                    name,
                    session,
                    service_date,
                    scheduled_start
                )
                values (%s, %s, %s, %s, %s, %s, %s)
                returning *
                """,
                (
                    input_data.school_id,
                    input_data.bus_id,
                    input_data.driver_id,
                    input_data.name,
                    input_data.session,
                    input_data.service_date,
                    input_data.scheduled_start,
                ),
            ).fetchone()
        return dict(row)

    def create_trip_passenger(self, input_data) -> dict[str, Any]:
        with get_connection() as conn:
            row = conn.execute(
                """
                insert into trip_passengers (
                    school_id,
                    trip_id,
                    passenger_type,
                    student_id,
                    sequence_position,
                    estimated_minutes_from_start
                )
                values (%s, %s, 'student', %s, %s, %s)
                returning *
                """,
                (
                    input_data.school_id,
                    input_data.trip_id,
                    input_data.student_id,
                    input_data.sequence_position,
                    input_data.estimated_minutes_from_start,
                ),
            ).fetchone()
        return dict(row)

    def update_student(self, student_id: str, input_data) -> dict[str, Any] | None:
        with get_connection() as conn:
            row = conn.execute(
                """
                update students
                set full_name = %s,
                    grade_level = %s,
                    home_address = %s,
                    home_location_note = %s
                where id = %s
                    and school_id = %s
                    and active = true
                returning id, school_id, full_name, grade_level, home_address, home_location_note
                """,
                (
                    input_data.full_name,
                    input_data.grade_level,
                    input_data.home_address,
                    input_data.home_location_note,
                    student_id,
                    input_data.school_id,
                ),
            ).fetchone()
        return dict(row) if row else None

    def create_student_setup(self, input_data, parent_link_token: str | None) -> dict[str, Any]:
        with get_connection() as conn:
            with conn.transaction():
                if input_data.trip_assignment:
                    trip = conn.execute(
                        """
                        select id
                        from trips
                        where id = %s and school_id = %s
                        """,
                        (input_data.trip_assignment.trip_id, input_data.school_id),
                    ).fetchone()
                    if not trip:
                        return {"status": "trip_not_found"}

                student = conn.execute(
                    """
                    insert into students (school_id, full_name, grade_level, home_address, home_location_note)
                    values (%s, %s, %s, %s, %s)
                    returning id, school_id, full_name, grade_level, home_address, home_location_note
                    """,
                    (
                        input_data.school_id,
                        input_data.student.full_name,
                        input_data.student.grade_level,
                        input_data.student.home_address,
                        input_data.student.home_location_note,
                    ),
                ).fetchone()

                parent_contact = None
                if input_data.parent_contact:
                    parent_contact = conn.execute(
                        """
                        insert into parent_contacts (
                            school_id,
                            student_id,
                            contact_1_name,
                            contact_1_phone,
                            contact_1_relationship,
                            contact_2_name,
                            contact_2_phone,
                            contact_2_relationship
                        )
                        values (%s, %s, %s, %s, %s, %s, %s, %s)
                        on conflict (student_id) do update set
                            school_id = excluded.school_id,
                            contact_1_name = excluded.contact_1_name,
                            contact_1_phone = excluded.contact_1_phone,
                            contact_1_relationship = excluded.contact_1_relationship,
                            contact_2_name = excluded.contact_2_name,
                            contact_2_phone = excluded.contact_2_phone,
                            contact_2_relationship = excluded.contact_2_relationship
                        returning *
                        """,
                        (
                            input_data.school_id,
                            student["id"],
                            input_data.parent_contact.contact_1_name,
                            input_data.parent_contact.contact_1_phone,
                            input_data.parent_contact.contact_1_relationship,
                            input_data.parent_contact.contact_2_name,
                            input_data.parent_contact.contact_2_phone,
                            input_data.parent_contact.contact_2_relationship,
                        ),
                    ).fetchone()

                parent_link = None
                if parent_link_token:
                    parent_link = conn.execute(
                        """
                        insert into parent_links (school_id, student_id, token)
                        values (%s, %s, %s)
                        returning id, school_id, student_id, token
                        """,
                        (input_data.school_id, student["id"], parent_link_token),
                    ).fetchone()

                trip_passenger = None
                if input_data.trip_assignment:
                    trip_passenger = conn.execute(
                        """
                        insert into trip_passengers (
                            school_id,
                            trip_id,
                            passenger_type,
                            student_id,
                            sequence_position,
                            estimated_minutes_from_start
                        )
                        values (%s, %s, 'student', %s, %s, %s)
                        returning *
                        """,
                        (
                            input_data.school_id,
                            input_data.trip_assignment.trip_id,
                            student["id"],
                            input_data.trip_assignment.sequence_position,
                            input_data.trip_assignment.estimated_minutes_from_start,
                        ),
                    ).fetchone()

                return {
                    "status": "ok",
                    "data": {
                        "student": dict(student),
                        "parentContact": dict(parent_contact) if parent_contact else None,
                        "parentLink": dict(parent_link) if parent_link else None,
                        "tripPassenger": dict(trip_passenger) if trip_passenger else None,
                    },
                }

    def upsert_daily_attendance(self, input_data) -> dict[str, Any]:
        with get_connection() as conn:
            return self._upsert_daily_attendance(conn, input_data)

    def apply_daily_attendance(self, attendance_row: dict[str, Any]) -> None:
        with get_connection() as conn:
            self._apply_daily_attendance(conn, attendance_row)

    def mark_daily_attendance(self, input_data) -> dict[str, Any]:
        with get_connection() as conn:
            attendance_row = self._upsert_daily_attendance(conn, input_data)
            self._apply_daily_attendance(conn, attendance_row)
            return attendance_row

    def get_trip_passenger_for_update(self, school_id: str, trip_passenger_id: str) -> dict[str, Any] | None:
        with get_connection() as conn:
            return self._get_trip_passenger_for_update(conn, school_id, trip_passenger_id)

    def correct_trip_passenger_status(
        self,
        school_id: str,
        trip_passenger_id: str,
        corrected_status: str,
    ) -> dict[str, Any]:
        with get_connection() as conn:
            return self._correct_trip_passenger_status(
                conn,
                school_id,
                trip_passenger_id,
                corrected_status,
            )

    def insert_audit_log(
        self,
        school_id: str,
        entity_id: str,
        original_value: dict,
        corrected_value: dict,
        reason: str,
    ):
        with get_connection() as conn:
            return self._insert_audit_log(
                conn,
                school_id,
                entity_id,
                original_value,
                corrected_value,
                reason,
            )

    def correct_trip_passenger_status_with_audit(
        self,
        school_id: str,
        trip_passenger_id: str,
        corrected_status: str,
        reason: str,
    ) -> dict[str, Any]:
        with get_connection() as conn:
            existing = self._get_trip_passenger_for_update(conn, school_id, trip_passenger_id)
            if not existing:
                return {"status": "not_found"}
            if existing["trip_status"] != "completed":
                return {"status": "not_completed"}

            original_value = {
                "status": existing["status"],
                "actual_pickup_time": existing["actual_pickup_time"],
                "actual_dropoff_time": existing["actual_dropoff_time"],
            }
            corrected_value = {"status": corrected_status}
            self._correct_trip_passenger_status(
                conn,
                school_id,
                trip_passenger_id,
                corrected_status,
            )
            audit_id = self._insert_audit_log(
                conn,
                school_id,
                trip_passenger_id,
                original_value,
                corrected_value,
                reason,
            )
            return {"status": "ok", "audit_id": audit_id}

    def _upsert_daily_attendance(self, conn, input_data) -> dict[str, Any]:
        row = conn.execute(
            """
            insert into daily_attendance (
                school_id,
                student_id,
                attendance_date,
                status,
                marked_by,
                note
            )
            values (%s, %s, %s, %s, 'local-admin', %s)
            on conflict (student_id, attendance_date) do update set
                school_id = excluded.school_id,
                status = excluded.status,
                marked_by = excluded.marked_by,
                marked_at = now(),
                note = excluded.note
            returning *
            """,
            (
                input_data.school_id,
                input_data.student_id,
                input_data.attendance_date,
                input_data.status,
                input_data.note,
            ),
        ).fetchone()
        return dict(row)

    def _apply_daily_attendance(self, conn, attendance_row: dict[str, Any]) -> None:
        conn.execute(
            """
            update trip_passengers tp
            set status = case
                when %s = 'absent' then 'absent_admin'::trip_passenger_status
                when %s = 'alternative_transport' then 'alternative_transport'::trip_passenger_status
                else 'pending'::trip_passenger_status
            end
            from trips t
            where tp.trip_id = t.id
                and tp.school_id = t.school_id
                and tp.student_id = %s
                and t.school_id = %s
                and t.service_date = %s
                and t.status in ('scheduled', 'active')
                and tp.status in (
                    'pending',
                    'absent_admin',
                    'alternative_transport'
                )
            """,
            (
                attendance_row["status"],
                attendance_row["status"],
                attendance_row["student_id"],
                attendance_row["school_id"],
                attendance_row["attendance_date"],
            ),
        )

    def _get_trip_passenger_for_update(self, conn, school_id: str, trip_passenger_id: str) -> dict[str, Any] | None:
        row = conn.execute(
            """
            select
                tp.id,
                tp.school_id,
                tp.status,
                tp.actual_pickup_time,
                tp.actual_dropoff_time,
                t.status as trip_status
            from trip_passengers tp
            join trips t on t.id = tp.trip_id and t.school_id = tp.school_id
            where tp.school_id = %s and tp.id = %s
            for update of tp
            """,
            (school_id, trip_passenger_id),
        ).fetchone()
        return dict(row) if row else None

    def _correct_trip_passenger_status(
        self,
        conn,
        school_id: str,
        trip_passenger_id: str,
        corrected_status: str,
    ) -> dict[str, Any]:
        row = conn.execute(
            """
            update trip_passengers
            set status = %s
            where id = %s and school_id = %s
            returning *
            """,
            (corrected_status, trip_passenger_id, school_id),
        ).fetchone()
        return dict(row)

    def _insert_audit_log(
        self,
        conn,
        school_id: str,
        entity_id: str,
        original_value: dict,
        corrected_value: dict,
        reason: str,
    ):
        row = conn.execute(
            """
            insert into audit_log (
                school_id,
                entity_table,
                entity_id,
                original_value,
                corrected_value,
                reason
            )
            values (%s, 'trip_passengers', %s, %s, %s, %s)
            returning id
            """,
            (
                school_id,
                entity_id,
                Jsonb(original_value),
                Jsonb(corrected_value),
                reason,
            ),
        ).fetchone()
        return row["id"]
