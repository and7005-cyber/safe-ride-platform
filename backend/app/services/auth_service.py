import datetime as dt
import hmac
import logging
import uuid

from psycopg import errors as pg_errors

from app.core.config import get_settings
from app.core.errors import (
    BadRequestError,
    ForbiddenError,
    NotFoundError,
    TooManyRequestsError,
    UnauthorizedError,
)
from app.core.request_context import current_request_id
from app.core.security import (
    PinRefused,
    create_pin_session_token,
    create_session_token,
    hash_password,
    hash_pin_v2,
    hash_session_token,
    is_driver_code,
    is_v2_pin_hash,
    normalise_driver_code,
    normalise_relayed_code,
    validate_pin,
    verify_password,
    verify_pin_hmac,
)
from app.core.validation import clean_email
from app.dao.account_dao import AccountDao
from app.dao.auth_dao import AuthDao
from app.services.mail_service import (
    MAIL_CAP_PER_HOUR,
    RESET_TTL_MINUTES,
    VERIFY_TTL_MINUTES,
    MailMessage,
    MailService,
    existing_account_mail,
    get_mail_service,
    reset_mail,
    verification_mail,
)

logger = logging.getLogger("saferide.auth")

# U8: driver self-signup is retired — drivers are created by their school
# (R5); the public form only ever creates parent accounts now.
SIGNUP_ROLES = {"parent"}

# The neutral answers (the platform's enumeration posture, plan U7): the same
# words whether or not the address has an account, whether or not a mail went
# out (per-recipient cap, disabled account, failed send).
FORGOT_MESSAGE = "If an account exists for this address, a reset link has been sent"
SIGNUP_MESSAGE = "Check your email to finish signing up"
RESET_INVALID_MESSAGE = "Reset link is invalid or has expired"
# The verify endpoint is session-bound (plan U8): a link opened without a
# session, or under another account's session, links nothing.
VERIFY_SIGN_IN_MESSAGE = "Sign in to the account you are verifying"
VERIFY_LINK_INVALID_MESSAGE = "This verification link has expired or was already used"
# Self-service change (hardening U9, DL13): a session created by PIN never
# held the account password — the office set it — so it may not change it.
PIN_SESSION_CHANGE_MESSAGE = (
    "You signed in with a PIN. Sign in with your email and password to change your password."
)


# Driver sign-in bound to the school (hardening U10). The phone stores the
# school it was set up for (the driver code lookup) and sends its id with the
# PIN; there is no PIN-only fallback. More than PIN_LOCK_MAX_FAILURES failed
# attempts from one source at one school inside PIN_LOCK_WINDOW_MINUTES pause
# PIN sign-in for that source at that school until the window passes (D3).
PIN_LOCK_WINDOW_MINUTES = 15
PIN_LOCK_MAX_FAILURES = 20
INVALID_PIN_MESSAGE = "Invalid PIN"
SCHOOL_REQUIRED_MESSAGE = (
    "This phone is not set up for a school yet. Enter the driver code from your school office."
)
SCHOOL_PIN_LOCKED_MESSAGE = (
    "Sign-in for your school is paused for a few minutes. Try again shortly."
)
DRIVER_CODE_UNKNOWN_MESSAGE = "No school has that driver code"


