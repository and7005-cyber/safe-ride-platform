"""Transactional email (hardening plan U7; Release 1b).

Sent INLINE from the request, after the token row is committed: Lambda freezes
the environment when the handler returns, so nothing detached would ever run.
Each send is bounded (SES client: a five-second connect and an eight-second
read timeout, a single attempt) — the office's email change sends two mails
in a row, so two hung sends stay under about 26 s, inside the API function's
30 s timeout — and best-effort: a failure logs ONE error line and never fails
the request, because every mail-sending route answers a neutral message
whether or not the address exists.

The failure line is the alarm's input (U12 builds a metric filter on the
literal ``mail-send-failed``):

    mail-send-failed kind=<reset|verify|existing-account|email-changed|office-reset> request_id=<id> ...

It never carries the recipient, the subject, the link or the provider's
error text (an SES ``MessageRejected`` message names the address), and it is
logged without ``exc_info`` (the JSON formatter would turn the exception's
text into ``errorMessage``).

Two transports. ``SesTransport`` (SES v2, from ``MAIL_FROM_ADDRESS``, through
the ``MAIL_CONFIGURATION_SET`` configuration set, text and HTML parts) when
the operator switched mail on outside the local stack; ``SimulatedTransport``
otherwise. The simulated transport logs the kind only, and on the local stack
records the last link per purpose for ``GET /api/auth/dev/last-reset-link``
and ``GET /api/auth/dev/last-verification-link`` (both 404 outside local).

Templates are plain functions over ``APP_BASE_URL`` and a token: no name,
school name or any other user-supplied text ever reaches a mail.
"""

from __future__ import annotations

import html
import logging
from collections import deque
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Protocol

from app.core.config import get_settings
from app.core.request_context import current_request_id

logger = logging.getLogger("saferide.mail")

# Token lifetimes (plan U7): a reset link is short-lived, a verification link
# lasts a day so a parent can finish sign-up the next morning.
RESET_TTL_MINUTES = 30
VERIFY_TTL_MINUTES = 24 * 60
# Per-recipient cap (execution decisions, Release 1b): at most this many
# token-bearing mails per account per hour, counted from the token rows
# (reset + verify) created in the last hour. Over the cap nothing is created
# and nothing is sent; the route answers the same neutral message.
MAIL_CAP_PER_HOUR = 3

# The SES client's bounds on one send: connect 5 s, read 8 s, one attempt —
# at most about 13 s. The office's email change (account_service.update_parent)
# sends two mails in sequence after its commit, so two hung sends hold the
# request about 26 s, inside the API function's 30 s timeout with room for
# the database work before them.
SES_CONNECT_TIMEOUT_SECONDS = 5
SES_READ_TIMEOUT_SECONDS = 8
# botocore's total attempts, the first included: one attempt, no retry. A
# retry would double a hung send's hold and take two of them past the 30 s
# timeout; a failed send is logged and alarmed instead.
SES_TOTAL_MAX_ATTEMPTS = 1

MAIL_FAILED_TOKEN = "mail-send-failed"


@dataclass(frozen=True)
class MailMessage:
    kind: str  # reset | verify | existing-account | email-changed | office-reset
    to: str
    subject: str
    text: str
    html: str
    # The token-bearing link, when the mail carries one; recorded by the
    # simulated transport for the local dev routes, never logged.
    link: str | None = None
    # reset | verify — which dev route exposes the link.
    purpose: str | None = None


# --- templates -----------------------------------------------------------------

_FOOTER = "SafeRide — school bus tracking."


def _base_url() -> str:
    return get_settings().app_base_url.rstrip("/")


def reset_link(token: str) -> str:
    return f"{_base_url()}/reset-password?token={token}"


def verification_link(token: str) -> str:
    return f"{_base_url()}/verify-email?token={token}"


