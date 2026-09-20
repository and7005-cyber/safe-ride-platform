"""The provider second factor's arithmetic (U10): RFC vectors, the ±1
window, the replay guard, salt/pepper derivation and the pepper key id."""

import pytest

from app.core.totp import (
    STEP_SECONDS,
    current_step,
    derive_secret,
    pepper_key,
    provisioning_uri,
    secret_b32,
    totp_code,
    verify_code,
)

# RFC 6238 Appendix B, SHA-1 rows: (unix time, 8-digit code). The spec prints
# eight digits; the six-digit form is the same dynamically-truncated value
# modulo 10^6 (RFC 4226 §5.3), i.e. the last six digits.
RFC_SECRET = b"12345678901234567890"
RFC_VECTORS = [
    (59, 94287082),
    (1111111109, 7081804),
    (1111111111, 14050471),
    (1234567890, 89005924),
    (2000000000, 69279037),
    (20000000000, 65353130),
]


@pytest.mark.parametrize(("at", "eight_digit"), RFC_VECTORS)
def test_rfc6238_appendix_b_sha1_vectors(at, eight_digit):
    step = at // STEP_SECONDS
    assert current_step(at) == step
    assert totp_code(RFC_SECRET, step) == f"{eight_digit % 10 ** 6:06d}"


def test_verify_accepts_the_current_step_and_returns_it():
    step = 1111111109 // STEP_SECONDS
    code = totp_code(RFC_SECRET, step)
    assert verify_code(RFC_SECRET, code, step, None) == step


def test_previous_step_code_accepted_once_then_refused_on_reuse():
    now = 41152263
    previous_code = totp_code(RFC_SECRET, now - 1)
    # Clock skew: the previous step's code is inside the window…
    matched = verify_code(RFC_SECRET, previous_code, now, None)
    assert matched == now - 1
    # …but once its step is recorded, the SAME code can never pass again.
    assert verify_code(RFC_SECRET, previous_code, now, matched) is None


def test_next_step_code_accepted_within_the_window():
    now = 41152263
    ahead = totp_code(RFC_SECRET, now + 1)
    assert verify_code(RFC_SECRET, ahead, now, None) == now + 1


def test_replay_guard_blocks_every_step_at_or_below_last():
    now = 41152263
    for step in (now - 1, now):
        code = totp_code(RFC_SECRET, step)
        assert verify_code(RFC_SECRET, code, now, now) is None, step
    # A later step stays acceptable: the guard is a floor, not a lockout.
    assert verify_code(RFC_SECRET, totp_code(RFC_SECRET, now + 1), now, now) == now + 1


def test_codes_outside_the_window_are_refused():
    now = 41152263
    for step in (now - 2, now + 2):
        code = totp_code(RFC_SECRET, step)
        if code != totp_code(RFC_SECRET, now):  # avoid the 1-in-10^6 clash
            assert verify_code(RFC_SECRET, code, now, None) is None


def test_malformed_codes_are_refused_without_matching():
    now = 41152263
    for junk in (None, "", "12345", "1234567", "12a456", "abcdef"):
        assert verify_code(RFC_SECRET, junk, now, None) is None
    # Whitespace and the common "123 456" spacing are tolerated.
    spaced = totp_code(RFC_SECRET, now)
    assert verify_code(RFC_SECRET, f" {spaced[:3]} {spaced[3:]} ", now, None) == now


def test_regenerated_salt_produces_a_different_secret():
    pepper = "saferide-local-totp-pepper"
    first = derive_secret(pepper, "5eedab1e5a17c0ffee00000000000001")
    second = derive_secret(pepper, "5eedab1e5a17c0ffee00000000000002")
    assert first != second
    assert len(first) == len(second) == 20
    # Same inputs stay deterministic (the DB never stores the secret).
    assert derive_secret(pepper, "5eedab1e5a17c0ffee00000000000001") == first


def test_pepper_rotation_changes_the_secret_and_the_key_id():
    salt = "5eedab1e5a17c0ffee00000000000001"
    old_pepper, new_pepper = "pepper-v1", "pepper-v2"
    assert derive_secret(old_pepper, salt) != derive_secret(new_pepper, salt)
    # The stored key id detects the rotation: a mismatch means every code
    # from the old enrolment will fail → re-enrolment required.
    stored_at_enrolment = pepper_key(old_pepper)
    assert stored_at_enrolment != pepper_key(new_pepper)
    assert stored_at_enrolment == pepper_key(old_pepper)  # stable fingerprint
    assert len(stored_at_enrolment) == 8


def test_provisioning_uri_and_b32_secret_shape():
    secret = derive_secret("saferide-local-totp-pepper", "aa" * 16)
    encoded = secret_b32(secret)
    assert len(encoded) == 32 and "=" not in encoded  # 20 bytes → 32 chars
    uri = provisioning_uri("provider@kuumbai.test", secret)
    assert uri == (
        "otpauth://totp/SafeRide:provider@kuumbai.test"
        f"?secret={encoded}&issuer=SafeRide"
    )
