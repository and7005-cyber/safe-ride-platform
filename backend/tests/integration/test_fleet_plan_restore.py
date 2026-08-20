"""Fleet-plan restore (U7): the preserved 'previous' plan re-applied in one
act through the same gates — an apply whose source is the as-evolved capture.

Run with the stack up (scripts/start-local.sh):

    RUN_INTEGRATION=1 ../.venv/bin/python -m pytest tests/integration/test_fleet_plan_restore.py -q

Covers R21/R22/R23: apply A → apply B → restore returns live routes to A's
exact shape (memberships + stop order + times, route identity preserved in
place) and preserves B; restore again toggles back to B; restoring the
just-restored (applied) plan is a 200 no-op consistent with apply's; a child
enrolled-and-placed after the capture must be confirmed and is left routeless
with a route-unassigned feed row; a departed child in the preserved document
requires by-name confirmation (the 409 names them via the capture's
denormalized name); a preserved bus now out-of-service blocks the whole
restore naming it (no partial restore); restore notifies exactly the families
whose current communicated stop differs materially — provably the same set
the displacing apply notified, while a no-change family stays silent; audit
rows carry action 'plan-restored' with the self-contained detail and feed
rows carry that act's plan_audit_id; non-admin 403.

Conventions: 'IT '-prefixed entities, finally-cleanup, parents linked via
parent_email at student creation, sanctioned psycopg peeks only for tables
the API deliberately does not expose (plan statuses/documents, baselines,
audit rows, feed-row arbiter columns). Builders and readers are imported from
the apply suite — shared machinery, shared fixtures.
"""

import os
import uuid

import httpx
import pytest

from test_fleet_plan_apply import (
    AFTERNOON,
    ADMIN,
    DEPOT_EAST,
    DEPOT_WEST,
    EAST_HOMES,
    MORNING,
    PARENT,
    WEST_HOMES,
    _acks_for,
    _apply,
    _build_plan,
    _bus_of,
    _draft,
    _edit,
    _links,
    _make_student,
    _pg_all,
    _pg_one,
    _plan_feed,
    _plan_status,
    _plan_statuses,
    _review,
    _school_routes,
    _teardown,
    login,
    signup_parent,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="needs the local stack; set RUN_INTEGRATION=1",
)

BASE = os.environ.get("INTEGRATION_API_URL", "http://localhost:9001")


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE, timeout=30) as c:
        yield c


@pytest.fixture(scope="module")
def admin_headers(client):
    return login(client, ADMIN["email"], ADMIN["password"])


@pytest.fixture(scope="module")
def parent_headers(client):
    return login(client, PARENT["email"], PARENT["password"])


def _restore(client, headers, plan_id: str, *, confirmations=None,
             acknowledgments=None) -> httpx.Response:
    return client.post(
        f"/api/fleet-plans/{plan_id}/restore",
        json={"confirmations": confirmations or [],
              "acknowledgments": acknowledgments or []},
        headers=headers,
    )


# --- readers ------------------------------------------------------------------

def _live_shape(client, headers, school_id: str) -> dict:
    """The school's live route truth as one comparable value: per (type, bus)
    the exact stop rows (order, name, time, gate flag, student), keyed the
    way restore must reproduce them."""
    shape = {}
    for r in _school_routes(client, headers, school_id):
        stops = sorted(
            (
                (s["stop_order"], s["name"], s["scheduled_time"],
                 s["is_school_gate"], s["student_id"])
                for s in r["route_stops"]
            ),
        )
        shape[(r["type"], r["bus_id"])] = stops
    return shape


def _student_attrs(client, headers, ids: list[str]) -> dict:
    live = {s["id"]: s for s in client.get("/api/students", headers=headers).json()}
    return {sid: (live[sid]["bus_id"], live[sid]["pickup_time"]) for sid in ids}


def _restore_audits(school_id: str) -> list[dict]:
    return _pg_all(
        "select id, actor_email, detail from live_admin_audit "
        "where school_id = %s and action = 'plan-restored' order by created_at asc",
        (school_id,),
    )


def _last_plan_feed_audit(user_id: str) -> str | None:
    row = _pg_one(
        "select plan_audit_id from live_notifications "
        "where user_id = %s and type in ('route-updated', 'route-unassigned') "
        "order by created_at desc limit 1",
        (user_id,),
    )
    return str(row["plan_audit_id"]) if row and row["plan_audit_id"] else None


# --- toggle + notification precision -------------------------------------------

