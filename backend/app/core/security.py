import base64
import hashlib
import hmac
import secrets

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
    except (ValueError, AttributeError):
        return False


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


def hash_session_token(session_token: str) -> str:
    return hashlib.sha256(session_token.encode("utf-8")).hexdigest()
