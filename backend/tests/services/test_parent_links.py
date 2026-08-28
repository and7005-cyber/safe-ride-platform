"""Parent-link sync rules (R9–R11, U11/R31) against an in-memory store — no DB.

Covers the email-slot matching, same-email-once, cap-of-two (accepted AND
pending both count), prune-only-on-change, and accepted-vs-pending rules of
``student_live_dao.sync_parent_links`` (a link is ``accepted`` only when the
account already has an accepted child at the same school, else ``pending``),
the signup-side backfill (``link_account_to_matching_students`` — one matched
school auto-accepts, several matched schools go all-pending), and the
two-parent payload invariant enforced by ``students_live._clean_student``.
"""

import pytest

from app.api.students_live import _clean_student
from app.core.errors import BadRequestError
from app.dao.student_live_dao import (
    MAX_PARENT_LINKS,
    link_account_to_matching_students,
    sync_parent_links,
)

SCHOOL = "school-1"  # the default school every un-annotated fake row lives in
OTHER_SCHOOL = "school-2"


class FakeLinkStore:
    """In-memory stand-in for student_live_dao._ConnParentLinks."""

    def __init__(self) -> None:
        self.accounts: dict[str, str] = {}  # lower(email) -> account id
        self.students: dict[str, tuple] = {}  # student id -> (parent_email, parent2_email)
        self.student_schools: dict[str, str] = {}  # student id -> school id
        self.links: list[dict] = []
        self._seq = 0

    # test seeding helpers ---------------------------------------------------

    def add_account(self, account_id: str, email: str) -> None:
        self.accounts[email.lower()] = account_id

    def seed_link(
        self, parent_id: str, student_id: str, email: str,
        school_id: str = SCHOOL, status: str = "accepted",
    ) -> None:
        """Seed a pre-existing link with an arbitrary account email — lets tests
        model drift (the account's email was renamed after linking)."""
        self._seq += 1
        self.links.append({
            "id": f"link-{self._seq}", "parent_id": parent_id,
            "student_id": student_id, "email": email.lower(),
            "school_id": school_id, "status": status,
        })

    def linked_parents(self, student_id: str) -> set:
        return {l["parent_id"] for l in self.links if l["student_id"] == student_id}

    def link_status(self, parent_id: str, student_id: str) -> str | None:
        return next(
            (l["status"] for l in self.links
             if l["parent_id"] == parent_id and l["student_id"] == student_id),
            None,
        )

    # _ConnParentLinks interface ----------------------------------------------

    def parent_account_id(self, email: str) -> str | None:
        return self.accounts.get(email.lower())

    def student_links(self, student_id) -> list[dict]:
        return [dict(l) for l in self.links if l["student_id"] == student_id]

    def signup_matches(self, email: str) -> list[dict]:
        needle = email.lower()
        return [
            {
                "student_id": sid,
                "school_id": self.student_schools.get(sid, SCHOOL),
            }
            for sid, slots in self.students.items()
            if any(slot and slot.lower() == needle for slot in slots)
        ]

    def has_accepted_at_school(self, parent_id, school_id) -> bool:
        return any(
            l["parent_id"] == parent_id
            and l["school_id"] == school_id
            and l["status"] == "accepted"
            for l in self.links
        )

    def add_link(self, parent_id, student_id, school_id, status: str) -> None:
        email = next(e for e, aid in self.accounts.items() if aid == parent_id)
        self.seed_link(parent_id, student_id, email, school_id=school_id, status=status)

    def remove_link(self, link_id) -> None:
        self.links = [l for l in self.links if l["id"] != link_id]


@pytest.fixture
def store() -> FakeLinkStore:
    return FakeLinkStore()


# sync_parent_links: matching -------------------------------------------------

def test_links_account_matching_parent_email(store):
    store.add_account("acc-a", "mum@test.com")
    created = sync_parent_links(store, "s1", ("mum@test.com", None), school_id=SCHOOL)
    assert created == 1
    assert store.linked_parents("s1") == {"acc-a"}


def test_links_account_matching_parent2_email(store):
    store.add_account("acc-b", "dad@test.com")
    created = sync_parent_links(store, "s1", (None, "dad@test.com"), school_id=SCHOOL)
    assert created == 1
    assert store.linked_parents("s1") == {"acc-b"}


def test_matching_is_case_insensitive(store):
    store.add_account("acc-a", "Mum@Test.com")
    assert sync_parent_links(store, "s1", ("MUM@test.COM", None), school_id=SCHOOL) == 1
    assert store.linked_parents("s1") == {"acc-a"}


