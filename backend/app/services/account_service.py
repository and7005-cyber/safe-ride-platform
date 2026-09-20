from app.core.config import get_settings
from app.core.errors import BadRequestError, ConflictError
from app.core.scope import SchoolScope
from app.core.security import hash_password, hash_pin_hmac
from app.core.validation import clean_email, clean_phone
from app.dao.account_dao import AccountDao

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
    def __init__(self, dao: AccountDao | None = None) -> None:
        self.dao = dao or AccountDao()
        self.pepper = get_settings().pin_pepper

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
        email = clean_email(email, required=True)
        phone = clean_phone(phone, field="parent phone")
        try:
            return self.dao.update_parent(
                scope, parent_id, full_name, email, phone, actor=actor
            )
        except Exception as exc:
            raise _map_unique_violation(exc) from exc

    def delete_parent(self, scope: SchoolScope, parent_id, *, actor: dict):
        self.dao.delete_parent(scope, parent_id, actor=actor)
