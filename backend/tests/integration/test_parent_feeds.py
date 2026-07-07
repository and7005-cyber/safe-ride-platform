"""Parent feed windows and derived display_status (U15; R35/R36, AE10/AE11).

Run with the stack up (scripts/start-local.sh):

    RUN_INTEGRATION=1 ../.venv/bin/python -m pytest tests/integration -q

These tests certify the windowed notification/alert feeds (window_hours +
min_age_hours + limit with the 200 hard cap) and the parent-portal
display_status derivation (today-absence, stale dropped-off). They use the
seeded demo data (backend/db/seeds/003_local_snapshot.sql) and clean up what
they create; runs they start are always ended or deleted.

Also covers the disjoint Recent/History split (ops-refinement U9: R5–R7,
AE2): min_age_hours on both list endpoints excludes rows younger than that
age server-side, BEFORE the 200-row cap, and both hour params are bounded at
8760 (beyond it → 422; unbounded values used to reach interval arithmetic
and 500).

created_at cannot be back-dated over HTTP (no API exposes it), so window
assertions lean on three sources of aged rows:
- the seed's own notifications, all dated weeks in the past — they prove the
  exclusion side of window_hours;
- freshly generated rows (driver incidents) — they prove the inclusion side;
- rows back-dated by direct SQL against the stack's published Postgres (the
  suite's accepted escape hatch — see test_students_parents.py) — they stage
  ages *between* the windows for the U9 disjointness and cap-starvation
  proofs, and are always deleted in finally blocks.
"""

import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import psycopg
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="needs the local stack; set RUN_INTEGRATION=1",
)

BASE = os.environ.get("INTEGRATION_API_URL", "http://localhost:9001")
# The local stack's Postgres, published by docker-compose.local.yml. Used only
# to back-date created_at (no API exposes it) and to prove excluded ≠ deleted.
DB_URL = os.environ.get(
    "INTEGRATION_DB_URL", "postgresql://saferide:saferide@localhost:5432/saferide"
)

ADMIN = {"email": "admin@test.com", "password": "test1234."}
PARENT = {"email": "and7005@gmail.com", "password": "Test1234"}
DRIVER_PIN = "0322"

# Seeded fixtures (backend/db/seeds/003_local_snapshot.sql). On seed drift,
# update these constants instead of individual tests.
PARENT_CHILD = "Faith Achieng"  # rides Simba morning AND afternoon (run_stops roster)
PARENT_BUSLESS_CHILD = "Grace Njeri"  # bus-less: no route, no run can ever touch her

# Africa/Nairobi is UTC+3 year-round (no DST), so "today" is deterministic.
NAIROBI_OFFSET = timedelta(hours=3)


def nairobi_today() -> str:
    return (datetime.now(timezone.utc) + NAIROBI_OFFSET).date().isoformat()


