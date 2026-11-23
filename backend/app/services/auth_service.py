import datetime as dt
import logging

from psycopg import errors as pg_errors

from app.core.config import get_settings
from app.core.errors import BadRequestError, UnauthorizedError
from app.core.request_context import current_request_id
from app.core.security import (
    create_session_token,
    hash_password,
    hash_session_token,
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


class VerificationError(BadRequestError):
    """A verify-email refusal the client branches on (DL10's states):
    ``sign-in-required`` (no session), ``wrong-account`` (the token belongs
    to another account — signed in as someone else) and ``link-invalid``
    (expired, used or unknown)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


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

    def _issue_session(self, user: dict) -> dict:
        token = create_session_token()
        self.dao.create_session(user["id"], hash_session_token(token))
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
        if not user or not verify_password(password, user["password_hash"]):
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

    def pin_login(self, pin: str) -> dict:
        matches = [
            u
            for u in self.dao.list_driver_pin_users()
            if verify_pin_hmac(pin, u["pin_hash"], self.pepper)
        ]
        if len(matches) != 1:
            raise UnauthorizedError("Invalid PIN")
        user = self.dao.get_user_by_id(matches[0]["id"])
        return self._issue_session(user)

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
        the calling one."""
        credentials = self.dao.get_user_credentials(user["id"])
        if not credentials or not verify_password(
            current_password, credentials["password_hash"]
        ):
            raise BadRequestError("Current password is incorrect")
        self.dao.set_user_password(user["id"], hash_password(new_password))
        self.dao.revoke_other_sessions(user["id"], user["session_id"])
