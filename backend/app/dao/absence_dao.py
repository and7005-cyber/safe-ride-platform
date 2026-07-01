"""Per-date student absences (#7).

Marking a student absent for a date suppresses their stop on that day's run,
so the record is self-clearing the next day. TODAY-dated marks/clears also
carry operational side-effects (R25b): a mark sets the live status to
'absent' and appends the run_absences snapshot of any active run whose
roster (run_stops) carries the student; a clear resets an 'absent' status to
'at-school' — but is rejected while the student's bus has an active run
(the run already excluded the stop; un-absenting mid-run is incoherent).
Past/future-dated marks and clears never touch the live status.
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
        """Upsert an absence (date=None → today, Nairobi). A TODAY-dated mark
        also sets the live status to 'absent' and appends the run_absences
        snapshot of any active run whose run_stops roster carries the student
        (run-scoped — never the derived bus_id roster), all in one
        transaction. Other dates have no status side-effects."""
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
                returning *,
                    (absence_date = (now() at time zone 'Africa/Nairobi')::date) as is_today
                """,
                (student_id, date, reason, marked_by),
            ).fetchone()
            if row["is_today"]:
                conn.execute(
                    "update live_students set status = 'absent' where id = %s",
                    (student_id,),
                )
                conn.execute(
                    """
                    insert into run_absences (run_id, student_id, student_name, reason)
                    select r.id, s.id, s.name, %s
                    from live_runs r
                    join live_students s on s.id = %s
                    where r.status <> 'completed'
                      and r.date = (now() at time zone 'Africa/Nairobi')::date
                      and exists (
                          select 1 from run_stops rs
                          where rs.run_id = r.id and rs.student_id = s.id
                      )
                    on conflict (run_id, student_id) do nothing
                    """,
                    (reason, student_id),
                )
        result = dict(row)
        result.pop("is_today", None)
        return result

    def clear_absence(self, absence_id: str) -> None:
        """Delete an absence. Clearing a TODAY-dated absence is rejected while
        the student's bus has an active run ('End the run first' — the run
        already excluded the stop); otherwise it resets the live status to
        'at-school', but only when the current status is 'absent'. Clearing
        past/future-dated absences never touches the status."""
        from app.core.errors import ConflictError

        with get_connection() as conn:
            row = conn.execute(
                """
                select a.student_id,
                       (a.absence_date = (now() at time zone 'Africa/Nairobi')::date) as is_today,
                       s.bus_id, s.status
                from live_student_absences a
                join live_students s on s.id = a.student_id
                where a.id = %s
                """,
                (absence_id,),
            ).fetchone()
            if row and row["is_today"]:
                if row["bus_id"]:
                    active = conn.execute(
                        """
                        select 1 from live_runs
                        where bus_id = %s and status <> 'completed'
                          and date = (now() at time zone 'Africa/Nairobi')::date
                        limit 1
                        """,
                        (row["bus_id"],),
                    ).fetchone()
                    if active:
                        raise ConflictError("End the run first")
                if row["status"] == "absent":
                    conn.execute(
                        "update live_students set status = 'at-school' where id = %s",
                        (row["student_id"],),
                    )
            conn.execute("delete from live_student_absences where id = %s", (absence_id,))

    def clear_for_student_date(self, student_id: str, date: str) -> None:
        with get_connection() as conn:
            conn.execute(
                "delete from live_student_absences where student_id = %s and absence_date = %s::date",
                (student_id, date),
            )