def _ts(value: str) -> datetime:
    """Parse an API created_at into an aware UTC datetime."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE, timeout=20) as c:
        yield c


def login(client: httpx.Client, email: str, password: str) -> dict:
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


@pytest.fixture(scope="module")
def admin_headers(client):
    return login(client, ADMIN["email"], ADMIN["password"])


@pytest.fixture(scope="module")
def parent_headers(client):
    return login(client, PARENT["email"], PARENT["password"])


@pytest.fixture(scope="module")
def driver_headers(client):
    response = client.post("/api/auth/pin-login", json={"pin": DRIVER_PIN})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


@pytest.fixture()
def no_runs_today(client, admin_headers, driver_headers):
    """End the driver's active run and delete today's runs for their bus,
    before and after: display_status derivations need a known-clean run slate,
    and deleting completed runs keeps the completed-today start gating (U6)
    from blocking repeated lifecycle tests."""

    def sweep():
        context = client.get("/api/runs/driver/context", headers=driver_headers).json()
        active = context.get("active_run")
        if active:
            client.post(
                "/api/runs/driver/end", headers=driver_headers, json={"run_id": active["id"]}
            )
        bus = context.get("bus") or {}
        today = nairobi_today()
        for run in client.get("/api/runs", headers=admin_headers).json():
            if str(run.get("bus_id")) == str(bus.get("id")) and str(run.get("date")) == today:
                client.delete(f"/api/runs/{run['id']}", headers=admin_headers)

    sweep()
    yield
    sweep()


def get_child(client, parent_headers, name: str) -> dict:
    children = client.get("/api/parent-portal/children", headers=parent_headers).json()
    return next(c for c in children if c["name"] == name)


def run_morning_and_end(client, driver_headers) -> None:
    """Start and immediately end a morning run: the end-run sweep normalizes
    the roster back to at-school, restoring the seeded state."""
    context = client.get("/api/runs/driver/context", headers=driver_headers).json()
    morning = next(r for r in context["routes"] if r["type"] == "morning")
    started = client.post(
        "/api/runs/driver/start", json={"route_id": morning["id"]}, headers=driver_headers
    )
    if started.status_code == 200:
        client.post(
            "/api/runs/driver/end",
            json={"run_id": started.json()["id"]},
            headers=driver_headers,
        )


# Feed windows (R35, AE10) -----------------------------------------------------

def test_window_24h_excludes_rows_older_than_the_window(client, parent_headers):
    """The seed's notifications are weeks old: they appear without a window
    and disappear with window_hours=24."""
    full = client.get(
        "/api/push/notifications", params={"limit": 200}, headers=parent_headers
    ).json()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    old_ids = {n["id"] for n in full if _ts(n["created_at"]) < cutoff}
    if not old_ids:
        pytest.skip("feed has no rows older than 24h to exclude (re-seed the local DB)")

    windowed = client.get(
        "/api/push/notifications",
        params={"window_hours": 24, "limit": 200},
        headers=parent_headers,
    ).json()
    windowed_ids = {n["id"] for n in windowed}
    assert not (old_ids & windowed_ids), "window_hours=24 returned rows older than 24h"
    assert all(_ts(n["created_at"]) >= cutoff for n in windowed)


def test_fresh_row_is_inside_both_windows_and_feed_is_newest_first(
    client, parent_headers, driver_headers
):
    """Inclusion side: a just-created incident notification shows up with
    window_hours=24 and window_hours=168, and ordering stays newest-first."""
    marker = f"IT window incident {uuid.uuid4().hex[:6]}"
    reported = client.post(
        "/api/incidents/driver",
        json={"type": "traffic", "description": marker},
        headers=driver_headers,
    )
    assert reported.status_code == 200, reported.text

    # BackgroundTasks deliver after the response; poll briefly.
    deadline = time.time() + 10
    entry = None
    while time.time() < deadline and entry is None:
        feed = client.get(
            "/api/push/notifications", params={"window_hours": 24}, headers=parent_headers
        ).json()
        entry = next((n for n in feed if n["body"] == marker), None)
        time.sleep(0.5)
    assert entry is not None, "fresh notification missing from the 24h window"

    history = client.get(
        "/api/push/notifications", params={"window_hours": 168}, headers=parent_headers
    ).json()
    assert any(n["body"] == marker for n in history)

    stamps = [_ts(n["created_at"]) for n in history]
    assert stamps == sorted(stamps, reverse=True), "feed is not newest-first"

    # The incidents feed accepts the same params and carries the fresh row.
    alerts = client.get(
        "/api/parent-portal/alerts",
        params={"window_hours": 24, "limit": 200},
        headers=parent_headers,
    ).json()
    assert any(a["description"] == marker for a in alerts)


# Disjoint Recent/History (ops-refinement U9: R5–R7, AE2) -----------------------
# These replace two former skip placeholders ("needs an orchestrator-side SQL
# fixture"): the fixture is now in-module — back-dated rows via direct SQL,
# always deleted in finally.

@pytest.fixture(scope="module")
def throwaway_parent(client):
    """A fresh parent account whose notification feed starts EMPTY and can
    only ever contain rows this suite stages — the seeded parent's feed
    accretes hundreds of real last-24h rows from the other suites' run
    lifecycles, which would truncate the 200-row responses these partition
    assertions compare (signup precedent: test_route_broadcast.py)."""
    email = f"it-u9-{uuid.uuid4().hex[:6]}@test.local"
    response = client.post(
        "/api/auth/signup",
        json={"email": email, "password": "ParentPass1!",
              "full_name": "IT U9 Feed Parent", "role": "parent"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    yield {"id": body["user"]["id"], "headers": {"Authorization": f"Bearer {body['token']}"}}
    with psycopg.connect(DB_URL, autocommit=True) as conn:
        # Notifications, roles and sessions cascade with the user row.
        conn.execute("delete from app_users where id = %s", (body["user"]["id"],))


@pytest.fixture(scope="module")
def child_bus(client, parent_headers):
    """The seeded child's bus (id + name) for staging parent-visible incidents."""
    kid = get_child(client, parent_headers, PARENT_CHILD)
    with psycopg.connect(DB_URL, autocommit=True) as conn:
        row = conn.execute(
            "select s.bus_id, b.name from live_students s "
            "join live_buses b on b.id = s.bus_id where s.id = %s",
            (kid["id"],),
        ).fetchone()
    assert row and row[0], f"seeded child {PARENT_CHILD} has no bus"
    return {"bus_id": str(row[0]), "bus_name": row[1]}


