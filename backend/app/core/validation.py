"""Input validation helpers — Kenyan mobile numbers and email addresses (#13).

Phone numbers are normalised to E.164 (`+2547XXXXXXXX` / `+2541XXXXXXXX`) so
the rest of the system stores one canonical format regardless of how the admin
typed it (``0712 345 678``, ``+254712345678``, ``254712345678`` all accepted).
"""
import re

from app.core.errors import BadRequestError

_PHONE_CLEAN = re.compile(r"[\s\-()./]+")
# National significant number for a Kenyan mobile: 7XXXXXXXX or 1XXXXXXXX.
_KE_MOBILE = re.compile(r"^(?:7|1)\d{8}$")
# Landlines / other fixed lines: 9 national digits not starting with 0.
_KE_LANDLINE = re.compile(r"^[2-9]\d{6,8}$")
# Deliberately permissive but structurally sound email check.
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")


def normalize_kenyan_phone(raw: str | None, *, allow_landline: bool = False) -> str | None:
    """Return the E.164 form of a valid Kenyan number, or ``None`` if invalid
    or empty. Mobile numbers (07xx / 01xx) are always accepted; landlines only
    when ``allow_landline`` is set."""
    if raw is None:
        return None
    cleaned = _PHONE_CLEAN.sub("", str(raw).strip())
    if not cleaned:
        return None
    if cleaned.startswith("+254"):
        national = cleaned[4:]
    elif cleaned.startswith("254"):
        national = cleaned[3:]
    elif cleaned.startswith("0"):
        national = cleaned[1:]
    else:
        national = cleaned
    if not national.isdigit():
        return None
    if _KE_MOBILE.match(national):
        return "+254" + national
    if allow_landline and _KE_LANDLINE.match(national):
        return "+254" + national
    return None


def is_valid_email(raw: str | None) -> bool:
    return bool(raw and _EMAIL.match(str(raw).strip()))


def clean_phone(
    raw: str | None, *, field: str = "phone number", allow_landline: bool = False
) -> str | None:
    """Validate and normalise an optional phone field. Empty → ``None``;
    a non-empty invalid value raises :class:`BadRequestError`."""
    if raw is None or not str(raw).strip():
        return None
    normalized = normalize_kenyan_phone(raw, allow_landline=allow_landline)
    if not normalized:
        raise BadRequestError(
            f"Enter a valid Kenyan mobile number for {field} "
            f"(e.g. 0712 345 678 or +254712345678)"
        )
    return normalized


def clean_email(raw: str | None, *, required: bool = False, field: str = "email") -> str | None:
    """Validate an optional/required email field. Empty → ``None`` (or raise if
    required); a malformed value raises :class:`BadRequestError`."""
    if raw is None or not str(raw).strip():
        if required:
            raise BadRequestError(f"A valid {field} is required")
        return None
    value = str(raw).strip()
    if not is_valid_email(value):
        raise BadRequestError(f"Enter a valid {field} address")
    return value
