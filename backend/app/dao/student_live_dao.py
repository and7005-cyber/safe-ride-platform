from typing import Any

from app.core.db import get_connection
from app.core.errors import ConflictError
from app.dao.fleet_dao import regenerate_route_stops
from app.dao.status_sql import display_status_case

STUDENT_COLUMNS = (
    "name", "grade", "parent_name", "parent_phone", "parent_phone2", "parent_email",
    "parent2_name", "parent2_email",
    "home_address", "home_lat", "home_lng", "pickup_time", "status", "bus_id", "school_id",
)

MAX_PARENT_LINKS = 2  # a student never has more than two linked parent accounts (R11)


class _ConnParentLinks:
    """Data access needed by the parent-link sync rules, over a live connection.

    ``sync_parent_links`` / ``link_account_to_matching_students`` take any
    object with this interface, so the linking rules unit-test against an
    in-memory fake (tests/services/test_parent_links.py) without a database.
    """

    def __init__(self, conn) -> None:
        self._conn = conn

    def parent_account_id(self, email: str) -> Any | None:
        row = self._conn.execute(
            "select u.id from app_users u join app_user_roles r on r.user_id = u.id "
            "where lower(u.email) = lower(%s) and r.role = 'parent'",
            (email,),
        ).fetchone()
        return row["id"] if row else None

    def student_links(self, student_id) -> list[dict]:
        rows = self._conn.execute(
            "select ps.id, ps.parent_id, lower(u.email) as email "
            "from live_parent_students ps join app_users u on u.id = ps.parent_id "
            "where ps.student_id = %s",
            (student_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def students_with_email(self, email: str) -> list[Any]:
        rows = self._conn.execute(
            "select id from live_students "
            "where lower(parent_email) = lower(%s) or lower(parent2_email) = lower(%s)",
            (email, email),
        ).fetchall()
        return [r["id"] for r in rows]

    def add_link(self, parent_id, student_id) -> None:
        self._conn.execute(
            "insert into live_parent_students (parent_id, student_id) values (%s, %s) "
            "on conflict (parent_id, student_id) do nothing",
            (parent_id, student_id),
        )

    def remove_link(self, link_id) -> None:
        self._conn.execute("delete from live_parent_students where id = %s", (link_id,))


def _slot_emails(emails) -> list[str]:
    """Normalise the (parent_email, parent2_email) pair: lower-cased, blanks
    dropped, order preserved, duplicates collapsed (the same email in both
    slots is one parent — one link)."""
    slots: list[str] = []
    for email in emails:
        value = str(email or "").strip().lower()
        if value and value not in slots:
            slots.append(value)
    return slots


def sync_parent_links(db, student_id, emails, old_emails=None) -> int:
    """Reconcile ``live_parent_students`` with a student's parent email slots (R11).

    ``db`` is a live connection or a ``_ConnParentLinks``-shaped store.
    ``emails`` is the slot-ordered ``(parent_email, parent2_email)`` pair after
    the write; ``old_emails`` the pair before it (``None`` on create). Links
    are upserted for parent-role accounts whose email matches either slot
    case-insensitively. Pruning is per-slot: a link is removed only when its
    account email matched a slot value that was REMOVED in this write — an
    unrelated edit, or an edit to the other slot, must never sever a link,
    and a drifted link (account email renamed after linking, matching no old
    slot) is never pruned. The student never exceeds ``MAX_PARENT_LINKS``;
    when accounts compete for the last seat, slot order wins.

    Returns the number of links created.
    """
    store = _ConnParentLinks(db) if hasattr(db, "execute") else db
    slots = _slot_emails(emails)
    links = store.student_links(student_id)

    if old_emails is not None:
        removed_values = set(_slot_emails(old_emails)) - set(slots)
        for link in list(links):
            if link["email"] in removed_values and link["email"] not in slots:
                store.remove_link(link["id"])
                links.remove(link)

    linked_ids = {link["parent_id"] for link in links}
    created = 0
    for email in slots:
        if len(linked_ids) >= MAX_PARENT_LINKS:
            break
        account_id = store.parent_account_id(email)
        if account_id is not None and account_id not in linked_ids:
            store.add_link(account_id, student_id)
            linked_ids.add(account_id)
            created += 1
    return created


def link_account_to_matching_students(store, parent_id, email: str) -> int:
    """Signup-side of R11: link a fresh parent account to every student whose
    email slots carry its email, honouring the per-student link cap.

    Returns the number of links created.
    """
    created = 0
    for student_id in store.students_with_email(email):
        links = store.student_links(student_id)
        if len(links) < MAX_PARENT_LINKS and parent_id not in {l["parent_id"] for l in links}:
            store.add_link(parent_id, student_id)
            created += 1
    return created


def _derive_student_bus(conn, student_id: str) -> None:
    """Students are assigned to routes, and buses to routes (#3); a student's
    bus is whichever bus runs their route — the morning route wins. Keeps the
    denormalised live_students.bus_id (used for parent tracking and filters)
    consistent instead of letting an admin set it by hand."""
    bus = conn.execute(
        """
        select r.bus_id from live_student_routes sr
        join live_routes r on r.id = sr.route_id
        where sr.student_id = %s and r.bus_id is not null
        order by case when r.type = 'morning' then 0 else 1 end, r.created_at asc
        limit 1
        """,
        (student_id,),
    ).fetchone()
    conn.execute(
        "update live_students set bus_id = %s where id = %s",
        (bus["bus_id"] if bus else None, student_id),
    )


def _sync_routes(conn, student_id: str, route_ids: list[str]) -> bool:
    """Reconcile a student's route links and regenerate the affected routes.

    Returns the aggregate ``stops_recalculated`` signal (U6/R10): False when
    any affected route's regeneration fell back instead of computing geometry;
    True otherwise (including when no route membership changed)."""
    existing = conn.execute(
        "select id, route_id from live_student_routes where student_id = %s", (student_id,)
    ).fetchall()
    # route_id comes back from psycopg as a uuid.UUID, but route_ids arrive as
    # strings from the API. Compare on str so an update never mistakes an
    # existing link for a removed one and deletes it (#5).
    existing_by_route = {str(r["route_id"]): r["id"] for r in existing}
    wanted = {str(rid) for rid in route_ids if rid}

    # Friendly guard (U5, R21/R23): at most one morning + one afternoon route per
    # student. A single payload naming two routes of the same period is rejected
    # up front — the deferrable unique(student_id, route_type) is the race-proof
    # backstop, but a raw violation would surface at commit as a 500.
    if wanted:
        dup = conn.execute(
            "select 1 from live_routes where id::text = any(%s) "
            "group by type having count(*) > 1 limit 1",
            (list(wanted),),
        ).fetchone()
        if dup:
            raise ConflictError(
                "A student can be assigned to at most one morning and one afternoon route."
            )

    affected: set[str] = set()
    added: set[str] = set()
    # Delete-before-insert (U5): the PRIMARY guarantee that a same-period move
    # (drop morning-A, add morning-B in one sync) never transiently holds two
    # links of one period — so the deferrable backstop is never tripped even
    # mid-transaction. route_type is set by the U2 trigger on insert.
    for route_id, row_id in existing_by_route.items():
        if route_id not in wanted:
            conn.execute("delete from live_student_routes where id = %s", (row_id,))
            affected.add(route_id)
    for route_id in wanted - set(existing_by_route):
        conn.execute(
            "insert into live_student_routes (student_id, route_id) values (%s, %s) "
            "on conflict (student_id, route_id) do nothing",
            (student_id, route_id),
        )
        added.add(route_id)
        affected.add(route_id)
    # sorted(): route ids are UUID strings, so iteration order is a global
    # total order — every multi-route writer takes the route-row locks in the
    # same sequence and two concurrent edits cannot deadlock across routes.
    for route_id in sorted(added):
        # Handover (R18): assigning a student to a planner-saved route hands
        # ownership back to the students — flip custom_stops off and drop the
        # stored polyline/totals (now stale) so the regeneration below rebuilds
        # the stops from the assigned students.
        conn.execute(
            "update live_routes set custom_stops = false, polyline = null, "
            "total_distance_m = null, total_duration_s = null "
            "where id = %s and custom_stops",
            (route_id,),
        )
    stops_recalculated = True
    for route_id in sorted(affected):
        stops_recalculated = regenerate_route_stops(conn, route_id) and stops_recalculated
    _derive_student_bus(conn, student_id)
    return stops_recalculated


def _bulk_link_and_regenerate(conn, assignments: list[tuple[str, str]]) -> None:
    """Insert the bulk (student, route) links, then regenerate each affected
    route ONCE and re-derive each linked student's bus (U8). Takes an explicit
    connection so the burst-guard batching is unit-testable."""
    affected: set[str] = set()
    for student_id, route_id in assignments:
        conn.execute(
            "insert into live_student_routes (student_id, route_id) values (%s, %s) "
            "on conflict (student_id, route_id) do nothing",
            (student_id, route_id),
        )
        affected.add(str(route_id))
    for route_id in sorted(affected):
        regenerate_route_stops(conn, route_id)
    for student_id in {a[0] for a in assignments}:
        _derive_student_bus(conn, student_id)


class StudentLiveDao:
    def list_students(self) -> list[dict[str, Any]]:
        """All students, each with ``route_ids`` and a derived
        ``display_status`` — the parent-portal derivation (app.dao.status_sql)
        wrapped by the admin-only unassigned rule: a student with zero route
        assignments displays 'unassigned', overriding everything (R1–R4). The
        raw ``status`` stays in the payload untouched."""
        with get_connection() as conn:
            rows = conn.execute(
                f"""
                select s.*,
                       case
                           when not exists (
                               select 1 from live_student_routes lsr
                               where lsr.student_id = s.id
                           ) then 'unassigned'
                           else {display_status_case("s")}
                       end as display_status
                from live_students s
                order by s.name asc
                """
            ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                rids = conn.execute(
                    "select route_id from live_student_routes where student_id = %s", (row["id"],)
                ).fetchall()
                item["route_ids"] = [r["route_id"] for r in rids]
                result.append(item)
        return result

    def create_student(self, data: dict, route_ids: list[str]) -> dict[str, Any]:
        with get_connection() as conn:
            row = conn.execute(
                """
                insert into live_students
                    (name, grade, parent_name, parent_phone, parent_phone2, parent_email,
                     parent2_name, parent2_email,
                     home_address, home_lat, home_lng, pickup_time, status, school_id, provenance)
                values
                    (%(name)s, %(grade)s, %(parent_name)s, %(parent_phone)s, %(parent_phone2)s,
                     %(parent_email)s, %(parent2_name)s, %(parent2_email)s,
                     %(home_address)s, %(home_lat)s, %(home_lng)s, %(pickup_time)s,
                     coalesce(%(status)s,'at-school'), %(school_id)s, %(provenance)s)
                returning *
                """,
                data,
            ).fetchone()
            # bus_id is derived from the assigned route(s), not set by hand (#3).
            stops_recalculated = _sync_routes(conn, row["id"], route_ids)
            sync_parent_links(conn, row["id"], (data.get("parent_email"), data.get("parent2_email")))
            row = conn.execute("select * from live_students where id = %s", (row["id"],)).fetchone()
        # stops_recalculated: false = the routes fell back to the preserved /
        # pickup-time order instead of recomputing geometry (U6/R10).
        return {**dict(row), "stops_recalculated": stops_recalculated}

    def update_student(self, student_id: str, data: dict, route_ids: list[str]) -> dict[str, Any] | None:
        with get_connection() as conn:
            before = conn.execute(
                "select parent_email, parent2_email from live_students where id = %s",
                (student_id,),
            ).fetchone()
            # No status in this UPDATE, ever: the payload's status (defaulted to
            # 'at-school') used to silently reset a live on-bus/dropped-off
            # status on every admin edit (R7). Status is written only by the
            # run lifecycle; the insert default is the single exception.
            row = conn.execute(
                """
                update live_students set
                    name=%(name)s, grade=%(grade)s, parent_name=%(parent_name)s,
                    parent_phone=%(parent_phone)s, parent_phone2=%(parent_phone2)s,
                    parent_email=%(parent_email)s, parent2_name=%(parent2_name)s,
                    parent2_email=%(parent2_email)s, home_address=%(home_address)s,
                    home_lat=%(home_lat)s, home_lng=%(home_lng)s, pickup_time=%(pickup_time)s,
                    school_id=%(school_id)s, provenance=%(provenance)s
                where id=%(id)s returning *
                """,
                {**data, "id": student_id},
            ).fetchone()
            stops_recalculated = True
            if row:
                # bus_id is derived from the assigned route(s) inside _sync_routes (#3).
                stops_recalculated = _sync_routes(conn, student_id, route_ids)
                sync_parent_links(
                    conn,
                    student_id,
                    (data.get("parent_email"), data.get("parent2_email")),
                    old_emails=(before["parent_email"], before["parent2_email"]) if before else None,
                )
                row = conn.execute("select * from live_students where id = %s", (student_id,)).fetchone()
        if not row:
            return None
        return {**dict(row), "stops_recalculated": stops_recalculated}

    def delete_student(self, student_id: str) -> None:
        with get_connection() as conn:
            # Cancelling a student must also cancel their stop on every route
            # they were on (#1, #6) — regenerate after the cascade delete clears
            # their live_student_routes rows. order by route_id: the same
            # global lock order every multi-route writer uses (_sync_routes).
            affected = [
                r["route_id"]
                for r in conn.execute(
                    "select route_id from live_student_routes where student_id = %s "
                    "order by route_id",
                    (student_id,),
                ).fetchall()
            ]
            conn.execute("delete from live_students where id = %s", (student_id,))
            for route_id in affected:
                regenerate_route_stops(conn, route_id)

    def insert_bulk_student(self, data: dict) -> dict[str, Any]:
        """Insert one bulk-upload row; returns ``{id, parent_links}`` (the new
        student id and the count of parent-account links auto-created from its
        email slots). A CSV row's home defaults to provenance 'imported' (U8/U4);
        a row repaired via the PlacePicker carries its own provenance."""
        data = {**data, "provenance": data.get("provenance") or "imported"}
        with get_connection() as conn:
            row = conn.execute(
                """
                insert into live_students
                    (name, grade, parent_name, parent_phone, parent_phone2, parent_email,
                     parent2_name, parent2_email,
                     home_address, home_lat, home_lng, pickup_time, status, provenance)
                values
                    (%(name)s, %(grade)s, %(parent_name)s, %(parent_phone)s, %(parent_phone2)s,
                     %(parent_email)s, %(parent2_name)s, %(parent2_email)s,
                     %(home_address)s, %(home_lat)s, %(home_lng)s, %(pickup_time)s, 'at-school',
                     %(provenance)s)
                returning id
                """,
                data,
            ).fetchone()
            assignments = sync_parent_links(
                conn, row["id"], (data.get("parent_email"), data.get("parent2_email"))
            )
        return {"id": row["id"], "parent_links": assignments}

    def resolve_route_id_by_name(self, name: str | None) -> str | None:
        """Resolve a CSV ``route_name`` to a route id (U8) — the term-start
        onboarding column. Case-insensitive exact match; None when unknown or
        ambiguous (>1 route shares the name)."""
        if not name or not name.strip():
            return None
        with get_connection() as conn:
            rows = conn.execute(
                "select id from live_routes where lower(name) = lower(%s) limit 2",
                (name.strip(),),
            ).fetchall()
        return rows[0]["id"] if len(rows) == 1 else None

    def bulk_link_and_regenerate(self, assignments: list[tuple[str, str]]) -> None:
        """Link bulk-imported students to their routes and regenerate each
        affected route EXACTLY ONCE (U8): per-row regeneration on a large import
        is O(rows) Google round-trips in one Lambda invocation (verified burst
        risk). route_type is set by the U2 trigger; buses are re-derived after."""
        if not assignments:
            return
        with get_connection() as conn:
            _bulk_link_and_regenerate(conn, assignments)