def backdate_notification(user_id: str, title: str, hours: int) -> str:
    """Insert a feed row aged `hours` into the past; returns its id."""
    with psycopg.connect(DB_URL, autocommit=True) as conn:
        row = conn.execute(
            """
            insert into live_notifications (user_id, type, title, body, created_at)
            values (%s, 'custom', %s, 'IT backdated', now() - (%s || ' hours')::interval)
            returning id
            """,
            (user_id, title, hours),
        ).fetchone()
    return str(row[0])


def backdate_incident(bus: dict, description: str, hours: int) -> str:
    """Insert a parent-visible incident (student_id NULL) aged `hours` back."""
    with psycopg.connect(DB_URL, autocommit=True) as conn:
        row = conn.execute(
            """
            insert into live_incidents (bus_id, bus_name, type, description, created_at)
            values (%s, %s, 'other', %s, now() - (%s || ' hours')::interval)
            returning id
            """,
            (bus["bus_id"], bus["bus_name"], description, hours),
        ).fetchone()
    return str(row[0])


def delete_rows(table: str, ids: list[str]) -> None:
    if not ids:
        return
    with psycopg.connect(DB_URL, autocommit=True) as conn:
        conn.execute(f"delete from {table} where id = any(%s::uuid[])", (ids,))


def fetch_id_set(client, headers, path: str, params: dict) -> set:
    response = client.get(path, params=params, headers=headers)
    assert response.status_code == 200, response.text
    rows = response.json()
    # The partition assertions below compare whole responses; at the cap they
    # would be comparing truncations. The flood test cleans up after itself,
    # so hitting 200 here means stale fixture rows leaked.
    assert len(rows) < 200, f"{path} at the row cap; partition asserts unsafe"
    return {r["id"] for r in rows}


def assert_disjoint_split(client, headers, path: str, ids: dict) -> None:
    """The AE2 contract, shared by both feeds: Recent (window 24) sees only
    the 23h row, History (min_age 24 + window 168) only the 25h row, the 8d
    row neither; the legacy shape (window 168, NO min_age) is untouched by
    this feature and still spans both sides — Recent and History partition
    it exactly (disjoint, jointly exhaustive)."""
    recent = fetch_id_set(client, headers, path, {"window_hours": 24, "limit": 200})
    history = fetch_id_set(
        client, headers, path, {"window_hours": 168, "min_age_hours": 24, "limit": 200}
    )
    legacy = fetch_id_set(client, headers, path, {"window_hours": 168, "limit": 200})

    assert ids["23h"] in recent and ids["23h"] not in history
    assert ids["25h"] in history and ids["25h"] not in recent
    assert ids["8d"] not in recent and ids["8d"] not in history
    # Omitted min_age_hours → today's behavior: the 168h window keeps rows
    # younger than 24h too.
    assert ids["23h"] in legacy and ids["25h"] in legacy and ids["8d"] not in legacy
    assert not (recent & history), "Recent and History overlap"
    assert (recent | history) == legacy, "the split lost or invented rows"


def count_rows(table: str, row_id: str) -> int:
    with psycopg.connect(DB_URL, autocommit=True) as conn:
        return conn.execute(
            f"select count(*) from {table} where id = %s", (row_id,)
        ).fetchone()[0]


def test_notifications_recent_history_split_is_disjoint(client, throwaway_parent):
    """Covers AE2 (R5, R6): 23h/25h/8d notifications split exactly — and the
    8d row is excluded, not deleted (the table stays the audit trail)."""
    marker = uuid.uuid4().hex[:6]
    ids: dict[str, str] = {}
    try:
        for tag, hours in (("23h", 23), ("25h", 25), ("8d", 192)):
            ids[tag] = backdate_notification(
                throwaway_parent["id"], f"IT U9 {marker} {tag}", hours
            )
        assert_disjoint_split(
            client, throwaway_parent["headers"], "/api/push/notifications", ids
        )
        assert count_rows("live_notifications", ids["8d"]) == 1  # R6: not deleted
    finally:
        delete_rows("live_notifications", list(ids.values()))


