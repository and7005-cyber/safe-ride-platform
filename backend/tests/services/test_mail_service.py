"""Transactional email (hardening plan U7): templates, transports, failure log.

No AWS here: the SES transport is driven through an injected stub client, the
shape boto3's ``sesv2`` client takes. The failure line is checked the way the
Lambda runtime would emit it (tests/lambda_json_log.py), because U12's metric
filter reads that line and the never-log list applies to all of it.
"""

import logging

import pytest

from app.core.config import Settings, get_settings
from app.core.request_context import RequestInfo, RequestLogFilter, bind_request, unbind_request
from app.services import mail_service as ms
from tests.lambda_json_log import JsonLines

RECIPIENT = "parent.mail@saferide.test"
TOKEN = "raw-token-value-123"


@pytest.fixture
def json_log():
    root = logging.getLogger()
    handler = JsonLines()
    handler.addFilter(RequestLogFilter())
    previous = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    yield handler
    root.removeHandler(handler)
    root.setLevel(previous)


@pytest.fixture
def settings(monkeypatch):
    """The cached Settings object, restored after each test."""
    current = get_settings()
    for name in ("app_env", "mail_enabled", "mail_reply_to", "app_base_url"):
        monkeypatch.setattr(current, name, getattr(current, name))
    return current


# --- templates -----------------------------------------------------------------


@pytest.mark.parametrize(
    "build,kind,purpose,path,expiry",
    [
        (ms.reset_mail, "reset", "reset", "/reset-password?token=", "30 minutes"),
        (ms.verification_mail, "verify", "verify", "/verify-email?token=", "24 hours"),
        (ms.existing_account_mail, "existing-account", "reset", "/reset-password?token=", "30 minutes"),
    ],
)
def test_token_mails_carry_the_link_the_expiry_and_the_ignore_line(
    settings, build, kind, purpose, path, expiry
):
    settings.app_base_url = "https://app.saferide.test/"
    message = build(RECIPIENT, TOKEN)
    assert message.kind == kind and message.purpose == purpose and message.to == RECIPIENT
    assert message.link == f"https://app.saferide.test{path}{TOKEN}"
    for part in (message.text, message.html):
        assert message.link in part
        assert expiry in part
        assert "ignore this email" in part
    assert message.subject and "SafeRide" in message.subject
    assert message.html.startswith("<!doctype html>")


def test_templates_carry_no_user_supplied_text(settings):
    # The builders take an address and a token only: no name, no school name.
    for build in (ms.reset_mail, ms.verification_mail, ms.existing_account_mail):
        message = build(RECIPIENT, TOKEN)
        assert RECIPIENT not in message.text and RECIPIENT not in message.html
    notice = ms.email_changed_notice(RECIPIENT)
    assert notice.link is None and notice.purpose is None
    assert "school office" in notice.text
    assert RECIPIENT not in notice.text


def test_the_office_password_notice_carries_no_password_link_or_school(settings):
    # Hardening U9: the Parents page's reset and Confirm and reset mail the
    # account a neutral notice. The password is heard from the office only.
    notice = ms.office_password_notice(RECIPIENT)
    assert notice.kind == "office-reset" and notice.to == RECIPIENT
    assert notice.link is None and notice.purpose is None
    for part in (notice.text, notice.html):
        assert "http" not in part.replace("<!doctype html>", "")
        assert RECIPIENT not in part
        assert "school office" in part
        assert "72 hours" in part
        assert "signed out" in part
    assert "SafeRide" in notice.subject


def test_the_html_part_escapes_the_link(settings):
    settings.app_base_url = 'https://x.test/"><script>'
    message = ms.reset_mail(RECIPIENT, TOKEN)
    assert "<script>" not in message.html


# --- transport selection -------------------------------------------------------


def test_local_always_simulates_and_records_even_with_mail_switched_on(settings):
    settings.app_env = "local"
    settings.mail_enabled = True
    service = ms.MailService()
    assert isinstance(service.transport, ms.SimulatedTransport)
    assert service.transport.record_links is True
    assert settings.mail_available is True


def test_production_with_mail_on_sends_through_ses(settings):
    settings.app_env = "production"
    settings.mail_enabled = True
    assert isinstance(ms.MailService().transport, ms.SesTransport)
    assert settings.mail_available is True


def test_production_with_mail_off_simulates_without_recording(settings):
    settings.app_env = "production"
    settings.mail_enabled = False
    service = ms.MailService()
    assert isinstance(service.transport, ms.SimulatedTransport)
    assert service.transport.record_links is False
    assert settings.mail_available is False
    service.send(ms.reset_mail(RECIPIENT, TOKEN))
    assert service.last_link("reset") is None