class PinLoginError(UnauthorizedError):
    """A refused PIN sign-in the client branches on: ``school-required`` (no
    school was sent — the phone is not bound) or ``invalid-pin`` (a wrong PIN,
    or a school that does not exist)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SchoolPinLockedError(TooManyRequestsError):
    """PIN sign-in paused for this source at this school (D3). The answer
    carries ``Retry-After``: seconds until the window lets the source in."""

    code = "school-pin-locked"

    def __init__(self, message: str, *, retry_after_s: float) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


def _canonical_school_id(value) -> str | None:
    try:
        return str(uuid.UUID(str(value).strip()))
    except (ValueError, AttributeError, TypeError):
        return None


class VerificationError(BadRequestError):
    """A verify-email refusal the client branches on (DL10's states):
    ``sign-in-required`` (no session), ``wrong-account`` (the token belongs
    to another account — signed in as someone else) and ``link-invalid``
    (expired, used or unknown)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class PinSessionError(ForbiddenError):
    """Change password from a session created by PIN (hardening U9): the
    client hides the entry; the server refuses it with ``pin-session``."""

    code = "pin-session"


def _password_matches(user: dict, password: str) -> bool:
    """The stored hash against what was typed. An account holding an
    office-issued temporary password (``must_change_password``) also accepts
    the relayed form however it was typed (DL6): any case, with the group
    separators, O for 0, I or L for 1. Staff temporary passwords are
    case-sensitive and never match the normalised form, so nothing changes
    for them; a password the account chose itself is compared exactly."""
    stored = user.get("password_hash")
    if not stored:
        return False
    if verify_password(password, stored):
        return True
    if not user.get("must_change_password"):
        return False
    normalised = normalise_relayed_code(password)
    return normalised != password and verify_password(normalised, stored)


class AuthService:
    def __init__(
        self,
        dao: AuthDao | None = None,
        accounts: AccountDao | None = None,
        mail: MailService | None = None,
    ) -> None:
        self.dao = dao or AuthDao()
        self.accounts = accounts or AccountDao()
        self._mail = mail
        self.pepper = get_settings().pin_pepper

    @property
    def mail(self) -> MailService:
        # Resolved on first use: the process-wide service (one transport, one
        # simulated record for the dev routes) unless a test injected one.
        if self._mail is None:
            self._mail = get_mail_service()
        return self._mail

    # --- session helpers ---------------------------------------------------

    def _issue_session(
        self,
        user: dict,
        *,
        pin: bool = False,
        school_id: str | None = None,
        pin_upgrade: tuple[str, str] | None = None,
    ) -> dict:
        # A PIN session's token carries the marker (security.PIN_SESSION_TOKEN_PREFIX):
        # the account menus hide Change password for it and the server
        # refuses the change (hardening U9). A PIN session also remembers the
        # school it signed in at, and carries the legacy-hash upgrade into
        # the same transaction (hardening U10).
        token = create_pin_session_token() if pin else create_session_token()
        extra: dict = {}
        if school_id is not None:
            extra["last_school_id"] = school_id
        if pin_upgrade is not None:
            extra["pin_upgrade"] = pin_upgrade
        self.dao.create_session(user["id"], hash_session_token(token), **extra)
        return {
            "token": token,
            "user": {
                "id": str(user["id"]),
                "email": user["email"],
                "fullName": user.get("full_name"),
                "role": user.get("role"),
                # The client must know right away whether it may leave the
                # change-password screen (R30); everything else comes from /me.
                "mustChangePassword": bool(user.get("must_change_password")),
                # Release 1b: Home shows the verify card from the first frame
                # instead of flashing the empty state until /me answers.
                "emailVerified": bool(user.get("email_verified", True)),
                # Hardening U9: no Change password entry for a PIN session.
                "signedInWithPin": pin,
            },
        }

    def resolve_session(self, token: str) -> dict | None:
        if not token:
            return None
        return self.dao.get_session_user(hash_session_token(token))

    # --- emailed tokens ------------------------------------------------------

    def _send_token_mail(
        self,
        user: dict,
        *,
        purpose: str,
        ttl_minutes: int,
        build,
        void_earlier: bool = False,
    ) -> bool:
        """Token row first, then the inline send (plan U7). Nothing for a
        disabled account; nothing over the per-recipient cap. Returns whether
        a mail was handed to the transport — the callers' answers never
        depend on it."""
        if user.get("disabled_at") is not None:
            return False
        raw = create_session_token()
        created = self.dao.create_reset_token(
            str(user["id"]),
            hash_session_token(raw),
            purpose,
            ttl_minutes,
            cap_per_hour=MAIL_CAP_PER_HOUR,
            void_earlier=void_earlier,
        )
        message: MailMessage = build(user["email"], raw)
        if not created:
            logger.info(
                "mail-capped kind=%s request_id=%s", message.kind, current_request_id() or "-"
            )
            return False
        return self.mail.send(message)

    def _complete_verification(self, user_id: str) -> None:
        """Stamp the address verified; when the account was WAITING for it
        (D1's unverified state), run the sign-up linking rule and its office
        incidents now — they were held back at sign-up (plan U8)."""
        stamped = self.dao.mark_email_verified(user_id)
        if stamped and stamped.get("was_unverified"):
            self.accounts.link_parent_to_matching_students(user_id, stamped["email"])

    # --- flows -------------------------------------------------------------

    def signup(self, email: str, password: str, full_name: str, role: str) -> dict:
        """No session in either branch (execution decisions, Release 1b; DL2):
        the client shows "check your email" and the parent signs in.

        A new address gets an unverified account and its verification token
        in one transaction, then the verification mail — it links nothing and
        raises no incident until its owner verifies. An address that already
        has an account creates nothing and gets the "you already have an
        account" mail with a reset link. Both branches hash the password, so
        the answer is the same in words and close in time."""
        if role not in SIGNUP_ROLES:
            raise BadRequestError("Role must be parent")
        email = clean_email(email, required=True)
        password_hash = hash_password(password)
        existing = self.dao.get_user_by_email(email)
        if existing:
            self._send_token_mail(
                existing, purpose="reset", ttl_minutes=RESET_TTL_MINUTES,
                build=existing_account_mail,
            )
            return {"ok": True, "message": SIGNUP_MESSAGE}
        raw = create_session_token()
        try:
            user = self.dao.create_unverified_parent(
                email, password_hash, full_name, hash_session_token(raw), VERIFY_TTL_MINUTES
            )
        except pg_errors.UniqueViolation:
            # A concurrent sign-up for the same address won the insert: the
            # account exists now, so this caller gets the same answer.
            return {"ok": True, "message": SIGNUP_MESSAGE}
        self.mail.send(verification_mail(user["email"], raw))
        return {"ok": True, "message": SIGNUP_MESSAGE}

    def login(self, email: str, password: str) -> dict:
        user = self.dao.get_user_by_email(email)
        if not user or not _password_matches(user, password):
            raise UnauthorizedError("Invalid email or password")
        if user.get("disabled_at") is not None:
            # Indistinguishable from a bad password: no account enumeration.
            raise UnauthorizedError("Invalid email or password")
        if user.get("must_change_password") and self._temporary_password_expired(user):
            # An unused temporary password dies after 72 hours (R6); the same
            # generic refusal — its holder asks the director for a new one.
            raise UnauthorizedError("Invalid email or password")
        return self._issue_session(user)

    @staticmethod
    def _temporary_password_expired(user: dict) -> bool:
        expires_at = user.get("temporary_password_expires_at")
        if expires_at is None:
            return False
        return expires_at <= dt.datetime.now(dt.timezone.utc)

    # --- driver sign-in bound to the school (hardening U10) --------------------

    def school_for_driver_code(self, code: str) -> dict:
        """The public lookup behind the driver's first sign-in on a phone:
        a typed driver code (any case, with or without the hyphen or spaces,
        O for 0, I or L for 1) answers the school's id and name — nothing
        else — or 404. The public AAA-NNN school code never matches."""
        normalised = normalise_driver_code(code)
        school = self.dao.school_by_driver_code(normalised) if is_driver_code(normalised) else None
        if not school:
            raise NotFoundError(DRIVER_CODE_UNKNOWN_MESSAGE)
        return {"schoolId": str(school["id"]), "name": school["name"]}

    def pin_login(self, pin: str, school_id: str | None, source: str = "unknown") -> dict:
        """A PIN resolves only among the bound school's active drivers.

        No school: 401 ``school-required`` (the phone must be set up with the
        driver code first). An unknown school: 401 ``invalid-pin``. More than
        PIN_LOCK_MAX_FAILURES failures from ``source`` at the school in the
        window: 429 ``school-pin-locked`` with the seconds to wait — the
        correct PIN included, other sources and schools unaffected. Then the
        school-salted hash is matched directly; failing that, the legacy
        global hashes OF THIS SCHOOL'S DRIVERS ONLY are verified and a match
        is rewritten to the salted form in the session's transaction. Every
        failure at a known school writes a failure row.

        Log lines (U12's metric filters match their first word:
        ``school-required``, ``pin-failure``, ``pin-locked``) carry the public
        school code only — never the PIN, a hash, the driver code, an address
        or the source; the request id is stamped by the log filter."""
        if school_id is None or not str(school_id).strip():
            logger.warning("school-required")
            raise PinLoginError("school-required", SCHOOL_REQUIRED_MESSAGE)
        canonical = _canonical_school_id(school_id)
        school = self.dao.school_for_pin_login(canonical) if canonical else None
        if not school:
            logger.warning("pin-failure school=unknown")
            raise PinLoginError("invalid-pin", INVALID_PIN_MESSAGE)
        school_id = str(school["id"])
        school_code = school.get("code") or "-"

        window = self.dao.pin_failures_since(
            school_id, source, PIN_LOCK_WINDOW_MINUTES, PIN_LOCK_MAX_FAILURES
        )
        if window["failures"] > PIN_LOCK_MAX_FAILURES:
            logger.warning("pin-locked school=%s", school_code)
            raise SchoolPinLockedError(
                SCHOOL_PIN_LOCKED_MESSAGE,
                retry_after_s=max(1.0, window["retry_after_s"] or 1.0),
            )

        match = self._match_school_pin(school_id, pin)
        if match is None:
            self.dao.record_pin_failure(school_id, source)
            logger.warning("pin-failure school=%s", school_code)
            raise PinLoginError("invalid-pin", INVALID_PIN_MESSAGE)
        user_id, upgrade = match
        user = self.dao.get_user_by_id(user_id)
        if user is None:
            raise PinLoginError("invalid-pin", INVALID_PIN_MESSAGE)
        return self._issue_session(user, pin=True, school_id=school_id, pin_upgrade=upgrade)

    def _match_school_pin(
        self, school_id: str, pin: str
    ) -> tuple[str, tuple[str, str] | None] | None:
        """(user id, legacy-to-salted upgrade or None) for the one driver at
        ``school_id`` the PIN belongs to, or None. The salted match wins."""
        try:
            validate_pin(pin, for_creation=False)
        except PinRefused:
            return None
        salted = hash_pin_v2(school_id, pin, self.pepper)
        members = self.dao.driver_members_for_school(school_id)
        for member in members:
            if hmac.compare_digest(str(member["pin_hash"]), salted):
                return str(member["id"]), None
        legacy = [
            member
            for member in members
            if not is_v2_pin_hash(member["pin_hash"])
            and verify_pin_hmac(pin, member["pin_hash"], self.pepper)
        ]
        if len(legacy) != 1:
            return None
        return str(legacy[0]["id"]), (legacy[0]["pin_hash"], salted)

    def logout(self, token: str) -> None:
        if token:
            self.dao.revoke_session(hash_session_token(token))

    def forgot_password(self, email: str) -> dict:
        """Always the neutral answer (no user enumeration): a reset token and
        the reset mail for an enabled account under the cap, nothing
        otherwise."""
        user = self.dao.get_user_by_email(email.strip())
        if user:
            self._send_token_mail(
                user, purpose="reset", ttl_minutes=RESET_TTL_MINUTES, build=reset_mail
            )
        return {"ok": True, "message": FORGOT_MESSAGE}

    def reset_password(self, token: str, password: str) -> dict:
        """The emailed reset (plan U7): consume a RESET token (a verification
        token is refused and left unused), set the password — which voids
        every other emailed and pre-auth token — end every session, drop the
        account's push registrations (the lost phone stops receiving child
        notices) and stamp the address verified: the inbox just proved
        control. An account that was waiting for verification is linked now.

        Returns a fresh session, except for an active provider account,
        which must still pass its second factor: the client lands it on
        sign-in instead (execution decisions, Release 1b)."""
        record = self.dao.consume_reset_token(hash_session_token(token), "reset")
        if not record:
            raise BadRequestError(RESET_INVALID_MESSAGE)
        user_id = str(record["user_id"])
        self.dao.set_user_password(user_id, hash_password(password))
        self.dao.revoke_all_sessions(user_id)
        self.dao.delete_push_registrations(user_id)
        self._complete_verification(user_id)
        user = self.dao.get_user_by_id(user_id)
        if user is None:
            raise BadRequestError(RESET_INVALID_MESSAGE)
        if user.get("is_provider"):
            return {"ok": True}
        return {"ok": True, **self._issue_session(user)}

    def verify_email(self, user: dict | None, token: str) -> dict:
        """Session-bound verification (plan U8): the signed-in caller must own
        the token. Another account's token is refused and left working for its
        owner; an already verified (or grandfathered) caller is simply told
        so. Success stamps the address and runs the linking rule."""
        if not user:
            raise VerificationError("sign-in-required", VERIFY_SIGN_IN_MESSAGE)
        user_id = str(user["id"])
        token_hash = hash_session_token(token)
        owner = self.dao.live_verify_token_owner(token_hash)
        if owner is not None and owner != user_id:
            raise VerificationError("wrong-account", VERIFY_SIGN_IN_MESSAGE)
        if user.get("email_verified"):
            return {"ok": True, "emailVerified": True}
        if owner is None or not self.dao.consume_verify_token(token_hash, user_id):
            raise VerificationError("link-invalid", VERIFY_LINK_INVALID_MESSAGE)
        self._complete_verification(user_id)
        return {"ok": True, "emailVerified": True}

    def resend_verification(self, user: dict) -> dict:
        """A new verification link for the signed-in, still unverified
        caller: earlier verification links stop working. Under the cap or
        over it, the same answer."""
        if user.get("email_verified"):
            return {"ok": True, "emailVerified": True}
        self._send_token_mail(
            {"id": user["id"], "email": user["email"]},
            purpose="verify",
            ttl_minutes=VERIFY_TTL_MINUTES,
            build=verification_mail,
            void_earlier=True,
        )
        return {"ok": True, "emailVerified": False}

    def change_password(self, user: dict, current_password: str, new_password: str) -> None:
        """Authenticated password change (R29): ALWAYS requires the correct
        current password — a forced first change included — then rotates the
        hash, clears the temporary flag/expiry, and revokes every session but
        the calling one. Push registrations are kept: a voluntary change is
        not a lost phone (hardening U9).

        A session created by PIN is refused (hardening U9, DL13: it never
        held the account password) — except while the account must replace
        an office-issued temporary password: that session is held on the
        change screen, and the temporary password it must type is still
        required, so refusing would only strand it there."""
        if user.get("pin_session") and not user.get("must_change_password"):
            raise PinSessionError(PIN_SESSION_CHANGE_MESSAGE)
        credentials = self.dao.get_user_credentials(user["id"])
        if not credentials or not _password_matches(credentials, current_password):
            raise BadRequestError("Current password is incorrect")
        self.dao.set_user_password(user["id"], hash_password(new_password))
        self.dao.revoke_other_sessions(user["id"], user["session_id"])
