"""Staff account management for one school (U8: R5, R6, R10, R12).

The service is the reuse seam for U10: the provider console creates a fresh
school's first director through :meth:`StaffService.create_staff` with a
scope it constructs for the new school — the response contract (created →
temporary password once; existing → offered) is identical there.

Temporary passwords come from ``secrets.token_urlsafe`` (URL-safe, 16
characters — comfortably over the 12-character floor), are returned to the
caller EXACTLY ONCE and are never logged or written into audit detail.
"""

import secrets
from typing import Any

from app.core.errors import BadRequestError
from app.core.scope import STAFF_ROLES, SchoolScope
from app.core.security import hash_password
from app.core.validation import clean_email
from app.dao.membership_dao import MembershipDao

TEMPORARY_PASSWORD_LENGTH = 16  # ≥ 12 required by R6


def generate_temporary_password() -> str:
    """URL-safe server-generated credential, trimmed to the fixed length.
    ``token_urlsafe(n)`` yields ~1.3 characters per byte, so requesting the
    target length in bytes always leaves enough to trim."""
    return secrets.token_urlsafe(TEMPORARY_PASSWORD_LENGTH)[:TEMPORARY_PASSWORD_LENGTH]


def _offered_response(email: str, role: str) -> dict[str, Any]:
    """The ONE offered answer. Real offers, idempotent repeats, provider
    emails and disabled emails all return this exact shape, so the response
    can never confirm whether — or in what state — an account exists (R6's
    anti-enumeration rule)."""
    return {"status": "offered", "email": email, "role": role}


class StaffService:
    def __init__(self, dao: MembershipDao | None = None) -> None:
        self.dao = dao or MembershipDao()

    def create_staff(
        self, scope: SchoolScope, email: str, full_name: str | None, role: str,
        *, actor: dict,
    ) -> dict[str, Any]:
        if role not in STAFF_ROLES:
            raise BadRequestError("Role must be director or coordinator")
        email = clean_email(email, required=True)
        # app_users.full_name is NOT NULL; without a name the email's local
        # part stands in until the person edits their profile.
        full_name = str(full_name or "").strip() or email.split("@", 1)[0]

        existing = self.dao.find_user_by_email(email)
        if existing is not None:
            if existing["disabled_at"] is not None or existing["is_provider"]:
                # Create nothing, audit nothing — and answer exactly like a
                # real offer so the caller learns nothing about the account.
                return _offered_response(email, role)
            self.dao.offer_role(scope, str(existing["id"]), role, actor=actor)
            return _offered_response(email, role)

        password = generate_temporary_password()
        user = self.dao.create_staff_user(
            scope, email, hash_password(password), full_name, role, actor=actor
        )
        return {
            "status": "created",
            "userId": str(user["id"]),
            "email": user["email"],
            "fullName": user.get("full_name"),
            "role": role,
            # Revealed once, here only: never logged, never audited.
            "temporaryPassword": password,
        }

    def list_staff(self, scope: SchoolScope) -> dict[str, Any]:
        data = self.dao.list_staff(scope)
        return {
            "members": [
                {
                    "userId": str(m["user_id"]),
                    "membershipId": str(m["membership_id"]),
                    "fullName": m.get("full_name"),
                    "email": m["email"],
                    "role": m["role"],
                    "state": "active",
                    "passwordSetOn": (
                        m["password_changed_at"].date().isoformat()
                        if m.get("password_changed_at")
                        else None
                    ),
                }
                for m in data["members"]
            ],
            "offers": [
                {
                    "id": str(o["id"]),
                    "userId": str(o["user_id"]),
                    "fullName": o.get("full_name"),
                    "email": o["email"],
                    "role": o["role"],
                    "state": "offered",
                    "offeredBy": o.get("offered_by_name"),
                    "offeredAt": (
                        o["created_at"].isoformat() if o.get("created_at") else None
                    ),
                }
                for o in data["offers"]
            ],
        }

    def remove_staff(self, scope: SchoolScope, user_id: str, *, actor: dict) -> None:
        self.dao.remove_staff(scope, user_id, actor=actor)

    def cancel_offer(self, scope: SchoolScope, offer_id: str, *, actor: dict) -> None:
        self.dao.cancel_offer(scope, offer_id, actor=actor)

    def reset_password(
        self, scope: SchoolScope, user_id: str, *, actor: dict
    ) -> dict[str, Any]:
        password = generate_temporary_password()
        self.dao.reset_staff_password(
            scope, user_id, hash_password(password), actor=actor
        )
        # Revealed once, here only (AE23).
        return {"ok": True, "temporaryPassword": password}
