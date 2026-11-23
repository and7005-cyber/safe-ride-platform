"""The office's PIN rules (hardening U10), without a database: what the
Drivers page may set, the hashes the DAO receives, and the refusal messages.

The database side — the in-school pre-check against both hash forms, the
one-school rule, the unique index and the driver code regeneration — runs
against the local stack (tests/integration/test_driver_pin_scoping.py).
"""

import pytest

from app.api._helpers import map_error
from app.core.config import get_settings
from app.core.errors import BadRequestError
from app.core.scope import SchoolScope
from app.core.security import hash_pin_hmac, hash_pin_v2
from app.services.account_service import AccountService

SCHOOL = "5cae0000-0000-0000-0000-000000000001"
SCOPE = SchoolScope(user_id="staff-1", school_id=SCHOOL, role="director")
ACTOR = {"id": "staff-1"}
PEPPER = get_settings().pin_pepper


class FakeDriverDao:
    def __init__(self):
        self.created: list = []
        self.updated: list = []

    def email_exists(self, email):
        return False

    def create_driver(self, scope, email, password_hash, full_name, phone, pin_hashes, *, actor):
        self.created.append(pin_hashes)
        return {"id": "d-1", "email": email}

    def update_driver(self, scope, driver_id, full_name, email, phone, pin_hashes, *, actor):
        self.updated.append(pin_hashes)
        return {"id": driver_id, "email": email}

    def regenerate_driver_code(self, scope, *, actor):
        return "NEWC0DE2"


@pytest.fixture
def dao():
    return FakeDriverDao()


@pytest.fixture
def service(dao):
    return AccountService(dao=dao)


def _create(service, pin):
    return service.create_driver(
        SCOPE, "new.driver@saferide.test", "Test1234", "New Driver", None, pin, actor=ACTOR
    )


def test_a_new_pin_is_stored_school_salted_with_its_legacy_form_for_the_pre_check(service, dao):
    _create(service, "482915")
    assert dao.created == [
        (hash_pin_v2(SCHOOL, "482915", PEPPER), hash_pin_hmac("482915", PEPPER))
    ]
    assert dao.created[0][0].startswith("v2:k1:")


def test_a_driver_without_a_pin_and_a_blank_reset_pass_no_hashes(service, dao):
    _create(service, None)
    _create(service, "")
    service.update_driver(SCOPE, "d-1", "Driver", "d@saferide.test", None, "", actor=ACTOR)
    service.update_driver(SCOPE, "d-1", "Driver", "d@saferide.test", None, None, actor=ACTOR)
    assert dao.created == [None, None] and dao.updated == [None, None]


@pytest.mark.parametrize(
    "pin, message",
    [
        ("48291", "PIN must be 6 digits"),
        ("0322", "PIN must be 6 digits"),
        ("48291a", "PIN must be 6 digits"),
        ("123456", "That PIN is too easy to guess"),
        ("111111", "That PIN is too easy to guess"),
        ("121212", "That PIN is too easy to guess"),
        ("112233", "That PIN is too easy to guess"),
    ],
)
def test_creation_and_reset_refuse_short_and_easy_pins(service, dao, pin, message):
    with pytest.raises(BadRequestError) as created:
        _create(service, pin)
    assert str(created.value) == message
    with pytest.raises(BadRequestError) as updated:
        service.update_driver(SCOPE, "d-1", "Driver", "d@saferide.test", None, pin, actor=ACTOR)
    assert str(updated.value) == message
    assert map_error(updated.value).detail == message
    assert dao.created == [] and dao.updated == []


def test_the_unique_index_race_reads_as_the_in_school_message(service, dao):
    def collide(*args, **kwargs):
        raise Exception(
            'duplicate key value violates unique constraint "app_users_pin_hash_key"'
        )

    dao.create_driver = collide
    with pytest.raises(Exception) as caught:
        _create(service, "482915")
    assert str(caught.value) == "That PIN is already in use at this school"
    assert caught.value.status_code == 409


def test_regenerate_answers_the_new_code(service):
    assert service.regenerate_driver_code(SCOPE, actor=ACTOR) == {"driverCode": "NEWC0DE2"}
