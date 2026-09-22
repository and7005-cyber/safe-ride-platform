"""Per-school thresholds and School Settings (GPS plan U11: R26, R31, R38).

Run with the stack up (scripts/start-local.sh, then scripts/sync-api.sh):

    RUN_INTEGRATION=1 ../.venv/bin/python -m pytest tests/integration/test_school_settings_thresholds.py -q

- A school with a 300 m custody threshold does not flag the 250 m Board a
  default school flags — driven through the driver API with fixes; the same
  read-side rule for "bus seen at stop" follows the school's vicinity radius
  with no new write.
- ``PUT /api/fleet/schools/{id}``: a knob omitted from the body keeps its
  stored value (an older Settings page cannot wipe them), an explicit null
  clears it to the default; ``GET /api/fleet/school`` serves the stored five
  (null when unset) beside ``tracking_defaults``.
- One ``school-updated`` audit row per save that changed something, with the
  old and new value of every changed column; a save that changes nothing
  writes no row.
- The driver context serves ``config`` — fix-wait budget, accuracy cap, ping
  interval — resolved per school on every poll, and the resolved cap is the
  one a tap's fix is normalised against.
- Only staff can edit (driver and parent 403); out-of-range values are 422
  from the payload, before any SQL — the column CHECK is never the first
  line of defence — and leave the row and the audit untouched.

Isolation: an own throwaway school (school_sandbox) with one driver, one bus,
one morning route and three students at their own stops. Every run a test
starts is purged in a finally block; every knob a test sets is cleared again.
"""

import os
import uuid

import httpx
import psycopg
import pytest
from psycopg.rows import dict_row

from conftest import DSN, purge_accounts, purge_run, school_sandbox
from test_gps_actions import (
    CUSTODY_AWAY,
    _create_driver,
    arrive,
    arrive_until,
    board,
    context,
    exceptions,
    fix,
    layout,
    login,
    near_stop,
    pending,
    pin_login,
    respond,
    start,
    trail,
)
from test_students_parents import signup_parent

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="needs the local stack; set RUN_INTEGRATION=1",
)

BASE = os.environ.get("INTEGRATION_API_URL", "http://localhost:9001")
SCHOOL_LAT, SCHOOL_LNG = -1.30, 36.80
KNOBS = (
    "custody_threshold_m", "vicinity_radius_m", "fix_accuracy_cap_m",
    "position_retention_days", "ping_interval_s",
)
DEFAULTS = {
    "custody_threshold_m": 150, "vicinity_radius_m": 100, "fix_accuracy_cap_m": 200,
    "position_retention_days": 90, "ping_interval_s": 10,
}
BOUNDS = {
    "custody_threshold_m": (25, 2000), "vicinity_radius_m": (25, 2000),
    "fix_accuracy_cap_m": (25, 2000), "position_retention_days": (7, 365),
    "ping_interval_s": (5, 60),
}


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE, timeout=30) as c:
        yield c


def db():
    return psycopg.connect(DSN, autocommit=True, row_factory=dict_row)


@pytest.fixture(scope="module")
def sandbox():
    with school_sandbox("IT Thresholds School", lat=SCHOOL_LAT, lng=SCHOOL_LNG) as sb:
        yield sb


@pytest.fixture(scope="module")
def admin_headers(client, sandbox):
    return login(client, sandbox["email"], sandbox["password"])