def test_alerts_recent_history_split_is_disjoint(client, parent_headers, child_bus):
    """Covers AE2 + R7: the split applies to the alerts feed too — back-dated
    incidents on the child's bus follow the identical partition."""
    marker = uuid.uuid4().hex[:6]
    ids: dict[str, str] = {}
    try:
        for tag, hours in (("23h", 23), ("25h", 25), ("8d", 192)):
            ids[tag] = backdate_incident(child_bus, f"IT U9 alert {marker} {tag}", hours)
        assert_disjoint_split(client, parent_headers, "/api/parent-portal/alerts", ids)
        assert count_rows("live_incidents", ids["8d"]) == 1  # R6: not deleted
    finally:
        delete_rows("live_incidents", list(ids.values()))


def test_history_cap_applies_after_the_exclusion(client, throwaway_parent):
    """Covers R5's server-side rationale (U9): with 250 rows inside the last
    24h and 10 rows aged between the windows, History (min_age 24 + window
    168, limit 200) returns ALL 10 older rows and none of the recent flood —
    the WHERE exclusion runs before LIMIT, so a busy day cannot starve
    History out of the cap (the failure mode of client-side filtering)."""
    marker = uuid.uuid4().hex[:6]
    flood_title = f"IT U9 flood {marker}"
    old_title = f"IT U9 old {marker}"
    user_id = throwaway_parent["id"]
    headers = throwaway_parent["headers"]
    with psycopg.connect(DB_URL, autocommit=True) as conn:
        conn.execute(
            """
            insert into live_notifications (user_id, type, title, body, created_at)
            select %s, 'custom', %s, 'recent ' || n,
                   now() - interval '1 hour' - (n || ' seconds')::interval
            from generate_series(1, 250) as n
            """,
            (user_id, flood_title),
        )
        conn.execute(
            """
            insert into live_notifications (user_id, type, title, body, created_at)
            select %s, 'custom', %s, 'old ' || n,
                   now() - interval '48 hours' - (n || ' minutes')::interval
            from generate_series(1, 10) as n
            """,
            (user_id, old_title),
        )
    try:
        history = client.get(
            "/api/push/notifications",
            params={"window_hours": 168, "min_age_hours": 24, "limit": 200},
            headers=headers,
        ).json()
        titles = [n["title"] for n in history]
        assert titles.count(old_title) == 10, "History lost aged rows to the cap"
        assert flood_title not in titles, "History leaked rows younger than min_age"

        # The flood still saturates Recent's cap without pulling in aged rows.
        recent = client.get(
            "/api/push/notifications",
            params={"window_hours": 24, "limit": 200},
            headers=headers,
        ).json()
        assert len(recent) == 200
        assert old_title not in [n["title"] for n in recent]
    finally:
        with psycopg.connect(DB_URL, autocommit=True) as conn:
            conn.execute(
                "delete from live_notifications where user_id = %s and title = any(%s)",
                (user_id, [flood_title, old_title]),
            )


def test_hour_params_are_bounded_at_a_year(client, parent_headers):
    """U9: window_hours and min_age_hours reject values beyond 8760 at
    validation (422) on both endpoints — unbounded values used to reach the
    DB's interval arithmetic and 500 (SQLSTATE 22015 is not mapped to 400).
    The boundary itself stays accepted, and ge=1 still holds."""
    for path in ("/api/push/notifications", "/api/parent-portal/alerts"):
        for param in ("window_hours", "min_age_hours"):
            over = client.get(path, params={param: 999999}, headers=parent_headers)
            assert over.status_code == 422, f"{path}?{param}=999999 → {over.status_code}"
            zero = client.get(path, params={param: 0}, headers=parent_headers)
            assert zero.status_code == 422, f"{path}?{param}=0 → {zero.status_code}"
            at_bound = client.get(path, params={param: 8760}, headers=parent_headers)
            assert at_bound.status_code == 200, at_bound.text


# Limit cap (R35) ---------------------------------------------------------------