def test_toggle_restores_a_then_back_to_b_notifying_only_material_changes(client, admin_headers):
    """apply A → apply B (one child moved buses) → restore returns the live
    routes to A's exact shape — memberships, stop order, times, student
    bus/pickup attributes, route identity in place — and preserves B; restore
    again toggles back to B. Each restore notifies exactly the families whose
    current communicated stop differs materially, which is provably the same
    set the displacing apply notified — the seeded no-change family stays
    silent both ways. Restoring the just-restored plan is a 200 no-op.
    Audit rows carry 'plan-restored' with the self-contained detail; feed
    rows carry the restore's plan_audit_id."""
    marker = uuid.uuid4().hex[:6]
    parents = {tag: signup_parent(client, marker, tag) for tag in ("p0", "p1", "p2", "p3")}
    emails = [parents[t]["email"] for t in ("p0", "p1", "p2", "p3")]
    fx = _build_plan(
        client, admin_headers, marker,
        buses=[("A", 8, DEPOT_EAST), ("B", 8, DEPOT_WEST)],
        homes=[EAST_HOMES[0], EAST_HOMES[1], WEST_HOMES[0], WEST_HOMES[1]],
        seed=7, emails=emails,
    )
    school_id = fx["school"]["id"]
    sids = [s["id"] for s in fx["students"]]
    s_moved = fx["students"][0]  # moved to the other bus in plan B
    try:
        plan_a = fx["plan"]
        review_a = _review(client, admin_headers, school_id)
        assert _acks_for(review_a) == []  # everyone placed
        applied_a = _apply(client, admin_headers, plan_a["id"])
        assert applied_a.status_code == 200, applied_a.text
        shape_a = _live_shape(client, admin_headers, school_id)
        attrs_a = _student_attrs(client, admin_headers, sids)
        links_a = {sid: _links(sid) for sid in sids}
        route_ids_a = {r["id"] for r in _school_routes(client, admin_headers, school_id)}

        # The stable-family guard needs the moved child to share a bus with
        # at least one other child whose stop stays put (seed-pinned).
        bus_of_moved = _bus_of(review_a, s_moved["id"], MORNING)
        same_bus = [sid for sid in sids[1:]
                    if _bus_of(review_a, sid, MORNING) == bus_of_moved]
        assert same_bus, "seed 7 should co-bus the east children"

        # --- plan B: identical draft, one child moved to the other bus ------
        plan_b = _draft(client, admin_headers, school_id, seed=7)
        other_bus = next(b["id"] for b in fx["buses"] if b["id"] != bus_of_moved)
        moved = _edit(client, admin_headers, plan_b["id"], "move",
                      {"student_id": s_moved["id"], "to_bus_id": other_bus})
        assert moved.status_code == 200, moved.text
        applied_b = _apply(client, admin_headers, plan_b["id"])
        assert applied_b.status_code == 200, applied_b.text
        b_notified = set(applied_b.json()["notified_families"])
        assert parents["p0"]["id"] in b_notified  # bus change always notifies
        silent_tags = [t for t in parents if parents[t]["id"] not in b_notified]
        assert silent_tags, "at least one family must be materially unchanged by B"
        silent_feed_before = {
            t: len(_plan_feed(client, parents[t]["headers"])) for t in silent_tags
        }
        shape_b = _live_shape(client, admin_headers, school_id)
        attrs_b = _student_attrs(client, admin_headers, sids)
        assert shape_b != shape_a
        assert _plan_statuses(school_id) == {"draft": 0, "applied": 1, "previous": 1}
        assert _plan_status(plan_a["id"]) == "previous"

        # --- restore #1: back to A ------------------------------------------
        restored = _restore(client, admin_headers, plan_a["id"])
        assert restored.status_code == 200, restored.text
        body = restored.json()
        assert body["already_applied"] is False
        assert body["routes_written"] == 4  # two buses x two legs
        assert body["routes_retired"] == 0  # identity reconciled in place

        # Live truth returned to A's exact shape: stop rows, memberships,
        # student attributes, and the very same route rows.
        assert _live_shape(client, admin_headers, school_id) == shape_a
        assert _student_attrs(client, admin_headers, sids) == attrs_a
        for sid in sids:
            assert _links(sid) == links_a[sid]
        assert {r["id"] for r in _school_routes(client, admin_headers, school_id)} == route_ids_a

        # Statuses toggled; the displaced live state (B, as evolved) is the
        # new previous, captured with B's placement of the moved child.
        assert _plan_status(plan_a["id"]) == "applied"
        assert _plan_status(plan_b["id"]) == "previous"
        assert _plan_statuses(school_id) == {"draft": 0, "applied": 1, "previous": 1}
        preserved_b = _pg_one(
            "select document from live_fleet_plans where id = %s", (plan_b["id"],)
        )["document"]
        moved_capture_buses = {
            r["bus_id"] for r in preserved_b["routes"]
            if any(s["id"] == s_moved["id"] for s in r["students"])
        }
        assert moved_capture_buses == {other_bus}

        # Notification precision: exactly the families B changed materially
        # are told they changed back; the no-change family hears nothing.
        assert set(body["notified_families"]) == b_notified
        for t in silent_tags:
            assert len(_plan_feed(client, parents[t]["headers"])) == silent_feed_before[t]
        p0_rows = _plan_feed(client, parents["p0"]["headers"])
        assert [r["type"] for r in p0_rows] == ["route-updated"] * 3  # A, B, restore

        # Audit + arbiter: one plan-restored row so far, self-contained
        # detail, and the notified feed rows point at THIS act.
        audits = _restore_audits(school_id)
        assert len(audits) == 1
        assert audits[0]["actor_email"] == ADMIN["email"]
        detail = audits[0]["detail"]
        assert detail["plan_id"] == plan_a["id"]
        assert detail["displaced_plan_id"] == plan_b["id"]
        assert detail["routes_written"] == 4
        assert detail["families_notified"] == len(b_notified)
        assert isinstance(detail["elapsed_ms"], int)
        assert body["audit_id"] == str(audits[0]["id"])
        assert _last_plan_feed_audit(parents["p0"]["id"]) == body["audit_id"]

        # --- restore #2: the toggle toggles back to B -----------------------
        restored2 = _restore(client, admin_headers, plan_b["id"])
        assert restored2.status_code == 200, restored2.text
        assert set(restored2.json()["notified_families"]) == b_notified
        assert _live_shape(client, admin_headers, school_id) == shape_b
        assert _student_attrs(client, admin_headers, sids) == attrs_b
        assert _plan_status(plan_b["id"]) == "applied"
        assert _plan_status(plan_a["id"]) == "previous"
        for t in silent_tags:
            assert len(_plan_feed(client, parents[t]["headers"])) == silent_feed_before[t]
        assert len(_restore_audits(school_id)) == 2

        # --- no-op: restoring the just-restored (applied) plan --------------
        again = _restore(client, admin_headers, plan_b["id"])
        assert again.status_code == 200, again.text
        assert again.json()["already_applied"] is True
        assert len(_restore_audits(school_id)) == 2  # zero side effects
        assert len(_plan_feed(client, parents["p0"]["headers"])) == 4
    finally:
        _teardown(client, admin_headers, fx)
        for p in parents.values():
            client.delete(f"/api/accounts/parents/{p['id']}", headers=admin_headers)