def test_unregistered_emails_create_no_links(store):
    created = sync_parent_links(
        store, "s1", ("nobody@test.com", "ghost@test.com"), school_id=SCHOOL
    )
    assert created == 0
    assert store.links == []


def test_same_email_in_both_slots_links_once(store):
    store.add_account("acc-a", "both@test.com")
    created = sync_parent_links(
        store, "s1", ("both@test.com", "Both@Test.com"), school_id=SCHOOL
    )
    assert created == 1
    assert len(store.links) == 1


# sync_parent_links: accepted vs pending (U11/R31) -----------------------------

def test_first_link_at_a_school_is_pending(store):
    # No accepted child at this school yet → the school's claim waits for the
    # parent, whatever the slot says.
    store.add_account("acc-a", "mum@test.com")
    sync_parent_links(store, "s1", ("mum@test.com", None), school_id=SCHOOL)
    assert store.link_status("acc-a", "s1") == "pending"


def test_accepted_sibling_at_same_school_links_accepted(store):
    store.add_account("acc-a", "mum@test.com")
    store.seed_link("acc-a", "sibling", "mum@test.com", school_id=SCHOOL, status="accepted")
    sync_parent_links(store, "s2", ("mum@test.com", None), school_id=SCHOOL)
    assert store.link_status("acc-a", "s2") == "accepted"


def test_acceptance_elsewhere_does_not_skip_the_handshake(store):
    # Accepted at ANOTHER school only → this school still needs consent.
    store.add_account("acc-a", "mum@test.com")
    store.seed_link(
        "acc-a", "other-kid", "mum@test.com", school_id=OTHER_SCHOOL, status="accepted"
    )
    sync_parent_links(store, "s1", ("mum@test.com", None), school_id=SCHOOL)
    assert store.link_status("acc-a", "s1") == "pending"


def test_pending_sibling_does_not_auto_accept(store):
    # A pending link at this school is not yet consent — the new link waits too.
    store.add_account("acc-a", "mum@test.com")
    store.seed_link("acc-a", "sibling", "mum@test.com", school_id=SCHOOL, status="pending")
    sync_parent_links(store, "s2", ("mum@test.com", None), school_id=SCHOOL)
    assert store.link_status("acc-a", "s2") == "pending"


def test_link_rows_carry_the_childs_school(store):
    store.add_account("acc-a", "mum@test.com")
    sync_parent_links(store, "s1", ("mum@test.com", None), school_id=OTHER_SCHOOL)
    assert store.links[0]["school_id"] == OTHER_SCHOOL


def test_redoing_a_declined_email_creates_a_fresh_pending_link(store):
    # Decline deletes the row (no tombstone): re-entering the email simply
    # offers again.
    store.add_account("acc-a", "mum@test.com")
    sync_parent_links(store, "s1", ("mum@test.com", None), school_id=SCHOOL)
    store.remove_link(store.links[0]["id"])  # the decline
    created = sync_parent_links(
        store, "s1", ("mum@test.com", None),
        old_emails=("mum@test.com", None), school_id=SCHOOL,
    )
    assert created == 1
    assert store.link_status("acc-a", "s1") == "pending"


# sync_parent_links: cap ------------------------------------------------------

def test_cap_two_links_slot_order_wins(store):
    # A drifted link already occupies one of the two seats; slot 1's account
    # takes the last seat, slot 2's account is left out (slot-order precedence).
    store.add_account("acc-1", "one@test.com")
    store.add_account("acc-2", "two@test.com")
    store.seed_link("acc-drift", "s1", "old@test.com")
    created = sync_parent_links(
        store, "s1", ("one@test.com", "two@test.com"),
        old_emails=("one@test.com", "two@test.com"),  # unchanged → no prune
        school_id=SCHOOL,
    )
    assert created == 1
    assert store.linked_parents("s1") == {"acc-drift", "acc-1"}


def test_full_student_gains_no_links(store):
    store.add_account("acc-new", "new@test.com")
    store.seed_link("acc-x", "s1", "x@test.com")
    store.seed_link("acc-y", "s1", "y@test.com")
    created = sync_parent_links(
        store, "s1", ("new@test.com", None), old_emails=("new@test.com", None),
        school_id=SCHOOL,
    )
    assert created == 0
    assert store.linked_parents("s1") == {"acc-x", "acc-y"}
    assert len(store.linked_parents("s1")) == MAX_PARENT_LINKS