def test_limit_default_and_hard_cap(client, parent_headers):
    default_feed = client.get("/api/push/notifications", headers=parent_headers)
    assert default_feed.status_code == 200
    assert len(default_feed.json()) <= 50  # default limit

    # The cap clamps instead of rejecting: limit=500 succeeds, returns <= 200.
    capped = client.get(
        "/api/push/notifications", params={"limit": 500}, headers=parent_headers
    )
    assert capped.status_code == 200
    assert len(capped.json()) <= 200

    one = client.get(
        "/api/push/notifications", params={"limit": 1}, headers=parent_headers
    ).json()
    assert len(one) == 1  # the seed guarantees at least one row for this parent

    # Nonsense values are rejected, not clamped.
    assert (
        client.get(
            "/api/push/notifications", params={"limit": 0}, headers=parent_headers
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/api/push/notifications", params={"window_hours": 0}, headers=parent_headers
        ).status_code
        == 422
    )


def test_alerts_limit_params(client, parent_headers):
    capped = client.get(
        "/api/parent-portal/alerts", params={"limit": 500}, headers=parent_headers
    )
    assert capped.status_code == 200
    assert len(capped.json()) <= 200

    one = client.get(
        "/api/parent-portal/alerts", params={"limit": 1}, headers=parent_headers
    )
    assert one.status_code == 200
    assert len(one.json()) <= 1

    assert (
        client.get(
            "/api/parent-portal/alerts", params={"limit": -5}, headers=parent_headers
        ).status_code
        == 422
    )


# display_status (R36, AE11) ----------------------------------------------------

def test_display_status_matches_raw_at_school(
    client, parent_headers, driver_headers, no_runs_today
):
    """Seeded children at-school display at-school — the else branch passes the
    raw status through, run or no run today."""
    # Normalize the bus roster first: a completed morning run sweeps everyone
    # (including a child a previous suite left dropped-off) back to at-school.
    run_morning_and_end(client, driver_headers)

    faith = get_child(client, parent_headers, PARENT_CHILD)
    assert faith["status"] == "at-school"
    assert faith["display_status"] == "at-school"

    # Grace is bus-less and route-less: nothing can ever flip her seeded status.
    grace = get_child(client, parent_headers, PARENT_BUSLESS_CHILD)
    assert grace["status"] == "at-school"
    assert grace["display_status"] == "at-school"


def test_today_absence_flips_display_status_and_clearing_restores(
    client, admin_headers, parent_headers, no_runs_today
):
    kid = get_child(client, parent_headers, PARENT_BUSLESS_CHILD)
    before = kid["display_status"]
    assert before != "absent"

    marked = client.post(
        "/api/students/absences", json={"student_id": kid["id"]}, headers=admin_headers
    )
    assert marked.status_code == 200, marked.text
    absence_id = marked.json()["id"]
    try:
        flipped = get_child(client, parent_headers, PARENT_BUSLESS_CHILD)
        assert flipped["display_status"] == "absent"
    finally:
        cleared = client.delete(
            f"/api/students/absences/{absence_id}", headers=admin_headers
        )
        assert cleared.status_code == 200, cleared.text

    restored = get_child(client, parent_headers, PARENT_BUSLESS_CHILD)
    assert restored["display_status"] == before


def test_dropped_off_child_with_no_afternoon_run_today_shows_at_home(
    client, admin_headers, parent_headers, driver_headers, no_runs_today
):
    """AE11: dropped-off is only trusted while an afternoon run today contains
    the student in run_stops; otherwise the badge decays to at-home. The raw
    status field is never rewritten by the read."""
    context = client.get("/api/runs/driver/context", headers=driver_headers).json()
    afternoon = next(r for r in context["routes"] if r["type"] == "afternoon")
    started = client.post(
        "/api/runs/driver/start", json={"route_id": afternoon["id"]}, headers=driver_headers
    )
    assert started.status_code == 200, started.text
    run_id = started.json()["id"]

    try:
        # End-run sweeps the run's roster to dropped-off (afternoon semantics).
        ended = client.post(
            "/api/runs/driver/end", json={"run_id": run_id}, headers=driver_headers
        )
        assert ended.status_code == 200, ended.text

        kid = get_child(client, parent_headers, PARENT_CHILD)
        assert kid["status"] == "dropped-off"
        # A completed afternoon run today contains her: dropped-off is trusted.
        assert kid["display_status"] == "dropped-off"

        # Admin deletes the run: now dropped-off with no afternoon run today.
        deleted = client.delete(f"/api/runs/{run_id}", headers=admin_headers)
        assert deleted.status_code == 200, deleted.text

        kid = get_child(client, parent_headers, PARENT_CHILD)
        assert kid["status"] == "dropped-off"  # raw status untouched
        assert kid["display_status"] == "at-home"
    finally:
        # Restore the seeded state: a morning run's end sweeps the roster
        # (Faith included) back to at-school. no_runs_today already deleted
        # today's completed runs, so the start cannot be gated.
        run_morning_and_end(client, driver_headers)


