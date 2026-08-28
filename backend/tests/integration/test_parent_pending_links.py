"""Pending parent links end to end (U11: R30–R33; AE10, AE21, AE22).

Runs the real ``create_app()`` in-process (TestClient) against the local
database, like test_scope_enforcement.py. Covers the accepted-vs-pending link
creation rule on the staff student surface, the sign-up grouping rule (one
matched school auto-accepts, several go all-pending, zero stays empty), the
parent's pending cards (grouped by school, NEVER carrying child fields), the
accept and decline flows (decline blanks the email slot, alerts the school
with the email but no child, and writes the parent-link-declined audit row
without the email), the accepted-and-pending two-seat cap, and the
re-entered-after-decline fresh offer.

World-building: two throwaway schools via conftest.temp_school (their
students, incidents and audit rows are swept on exit) and per-test parent
accounts via the real signup API, purged in finally blocks. Every assertion
is scoped to this file's own markers/emails — the suite shares the database
with parallel runs and never asserts global counts or touches seeded rows.
"""

import os
import uuid

import psycopg
import pytest

from conftest import DIRECTOR_A, DSN, TEST_PASSWORD, purge_accounts, temp_school

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="needs the local stack; set RUN_INTEGRATION=1",
)

PROVIDER_EMAIL = "provider@kuumbai.test"  # seeded Kuumbai provider identity


@pytest.fixture(scope="module")
def client(in_process_db):
    from fastapi.testclient import TestClient

    from app.main import create_app

    return TestClient(create_app())


@pytest.fixture(scope="module")
def schools():
    with temp_school("IT PendLink School A") as school_a, \
            temp_school("IT PendLink School B") as school_b:
        yield {"a": school_a, "b": school_b}


@pytest.fixture(scope="module")
def staff(client, schools):
    resp = client.post(
        "/api/auth/login", json={"email": DIRECTOR_A, "password": TEST_PASSWORD}
    )
    assert resp.status_code == 200, resp.text
    base = {"Authorization": f"Bearer {resp.json()['token']}"}
    return {
        "a": {**base, "X-School-Id": schools["a"]},
        "b": {**base, "X-School-Id": schools["b"]},
    }


