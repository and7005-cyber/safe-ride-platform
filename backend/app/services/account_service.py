from app.core.config import get_settings
from app.core.errors import BadRequestError, ConflictError
from app.core.security import hash_password, hash_pin_hmac
from app.dao.account_dao import AccountDao


class AccountService:
    def __init__(self, dao: AccountDao | None = None) -> None:
        self.dao = dao or AccountDao()
        self.pepper = get_settings().pin_pepper

    def list_drivers(self):
        return self.dao.list_drivers()

    def create_driver(self, email, password, full_name, phone, pin):
        if self.dao.email_exists(email):
            raise BadRequestError("An account with this email already exists")
        if not password or len(password) < 6:
            raise BadRequestError("Password must be at least 6 characters")
        pin_hash = hash_pin_hmac(pin, self.pepper) if pin else None
        try:
            return self.dao.create_driver(email, hash_password(password), full_name, phone, pin_hash)
        except Exception as exc:  # unique pin index → collision
            if "app_users_pin_hash_key" in str(exc):
                raise ConflictError("That PIN is already in use by another driver") from exc
            raise

    def update_driver(self, driver_id, full_name, email, phone, pin):
        # Blank PIN keeps the existing PIN; a 4-digit value replaces it.
        pin_hash = hash_pin_hmac(pin, self.pepper) if pin else None
        try:
            return self.dao.update_driver(driver_id, full_name, email, phone, pin_hash)
        except Exception as exc:
            if "app_users_pin_hash_key" in str(exc):
                raise ConflictError("That PIN is already in use by another driver") from exc
            raise

    def delete_driver(self, driver_id):
        self.dao.delete_driver(driver_id)

    def list_parents(self):
        return self.dao.list_parents()

    def update_parent(self, parent_id, full_name, email, phone):
        return self.dao.update_parent(parent_id, full_name, email, phone)

    def delete_parent(self, parent_id):
        self.dao.delete_parent(parent_id)

    def list_parent_students(self):
        return self.dao.list_parent_students()

    def link_parent_student(self, parent_id, student_id):
        return self.dao.link_parent_student(parent_id, student_id)

    def unlink_parent_student(self, link_id):
        self.dao.unlink_parent_student(link_id)