def test_cap_counts_accepted_and_pending_together(store):
    # One accepted + one PENDING parent already on the student: a third email
    # is refused — a pending seat is a held seat (U11).
    store.add_account("acc-3", "three@test.com")
    store.seed_link("acc-x", "s1", "x@test.com", status="accepted")
    store.seed_link("acc-y", "s1", "y@test.com", status="pending")
    created = sync_parent_links(
        store, "s1", ("three@test.com", None), old_emails=("three@test.com", None),
        school_id=SCHOOL,
    )
    assert created == 0
    assert store.linked_parents("s1") == {"acc-x", "acc-y"}


# sync_parent_links: pruning --------------------------------------------------

def test_email_change_swaps_link(store):
    store.add_account("acc-a", "a@test.com")
    store.add_account("acc-b", "b@test.com")
    store.seed_link("acc-a", "s1", "a@test.com")
    created = sync_parent_links(
        store, "s1", ("b@test.com", None), old_emails=("a@test.com", None),
        school_id=SCHOOL,
    )
    assert created == 1
    assert store.linked_parents("s1") == {"acc-b"}


def test_unrelated_edit_preserves_drifted_link(store):
    # The linked account's email matches neither slot (renamed after linking),
    # but this write did not touch the email slots → the link must survive.
    store.seed_link("acc-old", "s1", "renamed@test.com")
    created = sync_parent_links(
        store, "s1", ("a@test.com", None), old_emails=("a@test.com", None),
        school_id=SCHOOL,
    )
    assert created == 0
    assert store.linked_parents("s1") == {"acc-old"}


def test_email_change_keeps_drifted_link(store):
    # Pruning is per removed slot value: a drifted link (account renamed after
    # linking — matches no old slot) is never severed, even when another slot
    # changes in the same write.
    store.add_account("acc-b", "b@test.com")
    store.seed_link("acc-old", "s1", "renamed@test.com")
    sync_parent_links(
        store, "s1", ("b@test.com", None), old_emails=("a@test.com", None),
        school_id=SCHOOL,
    )
    assert store.linked_parents("s1") == {"acc-b", "acc-old"}


def test_other_slot_change_keeps_untouched_slots_drifted_link(store):
    # Slot 2 changes; a drifted link that once belonged to slot 1 survives.
    store.add_account("acc-c", "c@test.com")
    store.seed_link("acc-drift", "s1", "renamed@test.com")
    sync_parent_links(
        store, "s1", ("a@test.com", "c@test.com"),
        old_emails=("a@test.com", "b@test.com"), school_id=SCHOOL,
    )
    assert store.linked_parents("s1") == {"acc-drift", "acc-c"}


def test_removing_a_slot_prunes_its_link(store):
    store.add_account("acc-b", "b@test.com")
    store.seed_link("acc-b", "s1", "b@test.com")
    sync_parent_links(
        store, "s1", ("a@test.com", None), old_emails=("a@test.com", "b@test.com"),
        school_id=SCHOOL,
    )
    assert store.linked_parents("s1") == set()


def test_removing_a_slot_prunes_its_pending_link_too(store):
    # A pending offer dies with its slot: the school withdrew the claim before
    # the parent answered.
    store.add_account("acc-b", "b@test.com")
    store.seed_link("acc-b", "s1", "b@test.com", status="pending")
    sync_parent_links(
        store, "s1", ("a@test.com", None), old_emails=("a@test.com", "b@test.com"),
        school_id=SCHOOL,
    )
    assert store.linked_parents("s1") == set()


def test_swapping_slots_prunes_nothing(store):
    store.add_account("acc-a", "a@test.com")
    store.add_account("acc-b", "b@test.com")
    store.seed_link("acc-a", "s1", "a@test.com")
    store.seed_link("acc-b", "s1", "b@test.com")
    created = sync_parent_links(
        store, "s1", ("b@test.com", "a@test.com"),
        old_emails=("a@test.com", "b@test.com"), school_id=SCHOOL,
    )
    assert created == 0
    assert store.linked_parents("s1") == {"acc-a", "acc-b"}


def test_create_semantics_never_prune(store):
    # old_emails=None (create / bulk): nothing is ever removed.
    store.seed_link("acc-old", "s1", "elsewhere@test.com")
    sync_parent_links(store, "s1", ("new@test.com", None), school_id=SCHOOL)
    assert store.linked_parents("s1") == {"acc-old"}


