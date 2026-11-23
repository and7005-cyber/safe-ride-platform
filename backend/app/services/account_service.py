from app.core.config import get_settings
from app.core.errors import BadRequestError, ConflictError
from app.core.request_context import log_unexpected_error
from app.core.scope import SchoolScope
from app.core.security import (
    create_session_token,
    generate_relayed_temporary_password,
    hash_password,
    hash_pin_hmac,
    hash_session_token,
)
from app.core.validation import clean_email, clean_phone
from app.dao.account_dao import AccountDao
from app.services.mail_service import (
    VERIFY_TTL_MINUTES,
    MailService,
    email_changed_notice,
    get_mail_service,
    office_password_notice,
    verification_mail,
)

# One neutral wording for every email collision (R34's sibling rule): the
# address may exist at another school, as a parent, or as a provider — the
# message must never disclose where, or that anywhere else exists.
EMAIL_IN_USE_MESSAGE = "That email is already in use"


def _map_unique_violation(exc: Exception) -> Exception:
    """Translate the two relevant unique-index races into their contract
    errors; anything else propagates unchanged."""
    text = str(exc)
    if "app_users_pin_hash_key" in text:
        return ConflictError("That PIN is already in use by another driver")
    if "app_users_email_key" in text:
        return BadRequestError(EMAIL_IN_USE_MESSAGE)
    return exc


class AccountService:
    def __init__(self, dao: AccountDao | None = None, mail: MailService | None = None) -> None:
        self.dao = dao or AccountDao()
        self._mail = mail
        self.pepper = get_settings().pin_pepper

    @property
    def mail(self) -> MailService:
        if self._mail is None:
            self._mail = get_mail_service()
        return self._mail

    def list_drivers(self, scope: SchoolScope):
        return self.dao.list_drivers(scope)

    def create_driver(self, scope: SchoolScope, email, password, full_name, phone, pin, *, actor: dict):
        email = clean_email(email, required=True)
        phone = clean_phone(phone, field="driver phone")
        if self.dao.email_exists(email):
            # An email in use ANYWHERE is refused with the generic message —
            # never naming a school (R28/R34); the unique index is the
            # race-proof backstop below.
            raise BadRequestError(EMAIL_IN_USE_MESSAGE)
        if not password or len(password) < 6:
            raise BadRequestError("Password must be at least 6 characters")
        pin_hash = hash_pin_hmac(pin, self.pepper) if pin else None
        try:
            return self.dao.create_driver(
                scope, email, hash_password(password), full_name, phone, pin_hash,
                actor=actor,
            )
        except Exception as exc:  # unique pin/email index → collision
            raise _map_unique_violation(exc) from exc

    def update_driver(self, scope: SchoolScope, driver_id, full_name, email, phone, pin, *, actor: dict):
        email = clean_email(email, required=True)
        phone = clean_phone(phone, field="driver phone")
        # Blank PIN keeps the existing PIN; a 4-digit value replaces it.
        pin_hash = hash_pin_hmac(pin, self.pepper) if pin else None
        try:
            return self.dao.update_driver(
                scope, driver_id, full_name, email, phone, pin_hash, actor=actor
            )
        except Exception as exc:
            raise _map_unique_violation(exc) from exc

    def delete_driver(self, scope: SchoolScope, driver_id, *, actor: dict):
        self.dao.delete_driver(scope, driver_id, actor=actor)

    def list_parents(self, scope: SchoolScope):
        return self.dao.list_parents(scope)

    def update_parent(self, scope: SchoolScope, parent_id, full_name, email, phone, *, actor: dict):
        """A changed email restarts verification (hardening Release 1b): the
        DAO voids the old address's tokens, sessions and push registrations
        and writes a verification token in the same transaction; after the
        commit the verification mail goes to the NEW address and a neutral
        notice to the OLD one. Staff-initiated and audited, so the public
        endpoints' per-recipient cap does not apply."""
        email = clean_email(email, required=True)
        phone = clean_phone(phone, field="parent phone")
        raw = create_session_token()
        try:
            row = self.dao.update_parent(
                scope, parent_id, full_name, email, phone, actor=actor,
                verify_token_hash=hash_session_token(raw),
                verify_ttl_minutes=VERIFY_TTL_MINUTES,
            )
        except Exception as exc:
            raise _map_unique_violation(exc) from exc
        previous = row.pop("previous_email", None)
        if row.pop("email_changed", False):
            self.mail.send(verification_mail(row["email"], raw))
            if previous:
                self.mail.send(email_changed_notice(previous))
        return row

    def delete_parent(self, scope: SchoolScope, parent_id, *, actor: dict):
        self.dao.delete_parent(scope, parent_id, actor=actor)

    # --- office parent reset and Confirm and reset (hardening U9) ------------

    def reset_parent_password(self, scope: SchoolScope, parent_id, *, actor: dict):
        """A temporary password for a parent the office cannot otherwise get
        back in: relayed by phone, so drawn from the unambiguous alphabet
        (DL6). Revealed here once — never logged, never audited — and the
        parent is mailed a neutral notice (no password, no link) after the
        commit. A failed send never fails the request (mail service)."""
        password = generate_relayed_temporary_password()
        target = self.dao.reset_parent_password(
            scope, parent_id, hash_password(password), actor=actor
        )
        self.mail.send(office_password_notice(target["email"]))
        return {"ok": True, "temporaryPassword": password}

    def confirm_parent_slot(self, scope: SchoolScope, email, *, actor: dict):
        """"Confirm and reset" (execution decisions, Release 1b): verify the
        unverified account behind an Awaiting-signup slot, give it a temporary
        password, then run the sign-up linking rule — the same one the
        owner's own verification runs, with its office incidents — and mail
        the neutral notice. Returns the password once.

        The linking runs after the verification commits. Should it fail, the
        password is still returned (the office would otherwise hold an
        account it can neither see nor reset); the failure is logged with the
        request id, and the office's next save of the student record links
        the now verified account (the student sync)."""
        email = clean_email(email, required=True)
        password = generate_relayed_temporary_password()
        target = self.dao.confirm_parent_slot(
            scope, email, hash_password(password), actor=actor
        )
        try:
            self.dao.link_parent_to_matching_students(target["id"], target["email"])
        except Exception as error:  # noqa: BLE001 — the credential is already issued
            log_unexpected_error(error)
        self.mail.send(office_password_notice(target["email"]))
        return {"ok": True, "temporaryPassword": password}