@pytest.fixture(scope="module")
def fleet(client, admin_headers, sandbox):
    marker = uuid.uuid4().hex[:6]
    driver = _create_driver(client, admin_headers, f"thr{marker}", 1)
    created: dict = {"bus": None, "route": None, "students": [], "parent_id": None}
    try:
        bus = client.post(
            "/api/fleet/buses",
            json={"name": f"IT Thr Bus {marker}", "driver_id": driver["id"]},
            headers=admin_headers,
        ).json()
        created["bus"] = bus["id"]
        route = client.post(
            "/api/fleet/routes",
            json={"name": f"IT Thr Morning {marker}", "type": "morning", "bus_id": bus["id"]},
            headers=admin_headers,
        )
        assert route.status_code == 200, route.text
        created["route"] = route.json()["id"]
        students = []
        for tag, lat, pickup in (("A", -1.26, "06:20"), ("B", -1.27, "06:30"), ("C", -1.28, "06:40")):
            response = client.post(
                "/api/students",
                json={
                    "name": f"IT Thr Kid{tag} {marker}", "parent_name": f"IT Thr Parent{tag}",
                    "parent_phone": f"+2547120004{ord(tag) % 90:02d}",
                    "parent_email": f"it-thr-p{tag.lower()}-{marker}@test.local",
                    "home_lat": lat, "home_lng": 36.79, "pickup_time": pickup,
                    "route_ids": [created["route"]],
                },
                headers=admin_headers,
            )
            assert response.status_code == 200, response.text
            students.append(response.json())
            created["students"].append(response.json()["id"])
        parent_id, _email, parent_headers = signup_parent(client, marker, "thr-parent")
        created["parent_id"] = parent_id
        yield {
            "school_id": sandbox["id"], "driver": driver,
            "driver_headers": pin_login(client, driver["pin"]),
            "parent_headers": parent_headers,
            "bus": bus, "route": route.json(), "students": students,
        }
    finally:
        for run in client.get("/api/runs", headers=admin_headers).json():
            if run.get("bus_id") == created["bus"]:
                purge_run(run["id"])
        for sid in created["students"]:
            client.delete(f"/api/students/{sid}", headers=admin_headers)
        if created["route"]:
            client.delete(f"/api/fleet/routes/{created['route']}", headers=admin_headers)
        if created["bus"]:
            client.delete(f"/api/fleet/buses/{created['bus']}", headers=admin_headers)
        client.delete(f"/api/accounts/drivers/{driver['id']}", headers=admin_headers)
        purge_accounts(created["parent_id"])


# Helpers ----------------------------------------------------------------------


