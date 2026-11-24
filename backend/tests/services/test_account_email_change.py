"""The office's parent email change sends two mails (hardening Release 1b).

The database side — verification restarted, every token voided, sessions and
push registrations gone, the audit row, links intact — runs on the live stack
(tests/integration/test_parent_signup_verification.py). Here, without a
database: which mails go where, and that the token the DAO stored is the one
the new address receives.
"""

from urllib.parse import parse_qs, urlparse

from app.core.scope import SchoolScope
from app.core.security import hash_session_token
from app.services.account_service import AccountService
from app.services.mail_service import VERIFY_TTL_MINUTES, MailService, SimulatedTransport

SCOPE = SchoolScope(user_id="staff-1", school_id="school-1", role="director")


class FakeParentsDao:
    def __init__(self, current_email: str) -> None:
        self.current_email = current_email
        self.calls: list[dict] = []

    def update_parent(self, scope, parent_id, full_name, email, phone, *, actor,
                      verify_token_hash=None, verify_ttl_minutes=None):
        changed = email.lower() != self.current_email.lower()
        self.calls.append({
            "verify_token_hash": verify_token_hash, "ttl": verify_ttl_minutes,
            "changed": changed,
        })
        previous = self.current_email if changed else None
        self.current_email = email
        return {
            "id": parent_id, "full_name": full_name, "email": email, "phone": phone,
            "email_changed": changed, "previous_email": previous,
        }


def _service(current_email: str):
    transport = SimulatedTransport(record_links=True)
    dao = FakeParentsDao(current_email)
    return AccountService(dao=dao, mail=MailService(transport)), dao, transport


def test_a_changed_email_mails_the_new_address_a_link_and_the_old_one_a_notice():
    service, dao, transport = _service("old@saferide.test")
    row = service.update_parent(
        SCOPE, "p-1", "Parent", "new@saferide.test", None, actor={"id": "staff-1"}
    )
    # The internal fields never reach the API response.
    assert row == {"id": "p-1", "full_name": "Parent", "email": "new@saferide.test", "phone": None}
    verify, notice = transport.sent
    assert (verify.kind, verify.to) == ("verify", "new@saferide.test")
    assert (notice.kind, notice.to) == ("email-changed", "old@saferide.test")
    assert notice.link is None
    # The link the new address receives carries the token the DAO stored.
    raw = parse_qs(urlparse(verify.link).query)["token"][0]
    assert dao.calls[0]["verify_token_hash"] == hash_session_token(raw)
    assert dao.calls[0]["ttl"] == VERIFY_TTL_MINUTES


def test_an_unchanged_email_sends_nothing():
    service, _, transport = _service("same@saferide.test")
    service.update_parent(
        SCOPE, "p-1", "Renamed Parent", "SAME@saferide.test", None, actor={"id": "staff-1"}
    )
    assert list(transport.sent) == []
