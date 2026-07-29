from typing import Any

from app.core.db import get_connection

# One sentence shape for every run-lifecycle row: "<where>: <what>. <detail>".
# The office reads this feed by scanning it, and four new event types arriving
# in four different shapes is how a feed stops being scannable.
_LIFECYCLE_HEADLINE = {
    "run-started": "run started.",
    "run-completed": "run completed.",
    "closure-refused": "the driver could not close the run.",
    "force-closed": "the office force-closed the run.",
    "handover-recorded": "a child left the bus away from their stop.",
    "action-reversed": "the driver corrected their own entry.",
}


class IncidentDao:
    def list_incidents(self) -> list[dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                "select * from live_incidents order by created_at desc"
            ).fetchall()
        return [dict(r) for r in rows]

    # Run-lifecycle rows share this table but are not incidents (U16): they are
    # the office's normal operational feed, roughly four per bus per day. They
    # are excluded from both counters — otherwise the Dashboard's "Incidents
    # Today" tile turns red on an ordinary day and the acknowledgement badge
    # fills with items nobody needs to acknowledge, which is how a counter stops
    # meaning anything.
    _NOT_LIFECYCLE = "lifecycle = false"

    def unacknowledged_count(self) -> int:
        with get_connection() as conn:
            row = conn.execute(
                f"select count(*) as n from live_incidents "
                f"where acknowledged = false and {self._NOT_LIFECYCLE}"
            ).fetchone()
        return row["n"]

    def today_count(self) -> int:
        with get_connection() as conn:
            row = conn.execute(
                f"""
                select count(*) as n from live_incidents
                where created_at >= (now() at time zone 'Africa/Nairobi')::date
                  and {self._NOT_LIFECYCLE}
                """
            ).fetchone()
        return row["n"]

    def create_lifecycle_incident(
        self,
        run_id: str,
        incident_type: str,
        detail: str | None = None,
        *,
        dedup: bool = False,
    ) -> dict[str, Any] | None:
        """Record a run-lifecycle event on the office feed.

        Dispatched DAO-direct from a local wrapper in the router, never through
        push_service.notify_incident — the same rule the driver-absent and
        cancellation alerts already follow, because that path fans out bus-wide
        to parents.

        Resolves bus, driver and route names itself: live_runs carries only ids,
        and every caller would otherwise repeat the joins. The description names
        bus, route and period so the office can read the feed without opening
        anything.

        Written pre-acknowledged as well as lifecycle-marked. The marker keeps
        these out of the counters; the acknowledgement keeps the Alerts page
        from offering an "Acknowledge" button on an event that asks nothing of
        anyone.

        ``detail`` carries what the event itself is about — the blocking
        children, the driver's note, the outcome retracted (U11). Without it a
        completed run could mean every drop-off confirmed or a driver
        self-attesting a hand-over, and the office cannot tell which.

        ``dedup`` suppresses a repeat carrying the identical description. Used
        by the refusal alert, where the description encodes the blocking set:
        the driver tapping End four times against the same unresolved children
        is one situation, while a run still stuck after partial progress is a
        new one the office has not been told about. Keying on the run alone
        would report it once and then go quiet exactly as it got worse.
        """
        with get_connection() as conn:
            run = conn.execute(
                """
                select r.id, r.driver_id, r.bus_id, r.type,
                       b.name as bus_name, b.driver_name, rt.name as route_name
                from live_runs r
                left join live_buses b on b.id = r.bus_id
                left join live_routes rt on rt.id = r.route_id
                where r.id = %s
                """,
                (run_id,),
            ).fetchone()
            if not run:
                return None
            period = "morning" if run["type"] == "morning" else "afternoon"
            where = f"{run['route_name'] or 'Route'} ({period}) — {run['bus_name'] or 'bus'}"
            headline = _LIFECYCLE_HEADLINE.get(incident_type, incident_type)
            description = f"{where}: {headline}"
            if detail:
                description = f"{description} {detail}"
            if dedup:
                existing = conn.execute(
                    """
                    select 1 from live_incidents
                    where run_id = %s and type = %s and description = %s
                    limit 1
                    """,
                    (run["id"], incident_type, description),
                ).fetchone()
                if existing:
                    return None
            row = conn.execute(
                """
                insert into live_incidents
                    (run_id, driver_id, driver_name, bus_id, bus_name, type, description,
                     run_type, lifecycle, acknowledged)
                values (%s, %s, %s, %s, %s, %s, %s, %s, true, true)
                returning *
                """,
                (run["id"], run["driver_id"], run["driver_name"], run["bus_id"],
                 run["bus_name"], incident_type, description, run["type"]),
            ).fetchone()
        return dict(row) if row else None

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

    def create_cancellation_incident(
        self,
        student_id: str,
        description: str,
        bus_id: str | None,
        bus_name: str | None,
        run_type: str | None,
    ) -> dict[str, Any]:
        """Insert a parent Cancel-a-Ride alert for the admin Alerts page (U5).

        Student-stamped like the driver-absent incident: these rows surface
        only on the admin Alerts page (ParentLiveDao.list_alerts excludes
        student_id rows) and callers must never hand them to
        push_service.notify_incident — its bus-wide fan-out would tell every
        family on the bus about a named child; the household's channel is
        notify_ride_cancelled. Not create_driver_incident either: that path
        keys its bus lookup on driver_id, which would stamp NULL bus context
        — or the acting parent's name in the driver slot. Here the caller
        passes the covered route's bus and the driver columns stay NULL; the
        acting parent is named only in the description. run_id stays NULL
        (the cancellation precedes any run); run_type carries the period,
        scope-mapped (whole-day → NULL).
        """
        with get_connection() as conn:
            row = conn.execute(
                """
                insert into live_incidents
                    (driver_id, driver_name, bus_id, bus_name, type, description,
                     run_id, run_type, student_id)
                values (null, null, %s, %s, 'cancellation', %s, null, %s, %s)
                returning *
                """,
                (bus_id, bus_name, description, run_type, student_id),
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
