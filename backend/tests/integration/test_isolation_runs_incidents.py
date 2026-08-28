"""School isolation, slice 2 (U7): runs and incidents.

Runs the real ``create_app()`` in-process (TestClient) against the local
database. Covers: the scoped runs list (staff = their school; a driver token
= their own bus only), create/update refusing foreign bus/route ids with 404
(AE25's run-side twin), the AE27 delete split (coordinator cleans up an open
run; a completed run's delete is director-only; the completed-today-with-
evidence refusal stands for directors), AE3's force-close for the active
school with 404 on foreign ids, and incidents stamped with their school —
B's driver report never on A's Alerts, per-school unread counts,
director-only deletes, and the acknowledge/delete not-found contract.

Every row a test creates is removed in a finally block; seeded rows are
never deleted (the seeded stale run included).
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
)

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="needs the local stack; set RUN_INTEGRATION=1",
)

# Seeded fixtures (backend/db/seeds/003_local_snapshot.sql).
DRIVER_A_PIN = "0322"          # Daniel Kamau — bus Simba, school A
DRIVER_B_PIN = "7391"          # Dan Wekesa — IT Bus B, school B
BUS_A_SIMBA = "146a1837-af5e-494c-8be6-f78db9c4280a"
ROUTE_A_MORNING = "40000000-0000-0000-0000-000000000001"  # Express 1 — Morning
BUS_B_ID = "146a0000-0000-0000-0000-00000000000b"
ROUTE_B_ID = "40000000-0000-0000-0000-00000000000b"
STUDENT_A_FAITH_ID = "50000000-0000-0000-0000-000000000001"


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


def pin_tok(client, pin: str) -> str:
    key = f"pin:{pin}"
    if key not in _tokens:
        resp = client.post("/api/auth/pin-login", json={"pin": pin})
        assert resp.status_code == 200, resp.text
        _tokens[key] = resp.json()["token"]
    return _tokens[key]


def hdr(client, email: str, school: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {tok(client, email)}"}
    if school is not None:
        headers["X-School-Id"] = school
    return headers


def pin_hdr(client, pin: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {pin_tok(client, pin)}"}


def db():
    return psycopg.connect(DSN, autocommit=True, row_factory=dict_row)


def make_run(school_id: str, *, status: str, date_sql: str,
             bus_id: str | None = None, route_id: str | None = None) -> str:
    with db() as conn:
        return str(conn.execute(
            "insert into live_runs (school_id, bus_id, route_id, type, date, status) "
            f"values (%s, %s, %s, 'morning', {date_sql}, %s) returning id",
            (school_id, bus_id, route_id, status),
        ).fetchone()["id"])


def purge_run(run_id: str | None) -> None:
    if not run_id:
        return
    with db() as conn:
        conn.execute("delete from live_runs where id = %s", (run_id,))


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


# --- the runs list -----------------------------------------------------------


def test_runs_list_is_scoped_per_school_and_narrowed_for_drivers(client):
    b_run = make_run(SCHOOL_B_ID, status="in-progress", date_sql="current_date",
                     bus_id=BUS_B_ID, route_id=ROUTE_B_ID)
    try:
        listed_a = client.get("/api/runs", headers=hdr(client, DIRECTOR_A, SCHOOL_A_ID))
        assert listed_a.status_code == 200, listed_a.text
        a_ids = {str(r["id"]) for r in listed_a.json()}
        assert b_run not in a_ids
        with db() as conn:
            a_count = conn.execute(
                "select count(*) as n from live_runs where school_id = %s",
                (SCHOOL_A_ID,),
            ).fetchone()["n"]
        assert len(a_ids) == a_count  # everything of A, nothing else

        listed_b = client.get(
            "/api/runs", params={"active": "true"},
            headers=hdr(client, DIRECTOR_B, SCHOOL_B_ID),
        )
        assert b_run in {str(r["id"]) for r in listed_b.json()}

        # Driver A: only their own bus's runs — never the school-wide list.
        driver_a = client.get("/api/runs", headers=pin_hdr(client, DRIVER_A_PIN))
        assert driver_a.status_code == 200, driver_a.text
        rows = driver_a.json()
        assert rows, "seeded Simba runs expected"
        assert all(str(r["bus_id"]) == BUS_A_SIMBA for r in rows)

        # Driver B: only IT Bus B's runs, never A's.
        driver_b = client.get("/api/runs", headers=pin_hdr(client, DRIVER_B_PIN))
        b_rows = driver_b.json()
        assert {str(r["id"]) for r in b_rows} == {b_run}
    finally:
        purge_run(b_run)


# --- create/update refuse foreign references (AE25's run twin) ---------------


def test_run_create_and_update_refuse_foreign_bus_and_route(client):
    headers = hdr(client, DIRECTOR_A, SCHOOL_A_ID)
    with db() as conn:
        before = conn.execute(
            "select count(*) as n from live_runs"
        ).fetchone()["n"]
    for payload in (
        {"route_id": ROUTE_B_ID, "type": "morning", "status": "completed"},
        {"bus_id": BUS_B_ID, "type": "morning", "status": "completed"},
    ):
        refused = client.post("/api/runs", json=payload, headers=headers)
        assert refused.status_code == 404, refused.text
    with db() as conn:
        after = conn.execute("select count(*) as n from live_runs").fetchone()["n"]
    assert after == before  # nothing was created

    created = client.post(
        "/api/runs",
        # A stray client-supplied school id is ignored: the scope stamps it.
        json={"type": "morning", "status": "completed", "school_id": SCHOOL_B_ID,
              "date": "2031-03-03"},
        headers=headers,
    )
    assert created.status_code == 200, created.text
    run_id = str(created.json()["id"])
    try:
        with db() as conn:
            row = conn.execute(
                "select school_id from live_runs where id = %s", (run_id,)
            ).fetchone()
        assert str(row["school_id"]) == SCHOOL_A_ID

        edited = client.put(
            f"/api/runs/{run_id}",
            json={"type": "morning", "status": "completed", "route_id": ROUTE_B_ID,
                  "date": "2031-03-03"},
            headers=headers,
        )
        assert edited.status_code == 404, edited.text
        with db() as conn:
            assert conn.execute(
                "select route_id from live_runs where id = %s", (run_id,)
            ).fetchone()["route_id"] is None

        # A foreign run id does not exist for this school: B cannot see it.
        foreign = client.put(
            f"/api/runs/{run_id}",
            json={"type": "morning", "status": "completed", "date": "2031-03-03"},
            headers=hdr(client, DIRECTOR_B, SCHOOL_B_ID),
        )
        assert foreign.status_code == 404, foreign.text
        assert audit_actions_for(run_id) == ["run-created"]
    finally:
        purge_run(run_id)
        purge_audit_for(run_id)


# --- AE27: the delete split --------------------------------------------------


def test_coordinator_deletes_an_open_run_and_the_route_is_startable_again(client):
    """A driver starts the wrong route; the coordinator cleans it up (204-ish)
    and the route is startable again — while a COMPLETED run's delete stays
    director-only, existing refusals included."""
    driver = pin_hdr(client, DRIVER_A_PIN)
    started = client.post(
        "/api/runs/driver/start", json={"route_id": ROUTE_A_MORNING}, headers=driver
    )
    assert started.status_code == 200, started.text
    run_id = str(started.json()["id"])
    second_run = None
    try:
        deleted = client.delete(
            f"/api/runs/{run_id}", headers=hdr(client, COORDINATOR_A, SCHOOL_A_ID)
        )
        assert deleted.status_code == 200, deleted.text
        with db() as conn:
            assert conn.execute(
                "select 1 from live_runs where id = %s", (run_id,)
            ).fetchone() is None
        assert audit_actions_for(run_id) == ["run-deleted"]

        # The route is startable again: the open-run block is gone.
        restarted = client.post(
            "/api/runs/driver/start", json={"route_id": ROUTE_A_MORNING}, headers=driver
        )
        assert restarted.status_code == 200, restarted.text
        second_run = str(restarted.json()["id"])
    finally:
        purge_run(second_run)
        purge_run(run_id)
        purge_audit_for(run_id)
        if second_run:
            purge_audit_for(second_run)


def test_completed_run_delete_is_director_only_and_refusals_stand(client):
    # Yesterday's completed run: the coordinator may not delete it — a
    # finished record is director territory (AE27) — while the director can.
    old_run = make_run(SCHOOL_A_ID, status="completed",
                       date_sql="current_date - 1", bus_id=BUS_A_SIMBA)
    evidence_run = None
    try:
        refused = client.delete(
            f"/api/runs/{old_run}", headers=hdr(client, COORDINATOR_A, SCHOOL_A_ID)
        )
        assert refused.status_code == 403, refused.text
        with db() as conn:
            assert conn.execute(
                "select 1 from live_runs where id = %s", (old_run,)
            ).fetchone() is not None

        deleted = client.delete(
            f"/api/runs/{old_run}", headers=hdr(client, DIRECTOR_A, SCHOOL_A_ID)
        )
        assert deleted.status_code == 200, deleted.text

        # Completed TODAY with recorded participation: the existing evidence
        # refusal stands even for the director.
        evidence_run = make_run(SCHOOL_A_ID, status="completed",
                                date_sql="current_date")
        with db() as conn:
            conn.execute(
                "insert into run_participation (run_id, student_id, student_name, boarded_at) "
                "values (%s, %s, 'IT U7 Evidence', now())",
                (evidence_run, STUDENT_A_FAITH_ID),
            )
        still_refused = client.delete(
            f"/api/runs/{evidence_run}", headers=hdr(client, DIRECTOR_A, SCHOOL_A_ID)
        )
        assert still_refused.status_code == 409, still_refused.text
        with db() as conn:
            assert conn.execute(
                "select 1 from live_runs where id = %s", (evidence_run,)
            ).fetchone() is not None
    finally:
        purge_run(evidence_run)
        purge_run(old_run)
        purge_audit_for(old_run)
        if evidence_run:
            purge_audit_for(evidence_run)


# --- AE3: force-close for the active school; foreign ids 404 ------------------


def test_coordinator_force_closes_a_stale_run_and_foreign_ids_answer_404(client):
    stale = make_run(SCHOOL_A_ID, status="in-progress", date_sql="current_date - 1")
    b_run = make_run(SCHOOL_B_ID, status="in-progress", date_sql="current_date - 1")
    try:
        # B's stale run does not exist for A's coordinator.
        foreign = client.post(
            f"/api/runs/{b_run}/force-close",
            headers=hdr(client, COORDINATOR_A, SCHOOL_A_ID),
        )
        assert foreign.status_code == 404, foreign.text
        with db() as conn:
            assert conn.execute(
                "select status from live_runs where id = %s", (b_run,)
            ).fetchone()["status"] == "in-progress"

        closed = client.post(
            f"/api/runs/{stale}/force-close",
            headers=hdr(client, COORDINATOR_A, SCHOOL_A_ID),
        )
        assert closed.status_code == 200, closed.text
        assert closed.json()["status"] == "completed"
        rows = audit_actions_for(stale)
        assert "run-force-closed" in rows
        with db() as conn:
            audit = conn.execute(
                "select school_id, actor_kind from live_admin_audit "
                "where resource_id = %s and action = 'run-force-closed'",
                (stale,),
            ).fetchone()
        assert str(audit["school_id"]) == SCHOOL_A_ID
        assert audit["actor_kind"] == "staff"

        # The run report reads through the scope too: B sees nothing.
        assert client.get(
            f"/api/runs/{stale}/report", headers=hdr(client, DIRECTOR_B, SCHOOL_B_ID)
        ).status_code == 404
        assert client.get(
            f"/api/runs/{stale}/report", headers=hdr(client, DIRECTOR_A, SCHOOL_A_ID)
        ).status_code == 200
    finally:
        purge_run(stale)
        purge_run(b_run)
        purge_audit_for(stale)
        purge_audit_for(b_run)


# --- incidents: stamped, scoped, per-school counts ----------------------------


def test_driver_incident_is_stamped_and_never_leaks_across_schools(client):
    driver_b = pin_hdr(client, DRIVER_B_PIN)
    reported = client.post(
        "/api/incidents/driver",
        json={"type": "traffic", "description": "IT U7 B-side jam"},
        headers=driver_b,
    )
    assert reported.status_code == 200, reported.text
    incident_id = str(reported.json()["id"])
    try:
        with db() as conn:
            row = conn.execute(
                "select school_id, bus_id from live_incidents where id = %s",
                (incident_id,),
            ).fetchone()
        assert str(row["school_id"]) == SCHOOL_B_ID
        assert str(row["bus_id"]) == BUS_B_ID

        listed_a = client.get(
            "/api/incidents", headers=hdr(client, DIRECTOR_A, SCHOOL_A_ID)
        )
        assert incident_id not in {str(i["id"]) for i in listed_a.json()}
        assert all(str(i["school_id"]) == SCHOOL_A_ID for i in listed_a.json())
        listed_b = client.get(
            "/api/incidents", headers=hdr(client, DIRECTOR_B, SCHOOL_B_ID)
        )
        assert incident_id in {str(i["id"]) for i in listed_b.json()}

        # Unread counts are per school: A's count does not include B's report.
        count_a = client.get(
            "/api/incidents/unread-count", headers=hdr(client, DIRECTOR_A, SCHOOL_A_ID)
        ).json()["count"]
        count_b = client.get(
            "/api/incidents/unread-count", headers=hdr(client, DIRECTOR_B, SCHOOL_B_ID)
        ).json()["count"]
        with db() as conn:
            expect_a = conn.execute(
                "select count(*) as n from live_incidents where school_id = %s "
                "and acknowledged = false and lifecycle = false",
                (SCHOOL_A_ID,),
            ).fetchone()["n"]
            expect_b = conn.execute(
                "select count(*) as n from live_incidents where school_id = %s "
                "and acknowledged = false and lifecycle = false",
                (SCHOOL_B_ID,),
            ).fetchone()["n"]
        assert count_a == expect_a
        assert count_b == expect_b
        assert count_b >= 1

        # Acknowledge threads the scope: A cannot acknowledge B's alert.
        foreign_ack = client.post(
            f"/api/incidents/{incident_id}/acknowledge",
            headers=hdr(client, DIRECTOR_A, SCHOOL_A_ID),
        )
        assert foreign_ack.status_code == 404, foreign_ack.text
        with db() as conn:
            assert conn.execute(
                "select acknowledged from live_incidents where id = %s",
                (incident_id,),
            ).fetchone()["acknowledged"] is False

        acked = client.post(
            f"/api/incidents/{incident_id}/acknowledge",
            headers=hdr(client, DIRECTOR_B, SCHOOL_B_ID),
        )
        assert acked.status_code == 200, acked.text

        # Deletes: coordinator refused (director-only, R8); a foreign id is
        # 404; B's own director removes it with the audit row.
        assert client.delete(
            f"/api/incidents/{incident_id}",
            headers=hdr(client, COORDINATOR_A, SCHOOL_A_ID),
        ).status_code == 403
        assert client.delete(
            f"/api/incidents/{incident_id}",
            headers=hdr(client, DIRECTOR_A, SCHOOL_A_ID),
        ).status_code == 404
        deleted = client.delete(
            f"/api/incidents/{incident_id}",
            headers=hdr(client, DIRECTOR_B, SCHOOL_B_ID),
        )
        assert deleted.status_code == 200, deleted.text
        with db() as conn:
            assert conn.execute(
                "select 1 from live_incidents where id = %s", (incident_id,)
            ).fetchone() is None
        assert audit_actions_for(incident_id) == [
            "incident-acknowledged", "incident-deleted",
        ]
    finally:
        with db() as conn:
            conn.execute("delete from live_incidents where id = %s", (incident_id,))
        purge_audit_for(incident_id)
