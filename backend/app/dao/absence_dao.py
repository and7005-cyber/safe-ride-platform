"""Per-date student absences (#7).

Marking a student absent for a date suppresses their stop on that day's run
without touching their persistent status, so the record is self-clearing the
next day.
"""
from typing import Any

from app.core.db import get_connection


def absent_student_ids(conn, date: str | None = None) -> set[str]:
    """Set of student ids marked absent on ``date`` (defaults to today, Nairobi)."""
    rows = conn.execute(
        """
        select student_id from live_student_absences
        where absence_date = coalesce(%s::date, (now() at time zone 'Africa/Nairobi')::date)
        """,
        (date,),
    ).fetchall()
    return {str(r["student_id"]) for r in rows}


class AbsenceDao:
    def list_absences(self, date: str | None = None) -> list[dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                select a.id, a.student_id, a.absence_date, a.reason, a.created_at,
                       s.name as student_name, s.grade
                from live_student_absences a
                join live_students s on s.id = a.student_id
                where %s::date is null or a.absence_date = %s::date
                order by a.absence_date desc, s.name asc
                """,
                (date, date),
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_absent(
        self, student_id: str, date: str | None, reason: str | None, marked_by: str | None
    ) -> dict[str, Any]:
        with get_connection() as conn:
            row = conn.execute(
                """
                insert into live_student_absences (student_id, absence_date, reason, marked_by)
                values (
                    %s,
                    coalesce(%s::date, (now() at time zone 'Africa/Nairobi')::date),
                    %s, %s
                )
                on conflict (student_id, absence_date)
                do update set reason = excluded.reason, marked_by = excluded.marked_by
                returning *
                """,
                (student_id, date, reason, marked_by),
            ).fetchone()
        return dict(row)

    def clear_absence(self, absence_id: str) -> None:
        with get_connection() as conn:
            conn.execute("delete from live_student_absences where id = %s", (absence_id,))

    def clear_for_student_date(self, student_id: str, date: str) -> None:
        with get_connection() as conn:
            conn.execute(
                "delete from live_student_absences where student_id = %s and absence_date = %s::date",
                (student_id, date),
            )