def signup_parent(client, email: str, name: str) -> tuple[str, dict]:
    resp = client.post(
        "/api/auth/signup",
        json={"email": email, "password": "ParentPass1!", "full_name": name,
              "role": "parent"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return body["user"]["id"], {"Authorization": f"Bearer {body['token']}"}


def student_payload(name: str, email: str, email2: str | None = None) -> dict:
    payload = {
        "name": name,
        "grade": "G4",
        "parent_name": "IT Pending Parent",
        "parent_phone": "+254711000001",
        "parent_email": email,
    }
    if email2:
        payload["parent2_name"] = "IT Pending Parent2"
        payload["parent2_email"] = email2
    return payload


def create_student(client, headers: dict, name: str, email: str,
                   email2: str | None = None) -> dict:
    resp = client.post(
        "/api/students", json=student_payload(name, email, email2), headers=headers
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def pending_cards(client, parent_headers: dict) -> list[dict]:
    resp = client.get("/api/parent-portal/pending", headers=parent_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def child_names(client, parent_headers: dict) -> set[str]:
    resp = client.get("/api/parent-portal/children", headers=parent_headers)
    assert resp.status_code == 200, resp.text
    return {c["name"] for c in resp.json()}


def school_incidents_naming(client, staff_headers: dict, email: str) -> list[dict]:
    rows = client.get("/api/incidents", headers=staff_headers).json()
    return [i for i in rows if email in (i.get("description") or "")]


def links_of_student(student_id: str) -> list[dict]:
    with psycopg.connect(DSN, autocommit=True) as pg:
        rows = pg.execute(
            "select parent_id::text, status from live_parent_students "
            "where student_id = %s",
            (student_id,),
        ).fetchall()
    return [{"parent_id": r[0], "status": r[1]} for r in rows]


CARD_KEYS = {"schoolId", "schoolName", "offeredAt", "linkCount"}


# AE10: a second school enters the child of an already-accepted parent ---------

def test_cross_school_entry_offers_pending_and_accept_links(client, schools, staff):
    marker = uuid.uuid4().hex[:6]
    email = f"it-pend-{marker}@test.local"
    kid_a, kid_b = f"IT PendKidA {marker}", f"IT PendKidB {marker}"
    parent_id = None
    try:
        # Child exists at A first; the fresh signup matches ONE school → the
        # single-school auto-link is preserved, with A's signup incident.
        create_student(client, staff["a"], kid_a, email)
        parent_id, parent_headers = signup_parent(client, email, f"IT Pend {marker}")
        assert child_names(client, parent_headers) == {kid_a}
        assert pending_cards(client, parent_headers) == []
        auto = school_incidents_naming(client, staff["a"], email)
        assert len(auto) == 1
        assert auto[0]["type"] == "other"
        assert "auto-linked" in auto[0]["description"]

        # B enters a student with the same email → ONE pending card naming B,
        # and nothing but school fields in the payload (AE22's assertion).
        created_b = create_student(client, staff["b"], kid_b, email)
        cards = pending_cards(client, parent_headers)
        assert len(cards) == 1
        card = cards[0]
        assert set(card) == CARD_KEYS
        assert card["schoolId"] == schools["b"]
        assert card["schoolName"] == "IT PendLink School B"
        assert card["linkCount"] == 1
        assert card["offeredAt"] is not None
        assert kid_b not in str(cards)  # no child detail anywhere in the payload

        # Pending grants no read: the B child is not listed, tracked or profiled.
        assert child_names(client, parent_headers) == {kid_a}
        track = client.get(
            "/api/parent-portal/track",
            params={"student_id": created_b["id"]}, headers=parent_headers,
        )
        assert track.status_code == 404

        # Accept: every pending link at B activates; both children listed with
        # their school names; the cards empty out.
        resp = client.post(
            f"/api/parent-portal/pending/{schools['b']}/accept", headers=parent_headers
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["accepted"] == 1
        children = client.get("/api/parent-portal/children", headers=parent_headers).json()
        assert {c["name"] for c in children} == {kid_a, kid_b}
        by_name = {c["name"]: c for c in children}
        assert by_name[kid_b]["school_name"] == "IT PendLink School B"
        assert by_name[kid_a]["school_name"] == "IT PendLink School A"
        assert pending_cards(client, parent_headers) == []

        # B's Parents page shows only its own child (R33/AE18 unchanged).
        parents_b = client.get("/api/accounts/parents", headers=staff["b"]).json()
        row = next(p for p in parents_b if (p.get("email") or "").lower() == email)
        assert row["students"] == [kid_b]
        assert row["status"] == "registered"
    finally:
        purge_accounts(parent_id)


# AE21: a fresh signup matching two schools goes all-pending -------------------

def test_multi_school_signup_goes_pending_with_per_school_incidents(
    client, schools, staff
):
    marker = uuid.uuid4().hex[:6]
    email = f"it-multi-{marker}@test.local"
    kid_a, kid_b = f"IT MultiKidA {marker}", f"IT MultiKidB {marker}"
    parent_id = None
    try:
        create_student(client, staff["a"], kid_a, email)
        create_student(client, staff["b"], kid_b, email)
        parent_id, parent_headers = signup_parent(client, email, f"IT Multi {marker}")

        # Nothing accepted; two pending cards, one per school.
        assert child_names(client, parent_headers) == set()
        cards = pending_cards(client, parent_headers)
        assert len(cards) == 2
        assert {c["schoolId"] for c in cards} == {schools["a"], schools["b"]}
        for card in cards:
            assert set(card) == CARD_KEYS
            assert card["linkCount"] == 1
        assert kid_a not in str(cards) and kid_b not in str(cards)

        # One signup incident per matched school, named by email, not accepted
        # wording.
        for school_key in ("a", "b"):
            mine = school_incidents_naming(client, staff[school_key], email)
            assert len(mine) == 1
            assert mine[0]["type"] == "other"
            assert "confirmation" in mine[0]["description"]

        # Accept A then B, one by one.
        resp = client.post(
            f"/api/parent-portal/pending/{schools['a']}/accept", headers=parent_headers
        )
        assert resp.status_code == 200, resp.text
        assert child_names(client, parent_headers) == {kid_a}
        remaining = pending_cards(client, parent_headers)
        assert [c["schoolId"] for c in remaining] == [schools["b"]]
        resp = client.post(
            f"/api/parent-portal/pending/{schools['b']}/accept", headers=parent_headers
        )
        assert resp.status_code == 200, resp.text
        assert child_names(client, parent_headers) == {kid_a, kid_b}
        assert pending_cards(client, parent_headers) == []
    finally:
        purge_accounts(parent_id)


# Zero matches and ineligible identities ---------------------------------------

def test_zero_match_signup_has_no_links_and_no_cards(client):
    marker = uuid.uuid4().hex[:6]
    parent_id = None
    try:
        parent_id, parent_headers = signup_parent(
            client, f"it-zero-{marker}@test.local", f"IT Zero {marker}"
        )
        assert child_names(client, parent_headers) == set()
        assert pending_cards(client, parent_headers) == []
    finally:
        purge_accounts(parent_id)


def test_provider_email_in_a_slot_never_links(client, schools, staff):
    marker = uuid.uuid4().hex[:6]
    created = create_student(
        client, staff["a"], f"IT ProvKid {marker}", PROVIDER_EMAIL
    )
    assert links_of_student(created["id"]) == []


# AE22: decline ----------------------------------------------------------------

def test_decline_blanks_slot_alerts_school_and_reoffer_after_reentry(
    client, schools, staff
):
    marker = uuid.uuid4().hex[:6]
    email = f"it-decl-{marker}@test.local"
    kid_a, kid_b = f"IT DeclKidA {marker}", f"IT DeclKidB {marker}"
    parent_id = None
    try:
        # Accepted at A (single-school signup), then B claims the email.
        create_student(client, staff["a"], kid_a, email)
        parent_id, parent_headers = signup_parent(client, email, f"IT Decl {marker}")
        created_b = create_student(client, staff["b"], kid_b, email)
        cards = pending_cards(client, parent_headers)
        assert len(cards) == 1
        assert set(cards[0]) == CARD_KEYS  # the payload never carried child fields

        resp = client.post(
            f"/api/parent-portal/pending/{schools['b']}/decline", headers=parent_headers
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["declined"] == 1

        # Links gone; A's world untouched.
        assert pending_cards(client, parent_headers) == []
        assert child_names(client, parent_headers) == {kid_a}
        assert links_of_student(created_b["id"]) == []

        # The matching slot is blanked on B's student record.
        students_b = client.get("/api/students", headers=staff["b"]).json()
        row = next(s for s in students_b if str(s["id"]) == str(created_b["id"]))
        assert row["parent_email"] is None
        assert row["parent2_email"] is None

        # B's Alerts: one mismatched-email incident naming the EMAIL, never the
        # child.
        mine = school_incidents_naming(client, staff["b"], email)
        declines = [i for i in mine if "mismatched email" in i["description"].lower()]
        assert len(declines) == 1
        assert declines[0]["type"] == "other"
        assert kid_b not in declines[0]["description"]

        # The audit row: parent-link-declined at B, count only — no email.
        with psycopg.connect(DSN, autocommit=True) as pg:
            rows = pg.execute(
                "select school_id::text, detail from live_admin_audit "
                "where action = 'parent-link-declined' and actor_id = %s",
                (parent_id,),
            ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == schools["b"]
        assert rows[0][1] == {"link_count": 1}
        assert email not in str(rows[0][1])

        # A declined email re-entered by the school is a fresh pending offer.
        resp = client.put(
            f"/api/students/{created_b['id']}",
            json=student_payload(kid_b, email), headers=staff["b"],
        )
        assert resp.status_code == 200, resp.text
        cards = pending_cards(client, parent_headers)
        assert len(cards) == 1
        assert cards[0]["schoolId"] == schools["b"]
        assert cards[0]["linkCount"] == 1
    finally:
        purge_accounts(parent_id)


# Cap: accepted + pending both hold a seat -------------------------------------

def test_third_email_is_refused_when_accepted_plus_pending_fill_the_seats(
    client, schools, staff
):
    marker = uuid.uuid4().hex[:6]
    email1 = f"it-cap1-{marker}@test.local"
    email2 = f"it-cap2-{marker}@test.local"
    email3 = f"it-cap3-{marker}@test.local"
    kid = f"IT CapKid {marker}"
    parent1 = parent2 = parent3 = None
    try:
        parent1, parent1_headers = signup_parent(client, email1, f"IT Cap1 {marker}")
        parent2, _ = signup_parent(client, email2, f"IT Cap2 {marker}")
        parent3, parent3_headers = signup_parent(client, email3, f"IT Cap3 {marker}")

        created = create_student(client, staff["a"], kid, email1, email2)
        links = links_of_student(created["id"])
        assert {l["parent_id"] for l in links} == {parent1, parent2}
        assert {l["status"] for l in links} == {"pending"}

        # Parent 1 accepts → one accepted + one pending on the student.
        resp = client.post(
            f"/api/parent-portal/pending/{schools['a']}/accept",
            headers=parent1_headers,
        )
        assert resp.status_code == 200, resp.text
        statuses = {l["parent_id"]: l["status"] for l in links_of_student(created["id"])}
        assert statuses == {parent1: "accepted", parent2: "pending"}

        # Drift parent 1's account email so the slot-1 rewrite below cannot
        # prune their link (the staged third-account scenario).
        with psycopg.connect(DSN, autocommit=True) as pg:
            pg.execute(
                "update app_users set email = %s where id = %s",
                (f"drift-{email1}", parent1),
            )

        # Slot 1 now names a third registered parent: both seats are held
        # (one accepted, one pending) → the third email is refused.
        resp = client.put(
            f"/api/students/{created['id']}",
            json=student_payload(kid, email3, email2), headers=staff["a"],
        )
        assert resp.status_code == 200, resp.text
        statuses = {l["parent_id"]: l["status"] for l in links_of_student(created["id"])}
        assert statuses == {parent1: "accepted", parent2: "pending"}
        assert parent3 not in statuses
        assert pending_cards(client, parent3_headers) == []
    finally:
        purge_accounts(parent1, parent2, parent3)
