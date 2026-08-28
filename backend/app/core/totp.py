"""Time-based one-time passwords for provider accounts (U10, stdlib only).

The database never stores the shared secret: each provider row keeps a random
``totp_salt`` and the secret is re-derived on every verification from the
deployment-wide ``TOTP_PEPPER`` (SSM-held in production) —
``HMAC-SHA256(pepper, salt)`` truncated to 20 bytes, the classic SHA-1 TOTP
key size. Rotating the pepper therefore invalidates every enrolment at once;
``pepper_key`` stamps a short fingerprint of the pepper on the row at
enrolment so a later mismatch names that cause (re-enrolment required)
instead of looking like a wrong code.

Verification accepts the current 30-second step and its two neighbours
(clock skew), refuses any step at or below the last accepted one (replay
guard, R20) and compares in constant time.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import struct
import time

STEP_SECONDS = 30
CODE_DIGITS = 6
SECRET_BYTES = 20  # RFC 4226 SHA-1 key size


def derive_secret(pepper: str, salt: str) -> bytes:
    """The per-account TOTP secret: keyed hash of the salt, 20 bytes."""
    return hmac.new(
        pepper.encode("utf-8"), salt.encode("utf-8"), hashlib.sha256
    ).digest()[:SECRET_BYTES]


def current_step(at: float | None = None) -> int:
    """The RFC 6238 time-step counter for a unix time (default: now)."""
    return int((time.time() if at is None else at) // STEP_SECONDS)


def totp_code(secret: bytes, step: int) -> str:
    """RFC 6238/4226: HMAC-SHA1 over the big-endian step counter, dynamic
    truncation, six decimal digits."""
    message = struct.pack(">Q", step)
    digest = hmac.new(secret, message, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = (
        ((digest[offset] & 0x7F) << 24)
        | (digest[offset + 1] << 16)
        | (digest[offset + 2] << 8)
        | digest[offset + 3]
    )
    return f"{binary % 10 ** CODE_DIGITS:0{CODE_DIGITS}d}"


def verify_code(
    secret: bytes, code: str, now_step: int, last_step: int | None
) -> int | None:
    """Match ``code`` against ``now_step`` and its ±1 neighbours.

    Returns the matched step (the caller persists it as the new
    ``totp_last_step``) or ``None``. Any candidate at or below ``last_step``
    is skipped — a code, even a still-in-window one, can never be accepted
    twice. Comparison is constant-time per candidate and does not
    short-circuit across candidates.
    """
    candidate = str(code or "").strip().replace(" ", "")
    if len(candidate) != CODE_DIGITS or not candidate.isdigit():
        return None
    matched: int | None = None
    for step in (now_step - 1, now_step, now_step + 1):
        if step < 0 or (last_step is not None and step <= last_step):
            continue
        if hmac.compare_digest(totp_code(secret, step), candidate) and matched is None:
            matched = step
    return matched


def pepper_key(pepper: str) -> str:
    """Short non-secret fingerprint of the pepper, stored at enrolment.

    A stored key that no longer matches the running pepper means the pepper
    was rotated after this enrolment: every code will fail and the account
    needs re-enrolment (peer reset path)."""
    return hashlib.sha256(pepper.encode("utf-8")).hexdigest()[:8]


def provisioning_uri(email: str, secret: bytes) -> str:
    """The ``otpauth://`` URI an authenticator app enrols from (shown once)."""
    encoded = base64.b32encode(secret).decode("ascii").rstrip("=")
    return f"otpauth://totp/SafeRide:{email}?secret={encoded}&issuer=SafeRide"


def secret_b32(secret: bytes) -> str:
    """The manual-entry form of the secret (no QR dependency this version)."""
    return base64.b32encode(secret).decode("ascii").rstrip("=")
