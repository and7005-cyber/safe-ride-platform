"""Period-scoped driver marks and precedence (plan 2026-07-28-001, U8/R17-R21).

A driver sees one run. Marking a child absent used to write a whole-day
absence from that single stop, which said two things the driver was in no
position to know: that the child was not travelling at all, and — because the
whole-day row gated both rosters — that the afternoon bus should not stop for
them either. A child who overslept and got a lift to school was struck off the
ride home, and the record blamed them for a stop nobody made.

The fix separates three facts that were previously one column's job:

- **scope** is coverage, and only ever widens.
- **source** is precedence — office, then parent, then driver — and a lower
  mark never re-attributes a higher one.
- **marked_period** is the witness: which period someone individually marked.
  It survives the collapse to 'day' that widening causes, and it is what the
  derivation keys on, because a driver at the stop is evidence about the child
  while a parent's partial cancellation is a statement of intent.

Run with the stack up: RUN_INTEGRATION=1 pytest tests/integration/test_absence_scoping.py -q
"""

import os
import random
import uuid

import httpx
import psycopg
import pytest

from conftest import purge_run

# Parent accounts come from signup; naming an email on a student only links a
# row, and without a linked account no notification is ever produced.
from test_students_parents import signup_parent

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="needs the local stack; set RUN_INTEGRATION=1",
)

BASE = os.environ.get("INTEGRATION_API_URL", "http://localhost:9001")
DSN = os.environ.get("DATABASE_URL", "postgresql://saferide:saferide@localhost:5432/saferide")
ADMIN = {"email": "admin@test.com", "password": "test1234."}


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE, timeout=30) as c:
        yield c


@pytest.fixture(scope="module")
def admin_headers(client):
    response = client.post("/api/auth/login", json=ADMIN)
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def absence_row(student_id: str) -> dict | None:
    with psycopg.connect(DSN, autocommit=True, row_factory=psycopg.rows.dict_row) as pg:
        return pg.execute(
            "select * from live_student_absences where student_id = %s "
            "and absence_date = (now() at time zone 'Africa/Nairobi')::date",
            (student_id,),
        ).fetchone()


def clear_absences(student_id: str) -> None:
    with psycopg.connect(DSN, autocommit=True) as pg:
        pg.execute(
            "delete from live_student_absences where student_id = %s", (student_id,)
        )
        pg.execute(
            "update live_students set status = 'at-school' where id = %s", (student_id,)
        )


def display_status(client, admin_headers, student_id: str) -> str:
    students = client.get("/api/students", headers=admin_headers).json()
    return next(s["display_status"] for s in students if s["id"] == student_id)


def notification_bodies(student_id: str, run_id: str) -> list[str]:
    """Scoped to the run: absence notifications survive the absence rows this
    module clears between tests, so an unscoped read returns the last test's."""
    with psycopg.connect(DSN, autocommit=True, row_factory=psycopg.rows.dict_row) as pg:
        rows = pg.execute(
            "select body from live_notifications "
            "where student_id = %s and run_id = %s and type = 'student-absent'",
            (student_id, run_id),
        ).fetchall()
    return [r["body"] for r in rows]


