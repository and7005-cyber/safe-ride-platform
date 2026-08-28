from app.core.db import UNSET, get_connection, get_global_connection
from app.dao.status_sql import display_status_case

# Every parent-recipient query below filters to ACCEPTED links on ENABLED
# accounts (U7/R31/R13): a pending cross-school link is not consent to be
# messaged, and a disabled account must go silent everywhere at once. The
# predicates live in one string so no recipient query can drift.
_RECIPIENT_PREDICATES = "ps.status = 'accepted' and u.disabled_at is null"


class PushDao:
    # Push tokens and the notification feed are user-owned, not school-owned
    # (the U5 seam's "non-school tables"): their methods use the deliberately
    # scope-free global connection, so a scoped request's background task can
    # deliver to a cross-school parent without borrowing any school GUC.

    def subscribe(self, user_id: str, endpoint: str, p256dh: str | None, auth: str | None, user_agent: str | None) -> None:
        with get_global_connection() as conn:
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
        with get_global_connection() as conn:
            conn.execute(
                "delete from live_push_subscriptions where endpoint = %s and user_id = %s",
                (endpoint, user_id),
            )

    def register_fcm_token(self, user_id: str, token: str, user_agent: str | None) -> None:
        with get_global_connection() as conn:
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
        with get_global_connection() as conn:
            conn.execute(
                "delete from live_fcm_tokens where token = %s and user_id = %s",
                (token, user_id),
            )

    def delete_fcm_token(self, token: str) -> None:
        """Drop a token FCM reported as dead, whoever owns it."""
        with get_global_connection() as conn:
            conn.execute("delete from live_fcm_tokens where token = %s", (token,))

    def fcm_tokens_for_users(self, user_ids: list[str]) -> list[dict]:
        if not user_ids:
            return []
        with get_global_connection() as conn:
            rows = conn.execute(
                "select user_id, token from live_fcm_tokens where user_id = any(%s)",
                (user_ids,),
            ).fetchall()
        return [dict(row) for row in rows]

    def web_push_subscriptions_for_users(self, user_ids: list[str]) -> list[dict]:
        if not user_ids:
            return []
        with get_global_connection() as conn:
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
        with get_global_connection() as conn:
            conn.execute(
                "delete from live_push_subscriptions where endpoint = %s", (endpoint,)
            )

    # School-owned reads (U7): every method below takes an explicit scope and
    # opens its connection through it. The UNSET default keeps unconverted
    # callers on the context-var fallback, where the strict flag turns a
    # missed thread inside a SchoolScope request into a loud error.

    def active_run_for_driver(self, scope) -> dict | None:
        """Today's active run for the calling driver's bus, if any.

        Mirrors RunDao.find_active_run_today: anything not completed counts
        (an admin marking a run 'delayed' must not mute its notifications).
        The bus resolves through the driver's OWN school (U7): the scope IS
        the driver — user and school together."""
        with get_connection(scope) as conn:
            row = conn.execute(
                """
                select r.* from live_runs r
                join live_buses b on b.id = r.bus_id
                where b.driver_id = %s and b.school_id = %s
                  and r.date = (now() at time zone 'Africa/Nairobi')::date
                  and r.status <> 'completed'
                order by r.created_at desc
                limit 1
                """,
                (scope.user_id, scope.school_id),
            ).fetchone()
        return dict(row) if row else None

    def bus_name(self, bus_id: str, scope: object = UNSET) -> str | None:
        with get_connection(scope) as conn:
            row = conn.execute("select name from live_buses where id = %s", (bus_id,)).fetchone()
        return row["name"] if row else None

    def parents_of_students(self, student_ids: list[str], scope: object = UNSET) -> list[dict]:
        """Resolve (parent_id, student_id, student_name) pairs for students —
        accepted links on enabled accounts only (U7)."""
        if not student_ids:
            return []
        with get_connection(scope) as conn:
            rows = conn.execute(
                f"""
                select ps.parent_id, ps.student_id, s.name as student_name
                from live_parent_students ps
                join live_students s on s.id = ps.student_id
                join app_users u on u.id = ps.parent_id
                where ps.student_id = any(%s) and {_RECIPIENT_PREDICATES}
                """,
                (student_ids,),
            ).fetchall()
        return [dict(row) for row in rows]

    def parents_of_bus(self, bus_id: str, scope: object = UNSET) -> list[dict]:
        """Resolve (parent_id, student_id, student_name) pairs for a bus —
        accepted links on enabled accounts only (U7)."""
        with get_connection(scope) as conn:
            rows = conn.execute(
                f"""
                select ps.parent_id, ps.student_id, s.name as student_name
                from live_parent_students ps
                join live_students s on s.id = ps.student_id
                join app_users u on u.id = ps.parent_id
                where s.bus_id = %s and {_RECIPIENT_PREDICATES}
                """,
                (bus_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def route_broadcast_context(self, scope, route_id: str) -> dict | None:
        """The route row plus its assigned-student count, for the broadcast
        endpoint's guards (U8). None = unknown route — which since U7 includes
        another school's route (404); a zero count backs the no-students 409 —
        a broadcast that can reach nobody must fail loudly, never answer 200
        with nothing sent."""
        with get_connection(scope) as conn:
            route = conn.execute(
                "select id, name, type, bus_id, school_id from live_routes "
                "where id = %s and school_id = %s",
                (route_id, scope.school_id),
            ).fetchone()
            if not route:
                return None
            count = conn.execute(
                "select count(*) as n from live_student_routes where route_id = %s",
                (route_id,),
            ).fetchone()
        return {"route": dict(route), "student_count": int(count["n"])}

    def parents_of_route(self, scope, route_id: str) -> list[str]:
        """DISTINCT parent account ids for the students ASSIGNED to a route
        (U8, R20/R21) — accepted links on enabled accounts only (U7).

        Assignment truth only: live_student_routes → live_parent_students.
        Never students.bus_id — bus membership drifts from route assignment
        (a student can ride the bus on the other route only, or keep a stale
        bus after reassignment), and R20 scopes the broadcast to the route's
        assigned students. Distinct at the SQL level: a parent with several
        children on the route is one recipient (AE5)."""
        with get_connection(scope) as conn:
            rows = conn.execute(
                f"""
                select distinct ps.parent_id
                from live_student_routes sr
                join live_parent_students ps on ps.student_id = sr.student_id
                join app_users u on u.id = ps.parent_id
                where sr.route_id = %s and {_RECIPIENT_PREDICATES}
                """,
                (route_id,),
            ).fetchall()
        return [str(row["parent_id"]) for row in rows]

    def routes_of_students(self, student_ids: list[str], scope: object = UNSET) -> list[str]:
        """DISTINCT route ids the given students are currently linked to — the
        pre-mutation capture for U13's manual-edit fan-out: a student update or
        delete rewrites/cascades the very links the post-commit fan-out would
        otherwise expand through, so the caller snapshots them first."""
        if not student_ids:
            return []
        with get_connection(scope) as conn:
            rows = conn.execute(
                "select distinct route_id from live_student_routes "
                "where student_id = any(%s::uuid[])",
                ([str(s) for s in student_ids],),
            ).fetchall()
        return sorted(str(row["route_id"]) for row in rows)

    def students_of_routes(self, route_ids: list[str], scope: object = UNSET) -> list[str]:
        """DISTINCT student ids linked to the given routes — captured BEFORE a
        route deletion so U13's fan-out can still diff the members the cascade
        is about to unlink (their baselines then read as removed)."""
        if not route_ids:
            return []
        with get_connection(scope) as conn:
            rows = conn.execute(
                "select distinct student_id from live_student_routes "
                "where route_id = any(%s::uuid[])",
                ([str(r) for r in route_ids],),
            ).fetchall()
        return sorted(str(row["student_id"]) for row in rows)

    def students_on_run(self, run_id: str, include_absent: bool = False, scope: object = UNSET) -> list[dict]:
        """Students with a seat on the run's stop roster.

        Recipients are filtered on the derived status (U3), not the raw column.
        The column no longer answers "should this family hear from us": a child
        the office recorded as unaccounted still reads 'on-bus' there, so a
        column-based filter kept sending them run notifications — contradicting
        the rule that their parents hear nothing until the office calls.
        """
        with get_connection(scope) as conn:
            rows = conn.execute(
                f"""
                select distinct s.id, s.name, {display_status_case("s")} as display_status
                from run_stops rs
                join live_students s on s.id = rs.student_id
                where rs.run_id = %s
                """,
                (run_id,),
            ).fetchall()
        students = [dict(row) for row in rows]
        if include_absent:
            return students
        silent = {"absent", "unaccounted"}
        return [s for s in students if s["display_status"] not in silent]

    def students_at_stop(self, run_id: str, stop_order: int, scope: object = UNSET) -> list[dict]:
        """Students whose stop sits at the given order on the run (coordinates
        not required — 'approaching' is stop-order based, not GPS based)."""
        with get_connection(scope) as conn:
            rows = conn.execute(
                """
                select rs.student_id, s.name as student_name,
                       {display_status} as student_status
                from run_stops rs
                join live_students s on s.id = rs.student_id
                where rs.run_id = %s and rs.stop_order = %s
                  and rs.is_school_gate = false and rs.student_id is not null
                """.format(display_status=display_status_case("s")),
                (run_id, stop_order),
            ).fetchall()
        return [dict(row) for row in rows]

    def remaining_student_stops(self, run_id: str, stops_completed: int, scope: object = UNSET) -> list[dict]:
        """Upcoming (not yet reached) student stops for a run, with coordinates."""
        with get_connection(scope) as conn:
            rows = conn.execute(
                """
                select rs.stop_order, rs.lat, rs.lng, rs.student_id, s.name as student_name,
                       {display_status} as student_status
                from run_stops rs
                join live_students s on s.id = rs.student_id
                where rs.run_id = %s
                  and rs.stop_order > %s
                  and rs.is_school_gate = false
                  and rs.student_id is not null
                  and rs.lat is not null
                  and rs.lng is not null
                """.format(display_status=display_status_case("s")),
                (run_id, stops_completed),
            ).fetchall()
        return [dict(row) for row in rows]

    def retract_notifications(self, run_id: str, student_id: str, types: list[str], scope: object = UNSET) -> int:
        """Remove notifications superseded by a driver correction (U5).

        The dedup index is unique on (user, run, student, type), which is what
        makes a retried tap harmless. It also means a re-confirmation after a
        reversal would be silently suppressed as a duplicate — the family would
        keep the false message and never receive the true one.

        Deleting the superseded rows fixes both halves: the parent feed stops
        showing a claim the driver retracted, and the corrected outcome can be
        delivered when it happens. The correction notification itself tells the
        family what changed, so nothing disappears unexplained.
        """
        with get_connection(scope) as conn:
            rows = conn.execute(
                """
                delete from live_notifications
                where run_id = %s and student_id = %s and type = any(%s)
                returning id
                """,
                (run_id, student_id, types),
            ).fetchall()
        return len(rows)

    def insert_notification(
        self,
        user_id: str,
        type: str,
        title: str,
        body: str,
        student_id: str | None = None,
        run_id: str | None = None,
        bus_id: str | None = None,
        run_type: str | None = None,
        school_id: str | None = None,
        scope: object = UNSET,
    ) -> dict | None:
        """Insert a feed row. Returns None when the run-scoped dedup suppressed it.

        run_type persists the run's period ('morning'/'afternoon') on the row
        itself — run_id is ON DELETE SET NULL, so a join would silently lose
        the period once an admin deletes the run. school_id (U7) stamps the
        originating school where the caller can derive one (run/route/plan
        context); a purely account-level notice stays NULL.
        """
        with get_connection(scope) as conn:
            row = conn.execute(
                """
                insert into live_notifications
                    (user_id, student_id, run_id, bus_id, type, title, body, run_type, school_id)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                on conflict do nothing
                returning id, user_id, student_id, run_id, bus_id, type, title, body,
                          run_type, school_id, read, created_at
                """,
                (user_id, student_id, run_id, bus_id, type, title, body, run_type, school_id),
            ).fetchone()
        return dict(row) if row else None

    def insert_plan_notification(
        self,
        conn,
        user_id: str,
        *,
        type: str,
        title: str,
        body: str,
        student_id: str | None,
        bus_id: str | None,
        run_type: str | None,
        plan_audit_id: str | None,
        school_id: str | None = None,
    ) -> dict | None:
        """Feed-row insert on the CALLER's connection (fleet-plan apply, U6).

        ``insert_notification`` opens its own connection per row, which would
        COMMIT feed rows independently of the apply transaction — a gate
        failure after the fan-out would roll the apply back while the feed
        kept claiming a change that never happened. Threading the apply's
        connection makes the feed rows part of the same atom.

        run_id stays NULL (no run is involved); ``plan_audit_id`` ties the
        row to the apply/restore act that produced it and drives the 011 plan
        dedup arbiter — ``on conflict do nothing`` returns None for a repeat
        (parent, student, type) within one act, mirroring the run-scoped
        dedup's contract. ``school_id`` (U7) stamps the plan's school.
        Returns the inserted row, or None when suppressed.
        """
        row = conn.execute(
            """
            insert into live_notifications
                (user_id, student_id, bus_id, type, title, body, run_type,
                 plan_audit_id, school_id)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict do nothing
            returning id, user_id, student_id, bus_id, type, title, body, run_type,
                      plan_audit_id, school_id, read, created_at
            """,
            (user_id, student_id, bus_id, type, title, body, run_type,
             plan_audit_id, school_id),
        ).fetchone()
        return dict(row) if row else None

    def list_notifications(
        self,
        user_id: str,
        limit: int = 50,
        window_hours: int | None = None,
        min_age_hours: int | None = None,
    ) -> list[dict]:
        """The user's feed, newest first — user-scoped (U7: any role, no
        school header), so it reads through the global connection like every
        per-account surface.

        window_hours, when set, keeps only rows newer than that rolling
        window; min_age_hours keeps only rows at least that old. The parent
        app's Recent view is window 24; its History tab is min_age 24 +
        window 168 — disjoint by construction (U9: R5–R7), and because both
        are WHERE predicates the exclusion happens BEFORE the row cap, so a
        busy last 24h can never starve History out of the 200 rows.
        limit is hard-capped at 200 — the feed is windowed, never paginated,
        and no rows are ever deleted (R36: the table is the audit trail).
        """
        limit = max(1, min(int(limit), 200))
        window_sql = ""
        params: list = [user_id]
        if window_hours is not None:
            window_sql += " and created_at > now() - (%s || ' hours')::interval"
            params.append(int(window_hours))
        if min_age_hours is not None:
            window_sql += " and created_at <= now() - (%s || ' hours')::interval"
            params.append(int(min_age_hours))
        params.append(limit)
        with get_global_connection() as conn:
            rows = conn.execute(
                f"""
                select id, student_id, run_id, bus_id, type, run_type, title, body, read, created_at
                from live_notifications
                where user_id = %s{window_sql}
                order by created_at desc
                limit %s
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_notifications_read(self, user_id: str) -> None:
        # Intentionally global, never window-scoped (R35): opening the feed
        # clears the unread badge for every row, History-tab rows included.
        with get_global_connection() as conn:
            conn.execute(
                "update live_notifications set read = true where user_id = %s and read = false",
                (user_id,),
            )

    def unread_count(self, user_id: str) -> int:
        with get_global_connection() as conn:
            row = conn.execute(
                "select count(*) as count from live_notifications where user_id = %s and read = false",
                (user_id,),
            ).fetchone()
        return int(row["count"]) if row else 0