def get_school(client, headers) -> dict:
    response = client.get("/api/fleet/school", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def base_fields(school: dict) -> dict:
    """What a pre-U11 Settings page sends: the base columns only."""
    return {
        key: school.get(key)
        for key in ("name", "address", "phone", "lat", "lng", "morning_bell", "afternoon_bell")
    }


def put_school(client, headers, school_id: str, body: dict) -> httpx.Response:
    return client.put(f"/api/fleet/schools/{school_id}", json=body, headers=headers)


def set_knobs(client, headers, school_id: str, **knobs) -> dict:
    """PUT the base fields as stored plus exactly the knobs named."""
    school = get_school(client, headers)
    response = put_school(client, headers, school_id, {**base_fields(school), **knobs})
    assert response.status_code == 200, response.text
    return response.json()


def clear_knobs(client, headers, school_id: str) -> None:
    set_knobs(client, headers, school_id, **{knob: None for knob in KNOBS})


def school_row(school_id: str) -> dict:
    with db() as conn:
        return conn.execute(
            f"select {', '.join(KNOBS)}, name, address from live_schools where id = %s",  # noqa: S608
            (school_id,),
        ).fetchone()


def school_audits(school_id: str) -> list[dict]:
    with db() as conn:
        return conn.execute(
            "select detail from live_admin_audit where school_id = %s and action = 'school-updated' "
            "order by created_at, id",
            (school_id,),
        ).fetchall()


def ordered_students(run_id: str, students: list[dict]) -> list[tuple[dict, int, tuple]]:
    """(student, stop_order, stop coords) in stop order — so the first child
    tapped is at the first stop and no Arrive passes a stop without an
    outcome (which would raise U2's bypass prompt and exempt the resolution
    tap from the custody check)."""
    plan = layout(run_id)
    rows = [(s, plan["by_student"][s["id"]], plan["coords"][plan["by_student"][s["id"]]]) for s in students]
    return sorted(rows, key=lambda r: r[1])


def arrive_near(client, headers, run_id: str, stop_order: int, coords: tuple, *, north_m: float, accuracy: float):
    arrive_until(client, headers, run_id, stop_order - 1)
    response = arrive(
        client, headers, run_id, expected=stop_order,
        fix_body=near_stop(coords, north_m=north_m, accuracy=accuracy),
    )
    assert response.status_code == 200, response.text


def custody_prompts(client, headers) -> list[dict]:
    return [p for p in pending(client, headers) if p["kind"] == CUSTODY_AWAY]


# The custody threshold and the vicinity radius through the driver API -------------


def test_a_300_m_school_does_not_flag_the_250_m_board_a_default_school_flags(
    client, admin_headers, fleet,
):
    """The same 250 m tap (238 m after the 12 m accuracy comes off): flagged
    under the 150 m default, silent once the school sets 300 m — the DAO
    resolves the school's value per tap, so the change needs no restart and
    no new run. "Bus seen at stop" (an Arrive fix 60 m from the stop) reads
    yes under the 100 m default vicinity and no once the school narrows it
    to 25 m — derived on read, no write."""
    h = fleet["driver_headers"]
    school_id = fleet["school_id"]
    run_id = start(client, h, fleet["route"]["id"], fix_body=fix()).json()["id"]
    try:
        (first, first_order, first_stop), (second, second_order, second_stop), *_ = ordered_students(
            run_id, fleet["students"]
        )

        # Default school: 250 m away is `away`.
        arrive_near(client, h, run_id, first_order, first_stop, north_m=80, accuracy=20)
        flagged = board(client, h, first["id"], near_stop(first_stop, north_m=250))
        assert flagged.status_code == 200, flagged.text
        prompts = custody_prompts(client, h)
        assert len(prompts) == 1 and prompts[0]["student_id"] == first["id"], prompts
        assert prompts[0]["distance_m"] == pytest.approx(250, abs=5)
        rows = exceptions(client, admin_headers, run_id, CUSTODY_AWAY)
        assert len(rows) == 1 and rows[0]["stop_order"] == first_order
        assert rows[0]["seen_at_stop"] is True, "the 60 m Arrive fix is inside the 100 m default"
        assert respond(client, h, prompts[0]["event_id"], "confirmed").status_code == 200

        # The school widens custody to 300 m: the same tap is `within`.
        assert set_knobs(client, admin_headers, school_id, custody_threshold_m=300)["custody_threshold_m"] == 300
        arrive_near(client, h, run_id, second_order, second_stop, north_m=80, accuracy=20)
        silent = board(client, h, second["id"], near_stop(second_stop, north_m=250))
        assert silent.status_code == 200, silent.text
        assert custody_prompts(client, h) == []
        rows = exceptions(client, admin_headers, run_id, CUSTODY_AWAY)
        assert [r["stop_order"] for r in rows] == [first_order], "still only the default-era row"
        assert tuple(trail(run_id)[-1]["flags"]) == ()

        # The school narrows the vicinity to 25 m: the stored row's "seen at
        # stop" re-derives to no — 60 m is outside — and back with the default.
        set_knobs(client, admin_headers, school_id, vicinity_radius_m=25)
        assert exceptions(client, admin_headers, run_id, CUSTODY_AWAY)[0]["seen_at_stop"] is False
        set_knobs(client, admin_headers, school_id, vicinity_radius_m=None)
        assert exceptions(client, admin_headers, run_id, CUSTODY_AWAY)[0]["seen_at_stop"] is True
    finally:
        purge_run(run_id)
        clear_knobs(client, admin_headers, school_id)


# Omitted vs null, the defaults on GET, the audited diff -----------------------------


def test_omitted_knobs_keep_their_value_null_clears_and_every_change_is_audited(
    client, admin_headers, fleet,
):
    school_id = fleet["school_id"]
    try:
        school = get_school(client, admin_headers)
        assert {knob: school[knob] for knob in KNOBS} == {knob: None for knob in KNOBS}
        assert school["tracking_defaults"] == DEFAULTS
        audits_before = len(school_audits(school_id))

        # Two knobs set: stored, served, audited old -> new.
        saved = set_knobs(client, admin_headers, school_id, custody_threshold_m=300, position_retention_days=30)
        assert (saved["custody_threshold_m"], saved["position_retention_days"]) == (300, 30)
        assert saved["tracking_defaults"] == DEFAULTS
        assert (saved["vicinity_radius_m"], saved["fix_accuracy_cap_m"], saved["ping_interval_s"]) == (None, None, None)
        audits = school_audits(school_id)
        assert len(audits) == audits_before + 1
        assert audits[-1]["detail"] == {
            "changes": {
                "custody_threshold_m": {"old": None, "new": 300},
                "position_retention_days": {"old": None, "new": 30},
            }
        }

        # An older Settings page: base fields only, no knob named — nothing
        # is wiped, and nothing changed means no audit row.
        older = put_school(client, admin_headers, school_id, base_fields(get_school(client, admin_headers)))
        assert older.status_code == 200, older.text
        row = school_row(school_id)
        assert (row["custody_threshold_m"], row["position_retention_days"]) == (300, 30)
        assert len(school_audits(school_id)) == audits_before + 1

        # The same values again: no audit row either.
        set_knobs(client, admin_headers, school_id, custody_threshold_m=300, position_retention_days=30)
        assert len(school_audits(school_id)) == audits_before + 1

        # Explicit null clears ONE knob to the default; the other stays.
        cleared = set_knobs(client, admin_headers, school_id, custody_threshold_m=None)
        assert cleared["custody_threshold_m"] is None and cleared["position_retention_days"] == 30
        audits = school_audits(school_id)
        assert len(audits) == audits_before + 2
        assert audits[-1]["detail"] == {"changes": {"custody_threshold_m": {"old": 300, "new": None}}}

        # A base-field change rides the same shape.
        renamed = set_knobs(client, admin_headers, school_id, address="IT Lane 2")
        assert renamed["address"] == "IT Lane 2"
        audits = school_audits(school_id)
        assert len(audits) == audits_before + 3
        assert audits[-1]["detail"]["changes"]["address"]["new"] == "IT Lane 2"
        assert set(audits[-1]["detail"]["changes"]) == {"address"}
    finally:
        clear_knobs(client, admin_headers, school_id)


# The driver context's config and the cap the fix is normalised against ---------------


def test_the_driver_context_serves_the_resolved_config_and_the_cap_reaches_the_fix(
    client, admin_headers, fleet,
):
    from app.core.config import get_settings

    settings = get_settings()
    defaults = {
        "fix_wait_budget_s": settings.gps_fix_wait_budget_s,
        "fix_accuracy_cap_m": settings.gps_fix_accuracy_cap_m,
        "ping_interval_s": settings.gps_ping_interval_s,
    }
    assert defaults == {"fix_wait_budget_s": 5, "fix_accuracy_cap_m": 200, "ping_interval_s": 10}
    h = fleet["driver_headers"]
    school_id = fleet["school_id"]
    assert context(client, h)["config"] == defaults

    run_id = start(client, h, fleet["route"]["id"], fix_body=fix(accuracy=300.0)).json()["id"]
    try:
        assert trail(run_id)[-1]["fix_reason"] == "coarse", "300 m is above the 200 m default cap"

        set_knobs(client, admin_headers, school_id, fix_accuracy_cap_m=500, ping_interval_s=20)
        # The next poll carries the school's values; the budget stays system-wide.
        assert context(client, h)["config"] == {
            "fix_wait_budget_s": 5, "fix_accuracy_cap_m": 500, "ping_interval_s": 20,
        }
        response = arrive(client, h, run_id, expected=1, fix_body=fix(accuracy=300.0))
        assert response.status_code == 200, response.text
        assert trail(run_id)[-1]["fix_reason"] == "none", "300 m is inside the school's 500 m cap"

        set_knobs(client, admin_headers, school_id, fix_accuracy_cap_m=None, ping_interval_s=None)
        assert context(client, h)["config"] == defaults
    finally:
        purge_run(run_id)
        clear_knobs(client, admin_headers, school_id)


# Who may edit, and the bounds before the database ------------------------------------


def test_only_staff_edit_and_out_of_range_values_are_refused_before_any_sql(
    client, admin_headers, fleet,
):
    school_id = fleet["school_id"]
    school = get_school(client, admin_headers)
    body = {**base_fields(school), "custody_threshold_m": 300}
    row_before = school_row(school_id)
    audits_before = len(school_audits(school_id))

    # A driver and a parent are not staff: 403, nothing written.
    assert put_school(client, fleet["driver_headers"], school_id, body).status_code == 403
    assert put_school(client, fleet["parent_headers"], school_id, body).status_code == 403

    # Out of range is the payload's 422 — a pydantic bound, not a CHECK
    # violation surfacing as a 500 or a 409 — for every knob and both ends;
    # a fraction and a word are 422 too.
    for knob, (lo, hi) in BOUNDS.items():
        for bad in (lo - 1, hi + 1, lo + 0.5, "abc"):
            response = put_school(client, admin_headers, school_id, {**base_fields(school), knob: bad})
            assert response.status_code == 422, (knob, bad, response.text)
            detail = response.json()["detail"]
            assert detail[0]["loc"][-1] == knob, detail
            assert detail[0]["type"] in (
                "greater_than_equal", "less_than_equal", "int_from_float", "int_parsing",
            ), detail
        # The bounds themselves are accepted.
        for good in (lo, hi):
            assert put_school(
                client, admin_headers, school_id, {**base_fields(school), knob: good}
            ).status_code == 200
    clear_knobs(client, admin_headers, school_id)

    # Only the accepted writes touched the row; the refused ones left no trace.
    assert school_row(school_id) == row_before
    audits = school_audits(school_id)
    # Per knob two audited saves (lo from None, then hi), then ONE clear that
    # nulls all five in a single save — one row carrying five changes.
    assert len(audits) == audits_before + 2 * len(BOUNDS) + 1
    assert all("changes" in a["detail"] for a in audits[audits_before:])
    assert set(audits[-1]["detail"]["changes"]) == set(BOUNDS)
    assert all(c["new"] is None for c in audits[-1]["detail"]["changes"].values())