@pytest.fixture(scope="module")
def fleet(client, admin_headers):
    marker = uuid.uuid4().hex[:6]
    created: dict = {"marker": marker}
    pin = str(random.randint(100000, 999999))

    created["driver"] = client.post(
        "/api/accounts/drivers",
        json={"full_name": f"IT AS Driver {marker}", "email": f"it-as-drv-{marker}@test.local",
              "password": "test1234.", "phone": f"+2547{random.randint(10000000, 99999999)}",
              "pin": pin},
        headers=admin_headers,
    ).json()
    created["pin"] = pin

    created["bus"] = client.post(
        "/api/fleet/buses",
        json={"name": f"IT AS Bus {marker}", "capacity": 20,
              "driver_id": created["driver"]["id"]},
        headers=admin_headers,
    ).json()

    created["school"] = client.post(
        "/api/fleet/schools",
        json={"name": f"IT AS School {marker}", "lat": -1.29, "lng": 36.82},
        headers=admin_headers,
    ).json()

    for period in ("morning", "afternoon"):
        created[period] = client.post(
            "/api/fleet/routes",
            json={"name": f"IT AS {period} {marker}", "type": period,
                  "bus_id": created["bus"]["id"], "school_id": created["school"]["id"]},
            headers=admin_headers,
        ).json()

    created["students"] = []
    created["parent_ids"] = []
    created["parent_headers"] = []
    for n, (lat, lng) in enumerate([(-1.30, 36.79), (-1.31, 36.78)], start=1):
        parent_id, parent_email, parent_headers = signup_parent(client, marker, f"as-p{n}")
        created["parent_ids"].append(parent_id)
        created["parent_headers"].append(parent_headers)
        created["students"].append(client.post(
            "/api/students",
            json={"name": f"IT AS Kid{n} {marker}", "parent_name": f"IT AS Parent{n}",
                  "parent_phone": f"+2547{random.randint(10000000, 99999999)}",
                  "parent_email": parent_email,
                  "school_id": created["school"]["id"], "home_lat": lat, "home_lng": lng,
                  "pickup_time": "06:30",
                  "route_ids": [created["morning"]["id"], created["afternoon"]["id"]]},
            headers=admin_headers,
        ).json())

    try:
        yield created
    finally:
        for run in client.get("/api/runs", headers=admin_headers).json():
            if run.get("bus_id") == created["bus"]["id"]:
                purge_run(run["id"])
        for s in created["students"]:
            clear_absences(s["id"])
            client.delete(f"/api/students/{s['id']}", headers=admin_headers)
        for period in ("morning", "afternoon"):
            client.delete(f"/api/fleet/routes/{created[period]['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/buses/{created['bus']['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/schools/{created['school']['id']}", headers=admin_headers)
        client.delete(f"/api/accounts/drivers/{created['driver']['id']}", headers=admin_headers)
        for parent_id in created["parent_ids"]:
            client.delete(f"/api/accounts/parents/{parent_id}", headers=admin_headers)


