import base64
import hashlib
import hmac
import re
import secrets
import uuid

PIN_HASH_SCHEME = "pbkdf2_sha256"
PIN_HASH_ITERATIONS = 200_000
PASSWORD_HASH_SCHEME = "pbkdf2_sha256"
PASSWORD_HASH_ITERATIONS = 200_000
PIN_HMAC_SCHEME = "hmac_sha256"


def hash_password(password: str, salt: str | None = None) -> str:
    """Hash an arbitrary-length password. Unlike hash_pin, no digit/length rule."""
    if not password:
        raise ValueError("Password must not be empty")

    salt_value = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt_value.encode("utf-8"),
        PASSWORD_HASH_ITERATIONS,
    )
    encoded = base64.b64encode(digest).decode("ascii")
    return f"{PASSWORD_HASH_SCHEME}${PASSWORD_HASH_ITERATIONS}${salt_value}${encoded}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        scheme, iterations, salt, encoded_digest = stored_hash.split("$", 3)
    except (ValueError, AttributeError):
        return False

    if scheme != PASSWORD_HASH_SCHEME:
        return False

    try:
        iteration_count = int(iterations)
    except ValueError:
        return False

    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iteration_count,
    )
    expected = base64.b64encode(digest).decode("ascii")
    return hmac.compare_digest(expected, encoded_digest)


