"""School isolation, slice 2 (U7): fleet routes, fleet plans, broadcast.

Runs the real ``create_app()`` in-process (TestClient) against the local
database. Covers: the scoped route list and CRUD (AE25 — a route payload
naming another school's bus answers 404 with nothing written; the payload's
school_id is ignored; delete stays director-only with the route audits), the
route-options preview taking its school from the scope, the fleet-plan
family resolving plan ids against the ACTIVE school (draft/review/edits/
apply/restore/discard/slot-ins; foreign plan ids 404), the plan audit
actions, slot-in proposals surviving the service's mid-connection commit
boundaries under a school scope, and the broadcast recipient rule — accepted
links on enabled accounts only, feed rows stamped with the school.

Every row a test creates is removed in a finally block (or by the
``temp_school`` sweep); seeded rows are never deleted.
"""

import os
import uuid

import psycopg
import pytest
from psycopg.rows import dict_row

from conftest import (
    COORDINATOR_A,
    DIRECTOR_A,
    DIRECTOR_B,
    DSN,
    SCHOOL_A_ID,
    SCHOOL_B_ID,
    TEST_PASSWORD,
    temp_school,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="needs the local stack; set RUN_INTEGRATION=1",
)

# Seeded fixtures (backend/db/seeds/003_local_snapshot.sql).
BUS_B_ID = "146a0000-0000-0000-0000-00000000000b"
ROUTE_B_ID = "40000000-0000-0000-0000-00000000000b"
STUDENT_B_ID = "50000000-0000-0000-0000-00000000000b"
SCHOOL_B_NAME = "IT Second School"
SCHOOL_A_NAME = "Greenfield Academy"


@pytest.fixture(scope="module")
def client(in_process_db):
    from fastapi.testclient import TestClient

    from app.main import create_app

    return TestClient(create_app())


_tokens: dict[str, str] = {}


def tok(client, email: str) -> str:
    if email not in _tokens:
        resp = client.post(
            "/api/auth/login", json={"email": email, "password": TEST_PASSWORD}
        )
        assert resp.status_code == 200, resp.text
        _tokens[email] = resp.json()["token"]
    return _tokens[email]


