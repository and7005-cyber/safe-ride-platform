"""The office parent reset and "Confirm and reset" (hardening U9), without a
database: the password the office sees, the hash the DAO stores, the notice
mail, the linking rule after a confirmation, and the refusal contract.

The database side — who may be reset, sessions, tokens, push rows, the audit
rows, the verification and the links — runs in-process against the local
database (tests/integration/test_parent_reset.py).
"""

import logging

import pytest

from app.api._helpers import map_error
from app.core.errors import AccountRefusedError, NotFoundError
from app.core.scope import SchoolScope
from app.core.security import RELAYED_CODE_ALPHABET, verify_password
from app.dao.account_dao import (
    NOT_RESETTABLE_HERE_CODE,
    NOT_RESETTABLE_HERE_MESSAGE,
    NOTHING_TO_CONFIRM_CODE,
    NOTHING_TO_CONFIRM_MESSAGE,
)
from app.services.account_service import AccountService
from app.services.mail_service import MailService, SimulatedTransport

SCOPE = SchoolScope(user_id="staff-1", school_id="school-1", role="coordinator")
ACTOR = {"id": "staff-1"}
PARENT_EMAIL = "parent.reset@saferide.test"


class FakeParentDao:
    def __init__(self, *, refuse: Exception | None = None, link_error: Exception | None = None):
        self.refuse = refuse
        self.link_error = link_error
        self.hashes: list[str] = []
        self.linked: list[tuple[str, str]] = []
        self.confirmed: list[str] = []

    def reset_parent_password(self, scope, parent_id, password_hash, *, actor):
        if self.refuse:
            raise self.refuse
        self.hashes.append(password_hash)
        return {"id": parent_id, "email": PARENT_EMAIL}

    def confirm_parent_slot(self, scope, email, password_hash, *, actor):
        if self.refuse:
            raise self.refuse
        self.confirmed.append(email)
        self.hashes.append(password_hash)
        return {"id": "p-1", "email": PARENT_EMAIL}

    def link_parent_to_matching_students(self, parent_id, email):
        if self.link_error:
            raise self.link_error
        self.linked.append((parent_id, email))
        return 1


def _service(**dao_kwargs):
    transport = SimulatedTransport(record_links=True)
    dao = FakeParentDao(**dao_kwargs)
    return AccountService(dao=dao, mail=MailService(transport)), dao, transport


def _assert_relayed(password: str) -> None:
    assert len(password) == 16
    assert set(password) <= set(RELAYED_CODE_ALPHABET)


def test_a_reset_reveals_a_relayed_password_once_and_mails_a_neutral_notice():
    service, dao, transport = _service()
    result = service.reset_parent_password(SCOPE, "p-1", actor=ACTOR)
    assert set(result) == {"ok", "temporaryPassword"}
    password = result["temporaryPassword"]
    _assert_relayed(password)
    # The DAO stored a hash of exactly that password.
    (stored,) = dao.hashes
    assert stored != password and verify_password(password, stored)
    (notice,) = transport.sent
    assert (notice.kind, notice.to, notice.link) == ("office-reset", PARENT_EMAIL, None)
    assert password not in notice.text and password not in notice.html


def test_two_resets_issue_two_different_passwords():
    service, dao, _ = _service()
    first = service.reset_parent_password(SCOPE, "p-1", actor=ACTOR)["temporaryPassword"]
    second = service.reset_parent_password(SCOPE, "p-1", actor=ACTOR)["temporaryPassword"]
    assert first != second
    assert not verify_password(first, dao.hashes[1])


@pytest.mark.parametrize(
    "refusal",
    [
        AccountRefusedError(NOT_RESETTABLE_HERE_MESSAGE, code=NOT_RESETTABLE_HERE_CODE),
        NotFoundError("Parent not found"),
    ],
)
def test_a_refused_reset_mails_nothing(refusal):
    service, _, transport = _service(refuse=refusal)
    with pytest.raises(type(refusal)):
        service.reset_parent_password(SCOPE, "p-1", actor=ACTOR)
    assert list(transport.sent) == []


def test_the_refusals_answer_a_structured_409_with_neutral_words():
    for code, message in (
        (NOT_RESETTABLE_HERE_CODE, NOT_RESETTABLE_HERE_MESSAGE),
        (NOTHING_TO_CONFIRM_CODE, NOTHING_TO_CONFIRM_MESSAGE),
    ):
        http = map_error(AccountRefusedError(message, code=code))
        assert http.status_code == 409
        assert http.detail == {"code": code, "message": message}
    assert NOT_RESETTABLE_HERE_MESSAGE == "This account cannot be reset from the Parents page"


def test_confirm_and_reset_verifies_links_after_the_commit_and_mails_the_notice():
    service, dao, transport = _service()
    result = service.confirm_parent_slot(SCOPE, "  Parent.Reset@SafeRide.test ", actor=ACTOR)
    _assert_relayed(result["temporaryPassword"])
    assert set(result) == {"ok", "temporaryPassword"}
    # The address is cleaned (trimmed; the DAO matches it case-insensitively)
    # before the lookup; the linking rule runs on the account it confirmed.
    assert dao.confirmed == ["Parent.Reset@SafeRide.test"]
    assert dao.linked == [("p-1", PARENT_EMAIL)]
    (notice,) = transport.sent
    assert (notice.kind, notice.to) == ("office-reset", PARENT_EMAIL)


def test_nothing_to_confirm_mails_and_links_nothing():
    service, dao, transport = _service(
        refuse=AccountRefusedError(NOTHING_TO_CONFIRM_MESSAGE, code=NOTHING_TO_CONFIRM_CODE)
    )
    with pytest.raises(AccountRefusedError):
        service.confirm_parent_slot(SCOPE, PARENT_EMAIL, actor=ACTOR)
    assert dao.linked == [] and list(transport.sent) == []


def test_a_failed_linking_still_returns_the_issued_password(caplog):
    service, _, transport = _service(link_error=RuntimeError(f"boom {PARENT_EMAIL}"))
    with caplog.at_level(logging.ERROR):
        result = service.confirm_parent_slot(SCOPE, PARENT_EMAIL, actor=ACTOR)
    _assert_relayed(result["temporaryPassword"])
    assert len(transport.sent) == 1
    lines = [r.getMessage() for r in caplog.records if "unexpected-error" in r.getMessage()]
    assert len(lines) == 1
    # The redacted record: the class, never the exception's text (an address).
    assert "RuntimeError" in lines[0] and PARENT_EMAIL not in lines[0]