def test_mail_available_defaults_off():
    assert Settings(_env_file=None).mail_available is False


def test_the_simulated_transport_records_the_last_link_per_purpose(json_log):
    transport = ms.SimulatedTransport(record_links=True)
    service = ms.MailService(transport)
    first = ms.reset_mail(RECIPIENT, "one")
    second = ms.verification_mail(RECIPIENT, "two")
    third = ms.existing_account_mail(RECIPIENT, "three")
    for message in (first, second, third):
        assert service.send(message) is True
    assert service.last_link("reset") == third.link
    assert service.last_link("verify") == second.link
    assert [m.kind for m in transport.sent] == ["reset", "verify", "existing-account"]
    # The simulated line names the kind only.
    text = json_log.text()
    assert "mail (simulated) kind=reset" in text
    assert RECIPIENT not in text and "one" not in text


# --- SES ---------------------------------------------------------------------------


class StubSes:
    def __init__(self, error: Exception | None = None):
        self.calls: list[dict] = []
        self.error = error

    def send_email(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return {"MessageId": "stub"}


class FakeClientError(Exception):
    """botocore's ClientError shape: the code and the text in ``response``."""

    def __init__(self, code: str, message: str):
        super().__init__(f"An error occurred ({code}) when calling SendEmail: {message}")
        self.response = {"Error": {"Code": code, "Message": message}}


def test_ses_send_uses_the_identity_the_configuration_set_and_both_parts(settings):
    settings.mail_reply_to = ""
    stub = StubSes()
    message = ms.reset_mail(RECIPIENT, TOKEN)
    assert ms.MailService(ms.SesTransport(client=stub)).send(message) is True
    (call,) = stub.calls
    assert call["FromEmailAddress"] == settings.mail_from_address == "no-reply@saferidelive.co.ke"
    assert call["ConfigurationSetName"] == settings.mail_configuration_set
    assert call["Destination"] == {"ToAddresses": [RECIPIENT]}
    body = call["Content"]["Simple"]["Body"]
    assert body["Text"]["Data"] == message.text
    assert body["Html"]["Data"] == message.html
    assert call["Content"]["Simple"]["Subject"]["Data"] == message.subject
    assert "ReplyToAddresses" not in call


def test_ses_send_sets_reply_to_when_configured(settings):
    settings.mail_reply_to = "office@saferide.test"
    stub = StubSes()
    ms.MailService(ms.SesTransport(client=stub)).send(ms.verification_mail(RECIPIENT, TOKEN))
    assert stub.calls[0]["ReplyToAddresses"] == ["office@saferide.test"]


def test_ses_client_is_bounded_ten_seconds_and_a_single_attempt():
    assert ms.SES_TIMEOUT_SECONDS == 10
    assert ms.SES_TOTAL_MAX_ATTEMPTS == 1


def test_message_rejected_logs_one_failure_line_without_the_address(json_log):
    stub = StubSes(
        FakeClientError(
            "MessageRejected",
            f"Email address is not verified. The following identities failed the check: {RECIPIENT}",
        )
    )
    token = bind_request(RequestInfo(request_id="req-mail-1", method="POST", path="/x"))
    try:
        sent = ms.MailService(ms.SesTransport(client=stub)).send(ms.reset_mail(RECIPIENT, TOKEN))
    finally:
        unbind_request(token)
    assert sent is False
    failures = [r for r in json_log.records() if r["message"].startswith(ms.MAIL_FAILED_TOKEN)]
    assert len(failures) == 1
    (line,) = failures
    assert line["level"] == "ERROR"
    assert line["message"] == (
        "mail-send-failed kind=reset request_id=req-mail-1 "
        "error=FakeClientError code=MessageRejected"
    )
    # No exception text anywhere in the emitted line (no exc_info → no
    # errorMessage), so neither the address nor the token can leak.
    text = json_log.text()
    assert RECIPIENT not in text
    assert TOKEN not in text
    assert "errorMessage" not in line and "stackTrace" not in line


def test_a_raising_transport_never_raises_past_the_service(json_log):
    class Broken:
        def send(self, message):
            raise RuntimeError(f"boom for {message.to}")

    assert ms.MailService(Broken()).send(ms.verification_mail(RECIPIENT, TOKEN)) is False
    text = json_log.text()
    assert "mail-send-failed kind=verify request_id=- error=RuntimeError code=-" in text
    assert RECIPIENT not in text