# --- roster drift gates (departed + enrolled-after-capture) ---------------------

def test_departed_and_enrolled_after_capture_require_confirmation(client, admin_headers):
    """The restore gate pipeline re-validates against CURRENT enrolment: a
    child in the preserved document who departed must be confirmed by name
    (the 409 names them via the capture's denormalized name — the student row
    is gone), and a child enrolled-and-placed after the capture must be
    confirmed and is left routeless — per-leg 'unassigned' semantics — with a
    route-unassigned feed row. A refused restore leaves zero side effects."""
    marker = uuid.uuid4().hex[:6]
    pe = signup_parent(client, marker, "pe")
    fx = _build_plan(
        client, admin_headers, marker,
        buses=[("A", 8, DEPOT_EAST)],
        homes=[EAST_HOMES[0], EAST_HOMES[1], EAST_HOMES[2]],
        seed=7,
    )
    school_id = fx["school"]["id"]
    plan_a = fx["plan"]
    departed = fx["students"][2]
    enrolled = None
    try:
        applied_a = _apply(client, admin_headers, plan_a["id"])
        assert applied_a.status_code == 200, applied_a.text
        plan_b = _draft(client, admin_headers, school_id, seed=7)
        applied_b = _apply(client, admin_headers, plan_b["id"])
        assert applied_b.status_code == 200, applied_b.text
        assert _plan_status(plan_a["id"]) == "previous"

        # AFTER the capture: enrol a new child straight onto the live routes,
        # and remove one the preserved document still places.
        live_routes = _school_routes(client, admin_headers, school_id)
        enrolled = _make_student(
            client, admin_headers, marker, 7, school_id, WEST_HOMES[0],
            email=pe["email"], route_ids=[r["id"] for r in live_routes],
        )
        assert set(_links(enrolled["id"])) == {MORNING, AFTERNOON}
        assert client.delete(
            f"/api/students/{departed['id']}", headers=admin_headers
        ).status_code == 200

        # Unconfirmed → 409 naming BOTH, by kind; nothing changed.
        refused = _restore(client, admin_headers, plan_a["id"])
        assert refused.status_code == 409, refused.text
        detail = refused.json()["detail"]
        assert departed["name"] in detail and "departed" in detail
        assert enrolled["name"] in detail and "enrolled" in detail
        assert _plan_status(plan_a["id"]) == "previous"
        assert set(_links(enrolled["id"])) == {MORNING, AFTERNOON}
        assert _restore_audits(school_id) == []
        assert _plan_feed(client, pe["headers"]) == []

        # Partially confirmed → still 409, naming the missing departed child.
        refused = _restore(
            client, admin_headers, plan_a["id"],
            confirmations=[{"student_id": enrolled["id"], "kind": "enrolled"}],
        )
        assert refused.status_code == 409, refused.text
        assert departed["name"] in refused.json()["detail"]

        # Fully confirmed → restored: the departed child is dropped from
        # every write, the enrolled child is routeless with a
        # route-unassigned feed row for their family.
        restored = _restore(
            client, admin_headers, plan_a["id"],
            confirmations=[
                {"student_id": enrolled["id"], "kind": "enrolled"},
                {"student_id": departed["id"], "kind": "departed"},
            ],
        )
        assert restored.status_code == 200, restored.text
        body = restored.json()
        assert body["routes_written"] == 2
        assert pe["id"] in body["notified_families"]
        assert _plan_status(plan_a["id"]) == "applied"
        assert _plan_status(plan_b["id"]) == "previous"

        placed_ids = {
            s["student_id"]
            for r in _school_routes(client, admin_headers, school_id)
            for s in r["route_stops"] if s["student_id"] is not None
        }
        assert placed_ids == {fx["students"][0]["id"], fx["students"][1]["id"]}
        assert _links(enrolled["id"]) == {}
        live = {s["id"]: s for s in client.get("/api/students", headers=admin_headers).json()}
        assert live[enrolled["id"]]["bus_id"] is None
        pe_rows = _plan_feed(client, pe["headers"])
        assert [r["type"] for r in pe_rows] == ["route-unassigned"]
        assert pe_rows[0]["student_id"] == enrolled["id"]
    finally:
        if enrolled:
            client.delete(f"/api/students/{enrolled['id']}", headers=admin_headers)
        _teardown(client, admin_headers, fx)
        client.delete(f"/api/accounts/parents/{pe['id']}", headers=admin_headers)