def _html(paragraphs: list[str], link: str | None, button: str | None) -> str:
    """A minimal HTML part: escaped paragraphs and, when present, the link as
    a button with the raw URL beneath it for clients that strip styles."""
    parts = [f"<p>{html.escape(p)}</p>" for p in paragraphs]
    if link and button:
        href = html.escape(link, quote=True)
        parts.insert(
            1,
            f'<p><a href="{href}" style="display:inline-block;padding:10px 16px;'
            f'background:#1f6f43;color:#ffffff;border-radius:6px;text-decoration:none">'
            f"{html.escape(button)}</a></p>"
            f'<p style="font-size:12px;color:#555555">Or copy this link: {href}</p>',
        )
    parts.append(f'<p style="font-size:12px;color:#555555">{html.escape(_FOOTER)}</p>')
    return "<!doctype html><html><body>" + "".join(parts) + "</body></html>"


def _text(paragraphs: list[str], link: str | None) -> str:
    lines = [paragraphs[0]]
    if link:
        lines.append(link)
    lines.extend(paragraphs[1:])
    lines.append(_FOOTER)
    return "\n\n".join(lines) + "\n"


def reset_mail(to: str, token: str) -> MailMessage:
    link = reset_link(token)
    paragraphs = [
        "Someone asked to reset the password for your SafeRide account. "
        "Open this link to choose a new password:",
        "The link works once and expires in 30 minutes.",
        "If you didn't ask for this, ignore this email. Your password stays the same.",
    ]
    return MailMessage(
        kind="reset", to=to, subject="Reset your SafeRide password",
        text=_text(paragraphs, link), html=_html(paragraphs, link, "Choose a new password"),
        link=link, purpose="reset",
    )


def verification_mail(to: str, token: str) -> MailMessage:
    link = verification_link(token)
    paragraphs = [
        "Confirm this email address to finish setting up your SafeRide parent account. "
        "Open this link, and sign in if SafeRide asks:",
        "The link expires in 24 hours. Your children appear on Home once the address "
        "is confirmed.",
        "If you didn't sign up for SafeRide, ignore this email.",
    ]
    return MailMessage(
        kind="verify", to=to, subject="Confirm your email for SafeRide",
        text=_text(paragraphs, link), html=_html(paragraphs, link, "Confirm my email"),
        link=link, purpose="verify",
    )


def existing_account_mail(to: str, token: str) -> MailMessage:
    link = reset_link(token)
    paragraphs = [
        "Someone tried to create a SafeRide account with this email address, but an "
        "account already exists for it. If that was you and you've forgotten your "
        "password, open this link to choose a new one:",
        "The link works once and expires in 30 minutes. Otherwise sign in with your "
        "existing password.",
        "If you didn't try to sign up, ignore this email. Nothing has changed.",
    ]
    return MailMessage(
        kind="existing-account", to=to, subject="You already have a SafeRide account",
        text=_text(paragraphs, link), html=_html(paragraphs, link, "Choose a new password"),
        link=link, purpose="reset",
    )


def email_changed_notice(to: str) -> MailMessage:
    """To the OLD address after the office changed an account's email: no
    link, no new address, nothing the old inbox's reader could act on."""
    paragraphs = [
        "Your school office changed the email address you use to sign in to SafeRide. "
        "SafeRide will no longer send email to this address.",
        "If you didn't expect this, contact your school office.",
    ]
    return MailMessage(
        kind="email-changed", to=to, subject="Your SafeRide sign-in email was changed",
        text=_text(paragraphs, None), html=_html(paragraphs, None, None),
    )


def office_password_notice(to: str) -> MailMessage:
    """To the account after the school office issued it a temporary password
    (hardening U9: the Parents page's Reset password, and Confirm and reset
    on an Awaiting-signup slot). Never the password, no link, no school
    name: the parent hears the password from the office, and a reader of a
    hijacked inbox learns nothing they could act on."""
    paragraphs = [
        "Your school office set a temporary password for your SafeRide account. "
        "Sign in with the temporary password the office gives you; SafeRide then "
        "asks you to choose your own. It stops working after 72 hours if it isn't used.",
        "Your other devices have been signed out, and SafeRide notifications on your "
        "phone stop until you sign in again there.",
        "If you didn't ask your school office for this, contact them.",
    ]
    return MailMessage(
        kind="office-reset", to=to, subject="Your SafeRide password was reset by your school",
        text=_text(paragraphs, None), html=_html(paragraphs, None, None),
    )


# --- transports ----------------------------------------------------------------


class MailTransport(Protocol):
    def send(self, message: MailMessage) -> None: ...


