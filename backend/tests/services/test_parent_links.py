"""Parent-link sync rules (R9–R11) against an in-memory store — no DB.

Covers the email-slot matching, same-email-once, cap-of-two, and
prune-only-on-change rules of ``student_live_dao.sync_parent_links``, the
signup-side backfill (``link_account_to_matching_students``), and the
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


class FakeLinkStore:
    """In-memory stand-in for student_live_dao._ConnParentLinks."""

    def __init__(self) -> None:
        self.accounts: dict[str, str] = {}  # lower(email) -> account id
        self.students: dict[str, tuple] = {}  # student id -> (parent_email, parent2_email)
        self.links: list[dict] = []
        self._seq = 0

    # test seeding helpers ---------------------------------------------------

    def add_account(self, account_id: str, email: str) -> None:
        self.accounts[email.lower()] = account_id

    def seed_link(self, parent_id: str, student_id: str, email: str) -> None:
        """Seed a pre-existing link with an arbitrary account email — lets tests
        model drift (the account's email was renamed after linking)."""
        self._seq += 1
        self.links.append({
            "id": f"link-{self._seq}", "parent_id": parent_id,
            "student_id": student_id, "email": email.lower(),
        })

    def linked_parents(self, student_id: str) -> set:
        return {l["parent_id"] for l in self.links if l["student_id"] == student_id}

    # _ConnParentLinks interface ----------------------------------------------

    def parent_account_id(self, email: str) -> str | None:
        return self.accounts.get(email.lower())

    def student_links(self, student_id) -> list[dict]:
        return [dict(l) for l in self.links if l["student_id"] == student_id]

    def students_with_email(self, email: str) -> list[str]:
        needle = email.lower()
        return [
            sid for sid, slots in self.students.items()
            if any(slot and slot.lower() == needle for slot in slots)
        ]

    def add_link(self, parent_id, student_id) -> None:
        email = next(e for e, aid in self.accounts.items() if aid == parent_id)
        self.seed_link(parent_id, student_id, email)

    def remove_link(self, link_id) -> None:
        self.links = [l for l in self.links if l["id"] != link_id]


@pytest.fixture
def store() -> FakeLinkStore:
    return FakeLinkStore()


# sync_parent_links: matching -------------------------------------------------

def test_links_account_matching_parent_email(store):
    store.add_account("acc-a", "mum@test.com")
    created = sync_parent_links(store, "s1", ("mum@test.com", None))
    assert created == 1
    assert store.linked_parents("s1") == {"acc-a"}


def test_links_account_matching_parent2_email(store):
    store.add_account("acc-b", "dad@test.com")
    created = sync_parent_links(store, "s1", (None, "dad@test.com"))
    assert created == 1
    assert store.linked_parents("s1") == {"acc-b"}


def test_matching_is_case_insensitive(store):
    store.add_account("acc-a", "Mum@Test.com")
    assert sync_parent_links(store, "s1", ("MUM@test.COM", None)) == 1
    assert store.linked_parents("s1") == {"acc-a"}


def test_unregistered_emails_create_no_links(store):
    assert sync_parent_links(store, "s1", ("nobody@test.com", "ghost@test.com")) == 0
    assert store.links == []


def test_same_email_in_both_slots_links_once(store):
    store.add_account("acc-a", "both@test.com")
    created = sync_parent_links(store, "s1", ("both@test.com", "Both@Test.com"))
    assert created == 1
    assert len(store.links) == 1


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
    )
    assert created == 1
    assert store.linked_parents("s1") == {"acc-drift", "acc-1"}


def test_full_student_gains_no_links(store):
    store.add_account("acc-new", "new@test.com")
    store.seed_link("acc-x", "s1", "x@test.com")
    store.seed_link("acc-y", "s1", "y@test.com")
    created = sync_parent_links(
        store, "s1", ("new@test.com", None), old_emails=("new@test.com", None)
    )
    assert created == 0
    assert store.linked_parents("s1") == {"acc-x", "acc-y"}
    assert len(store.linked_parents("s1")) == MAX_PARENT_LINKS


# sync_parent_links: pruning --------------------------------------------------

def test_email_change_swaps_link(store):
    store.add_account("acc-a", "a@test.com")
    store.add_account("acc-b", "b@test.com")
    store.seed_link("acc-a", "s1", "a@test.com")
    created = sync_parent_links(
        store, "s1", ("b@test.com", None), old_emails=("a@test.com", None)
    )
    assert created == 1
    assert store.linked_parents("s1") == {"acc-b"}