def test_matching_link_survives_email_change(store):
    # parent2_email changes, parent_email stays → its link is kept, not churned.
    store.add_account("acc-a", "a@test.com")
    store.add_account("acc-c", "c@test.com")
    store.seed_link("acc-a", "s1", "a@test.com")
    created = sync_parent_links(
        store, "s1", ("a@test.com", "c@test.com"),
        old_emails=("a@test.com", "b@test.com"), school_id=SCHOOL,
    )
    assert created == 1
    assert store.linked_parents("s1") == {"acc-a", "acc-c"}


# Signup-side backfill (single school accepts, several go pending — U11) --------

def test_signup_single_school_links_accepted_in_either_slot(store):
    store.add_account("acc-new", "parent@test.com")
    store.students = {
        "s1": ("parent@test.com", None),
        "s2": (None, "PARENT@test.com"),
        "s3": ("other@test.com", None),
    }
    created = link_account_to_matching_students(store, "acc-new", "parent@test.com")
    assert len(created) == 2
    assert {c["status"] for c in created} == {"accepted"}
    assert store.linked_parents("s1") == {"acc-new"}
    assert store.link_status("acc-new", "s1") == "accepted"
    assert store.linked_parents("s2") == {"acc-new"}
    assert store.linked_parents("s3") == set()


def test_signup_matching_two_schools_goes_all_pending(store):
    store.add_account("acc-new", "parent@test.com")
    store.students = {
        "s1": ("parent@test.com", None),
        "s2": ("parent@test.com", None),
    }
    store.student_schools = {"s1": SCHOOL, "s2": OTHER_SCHOOL}
    created = link_account_to_matching_students(store, "acc-new", "parent@test.com")
    assert len(created) == 2
    assert {c["status"] for c in created} == {"pending"}
    assert store.link_status("acc-new", "s1") == "pending"
    assert store.link_status("acc-new", "s2") == "pending"
    assert {c["school_id"] for c in created} == {SCHOOL, OTHER_SCHOOL}


def test_signup_with_zero_matches_creates_nothing(store):
    store.add_account("acc-new", "parent@test.com")
    store.students = {"s1": ("other@test.com", None)}
    assert link_account_to_matching_students(store, "acc-new", "parent@test.com") == []
    assert store.links == []


def test_signup_honours_link_cap(store):
    store.add_account("acc-new", "parent@test.com")
    store.students = {"s1": ("parent@test.com", None)}
    store.seed_link("acc-x", "s1", "x@test.com")
    store.seed_link("acc-y", "s1", "y@test.com")
    assert link_account_to_matching_students(store, "acc-new", "parent@test.com") == []
    assert store.linked_parents("s1") == {"acc-x", "acc-y"}


def test_signup_does_not_double_link(store):
    store.add_account("acc-new", "parent@test.com")
    store.students = {"s1": ("parent@test.com", None)}
    store.seed_link("acc-new", "s1", "parent@test.com")
    assert link_account_to_matching_students(store, "acc-new", "parent@test.com") == []
    assert len(store.links) == 1


# Payload invariant (_clean_student) -------------------------------------------

def _payload(**overrides) -> dict:
    data = {
        "name": "Kid",
        "parent_name": "Parent One",
        "parent_phone": "0712 345 678",
        "parent_phone2": None,
        "parent_email": "one@test.com",
        "parent2_name": None,
        "parent2_email": None,
        "home_address": None,
        "home_lat": None,
        "home_lng": None,
    }
    data.update(overrides)
    return data


def test_clean_student_requires_parent_name():
    with pytest.raises(BadRequestError, match="Parent 1 name"):
        _clean_student(_payload(parent_name="  "))


def test_clean_student_requires_at_least_one_phone():
    with pytest.raises(BadRequestError, match="phone"):
        _clean_student(_payload(parent_phone=None, parent_phone2=None))


def test_clean_student_requires_at_least_one_email():
    with pytest.raises(BadRequestError, match="email"):
        _clean_student(_payload(parent_email=None, parent2_email=None))


def test_clean_student_accepts_parent2_only_contacts():
    data = _clean_student(
        _payload(
            parent_phone=None, parent_email=None,
            parent2_name="Parent Two", parent_phone2="0712345679",
            parent2_email="two@test.com",
        )
    )
    assert data["parent_phone2"] == "+254712345679"
    assert data["parent2_email"] == "two@test.com"


def test_clean_student_normalises_and_validates_parent2_fields():
    with pytest.raises(BadRequestError, match="parent 2 email"):
        _clean_student(_payload(parent2_email="not-an-email"))
    data = _clean_student(_payload(parent2_name="  Two  "))
    assert data["parent2_name"] == "Two"
    assert data["parent_phone"] == "+254712345678"