@dataclass
class SimulatedTransport:
    """No mail leaves the process. Logs the kind (never the recipient or the
    link); with ``record_links`` (the local stack only) keeps the last link
    per purpose and a short history for the dev routes and the tests."""

    record_links: bool = False
    last_link: dict[str, str | None] = field(
        default_factory=lambda: {"reset": None, "verify": None}
    )
    sent: deque = field(default_factory=lambda: deque(maxlen=50))
    _lock: Lock = field(default_factory=Lock, repr=False)

    def send(self, message: MailMessage) -> None:
        logger.info("mail (simulated) kind=%s", message.kind)
        if not self.record_links:
            return
        with self._lock:
            self.sent.append(message)
            if message.purpose and message.link:
                self.last_link[message.purpose] = message.link


class SesTransport:
    """SES v2 ``SendEmail`` from the configured identity, through the
    configuration set (bounce and complaint events reach the ops topic).

    The client is created lazily on the first send — boto3 ships in the
    Lambda runtime and is not a dependency of the local stack, which never
    builds this transport. ``client`` lets tests inject a stub."""

    def __init__(self, client: Any | None = None) -> None:
        self._client = client

    def _get_client(self) -> Any:
        if self._client is None:
            import boto3  # noqa: PLC0415 — Lambda runtime only
            from botocore.config import Config  # noqa: PLC0415

            self._client = boto3.client(
                "sesv2",
                region_name=get_settings().backend_region,
                config=Config(
                    connect_timeout=SES_CONNECT_TIMEOUT_SECONDS,
                    read_timeout=SES_READ_TIMEOUT_SECONDS,
                    retries={"mode": "standard", "total_max_attempts": SES_TOTAL_MAX_ATTEMPTS},
                ),
            )
        return self._client

    def send(self, message: MailMessage) -> None:
        settings = get_settings()
        request: dict[str, Any] = {
            "FromEmailAddress": settings.mail_from_address,
            "Destination": {"ToAddresses": [message.to]},
            "Content": {
                "Simple": {
                    "Subject": {"Data": message.subject, "Charset": "UTF-8"},
                    "Body": {
                        "Text": {"Data": message.text, "Charset": "UTF-8"},
                        "Html": {"Data": message.html, "Charset": "UTF-8"},
                    },
                }
            },
            "ConfigurationSetName": settings.mail_configuration_set,
        }
        if settings.mail_reply_to.strip():
            request["ReplyToAddresses"] = [settings.mail_reply_to.strip()]
        self._get_client().send_email(**request)


def _error_code(error: BaseException) -> str:
    """The provider's machine code (``MessageRejected``, ``Throttling``…) from
    a botocore ``ClientError``'s response, or "-" — never its message."""
    response = getattr(error, "response", None)
    if isinstance(response, dict):
        code = (response.get("Error") or {}).get("Code")
        if isinstance(code, str) and code.isascii() and len(code) <= 64:
            return code
    return "-"


class MailService:
    """Picks the transport once per process from the settings; ``send``
    never raises."""

    def __init__(self, transport: MailTransport | None = None) -> None:
        if transport is None:
            settings = get_settings()
            transport = (
                SesTransport()
                if settings.mail_via_ses
                else SimulatedTransport(record_links=settings.is_local)
            )
        self.transport = transport

    def send(self, message: MailMessage) -> bool:
        """Deliver best-effort. True when the transport accepted the mail; on
        any failure one ERROR line without the recipient, then False."""
        try:
            self.transport.send(message)
            return True
        except Exception as error:  # noqa: BLE001 — a failed mail never fails the request
            logger.error(
                "%s kind=%s request_id=%s error=%s code=%s",
                MAIL_FAILED_TOKEN,
                message.kind,
                current_request_id() or "-",
                type(error).__name__,
                _error_code(error),
            )
            return False

    def last_link(self, purpose: str) -> str | None:
        """The simulated transport's last recorded link (local dev routes)."""
        transport = self.transport
        if isinstance(transport, SimulatedTransport):
            return transport.last_link.get(purpose)
        return None


_default: MailService | None = None
_default_lock = Lock()


def get_mail_service() -> MailService:
    """The process-wide service (one transport, one simulated record)."""
    global _default
    if _default is None:
        with _default_lock:
            if _default is None:
                _default = MailService()
    return _default