@pytest.fixture
def driver_headers(client, fleet):
    token = client.post("/api/auth/pin-login", json={"pin": fleet["pin"]}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def clean_absences(fleet):
    """Absence rows are per-student per-day, so one test's mark is the next
    test's starting state. Cleared out-of-band before and after: the API clear
    refuses while a covered run is active, which is a guard these tests exercise
    rather than something they should route around."""
    for s in fleet["students"]:
        clear_absences(s["id"])
    yield
    for s in fleet["students"]:
        clear_absences(s["id"])


def start_run(client, driver_headers, fleet, period: str) -> str:
    started = client.post("/api/runs/driver/start",
                          json={"route_id": fleet[period]["id"]}, headers=driver_headers)
    assert started.status_code == 200, started.text
    return started.json()["id"]


def roster_student_ids(client, driver_headers) -> set[str]:
    context = client.get("/api/runs/driver/context", headers=driver_headers).json()
    return {str(s["student_id"]) for s in context["run_stops"] if s.get("student_id")}


def absent_flags(client, driver_headers) -> dict[str, bool]:
    """The absent flag the driver's own screen shows, per student.

    run_stops is snapshotted when the run starts, so a mid-run mark never
    removes a stop — it flags it. Roster membership is the right question only
    for a run that starts *after* the mark.
    """
    context = client.get("/api/runs/driver/context", headers=driver_headers).json()
    return {str(s["id"]): bool(s.get("absent")) for s in context["students"]}


# R17 — the mark is scoped to the period witnessed ------------------------------

def test_an_afternoon_mark_records_the_afternoon_only(
    client, admin_headers, fleet, driver_headers
):
    """R17: the driver was on the afternoon run. That is the only period they
    can speak to."""
    kid = fleet["students"][0]
    run_id = start_run(client, driver_headers, fleet, "afternoon")
    try:
        marked = client.post("/api/runs/driver/absent",
                             json={"student_id": kid["id"]}, headers=driver_headers)
        assert marked.status_code == 200, marked.text

        row = absence_row(kid["id"])
        assert row["scope"] == "afternoon", "the mark claimed more than the run"
        assert row["marked_period"] == "afternoon"
        assert row["source"] == "driver"
    finally:
        purge_run(run_id)


def test_a_morning_mark_leaves_the_afternoon_stop_on_the_route(
    client, admin_headers, fleet, driver_headers
):
    """R17: the failure this unit exists for. A child missing from the morning
    pickup used to be struck off the ride home too, so the afternoon bus never
    stopped for them — a child who got a lift to school was then stranded."""
    kid = fleet["students"][0]
    morning = start_run(client, driver_headers, fleet, "morning")
    afternoon = None
    try:
        marked = client.post("/api/runs/driver/absent",
                             json={"student_id": kid["id"]}, headers=driver_headers)
        assert marked.status_code == 200, marked.text
        assert absence_row(kid["id"])["scope"] == "morning"

        assert absent_flags(client, driver_headers).get(kid["id"]) is True, (
            "the morning run does not show the child the driver just marked"
        )
        purge_run(morning)
        morning = None

        afternoon = start_run(client, driver_headers, fleet, "afternoon")
        assert kid["id"] in roster_student_ids(client, driver_headers), (
            "a morning absence removed the child's afternoon stop"
        )
    finally:
        purge_run(morning)
        purge_run(afternoon)


def test_the_driver_can_confirm_the_whole_day(client, admin_headers, fleet, driver_headers):
    """R17: whole-day coverage is still reachable — it just has to be said,
    because it rests on something a parent told the driver rather than on
    anything the driver saw."""
    kid = fleet["students"][0]
    run_id = start_run(client, driver_headers, fleet, "morning")
    try:
        marked = client.post("/api/runs/driver/absent",
                             json={"student_id": kid["id"], "whole_day": True},
                             headers=driver_headers)
        assert marked.status_code == 200, marked.text

        row = absence_row(kid["id"])
        assert row["scope"] == "day"
        assert row["marked_period"] == "day"
    finally:
        purge_run(run_id)


# R18 — a period marking writes and clears the status ---------------------------

def test_a_period_marked_child_reads_absent(client, admin_headers, fleet, driver_headers):
    """R18: partial coverage, but it still shows. A driver at the stop is
    evidence about the child; a parent's partial cancellation is a statement of
    intent, and only the first makes the child absent on every surface."""
    kid = fleet["students"][0]
    run_id = start_run(client, driver_headers, fleet, "afternoon")
    try:
        client.post("/api/runs/driver/absent",
                    json={"student_id": kid["id"]}, headers=driver_headers)
        assert display_status(client, admin_headers, kid["id"]) == "absent"
    finally:
        purge_run(run_id)


def test_clearing_a_period_marked_absence_resets_the_child(
    client, admin_headers, fleet, driver_headers
):
    """R18: it wrote the status, so it has to clear it. Whole-day scope used to
    be the proxy for 'this row wrote the status' — under that rule the row was
    deleted while the child stayed 'absent' on every surface, with nothing left
    to explain why."""
    kid = fleet["students"][0]
    run_id = start_run(client, driver_headers, fleet, "afternoon")
    client.post("/api/runs/driver/absent",
                json={"student_id": kid["id"]}, headers=driver_headers)
    absence_id = str(absence_row(kid["id"])["id"])
    # The clear guard refuses while a covered run is active, with its own
    # justification — the run already snapshotted the roster without this child.
    purge_run(run_id)

    cleared = client.delete(f"/api/students/absences/{absence_id}", headers=admin_headers)
    assert cleared.status_code == 200, cleared.text
    assert display_status(client, admin_headers, kid["id"]) == "at-home"


# R19 — coverage widens, never narrows ------------------------------------------

def test_marking_both_periods_widens_to_the_whole_day(
    client, admin_headers, fleet, driver_headers
):
    """R19: absent from both runs is absent all day, and the row says so without
    either mark having claimed it alone."""
    kid = fleet["students"][0]
    morning = start_run(client, driver_headers, fleet, "morning")
    afternoon = None
    try:
        client.post("/api/runs/driver/absent",
                    json={"student_id": kid["id"]}, headers=driver_headers)
        purge_run(morning)
        morning = None

        afternoon = start_run(client, driver_headers, fleet, "afternoon")
        client.post("/api/runs/driver/absent",
                    json={"student_id": kid["id"]}, headers=driver_headers)

        row = absence_row(kid["id"])
        assert row["scope"] == "day", "coverage did not widen"
        assert row["marked_period"] == "day", "both periods were witnessed"
    finally:
        purge_run(morning)
        purge_run(afternoon)


# R20 — precedence is office, then parent, then driver --------------------------

def test_a_driver_mark_does_not_take_over_an_office_absence(
    client, admin_headers, fleet, driver_headers
):
    """R20: the office recorded this deliberately, with a reason. The driver at
    the stop is the last actor, not the authoritative one — the old ratchet ran
    the other way and let them overwrite it."""
    kid = fleet["students"][0]
    # Started first: a whole-day absence already on the books removes the child
    # from the roster snapshot, and the driver has nobody left to mark.
    run_id = start_run(client, driver_headers, fleet, "afternoon")
    try:
        office = client.post("/api/students/absences",
                             json={"student_id": kid["id"], "reason": "IT office reason"},
                             headers=admin_headers)
        assert office.status_code == 200, office.text

        client.post("/api/runs/driver/absent",
                    json={"student_id": kid["id"]}, headers=driver_headers)

        row = absence_row(kid["id"])
        assert row["source"] == "admin", "the driver took over the office's record"
        assert row["scope"] == "day", "coverage narrowed below the office's whole day"
        assert row["marked_period"] == "afternoon", "the driver's witness was lost"
    finally:
        purge_run(run_id)


def test_a_driver_mark_widens_a_parent_cancellation_without_claiming_it(
    client, admin_headers, fleet, driver_headers
):
    """R19 + R20 together: coverage becomes the whole day, the parent keeps the
    row, and the afternoon is recorded as what the driver actually witnessed."""
    kid = fleet["students"][0]
    parent_headers = fleet["parent_headers"][0]
    cancelled = client.post("/api/parent-portal/cancel-ride",
                            json={"student_id": kid["id"], "scope": "morning"},
                            headers=parent_headers)
    assert cancelled.status_code == 200, cancelled.text

    run_id = start_run(client, driver_headers, fleet, "afternoon")
    try:
        client.post("/api/runs/driver/absent",
                    json={"student_id": kid["id"]}, headers=driver_headers)

        row = absence_row(kid["id"])
        assert row["scope"] == "day"
        assert row["source"] == "parent", "the driver re-attributed the parent's row"
        assert row["marked_period"] == "afternoon"
        assert display_status(client, admin_headers, kid["id"]) == "absent", (
            "the driver's observation stopped showing once the source stayed parent"
        )
    finally:
        purge_run(run_id)


def test_a_parent_withdrawal_does_not_clear_what_the_driver_witnessed(
    client, admin_headers, fleet, driver_headers
):
    """R18 + R19: the parent withdraws their morning cancellation. The driver's
    afternoon observation is not theirs to retract, so coverage stops at the
    afternoon and the child stays absent."""
    kid = fleet["students"][0]
    parent_headers = fleet["parent_headers"][0]
    client.post("/api/parent-portal/cancel-ride",
                json={"student_id": kid["id"], "scope": "morning"}, headers=parent_headers)

    run_id = start_run(client, driver_headers, fleet, "afternoon")
    client.post("/api/runs/driver/absent",
                json={"student_id": kid["id"]}, headers=driver_headers)
    # Withdrawal is refused while a covered run is active; that guard is not
    # what this test is about.
    purge_run(run_id)

    withdrawn = client.request("DELETE", "/api/parent-portal/cancel-ride",
                               json={"student_id": kid["id"], "scope": "morning"},
                               headers=parent_headers)
    assert withdrawn.status_code == 200, withdrawn.text

    row = absence_row(kid["id"])
    assert row is not None, "withdrawing the morning deleted the driver's afternoon"
    assert row["scope"] == "afternoon", "coverage narrowed below the marked period"
    assert row["marked_period"] == "afternoon"
    assert display_status(client, admin_headers, kid["id"]) == "absent", (
        "the parent's withdrawal cleared a status the driver's mark wrote"
    )


def test_a_parent_cannot_withdraw_the_period_the_driver_witnessed(
    client, admin_headers, fleet, driver_headers
):
    """R19: the driver was at the afternoon stop and the child was not. A parent
    changing their own plans does not make that untrue, so withdrawal cannot cut
    coverage below it — and the refusal says which run and who recorded it,
    rather than blaming a race the parent cannot see."""
    kid = fleet["students"][0]
    parent_headers = fleet["parent_headers"][0]
    # Cancels the morning, so the child is still on the afternoon roster for the
    # driver to mark. Widening leaves scope='day' with the afternoon witnessed.
    client.post("/api/parent-portal/cancel-ride",
                json={"student_id": kid["id"], "scope": "morning"}, headers=parent_headers)

    run_id = start_run(client, driver_headers, fleet, "afternoon")
    marked = client.post("/api/runs/driver/absent",
                         json={"student_id": kid["id"]}, headers=driver_headers)
    assert marked.status_code == 200, marked.text
    assert absence_row(kid["id"])["marked_period"] == "afternoon"
    purge_run(run_id)

    # Withdrawing the afternoon would leave 'morning' — below what was witnessed.
    refused = client.request("DELETE", "/api/parent-portal/cancel-ride",
                             json={"student_id": kid["id"], "scope": "afternoon"},
                             headers=parent_headers)
    assert refused.status_code == 409, refused.text
    detail = refused.json()["detail"]
    assert "driver recorded" in detail, detail
    assert "afternoon" in detail, detail
    assert absence_row(kid["id"]) is not None, "the row was withdrawn anyway"


# R21 — the record says which period ---------------------------------------------

def test_the_run_report_distinguishes_the_two_failures(
    client, admin_headers, fleet, driver_headers
):
    """R21: an afternoon non-boarding and a morning no-show are the same child
    on the same day and mean completely different things. Without the period the
    office cannot act on either."""
    kid = fleet["students"][0]
    run_id = start_run(client, driver_headers, fleet, "afternoon")
    try:
        client.post("/api/runs/driver/absent",
                    json={"student_id": kid["id"]}, headers=driver_headers)

        report = client.get(f"/api/runs/{run_id}/report", headers=admin_headers).json()
        entry = next(a for a in report["absent_students"] if a["student_id"] == kid["id"])
        assert entry["period"] == "afternoon"
    finally:
        purge_run(run_id)


def test_the_absence_list_carries_the_marked_period(
    client, admin_headers, fleet, driver_headers
):
    """R21: the office's own list has to make the same distinction the report
    does, or the two disagree about the same child."""
    kid = fleet["students"][0]
    run_id = start_run(client, driver_headers, fleet, "morning")
    try:
        client.post("/api/runs/driver/absent",
                    json={"student_id": kid["id"]}, headers=driver_headers)

        rows = client.get("/api/students/absences", headers=admin_headers).json()
        entry = next(a for a in rows if a["student_id"] == kid["id"])
        assert entry["marked_period"] == "morning"
        assert entry["scope"] == "morning"
    finally:
        purge_run(run_id)


def test_the_parent_message_states_the_period_and_no_more(
    client, admin_headers, fleet, driver_headers
):
    """R21: the message used to say the child "will not board the bus today"
    off a single morning run — telling a parent their child was not coming home
    either, which the driver had no way of knowing."""
    kid = fleet["students"][0]
    run_id = start_run(client, driver_headers, fleet, "morning")
    try:
        client.post("/api/runs/driver/absent",
                    json={"student_id": kid["id"]}, headers=driver_headers)

        bodies = notification_bodies(kid["id"], run_id)
        assert bodies, "no message reached the parent"
        body = bodies[0]
        assert "morning" in body.lower(), body
        assert "today" not in body.lower(), f"claimed the whole day: {body}"
    finally:
        purge_run(run_id)
