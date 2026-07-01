from typing import Any

from app.core.db import get_connection
from app.dao.student_live_dao import _ConnParentLinks, link_account_to_matching_students


class AccountDao:
    # --- drivers -----------------------------------------------------------

    def list_drivers(self) -> list[dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                select u.id, u.full_name, u.email, u.phone,
                       (u.pin_hash is not null) as has_pin,
                       b.name as assigned_bus, u.created_at
                from app_users u
                join app_user_roles r on r.user_id = u.id and r.role = 'driver'
                left join live_buses b on b.driver_id = u.id
                order by u.full_name asc
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def create_driver(self, email, password_hash, full_name, phone, pin_hash) -> dict[str, Any]:
        with get_connection() as conn:
            row = conn.execute(
                "insert into app_users (email, password_hash, full_name, phone, pin_hash) "
                "values (%s, %s, %s, %s, %s) returning id, full_name, email, phone",
                (email, password_hash, full_name, phone, pin_hash),
            ).fetchone()
            conn.execute(
                "insert into app_user_roles (user_id, role) values (%s, 'driver')", (row["id"],)
            )
        return dict(row)

    def update_driver(self, driver_id, full_name, email, phone, pin_hash) -> dict[str, Any] | None:
        with get_connection() as conn:
            if pin_hash is not None:
                row = conn.execute(
                    "update app_users set full_name=%s, email=%s, phone=%s, pin_hash=%s "
                    "where id=%s returning id, full_name, email, phone",
                    (full_name, email, phone, pin_hash, driver_id),
                ).fetchone()
            else:
                row = conn.execute(
                    "update app_users set full_name=%s, email=%s, phone=%s "
                    "where id=%s returning id, full_name, email, phone",
                    (full_name, email, phone, driver_id),
                ).fetchone()
        return dict(row) if row else None

    def delete_driver(self, driver_id: str) -> None:
        with get_connection() as conn:
            conn.execute("update live_buses set driver_id = null where driver_id = %s", (driver_id,))
            conn.execute("delete from app_users where id = %s", (driver_id,))

    # --- parents -----------------------------------------------------------

    def list_parents(self) -> list[dict[str, Any]]:
        with get_connection() as conn:
            registered = conn.execute(
                """
                select u.id, u.full_name, u.email, u.phone, u.created_at,
                       coalesce(array_agg(s.name) filter (where s.name is not null), '{}') as students
                from app_users u
                join app_user_roles r on r.user_id = u.id and r.role = 'parent'
                left join live_parent_students ps on ps.parent_id = u.id
                left join live_students s on s.id = ps.student_id
                group by u.id
                order by u.full_name asc
                """
            ).fetchall()
            result = [
                {**dict(r), "status": "registered", "students": list(r["students"])}
                for r in registered
            ]
            # Pending parents: an email in either parent slot with no matching
            # account (R11 — Parent 2 shows up as pending too).
            pending = conn.execute(
                """
                with slots as (
                    select parent_email as email, parent_name as pname, name
                    from live_students where parent_email is not null
                    union all
                    select parent2_email, parent2_name, name
                    from live_students where parent2_email is not null
                )
                select email as parent_email, pname as parent_name,
                       coalesce(array_agg(name) filter (where name is not null), '{}') as students
                from slots
                where lower(email) not in (
                        select lower(email) from app_users u
                        join app_user_roles r on r.user_id = u.id and r.role = 'parent'
                    )
                group by email, pname
                order by pname asc
                """
            ).fetchall()
            for p in pending:
                result.append({
                    "id": None,
                    "full_name": p["parent_name"],
                    "email": p["parent_email"],
                    "phone": None,
                    "status": "pending",
                    "students": list(p["students"]),
                    "created_at": None,
                })
        return result

    def update_parent(self, parent_id, full_name, email, phone) -> dict[str, Any] | None:
        with get_connection() as conn:
            row = conn.execute(
                "update app_users set full_name=%s, email=%s, phone=%s where id=%s "
                "returning id, full_name, email, phone",
                (full_name, email, phone, parent_id),
            ).fetchone()
        return dict(row) if row else None

    def delete_parent(self, parent_id: str) -> None:
        with get_connection() as conn:
            conn.execute("delete from app_users where id = %s", (parent_id,))

    def email_exists(self, email: str) -> bool:
        with get_connection() as conn:
            row = conn.execute(
                "select 1 from app_users where lower(email) = lower(%s)", (email,)
            ).fetchone()
        return bool(row)

    def link_parent_to_matching_students(self, parent_id, email: str) -> int:
        """Link a (new) parent account to every student carrying its email in
        either parent slot (R11), honouring the per-student link cap. Returns
        the number of links created."""
        with get_connection() as conn:
            return link_account_to_matching_students(_ConnParentLinks(conn), parent_id, email)
