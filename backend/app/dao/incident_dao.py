from typing import Any

from app.core.db import get_connection


class IncidentDao:
    def list_incidents(self) -> list[dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                "select * from live_incidents order by created_at desc"
            ).fetchall()
        return [dict(r) for r in rows]

    def unacknowledged_count(self) -> int:
        with get_connection() as conn:
            row = conn.execute(
                "select count(*) as n from live_incidents where acknowledged = false"
            ).fetchone()
        return row["n"]

    def today_count(self) -> int:
        with get_connection() as conn:
            row = conn.execute(
                """
                select count(*) as n from live_incidents
                where created_at >= (now() at time zone 'Africa/Nairobi')::date
                """
            ).fetchone()
        return row["n"]

    def create_incident(self, data: dict) -> dict[str, Any]:
        with get_connection() as conn:
            row = conn.execute(
                """
                insert into live_incidents (driver_id, driver_name, bus_id, bus_name, type, description)
                values (%(driver_id)s, %(driver_name)s, %(bus_id)s, %(bus_name)s, %(type)s, %(description)s)
                returning *
                """,
                data,
            ).fetchone()
        return dict(row)

    def create_driver_incident(
        self,
        driver_id: str,
        incident_type: str,
        description: str,
        run_id: str | None = None,
        run_type: str | None = None,
        student_id: str | None = None,
    ) -> dict[str, Any]:
        """Insert an incident reported by a driver, stamped with run context.

        run_type persists the period even after the run row is deleted
        (run_id is ON DELETE SET NULL). A non-null student_id marks a
        child-specific incident (the absent flow): those rows surface only on
        the admin Alerts page — ParentLiveDao.list_alerts excludes them — and
        this layer never fans out to parents, so callers of the absent flow
        insert directly here without notify_incident.
        """
        with get_connection() as conn:
            bus = conn.execute(
                "select * from live_buses where driver_id = %s limit 1", (driver_id,)
            ).fetchone()
            driver = conn.execute(
                "select full_name from app_users where id = %s", (driver_id,)
            ).fetchone()
            row = conn.execute(
                """
                insert into live_incidents
                    (driver_id, driver_name, bus_id, bus_name, type, description,
                     run_id, run_type, student_id)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s) returning *
                """,
                (driver_id, driver["full_name"] if driver else None,
                 bus["id"] if bus else None, bus["name"] if bus else None,
                 incident_type, description, run_id, run_type, student_id),
            ).fetchone()
        return dict(row)

    def acknowledge(self, incident_id: str, admin_id: str) -> dict[str, Any] | None:
        with get_connection() as conn:
            row = conn.execute(
                "update live_incidents set acknowledged=true, acknowledged_at=now(), acknowledged_by=%s "
                "where id=%s returning *",
                (admin_id, incident_id),
            ).fetchone()
        return dict(row) if row else None

    def delete_incident(self, incident_id: str) -> None:
        with get_connection() as conn:
            conn.execute("delete from live_incidents where id = %s", (incident_id,))
