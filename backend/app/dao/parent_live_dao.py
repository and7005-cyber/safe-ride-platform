from typing import Any

from app.core.db import get_connection
from app.core.scope import ParentScope
from app.dao.audit_dao import record_audit
from app.dao.status_sql import display_status_case


def _mask_stop_name(name: str, is_own: bool, is_gate: bool) -> str:
    """Privacy: strip leading house-number digits for stops that aren't the
    family's own or the school gate (matches live /parent/track)."""
    if is_own or is_gate or not name:
        return name
    stripped = name.lstrip("0123456789 ").strip()
    return stripped or "Stop"


class ParentLiveDao:
    """Every method takes the request's ``ParentScope`` and threads it into
    ``get_connection`` explicitly (U11) — the same seam the school surfaces
    adopted in U7, so U14's row security can rely on the armed GUC here too.
    The parent's identity is ``scope.user_id``; reads key on ACCEPTED links
    only (``_child_ids`` is the single choke point), while the pending
    surface below is the one place a not-yet-accepted school appears — as a
    school-level card, never as child data."""

    def _child_ids(self, conn, parent_id: str) -> list[str]:
        # Accepted links only (U11/R31): a pending link grants NO read of the
        # child — every parent read funnels through this filter.
        rows = conn.execute(
            "select student_id from live_parent_students "
            "where parent_id = %s and status = 'accepted'",
            (parent_id,),
        ).fetchall()
        return [r["student_id"] for r in rows]

    def list_children(self, scope: ParentScope) -> list[dict[str, Any]]:
        """The parent's children, each with a derived ``display_status``.

        ``display_status`` is computed at read time, never stored (the raw
        ``status`` field stays untouched in the payload — admin keeps the
        operational value). The branch-by-branch derivation lives in
        ``app.dao.status_sql``, shared with the admin students list.

        Each row also carries ``cancellation`` (U5): today's parent-sourced
        absence as ``{scope, withdrawable}``, else None (a staff-sourced
        absence is not a cancellation — it surfaces through display_status
        when whole-day). ``withdrawable`` mirrors the withdraw guard: some
        covered run type still has NO run row today involving the child
        (run-row existence, not the active-run predicate — completion must
        not reopen withdrawal). For a merged 'day' row that means at least
        one half is still withdrawable, which is exactly when the UI should
        offer the action (U13's dialog picks the half).
        """
        with get_connection(scope) as conn:
            ids = self._child_ids(conn, scope.user_id)
            if not ids:
                return []
            rows = conn.execute(
                f"""
                select s.*, b.name as bus_name, b.driver_name, b.driver_phone,
                       b.current_lat as bus_current_lat, b.current_lng as bus_current_lng,
                       sc.name as school_name,
                       {display_status_case("s")} as display_status,
                       a.scope as cancel_scope, a.source as cancel_source,
                       case when a.source = 'parent' then exists (
                           select 1
                           from (values ('morning'), ('afternoon')) as covered(run_type)
                           where (a.scope = 'day' or a.scope = covered.run_type)
                             and not exists (
                                 select 1 from live_runs r
                                 where r.date = (now() at time zone 'Africa/Nairobi')::date
                                   and r.type = covered.run_type
                                   and (
                                       exists (select 1 from run_stops rs
                                               where rs.run_id = r.id
                                                 and rs.student_id = s.id)
                                       or exists (select 1 from live_student_routes sr
                                                  where sr.route_id = r.route_id
                                                    and sr.student_id = s.id)
                                   )
                             )
                       ) end as cancel_withdrawable
                from live_students s
                left join live_buses b on b.id = s.bus_id
                left join live_schools sc on sc.id = s.school_id
                left join live_student_absences a
                    on a.student_id = s.id
                   and a.absence_date = (now() at time zone 'Africa/Nairobi')::date
                where s.id = any(%s)
                order by s.name asc
                """,
                (ids,),
            ).fetchall()
        children = []
        for r in rows:
            child = dict(r)
            scope = child.pop("cancel_scope")
            source = child.pop("cancel_source")
            withdrawable = child.pop("cancel_withdrawable")
            child["cancellation"] = (
                {"scope": scope, "withdrawable": bool(withdrawable)}
                if source == "parent"
                else None
            )
            children.append(child)
        return children

    def cancel_ride_context(self, scope: ParentScope, student_id: str) -> dict[str, Any] | None:
        """Ownership check + guard snapshot for Cancel-a-Ride (U5), one read.

        None means the student is not linked to this parent — the 404
        boundary, matching get_track. Ownership is the SOLE boundary and it
        evaluates before any guard: student UUIDs are harvestable by any
        authenticated token, and a guard's 409 fired first would leak another
        child's live on-bus state. Otherwise returns:

          student      {id, name, status, bus_id}
          absence      today's absence row {scope, source, reason,
                       marked_period} or None
          runs         today's run rows involving the child, each
                       {type, status} — involvement is run_stops membership
                       OR the run's route being one of the child's routes
                       (the child may have been excluded from run_stops at
                       snapshot time by this very absence)
          route_buses  {'morning'|'afternoon': {bus_id, bus_name}} from the
                       child's route assignments (first route per type)

        Guards evaluated over this snapshot are best-effort pre-reads for
        friendly messages; the atomic set_scope / withdraw_scope statements
        stay the authority under concurrency.
        """
        with get_connection(scope) as conn:
            ids = [str(cid) for cid in self._child_ids(conn, scope.user_id)]
            if str(student_id) not in ids:
                return None  # ownership: not this parent's child
            # display_status, not the raw column (U3): the Cancel-a-Ride guard
            # keys on whether the child is confirmed aboard, and an afternoon
            # roster is presumed aboard from run start.
            student = conn.execute(
                f"select s.id, s.name, s.status, s.bus_id, "
                f"{display_status_case('s')} as display_status "
                f"from live_students s where s.id = %s",
                (student_id,),
            ).fetchone()
            if not student:
                return None  # link row outlived the student: same 404
            absence = conn.execute(
                """
                select scope, source, reason, marked_period from live_student_absences
                where student_id = %s
                  and absence_date = (now() at time zone 'Africa/Nairobi')::date
                """,
                (student_id,),
            ).fetchone()
            runs = conn.execute(
                """
                select r.type, r.status from live_runs r
                where r.date = (now() at time zone 'Africa/Nairobi')::date
                  and (
                      exists (select 1 from run_stops rs
                              where rs.run_id = r.id and rs.student_id = %s)
                      or exists (select 1 from live_student_routes sr
                                 where sr.route_id = r.route_id and sr.student_id = %s)
                  )
                """,
                (student_id, student_id),
            ).fetchall()
            route_rows = conn.execute(
                """
                select r.type, b.id as bus_id, b.name as bus_name
                from live_student_routes sr
                join live_routes r on r.id = sr.route_id
                left join live_buses b on b.id = r.bus_id
                where sr.student_id = %s
                order by r.created_at asc
                """,
                (student_id,),
            ).fetchall()
        route_buses: dict[str, dict[str, Any]] = {}
        for r in route_rows:
            route_buses.setdefault(
                r["type"], {"bus_id": r["bus_id"], "bus_name": r["bus_name"]}
            )
        return {
            "student": dict(student),
            "absence": dict(absence) if absence else None,
            "runs": [dict(r) for r in runs],
            "route_buses": route_buses,
        }

    def get_track(self, scope: ParentScope, student_id: str) -> dict[str, Any] | None:
        with get_connection(scope) as conn:
            ids = [str(cid) for cid in self._child_ids(conn, scope.user_id)]
            if str(student_id) not in ids:
                return None  # ownership: not this parent's child
            student = conn.execute(
                """
                select s.*, b.name as bus_name, b.driver_name, b.driver_phone,
                       b.current_lat as bus_current_lat, b.current_lng as bus_current_lng
                from live_students s left join live_buses b on b.id = s.bus_id
                where s.id = %s
                """,
                (student_id,),
            ).fetchone()
            if not student:
                return None
            # The student's current (morning) route + its stops.
            route = conn.execute(
                """
                select r.* from live_routes r
                join live_student_routes sr on sr.route_id = r.id
                where sr.student_id = %s and r.bus_id = %s
                order by (r.type <> 'morning') asc, r.type asc limit 1
                """,
                (student_id, student["bus_id"]),
            ).fetchone()
            stops = []
            if route:
                raw = conn.execute(
                    "select * from live_route_stops where route_id = %s order by stop_order asc, name asc",
                    (route["id"],),
                ).fetchall()
                # Siblings can share a stop_order (one row per student): prefer
                # the requesting student's own row so is_own survives the dedup.
                raw = sorted(
                    raw,
                    key=lambda r: (
                        r["stop_order"],
                        str(r["student_id"]) != str(student_id),
                        r["name"] or "",
                    ),
                )
                seen_orders = set()
                for s in raw:
                    if s["stop_order"] in seen_orders:
                        continue
                    seen_orders.add(s["stop_order"])
                    is_own = str(s["student_id"]) == str(student_id)
                    stops.append({
                        "stop_order": s["stop_order"],
                        "name": _mask_stop_name(s["name"], is_own, s["is_school_gate"]),
                        "is_school_gate": s["is_school_gate"],
                        "is_own": is_own,
                        "lat": s["lat"],
                        "lng": s["lng"],
                    })
            run = None
            if student["bus_id"]:
                r = conn.execute(
                    """
                    select * from live_runs
                    where bus_id = %s and date = (now() at time zone 'Africa/Nairobi')::date
                    order by created_at desc limit 1
                    """,
                    (student["bus_id"],),
                ).fetchone()
                run = dict(r) if r else None
        return {"student": dict(student), "stops": stops, "run": run}

    def list_alerts(
        self,
        scope: ParentScope,
        window_hours: int | None = None,
        min_age_hours: int | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Incidents on the children's buses, newest first.

        window_hours keeps only rows newer than that rolling window;
        min_age_hours keeps only rows at least that old (U9: R5–R7 — Recent =
        window 24, History = min_age 24 + window 168, disjoint). Both are
        WHERE predicates, so the exclusion applies before the row cap and a
        busy last 24h cannot starve History. limit is hard-capped at 200
        (R35/R36: display windows over a feed that never deletes rows).
        """
        limit = max(1, min(int(limit), 200))
        with get_connection(scope) as conn:
            ids = self._child_ids(conn, scope.user_id)
            if not ids:
                return []
            bus_rows = conn.execute(
                "select distinct bus_id from live_students where id = any(%s) and bus_id is not null",
                (ids,),
            ).fetchall()
            bus_ids = [r["bus_id"] for r in bus_rows]
            if not bus_ids:
                return []
            window_sql = ""
            params: list = [bus_ids]
            if window_hours is not None:
                window_sql += " and created_at > now() - (%s || ' hours')::interval"
                params.append(int(window_hours))
            if min_age_hours is not None:
                window_sql += " and created_at <= now() - (%s || ' hours')::interval"
                params.append(int(min_age_hours))
            params.append(limit)
            # Two exclusions, for different reasons.
            #
            # student_id-stamped incidents are child-specific (absence reports
            # for the school): only the admin Alerts page may see them — no
            # other parent on the bus learns a named child's absence here.
            #
            # lifecycle rows are the office's operational feed (U16): run
            # started, run completed, and later the closure events. Parents
            # already receive their own messages for anything that concerns
            # them, so these would arrive as duplicate, vaguer news — and the
            # refused-closure and force-close rows name blocking children to an
            # audience that must not see them. Note this predicate also finally
            # excludes the pre-existing 'arrival' rows, which have reached the
            # parent feed all along because the only filter was student_id.
            rows = conn.execute(
                f"""
                select id, driver_name, bus_id, bus_name, type, run_type, description, created_at
                from live_incidents
                where bus_id = any(%s) and student_id is null
                  and lifecycle = false and type <> 'arrival'{window_sql}
                order by created_at desc
                limit %s
                """,
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    def get_profile(self, scope: ParentScope) -> dict[str, Any]:
        with get_connection(scope) as conn:
            user = conn.execute(
                "select id, email, full_name, phone from app_users where id = %s",
                (scope.user_id,),
            ).fetchone()
        children = self.list_children(scope)
        return {"profile": dict(user) if user else None, "children": children}

    # --- pending links (U11: R31/R32, AE10/AE21/AE22) -------------------------

    def list_pending(self, scope: ParentScope) -> list[dict[str, Any]]:
        """The calling parent's pending links, GROUPED BY SCHOOL — one card
        per school: schoolId, schoolName, offeredAt (earliest), linkCount.

        Deliberately NO student fields of any kind (AE22): until the parent
        accepts, the school's offer discloses only that the school claims a
        link — a child's name, grade or id in this payload would leak data
        the parent has not confirmed a right to.
        """
        with get_connection(scope) as conn:
            rows = conn.execute(
                """
                select ps.school_id, sc.name as school_name,
                       min(ps.offered_at) as offered_at, count(*) as link_count
                from live_parent_students ps
                join live_schools sc on sc.id = ps.school_id
                where ps.parent_id = %s and ps.status = 'pending'
                group by ps.school_id, sc.name
                order by min(ps.offered_at) asc nulls last, sc.name asc
                """,
                (scope.user_id,),
            ).fetchall()
        return [
            {
                "schoolId": str(r["school_id"]),
                "schoolName": r["school_name"],
                "offeredAt": r["offered_at"],
                "linkCount": r["link_count"],
            }
            for r in rows
        ]

    def has_pending_at(self, scope: ParentScope, school_id: str) -> bool:
        """Gate for accept/decline: the route verifies the parent really has a
        pending link at ``school_id`` (under their ordinary accepted-school
        scope) BEFORE widening the scope to include that school — a caller
        must not get a widened GUC out of a school id they merely guessed."""
        with get_connection(scope) as conn:
            row = conn.execute(
                "select 1 from live_parent_students "
                "where parent_id = %s and school_id = %s and status = 'pending' limit 1",
                (scope.user_id, school_id),
            ).fetchone()
        return bool(row)

    def accept_pending(self, scope: ParentScope, school_id: str) -> int:
        """Activate EVERY pending link of this parent at the school. ``scope``
        is the widened ParentScope (accepted schools ∪ the pending school) so
        the write passes the database backstop. Returns links activated."""
        with get_connection(scope) as conn:
            rows = conn.execute(
                """
                update live_parent_students
                set status = 'accepted', decided_at = now()
                where parent_id = %s and school_id = %s and status = 'pending'
                returning id
                """,
                (scope.user_id, school_id),
            ).fetchall()
        return len(rows)

    def decline_pending(
        self, scope: ParentScope, school_id: str, *, actor: dict, email: str
    ) -> int:
        """Decline a school's pending card (AE22), one transaction: DELETE the
        parent's pending links there, blank whichever email slot on each
        affected student equals the caller's email (case-insensitive), raise
        ONE school-stamped 'mismatched email' incident naming the EMAIL —
        never the child (the school knows its own students; the alert is
        about the address) — and record the parent-link-declined audit row,
        whose detail carries the link count and deliberately no email.

        ``scope`` is the widened ParentScope including this school. Returns
        the number of links removed (0 when nothing was pending — the caller
        keeps its 404 contract).
        """
        with get_connection(scope) as conn:
            rows = conn.execute(
                """
                delete from live_parent_students
                where parent_id = %s and school_id = %s and status = 'pending'
                returning student_id
                """,
                (scope.user_id, school_id),
            ).fetchall()
            if not rows:
                return 0
            student_ids = [r["student_id"] for r in rows]
            conn.execute(
                """
                update live_students set
                    parent_email = case
                        when lower(parent_email) = lower(%(email)s) then null
                        else parent_email end,
                    parent2_email = case
                        when lower(parent2_email) = lower(%(email)s) then null
                        else parent2_email end
                where id = any(%(ids)s) and school_id = %(school_id)s
                """,
                {"email": email, "ids": student_ids, "school_id": school_id},
            )
            # The signup auto-link incident's shape (type 'other', no student
            # or bus stamp — it never reaches a parent feed), school-stamped.
            conn.execute(
                "insert into live_incidents (type, description, school_id) "
                "values ('other', %s, %s)",
                (
                    f"Mismatched email: {email} declined the parent link for "
                    "this school — the parent email on the student record may "
                    "be wrong. Please verify the family's contact details.",
                    school_id,
                ),
            )
            record_audit(
                conn, action="parent-link-declined", actor=actor,
                school_id=school_id, resource_type="parent",
                resource_id=str(scope.user_id),
                detail={"link_count": len(student_ids)},
            )
        return len(student_ids)