# --- fleet drift ----------------------------------------------------------------

def test_preserved_bus_out_of_service_blocks_restore_whole(client, admin_headers):
    """A preserved bus now out-of-service 409s naming it — a preserved plan
    is restored whole or not at all: the refused restore leaves the world
    untouched, and fixing the fleet lets the same restore succeed."""
    marker = uuid.uuid4().hex[:6]
    fx = _build_plan(
        client, admin_headers, marker,
        buses=[("A", 8, DEPOT_EAST)],
        homes=[EAST_HOMES[0], EAST_HOMES[1]],
        seed=7,
    )
    school_id = fx["school"]["id"]
    plan_a = fx["plan"]
    bus = fx["buses"][0]
    try:
        assert _apply(client, admin_headers, plan_a["id"]).status_code == 200
        plan_b = _draft(client, admin_headers, school_id, seed=7)
        assert _apply(client, admin_headers, plan_b["id"]).status_code == 200

        parked = client.put(
            f"/api/fleet/buses/{bus['id']}",
            json={"name": bus["name"], "capacity": 8, "availability": "out-of-service"},
            headers=admin_headers,
        )
        assert parked.status_code == 200, parked.text

        refused = _restore(client, admin_headers, plan_a["id"])
        assert refused.status_code == 409, refused.text
        assert bus["name"] in refused.json()["detail"]
        assert "out-of-service" in refused.json()["detail"]
        assert _plan_status(plan_a["id"]) == "previous"
        assert _plan_status(plan_b["id"]) == "applied"
        assert _restore_audits(school_id) == []

        revived = client.put(
            f"/api/fleet/buses/{bus['id']}",
            json={"name": bus["name"], "capacity": 8, "availability": "in-service"},
            headers=admin_headers,
        )
        assert revived.status_code == 200, revived.text
        restored = _restore(client, admin_headers, plan_a["id"])
        assert restored.status_code == 200, restored.text
        assert _plan_status(plan_a["id"]) == "applied"
    finally:
        _teardown(client, admin_headers, fx)


# --- authorization + missing previous ---------------------------------------------

def test_non_admin_refused_and_missing_previous_is_404(client, admin_headers, parent_headers):
    """Restore is admin-only: unauthenticated 401, parent 403. For an admin,
    a plan id that does not exist — the shape a school with no preserved
    previous plan presents — is a 404 with a clear detail."""
    phantom = str(uuid.uuid4())
    unauth = client.post(f"/api/fleet-plans/{phantom}/restore",
                         json={"confirmations": [], "acknowledgments": []})
    assert unauth.status_code == 401
    forbidden = client.post(f"/api/fleet-plans/{phantom}/restore",
                            json={"confirmations": [], "acknowledgments": []},
                            headers=parent_headers)
    assert forbidden.status_code == 403
    missing = _restore(client, admin_headers, phantom)
    assert missing.status_code == 404, missing.text
    assert "previous" in missing.json()["detail"]