def test_unrelated_edit_preserves_drifted_link(store):
    # The linked account's email matches neither slot (renamed after linking),
    # but this write did not touch the email slots → the link must survive.
    store.seed_link("acc-old", "s1", "renamed@test.com")
    created = sync_parent_links(
        store, "s1", ("a@test.com", None), old_emails=("a@test.com", None)
    )
    assert created == 0
    assert store.linked_parents("s1") == {"acc-old"}


def test_email_change_keeps_drifted_link(store):
    # Pruning is per removed slot value: a drifted link (account renamed after
    # linking — matches no old slot) is never severed, even when another slot
    # changes in the same write.
    store.add_account("acc-b", "b@test.com")
    store.seed_link("acc-old", "s1", "renamed@test.com")
    sync_parent_links(store, "s1", ("b@test.com", None), old_emails=("a@test.com", None))
    assert store.linked_parents("s1") == {"acc-b", "acc-old"}


def test_other_slot_change_keeps_untouched_slots_drifted_link(store):
    # Slot 2 changes; a drifted link that once belonged to slot 1 survives.
    store.add_account("acc-c", "c@test.com")
    store.seed_link("acc-drift", "s1", "renamed@test.com")
    sync_parent_links(
        store, "s1", ("a@test.com", "c@test.com"), old_emails=("a@test.com", "b@test.com")
    )
    assert store.linked_parents("s1") == {"acc-drift", "acc-c"}


def test_removing_a_slot_prunes_its_link(store):
    store.add_account("acc-b", "b@test.com")
    store.seed_link("acc-b", "s1", "b@test.com")
    sync_parent_links(store, "s1", ("a@test.com", None), old_emails=("a@test.com", "b@test.com"))
    assert store.linked_parents("s1") == set()


def test_swapping_slots_prunes_nothing(store):
    store.add_account("acc-a", "a@test.com")
    store.add_account("acc-b", "b@test.com")
    store.seed_link("acc-a", "s1", "a@test.com")
    store.seed_link("acc-b", "s1", "b@test.com")
    created = sync_parent_links(
        store, "s1", ("b@test.com", "a@test.com"), old_emails=("a@test.com", "b@test.com")
    )
    assert created == 0
    assert store.linked_parents("s1") == {"acc-a", "acc-b"}


def test_create_semantics_never_prune(store):
    # old_emails=None (create / bulk): nothing is ever removed.
    store.seed_link("acc-old", "s1", "elsewhere@test.com")
    sync_parent_links(store, "s1", ("new@test.com", None))
    assert store.linked_parents("s1") == {"acc-old"}


def test_matching_link_survives_email_change(store):
    # parent2_email changes, parent_email stays → its link is kept, not churned.
    store.add_account("acc-a", "a@test.com")
    store.add_account("acc-c", "c@test.com")
    store.seed_link("acc-a", "s1", "a@test.com")
    created = sync_parent_links(
        store, "s1", ("a@test.com", "c@test.com"), old_emails=("a@test.com", "b@test.com")
    )
    assert created == 1
    assert store.linked_parents("s1") == {"acc-a", "acc-c"}


# Signup-side backfill ---------------------------------------------------------

def test_signup_links_students_carrying_email_in_either_slot(store):
    store.add_account("acc-new", "parent@test.com")
    store.students = {
        "s1": ("parent@test.com", None),
        "s2": (None, "PARENT@test.com"),
        "s3": ("other@test.com", None),
    }
    created = link_account_to_matching_students(store, "acc-new", "parent@test.com")
    assert created == 2
    assert store.linked_parents("s1") == {"acc-new"}
    assert store.linked_parents("s2") == {"acc-new"}
    assert store.linked_parents("s3") == set()


def test_signup_honours_link_cap(store):
    store.add_account("acc-new", "parent@test.com")
    store.students = {"s1": ("parent@test.com", None)}
    store.seed_link("acc-x", "s1", "x@test.com")
    store.seed_link("acc-y", "s1", "y@test.com")
    assert link_account_to_matching_students(store, "acc-new", "parent@test.com") == 0
    assert store.linked_parents("s1") == {"acc-x", "acc-y"}


def test_signup_does_not_double_link(store):
    store.add_account("acc-new", "parent@test.com")
    store.students = {"s1": ("parent@test.com", None)}
    store.seed_link("acc-new", "s1", "parent@test.com")
    assert link_account_to_matching_students(store, "acc-new", "parent@test.com") == 0
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