def test_on_bus_during_delayed_run_stays_on_bus(
    client, admin_headers, parent_headers, driver_headers, no_runs_today
):
    """'delayed' counts as active (status <> 'completed' convention): an on-bus
    child on a delayed run must not decay to at-home."""
    context = client.get("/api/runs/driver/context", headers=driver_headers).json()
    morning = next(r for r in context["routes"] if r["type"] == "morning")
    started = client.post(
        "/api/runs/driver/start", json={"route_id": morning["id"]}, headers=driver_headers
    )
    assert started.status_code == 200, started.text
    run_id = started.json()["id"]

    try:
        assert (
            client.post(
                "/api/runs/driver/arrive", json={"run_id": run_id}, headers=driver_headers
            ).status_code
            == 200
        )
        kid = get_child(client, parent_headers, PARENT_CHILD)
        boarded = client.post(
            "/api/runs/driver/boarding",
            json={"student_id": kid["id"], "on_bus": True},
            headers=driver_headers,
        )
        assert boarded.status_code == 200, boarded.text

        kid = get_child(client, parent_headers, PARENT_CHILD)
        assert kid["status"] == "on-bus"
        assert kid["display_status"] == "on-bus"

        # Admin marks the run delayed (PUT overwrites unmentioned fields, so
        # echo the current row back with only status changed).
        run = next(
            r for r in client.get("/api/runs", headers=admin_headers).json()
            if str(r["id"]) == str(run_id)
        )
        updated = client.put(
            f"/api/runs/{run_id}",
            json={
                "bus_id": run["bus_id"],
                "route_id": run["route_id"],
                "type": run["type"],
                "date": str(run["date"]),
                "start_time": run["start_time"],
                "end_time": run["end_time"],
                "status": "delayed",
                "total_stops": run["total_stops"],
                "stops_completed": run["stops_completed"],
                "total_students": run["total_students"],
                "students_boarded": run["students_boarded"],
                "incidents": run["incidents"],
            },
            headers=admin_headers,
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["status"] == "delayed"

        kid = get_child(client, parent_headers, PARENT_CHILD)
        assert kid["status"] == "on-bus"
        assert kid["display_status"] == "on-bus"  # delayed run is still active
    finally:
        # end_run accepts delayed runs and sweeps the roster to at-school.
        client.post("/api/runs/driver/end", json={"run_id": run_id}, headers=driver_headers)


@pytest.mark.skip(
    reason="needs an orchestrator-side SQL fixture: a stale on-bus (raw "
    "'on-bus' with no run today) cannot be staged over HTTP — deleting an "
    "in-progress run restores roster statuses once U6 lands, and admin "
    "student PUT deliberately ignores status (R7). Fixture: update "
    "live_students set status = 'on-bus' where id = "
    "'50000000-0000-0000-0000-000000000005' (Grace, bus-less: no run can "
    "contain her); then assert display_status == 'at-home' and raw status "
    "== 'on-bus' via GET /api/parent-portal/children; restore with status = "
    "'at-school'."
)
def test_stale_on_bus_child_decays_to_at_home():
    pass


@pytest.mark.skip(
    reason="needs an orchestrator-side SQL fixture: a stale absent (raw "
    "'absent' with no live_student_absences row for today) cannot be staged "
    "over HTTP — the driver absent flow (U6) writes both the status and the "
    "absence row, and clearing the absence resets the status. Fixture: update "
    "live_students set status = 'absent' where id = "
    "'50000000-0000-0000-0000-000000000005'; then assert display_status == "
    "'at-home' via GET /api/parent-portal/children; restore with status = "
    "'at-school'."
)
def test_stale_absent_child_decays_to_at_home():
    pass


def test_profile_children_carry_display_status(client, parent_headers):
    """get_profile reuses list_children, so the same derived field rides along."""
    profile = client.get("/api/parent-portal/profile", headers=parent_headers).json()
    assert profile["children"], "seeded parent should have children"
    assert all("display_status" in c and "status" in c for c in profile["children"])