def hash_pin_hmac(pin: str, pepper: str) -> str:
    """Deterministic keyed PIN hash so PINs can be DB-uniquely indexed (KTD-4)."""
    if not pin.isdigit() or not 4 <= len(pin) <= 6:
        raise ValueError("Driver PIN must be 4 to 6 digits")
    digest = hmac.new(
        pepper.encode("utf-8"), pin.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{PIN_HMAC_SCHEME}${digest}"


def verify_pin_hmac(pin: str, stored_hash: str, pepper: str) -> bool:
    try:
        return hmac.compare_digest(hash_pin_hmac(pin, pepper), stored_hash)
    except (ValueError, AttributeError, TypeError):
        return False


# --- school-salted PINs (hardening U10) ------------------------------------------
# A PIN resolves only inside the school the phone is bound to. New and
# re-issued PINs are stored as "v2:k1:" + HMAC-SHA256(pepper, canonical school
# id + ":" + PIN): the global unique index on app_users.pin_hash then refuses
# a PIN twice within one school and allows it at two schools, and no message
# can confirm another school's PIN. "k1" names the pepper that keyed the hash
# (PIN_PEPPER today), so a future rotation can verify under both keys instead
# of re-issuing every PIN on one day. The legacy global form above stays
# readable: sign-in verifies it among the bound school's drivers only and
# rewrites the row to this form on success.
PIN_V2_PREFIX = "v2:k1:"
_PIN_SIGN_IN = re.compile(r"[0-9]{4,6}")
PIN_LENGTH = 6

# Refused for a NEW PIN on top of the rules in pin_is_too_easy (repeated
# digits, runs, a repeated two- or three-digit pattern). Mirrored by
# frontend/src/lib/driverPin.ts, whose generator samples until both agree —
# a unit test there compares this tuple with its own list.
PIN_DENYLIST = (
    "112233", "123321", "159753", "159357", "147258", "147852", "258369",
    "789456", "456123", "102030", "010203", "112211", "001122", "000123",
    "998877",
)

PIN_LENGTH_MESSAGE = "PIN must be 6 digits"
PIN_TOO_EASY_MESSAGE = "That PIN is too easy to guess"


class PinRefused(ValueError):
    """A PIN the rules refuse. ``code`` is ``pin-format`` (not four to six
    ASCII digits), ``pin-length`` (a new PIN that is not six digits) or
    ``pin-too-easy`` (a new PIN that is repeated, a run, a repeated pattern or
    on the denylist)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def pin_is_too_easy(pin: str) -> bool:
    """Repeated digits, an ascending or descending run (wrapping past 9),
    a two- or three-digit pattern repeated, or a denylisted value."""
    digits = [int(ch) for ch in pin]
    steps = {(b - a) % 10 for a, b in zip(digits, digits[1:])}
    if len(steps) == 1 and steps <= {0, 1, 9}:
        return True
    if any(pin == pin[:period] * (len(pin) // period) for period in (2, 3)):
        return True
    return pin in PIN_DENYLIST


def validate_pin(pin: str, *, for_creation: bool) -> str:
    """The PIN rules (hardening U10). Sign-in accepts four to six ASCII
    digits — shorter legacy PINs keep working until they are re-issued. A
    new or re-issued PIN must be exactly six digits and not an easy guess.
    Returns the PIN; raises PinRefused."""
    if not isinstance(pin, str) or not _PIN_SIGN_IN.fullmatch(pin):
        if for_creation:
            raise PinRefused("pin-length", PIN_LENGTH_MESSAGE)
        raise PinRefused("pin-format", "Driver PIN must be 4 to 6 digits")
    if not for_creation:
        return pin
    if len(pin) != PIN_LENGTH:
        raise PinRefused("pin-length", PIN_LENGTH_MESSAGE)
    if pin_is_too_easy(pin):
        raise PinRefused("pin-too-easy", PIN_TOO_EASY_MESSAGE)
    return pin


def hash_pin_v2(school_id, pin: str, pepper: str) -> str:
    """The school-salted PIN hash. Deterministic (a unique index serves it)
    and keyed on the canonical form of the school id, so any spelling of the
    same uuid salts alike. Raises ValueError for a malformed id or PIN."""
    canonical = str(uuid.UUID(str(school_id)))
    validate_pin(pin, for_creation=False)
    digest = hmac.new(
        pepper.encode("utf-8"), f"{canonical}:{pin}".encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return PIN_V2_PREFIX + digest


def is_v2_pin_hash(stored_hash: str | None) -> bool:
    return isinstance(stored_hash, str) and stored_hash.startswith("v2:")


def hash_pin(pin: str, salt: str | None = None) -> str:
    if not pin.isdigit() or not 4 <= len(pin) <= 6:
        raise ValueError("Driver PIN must be 4 to 6 digits")

    salt_value = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        pin.encode("utf-8"),
        salt_value.encode("utf-8"),
        PIN_HASH_ITERATIONS,
    )
    encoded = base64.b64encode(digest).decode("ascii")
    return f"{PIN_HASH_SCHEME}${PIN_HASH_ITERATIONS}${salt_value}${encoded}"


def verify_pin(pin: str, stored_hash: str) -> bool:
    try:
        scheme, iterations, salt, encoded_digest = stored_hash.split("$", 3)
    except ValueError:
        return False

    if scheme != PIN_HASH_SCHEME:
        return False

    try:
        iteration_count = int(iterations)
    except ValueError:
        return False

    if iteration_count != PIN_HASH_ITERATIONS:
        return False

    digest = hashlib.pbkdf2_hmac(
        "sha256",
        pin.encode("utf-8"),
        salt.encode("utf-8"),
        iteration_count,
    )
    expected = base64.b64encode(digest).decode("ascii")
    return hmac.compare_digest(expected, encoded_digest)


def create_session_token() -> str:
    return secrets.token_hex(32)


# --- sessions created by PIN (hardening U9) -------------------------------------
# A driver's PIN session never held the account password (the office set it),
# so it may not change it (DL13). The marker lives in the token itself rather
# than in a new column (Release 1b adds no migration): the stored hash covers
# the whole token, prefix included, so a password session's token cannot be
# turned into a PIN token — or the reverse — without matching no session at
# all.
PIN_SESSION_TOKEN_PREFIX = "pin_"


def create_pin_session_token() -> str:
    return PIN_SESSION_TOKEN_PREFIX + create_session_token()


def is_pin_session_token(session_token: str | None) -> bool:
    return bool(session_token) and session_token.startswith(PIN_SESSION_TOKEN_PREFIX)


# --- human-relayed temporary passwords (hardening U9, DL6) ----------------------
# The office reads a parent's temporary password out over the phone, so it is
# drawn from the driver code's alphabet (migration 017: Crockford base32, no
# I, L, O or U), shown grouped in fours and accepted however the parent types
# it: any case, with or without the spaces or hyphens between the groups, and
# with O for 0 and I or L for 1. Sixteen characters carry 80 bits.
RELAYED_CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
RELAYED_TEMPORARY_PASSWORD_LENGTH = 16
_RELAYED_CODE_LOOKALIKES = str.maketrans({"O": "0", "I": "1", "L": "1"})


def generate_relayed_temporary_password(
    length: int = RELAYED_TEMPORARY_PASSWORD_LENGTH,
) -> str:
    """Uniform over the 32-symbol alphabet (``secrets.choice``, no modulo
    bias); stored and revealed ungrouped, uppercase."""
    return "".join(secrets.choice(RELAYED_CODE_ALPHABET) for _ in range(length))


def normalise_relayed_code(value: str) -> str:
    """The canonical form of a typed relayed code: uppercase, whitespace and
    hyphens removed, the look-alike letters mapped to their digits."""
    stripped = "".join(ch for ch in str(value or "") if not ch.isspace() and ch != "-")
    return stripped.upper().translate(_RELAYED_CODE_LOOKALIKES)


# --- the driver code (hardening U10, execution decision D2) ----------------------
# live_schools.driver_code: eight symbols of the same alphabet, drawn by the
# database (saferide_new_driver_code(), migration 017), stored uppercase with
# no hyphen and shown XXXX-XXXX. The driver types it once per phone; the
# public AAA-NNN value stays the "school code".
DRIVER_CODE_LENGTH = 8


def normalise_driver_code(value: str) -> str:
    """A typed driver code in its stored form (any case, hyphens and spaces
    removed, O read as 0 and I or L as 1)."""
    return normalise_relayed_code(value)


def is_driver_code(value: str) -> bool:
    """Whether a NORMALISED value can be a driver code at all."""
    return (
        isinstance(value, str)
        and len(value) == DRIVER_CODE_LENGTH
        and all(ch in RELAYED_CODE_ALPHABET for ch in value)
    )


def format_driver_code(code: str) -> str:
    return f"{code[:4]}-{code[4:]}"


def hash_session_token(session_token: str) -> str:
    return hashlib.sha256(session_token.encode("utf-8")).hexdigest()
