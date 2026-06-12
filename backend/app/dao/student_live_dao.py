from typing import Any

from app.core.db import get_connection
from app.dao.fleet_dao import regenerate_route_stops

STUDENT_COLUMNS = (
    "name", "grade", "parent_name", "parent_phone", "parent_phone2", "parent_email",
    "home_address", "home_lat", "home_lng", "pickup_time", "status", "bus_id", "school_id",
)


def _sync_routes(conn, student_id: str, route_ids: list[str]) -> None:
    existing = conn.execute(
        "select id, route_id from live_student_routes where student_id = %s", (student_id,)
    ).fetchall()
    existing_by_route = {r["route_id"]: r["id"] for r in existing}
    wanted = {rid for rid in route_ids if rid}

    affected: set[str] = set()
    for route_id in wanted - set(existing_by_route):
        conn.execute(
            "insert into live_student_routes (student_id, route_id) values (%s, %s) "
            "on conflict (student_id, route_id) do nothing",
            (student_id, route_id),
        )
        affected.add(route_id)
    for route_id, row_id in existing_by_route.items():
        if route_id not in wanted:
            conn.execute("delete from live_student_routes where id = %s", (row_id,))
            affected.add(route_id)
    for route_id in affected:
        regenerate_route_stops(conn, route_id)


class StudentLiveDao:
    def list_students(self) -> list[dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute("select * from live_students order by name asc").fetchall()
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
                     home_address, home_lat, home_lng, pickup_time, status, bus_id, school_id)
                values
                    (%(name)s, %(grade)s, %(parent_name)s, %(parent_phone)s, %(parent_phone2)s,
                     %(parent_email)s, %(home_address)s, %(home_lat)s, %(home_lng)s, %(pickup_time)s,
                     coalesce(%(status)s,'at-school'), %(bus_id)s, %(school_id)s)
                returning *
                """,
                data,
            ).fetchone()
            _sync_routes(conn, row["id"], route_ids)
        return dict(row)

    def update_student(self, student_id: str, data: dict, route_ids: list[str]) -> dict[str, Any] | None:
        with get_connection() as conn:
            row = conn.execute(
                """
                update live_students set
                    name=%(name)s, grade=%(grade)s, parent_name=%(parent_name)s,
                    parent_phone=%(parent_phone)s, parent_phone2=%(parent_phone2)s,
                    parent_email=%(parent_email)s, home_address=%(home_address)s,
                    home_lat=%(home_lat)s, home_lng=%(home_lng)s, pickup_time=%(pickup_time)s,
                    status=coalesce(%(status)s,'at-school'), bus_id=%(bus_id)s, school_id=%(school_id)s
                where id=%(id)s returning *
                """,
                {**data, "id": student_id},
            ).fetchone()
            if row:
                _sync_routes(conn, student_id, route_ids)
        return dict(row) if row else None

    def delete_student(self, student_id: str) -> None:
        with get_connection() as conn:
            conn.execute("delete from live_students where id = %s", (student_id,))

    def insert_bulk_student(self, data: dict) -> str:
        with get_connection() as conn:
            row = conn.execute(
                """
                insert into live_students
                    (name, grade, parent_name, parent_phone, parent_phone2, parent_email,
                     home_address, home_lat, home_lng, pickup_time, status)
                values
                    (%(name)s, %(grade)s, %(parent_name)s, %(parent_phone)s, %(parent_phone2)s,
                     %(parent_email)s, %(home_address)s, %(home_lat)s, %(home_lng)s, %(pickup_time)s, 'at-school')
                returning id
                """,
                data,
            ).fetchone()
            assignments = 0
            if data.get("parent_email"):
                parent = conn.execute(
                    "select u.id from app_users u join app_user_roles r on r.user_id = u.id "
                    "where lower(u.email) = lower(%s) and r.role = 'parent'",
                    (data["parent_email"],),
                ).fetchone()
                if parent:
                    conn.execute(
                        "insert into live_parent_students (parent_id, student_id) values (%s, %s) "
                        "on conflict (parent_id, student_id) do nothing",
                        (parent["id"], row["id"]),
                    )
                    assignments = 1
        return assignments
