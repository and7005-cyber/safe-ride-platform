import pytest

from app.core.security import (
    PIN_SESSION_TOKEN_PREFIX,
    RELAYED_CODE_ALPHABET,
    create_pin_session_token,
    create_session_token,
    generate_relayed_temporary_password,
    hash_pin,
    hash_session_token,
    is_pin_session_token,
    normalise_relayed_code,
    verify_pin,
)


def test_hash_pin_round_trips_with_valid_pin() -> None:
    pin_hash = hash_pin("1234", salt="demo-driver-salt")

    assert (
        pin_hash
        == "pbkdf2_sha256$200000$demo-driver-salt$ooEh79F7IwGlxeLQ4G000PzDJkAtL1EHMqH7/qj6jb0="
    )
    assert verify_pin("1234", pin_hash) is True
    assert verify_pin("9999", pin_hash) is False


def test_hash_pin_rejects_invalid_pin() -> None:
    with pytest.raises(ValueError, match="Driver PIN must be 4 to 6 digits"):
        hash_pin("12ab")


def test_verify_pin_rejects_malformed_hashes() -> None:
    assert verify_pin("1234", "not-a-valid-hash") is False
    assert verify_pin("1234", "pbkdf2_sha512$200000$salt$digest") is False
    assert verify_pin("1234", "pbkdf2_sha256$abc$salt$digest") is False
    assert verify_pin("1234", "pbkdf2_sha256$999999999$salt$digest") is False


def test_session_tokens_are_hashable_and_not_empty() -> None:
    session_token = create_session_token()

    assert len(session_token) == 64
    assert hash_session_token(session_token) == hash_session_token(session_token)
    assert hash_session_token(session_token) != session_token


# --- hardening U9 ----------------------------------------------------------------


def test_pin_session_tokens_carry_their_marker_and_others_do_not() -> None:
    pin_token = create_pin_session_token()
    assert pin_token.startswith(PIN_SESSION_TOKEN_PREFIX)
    assert len(pin_token) == len(PIN_SESSION_TOKEN_PREFIX) + 64
    assert is_pin_session_token(pin_token) is True
    assert is_pin_session_token(create_session_token()) is False
    assert is_pin_session_token("") is False
    assert is_pin_session_token(None) is False
    # The marker is inside the hashed token: stripping it names another hash.
    assert hash_session_token(pin_token) != hash_session_token(
        pin_token[len(PIN_SESSION_TOKEN_PREFIX):]
    )


def test_relayed_temporary_passwords_use_the_unambiguous_alphabet() -> None:
    # DL6: the driver code's 32 symbols (017) — no I, L, O or U to misread.
    assert len(RELAYED_CODE_ALPHABET) == 32
    assert not set("ILOU") & set(RELAYED_CODE_ALPHABET)
    seen = set()
    for _ in range(200):
        password = generate_relayed_temporary_password()
        assert len(password) == 16
        assert set(password) <= set(RELAYED_CODE_ALPHABET)
        seen.add(password)
    assert len(seen) == 200


@pytest.mark.parametrize(
    "typed",
    [
        "AB01CDEF2345GHJK",
        "ab01cdef2345ghjk",
        "AB01 CDEF 2345 GHJK",
        "ab01-cdef-2345-ghjk",
        " aBoI\tcdef 2345 ghjk ",
        "ABOL-CDEF-2345-GHJK",
    ],
)
def test_a_typed_relayed_code_normalises_to_its_stored_form(typed: str) -> None:
    assert normalise_relayed_code(typed) == "AB01CDEF2345GHJK"