def hdr(client, email: str, school: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {tok(client, email)}"}
    if school is not None:
        headers["X-School-Id"] = school
    return headers


def db():
    return psycopg.connect(DSN, autocommit=True, row_factory=dict_row)


def user_id_of(email: str) -> str:
    with db() as conn:
        return str(conn.execute(
            "select id from app_users where email = %s", (email,)
        ).fetchone()["id"])


def purge_audit_for(resource_id: str | None) -> None:
    if not resource_id:
        return
    with db() as conn:
        conn.execute(
            "delete from live_admin_audit where resource_id = %s", (resource_id,)
        )


def audit_actions_for(resource_id: str) -> list[str]:
    with db() as conn:
        return [
            r["action"] for r in conn.execute(
                "select action from live_admin_audit where resource_id = %s "
                "order by created_at",
                (resource_id,),
            ).fetchall()
        ]


def student_payload(marker: str, lat: float, lng: float, **overrides) -> dict:
    payload = {
        "name": f"IT U7 Rider {marker}",
        "grade": "Grade 1",
        "parent_name": f"IT U7 Parent {marker}",
        "parent_phone": "+254711000300",
        "parent_email": f"it-u7-{marker}@test.local",
        "home_lat": lat,
        "home_lng": lng,
    }
    payload.update(overrides)
    return payload


# --- routes: AE25 + scoped CRUD ----------------------------------------------


def test_route_list_is_scoped(client):
    listed = client.get("/api/fleet/routes", headers=hdr(client, DIRECTOR_A, SCHOOL_A_ID))
    assert listed.status_code == 200, listed.text
    assert ROUTE_B_ID not in {str(r["id"]) for r in listed.json()}
    assert all(str(r["school_id"]) == SCHOOL_A_ID for r in listed.json())

    listed_b = client.get("/api/fleet/routes", headers=hdr(client, DIRECTOR_B, SCHOOL_B_ID))
    assert ROUTE_B_ID in {str(r["id"]) for r in listed_b.json()}


def test_route_with_foreign_bus_is_404_and_nothing_changes(client):
    """AE25: a route created or updated with another school's bus answers 404
    — the bus does not exist for this caller — and no row moves."""
    marker = uuid.uuid4().hex[:6]
    with temp_school(f"IT U7 Routes {marker}") as school_c:
        headers = hdr(client, DIRECTOR_A, school_c)
        own_bus = client.post(
            "/api/fleet/buses", json={"name": f"IT U7 RBus {marker}"}, headers=headers
        ).json()

        refused = client.post(
            "/api/fleet/routes",
            json={"name": f"IT U7 Foreign {marker}", "bus_id": BUS_B_ID},
            headers=headers,
        )
        assert refused.status_code == 404, refused.text
        assert refused.json()["detail"] == "Bus not found"  # never names a school
        with db() as conn:
            assert conn.execute(
                "select 1 from live_routes where name = %s",
                (f"IT U7 Foreign {marker}",),
            ).fetchone() is None

        # The payload's school_id is IGNORED — the scope stamps the school.
        created = client.post(
            "/api/fleet/routes",
            json={"name": f"IT U7 Route {marker}", "bus_id": own_bus["id"],
                  "school_id": SCHOOL_B_ID},
            headers=headers,
        )
        assert created.status_code == 200, created.text
        route_id = str(created.json()["id"])
        with db() as conn:
            row = conn.execute(
                "select school_id, bus_id from live_routes where id = %s", (route_id,)
            ).fetchone()
        assert str(row["school_id"]) == school_c

        # Update with B's bus: 404, byte-identical row.
        edited = client.put(
            f"/api/fleet/routes/{route_id}",
            json={"name": f"IT U7 Route {marker}", "bus_id": BUS_B_ID},
            headers=headers,
        )
        assert edited.status_code == 404, edited.text
        with db() as conn:
            after = conn.execute(
                "select school_id, bus_id, name from live_routes where id = %s",
                (route_id,),
            ).fetchone()
        assert str(after["bus_id"]) == str(own_bus["id"])

        # A foreign caller: the route does not exist for school B's director.
        for refusal in (
            client.put(
                f"/api/fleet/routes/{route_id}",
                json={"name": "IT U7 Hacked"},
                headers=hdr(client, DIRECTOR_B, SCHOOL_B_ID),
            ),
            client.delete(
                f"/api/fleet/routes/{route_id}",
                headers=hdr(client, DIRECTOR_B, SCHOOL_B_ID),
            ),
            client.put(
                f"/api/fleet/routes/{route_id}/stop-order",
                json={"order": []},
                headers=hdr(client, DIRECTOR_B, SCHOOL_B_ID),
            ),
            client.post(
                f"/api/fleet/routes/{route_id}/recalculate",
                headers=hdr(client, DIRECTOR_B, SCHOOL_B_ID),
            ),
        ):
            assert refusal.status_code == 404, refusal.text
        with db() as conn:
            assert conn.execute(
                "select name from live_routes where id = %s", (route_id,)
            ).fetchone()["name"] == f"IT U7 Route {marker}"

        # Stop-level edits resolve the path route through the scope too.
        foreign_stop = client.delete(
            f"/api/fleet/routes/{ROUTE_B_ID}/stops/{STUDENT_B_ID}", headers=headers
        )
        assert foreign_stop.status_code == 404, foreign_stop.text
        with db() as conn:
            assert conn.execute(
                "select 1 from live_student_routes where route_id = %s and student_id = %s",
                (ROUTE_B_ID, STUDENT_B_ID),
            ).fetchone() is not None
        foreign_time = client.put(
            f"/api/fleet/routes/{ROUTE_B_ID}/stops/{STUDENT_B_ID}",
            json={"pickup_time": "06:00"},
            headers=headers,
        )
        assert foreign_time.status_code == 404, foreign_time.text

        assert audit_actions_for(route_id) == ["route-created"]
        purge_audit_for(route_id)


def test_route_delete_is_director_only_with_audits(client):
    marker = uuid.uuid4().hex[:6]
    headers = hdr(client, DIRECTOR_A, SCHOOL_A_ID)
    created = client.post(
        "/api/fleet/routes", json={"name": f"IT U7 DelRoute {marker}"}, headers=headers
    )
    assert created.status_code == 200, created.text
    route_id = str(created.json()["id"])
    try:
        refused = client.delete(
            f"/api/fleet/routes/{route_id}",
            headers=hdr(client, COORDINATOR_A, SCHOOL_A_ID),
        )
        assert refused.status_code == 403, refused.text

        edited = client.put(
            f"/api/fleet/routes/{route_id}",
            json={"name": f"IT U7 DelRoute {marker} renamed"},
            headers=hdr(client, COORDINATOR_A, SCHOOL_A_ID),
        )
        assert edited.status_code == 200, edited.text  # edits stay staff-wide

        deleted = client.delete(f"/api/fleet/routes/{route_id}", headers=headers)
        assert deleted.status_code == 200, deleted.text
        with db() as conn:
            assert conn.execute(
                "select 1 from live_routes where id = %s", (route_id,)
            ).fetchone() is None
        assert audit_actions_for(route_id) == [
            "route-created", "route-updated", "route-deleted",
        ]
    finally:
        with db() as conn:
            conn.execute("delete from live_routes where id = %s", (route_id,))
        purge_audit_for(route_id)


def test_route_options_takes_the_school_from_the_scope(client):
    preview = client.post(
        "/api/fleet/route-options",
        json={
            # The payload names school B; the ACTIVE school (A) must win.
            "school_id": SCHOOL_B_ID,
            "type": "morning",
            "stops": [{"label": "IT U7 Stop", "lat": -1.30, "lng": 36.78}],
        },
        headers=hdr(client, DIRECTOR_A, SCHOOL_A_ID),
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    school_stops = [
        s for option in body["options"] for s in option["stops"] if s["is_school"]
    ]
    assert school_stops, "the preview should append the scope school's gate"
    assert all(s["label"] == SCHOOL_A_NAME for s in school_stops)
    assert not any(s["label"] == SCHOOL_B_NAME for s in school_stops)


# --- fleet plans: scoped end to end ------------------------------------------


def test_fleet_plan_family_resolves_against_the_active_school(client):
    """Draft → review → edit → apply → slot-ins on a sandbox school, with
    every foreign access answering 404 and the U7 plan audits written. Also
    proves slot-in generation survives the service's transaction boundaries
    under a school scope (the mid-connection-commit GUC scenario)."""
    from app.core.scope import SchoolScope, set_current_scope
    from app.services.slot_in_service import propose_slot_ins

    marker = uuid.uuid4().hex[:6]
    b_headers = hdr(client, DIRECTOR_B, SCHOOL_B_ID)
    with temp_school(f"IT U7 Plans {marker}", lat=-1.3000, lng=36.8000) as school_c:
        headers = hdr(client, DIRECTOR_A, school_c)
        bus = client.post(
            "/api/fleet/buses",
            json={"name": f"IT U7 PBus {marker}", "capacity": 20},
            headers=headers,
        ).json()
        s1 = client.post(
            "/api/students", json=student_payload(f"{marker}-1", -1.3010, 36.8010),
            headers=headers,
        ).json()
        s2 = client.post(
            "/api/students", json=student_payload(f"{marker}-2", -1.3020, 36.8020),
            headers=headers,
        ).json()

        # Draft: the payload's school_id is ignored — the scope decides.
        draft = client.post(
            "/api/fleet-plans/draft",
            json={"school_id": SCHOOL_B_ID, "seed": 1},
            headers=headers,
        )
        assert draft.status_code == 200, draft.text
        plan_id = str(draft.json()["id"])
        assert str(draft.json()["school_id"]) == school_c
        assert "plan-drafted" in audit_actions_for(plan_id)

        # Foreign plan ids answer the not-found contract everywhere.
        for refusal in (
            client.get(
                "/api/fleet-plans/review", params={"school_id": school_c},
                headers=b_headers,
            ),
            client.post(f"/api/fleet-plans/{plan_id}/discard", headers=b_headers),
            client.post(f"/api/fleet-plans/{plan_id}/resolve", headers=b_headers),
            client.post(
                f"/api/fleet-plans/{plan_id}/pin",
                json={"student_id": str(s1["id"]), "order": 0},
                headers=b_headers,
            ),
            client.post(
                f"/api/fleet-plans/{plan_id}/apply", json={}, headers=b_headers
            ),
        ):
            assert refusal.status_code == 404, refusal.text
        with db() as conn:
            assert conn.execute(
                "select status from live_fleet_plans where id = %s", (plan_id,)
            ).fetchone()["status"] == "draft"

        # The review reads the ACTIVE school — the query param is ignored.
        review = client.get(
            "/api/fleet-plans/review", params={"school_id": SCHOOL_B_ID},
            headers=headers,
        )
        assert review.status_code == 200, review.text
        assert str(review.json()["school_id"]) == school_c

        # A review edit lands the plan-updated audit in the same act.
        pinned = client.post(
            f"/api/fleet-plans/{plan_id}/pin",
            json={"student_id": str(s1["id"]), "order": 0},
            headers=headers,
        )
        assert pinned.status_code == 200, pinned.text
        assert "plan-updated" in audit_actions_for(plan_id)

        # Apply (AE3's plan half): acknowledgments straight off the review.
        acks = [
            {"student_id": u["student_id"], "leg": u["leg"]}
            for leg_list in review.json()["unplaceable"].values()
            for u in leg_list
        ]
        applied = client.post(
            f"/api/fleet-plans/{plan_id}/apply",
            json={"acknowledgments": acks},
            headers=headers,
        )
        assert applied.status_code == 200, applied.text
        assert applied.json()["routes_written"] >= 1
        assert "plan-applied" in audit_actions_for(plan_id)

        # Restore is idempotent on the applied plan id, and a foreign restore
        # answers 404 without toggling anything.
        assert client.post(
            f"/api/fleet-plans/{plan_id}/restore", json={}, headers=b_headers
        ).status_code == 404
        idempotent = client.post(
            f"/api/fleet-plans/{plan_id}/restore", json={}, headers=headers
        )
        assert idempotent.status_code == 200, idempotent.text
        assert idempotent.json()["already_applied"] is True

        # Slot-ins: a new plannable child with no links gets a proposal. The
        # generation call runs under a published SchoolScope — the strict
        # seam's request shape — and its writes must survive the service's
        # scoped_transaction boundaries (the mid-connection-commit scenario).
        s3 = client.post(
            "/api/students", json=student_payload(f"{marker}-3", -1.3030, 36.8030),
            headers=headers,
        ).json()
        scope_c = SchoolScope(
            user_id=user_id_of(DIRECTOR_A), school_id=school_c, role="director"
        )
        set_current_scope(scope_c)
        try:
            summary = propose_slot_ins([str(s3["id"])], scope=scope_c)
        finally:
            set_current_scope(None)
        assert summary is not None and summary["records"] >= 1

        listed = client.get(
            "/api/fleet-plans/slot-ins", params={"school_id": SCHOOL_B_ID},
            headers=headers,
        )
        assert listed.status_code == 200, listed.text
        proposals = listed.json()["proposals"]
        mine = [p for p in proposals if str(p["student_id"]) == str(s3["id"])]
        assert mine, f"expected a proposal for the new child: {listed.json()}"

        # Foreign accept: B has no applied plan, so the storage answers 404.
        assert client.post(
            "/api/fleet-plans/slot-ins/accept",
            json={"proposal_id": mine[0]["id"]},
            headers=b_headers,
        ).status_code == 404

        accepted = client.post(
            "/api/fleet-plans/slot-ins/accept",
            json={"school_id": SCHOOL_B_ID, "proposal_id": mine[0]["id"]},
            headers=headers,
        )
        assert accepted.status_code == 200, accepted.text
        assert "slot-in-accepted" in audit_actions_for(plan_id)
        with db() as conn:
            assert conn.execute(
                "select 1 from live_student_routes where student_id = %s",
                (str(s3["id"]),),
            ).fetchone() is not None

        # Dismiss: one more child, one more record, removed without effects.
        s4 = client.post(
            "/api/students", json=student_payload(f"{marker}-4", -1.3040, 36.8040),
            headers=headers,
        ).json()
        set_current_scope(scope_c)
        try:
            propose_slot_ins([str(s4["id"])], scope=scope_c)
        finally:
            set_current_scope(None)
        listed = client.get("/api/fleet-plans/slot-ins", headers=headers).json()
        record = next(
            r for r in listed["proposals"] + listed["unplaceable"]
            if str(r["student_id"]) == str(s4["id"])
        )
        dismissed = client.post(
            "/api/fleet-plans/slot-ins/dismiss",
            json={"proposal_id": record["id"]},
            headers=headers,
        )
        assert dismissed.status_code == 200, dismissed.text
        assert "slot-in-dismissed" in audit_actions_for(plan_id)
        purge_audit_for(plan_id)


# --- broadcast: recipient rule + school stamp ---------------------------------


def test_broadcast_reaches_accepted_enabled_parents_only(client):
    """A pending-link parent and a disabled parent receive nothing; the one
    accepted, enabled parent gets exactly one 'admin-notice' feed row carrying
    the school's id. A foreign route answers 404 with nothing inserted."""
    marker = uuid.uuid4().hex[:6]
    parent_ids: list[str] = []
    with temp_school(f"IT U7 Cast {marker}", lat=-1.31, lng=36.79) as school_c:
        headers = hdr(client, DIRECTOR_A, school_c)
        try:
            route = client.post(
                "/api/fleet/routes", json={"name": f"IT U7 CastRoute {marker}"},
                headers=headers,
            ).json()
            student = client.post(
                "/api/students",
                json={**student_payload(f"{marker}-c", -1.311, 36.791),
                      "route_ids": [str(route["id"])]},
                headers=headers,
            ).json()

            with db() as conn:
                for kind in ("accepted", "pending", "disabled"):
                    pid = str(conn.execute(
                        "insert into app_users (email, password_hash, full_name, disabled_at) "
                        "values (%s, 'x', %s, case when %s = 'disabled' then now() end) "
                        "returning id",
                        (f"it-u7-{kind}-{marker}@test.local",
                         f"IT U7 {kind} {marker}", kind),
                    ).fetchone()["id"])
                    conn.execute(
                        "insert into app_user_roles (user_id, role) values (%s, 'parent')",
                        (pid,),
                    )
                    conn.execute(
                        "insert into live_parent_students (parent_id, student_id, status) "
                        "values (%s, %s, %s)",
                        (pid, str(student["id"]),
                         "pending" if kind == "pending" else "accepted"),
                    )
                    parent_ids.append(pid)

            # A foreign route: 404, nothing sent, nothing audited.
            foreign = client.post(
                f"/api/fleet/routes/{ROUTE_B_ID}/broadcast",
                json={"body": "IT U7 foreign"},
                headers=headers,
            )
            assert foreign.status_code == 404, foreign.text

            sent = client.post(
                f"/api/fleet/routes/{route['id']}/broadcast",
                json={"body": f"IT U7 notice {marker}"},
                headers=headers,
            )
            assert sent.status_code == 200, sent.text
            assert sent.json()["recipients"] == 1  # accepted + enabled only

            with db() as conn:
                rows = conn.execute(
                    "select user_id, school_id, type from live_notifications "
                    "where user_id = any(%s::uuid[])",
                    (parent_ids,),
                ).fetchall()
            assert len(rows) == 1
            assert str(rows[0]["user_id"]) == parent_ids[0]  # the accepted one
            assert rows[0]["type"] == "admin-notice"
            assert str(rows[0]["school_id"]) == school_c  # U7 school stamp

            assert audit_actions_for(str(route["id"])) == [
                "route-created", "broadcast-sent",
            ]
            with db() as conn:
                detail = conn.execute(
                    "select detail from live_admin_audit where resource_id = %s "
                    "and action = 'broadcast-sent'",
                    (str(route["id"]),),
                ).fetchone()["detail"]
            assert detail == {"recipients": 1}
            purge_audit_for(str(route["id"]))
        finally:
            with db() as conn:
                for pid in parent_ids:
                    conn.execute("delete from app_users where id = %s", (pid,))
