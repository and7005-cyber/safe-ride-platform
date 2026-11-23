import pytest

import hashlib
import hmac
import uuid

from app.core.security import (
    PIN_DENYLIST,
    PIN_SESSION_TOKEN_PREFIX,
    PIN_V2_PREFIX,
    RELAYED_CODE_ALPHABET,
    PinRefused,
    create_pin_session_token,
    create_session_token,
    generate_relayed_temporary_password,
    hash_pin,
    hash_pin_hmac,
    hash_pin_v2,
    hash_session_token,
    is_driver_code,
    is_pin_session_token,
    is_v2_pin_hash,
    normalise_driver_code,
    normalise_relayed_code,
    validate_pin,
    verify_pin,
    verify_pin_hmac,
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


# --- school-salted PINs (hardening U10) -------------------------------------

PEPPER = "test-pepper"
SCHOOL_A = "5cae0000-0000-0000-0000-000000000001"
SCHOOL_B = "5cae0000-0000-0000-0000-000000000002"


def test_a_salted_pin_hash_is_keyed_on_the_canonical_school_id() -> None:
    salted = hash_pin_v2(SCHOOL_A, "482915", PEPPER)
    expected = hmac.new(
        PEPPER.encode(), f"{SCHOOL_A}:482915".encode(), hashlib.sha256
    ).hexdigest()
    assert salted == PIN_V2_PREFIX + expected
    assert salted.startswith("v2:k1:")
    # Any spelling of the same uuid is the same school.
    assert hash_pin_v2(SCHOOL_A.upper(), "482915", PEPPER) == salted
    assert hash_pin_v2(uuid.UUID(SCHOOL_A).hex, "482915", PEPPER) == salted
    assert hash_pin_v2(uuid.UUID(SCHOOL_A), "482915", PEPPER) == salted


def test_the_same_pin_hashes_differently_at_two_schools_and_unlike_the_legacy_form() -> None:
    a = hash_pin_v2(SCHOOL_A, "482915", PEPPER)
    b = hash_pin_v2(SCHOOL_B, "482915", PEPPER)
    legacy = hash_pin_hmac("482915", PEPPER)
    assert len({a, b, legacy}) == 3
    assert is_v2_pin_hash(a) and is_v2_pin_hash(b)
    assert not is_v2_pin_hash(legacy)
    assert not is_v2_pin_hash(None)
    # The legacy verification is unchanged: it never accepts a salted hash.
    assert verify_pin_hmac("482915", legacy, PEPPER)
    assert not verify_pin_hmac("482915", a, PEPPER)
    # Another pepper is another key: nothing matches.
    assert hash_pin_v2(SCHOOL_A, "482915", "other-pepper") != a


@pytest.mark.parametrize("school_id", ["", "not-a-uuid", None, "MSB-001"])
def test_a_salted_hash_needs_a_real_school_id(school_id) -> None:
    with pytest.raises(ValueError):
        hash_pin_v2(school_id, "482915", PEPPER)


@pytest.mark.parametrize("pin", ["0322", "48291", "482915"])
def test_sign_in_accepts_four_to_six_ascii_digits(pin: str) -> None:
    assert validate_pin(pin, for_creation=False) == pin
    assert hash_pin_v2(SCHOOL_A, pin, PEPPER).startswith(PIN_V2_PREFIX)


@pytest.mark.parametrize(
    "pin",
    ["", "123", "1234567", "12a4", " 1234", "1234 ", "\u0661\u0662\u0663\u0664", "１２３４", None],
)
def test_sign_in_refuses_anything_but_four_to_six_ascii_digits(pin) -> None:
    with pytest.raises(PinRefused):
        validate_pin(pin, for_creation=False)
    with pytest.raises(ValueError):
        hash_pin_v2(SCHOOL_A, pin, PEPPER)


def test_a_new_pin_must_be_exactly_six_digits() -> None:
    assert validate_pin("482915", for_creation=True) == "482915"
    for short in ("4829", "48291", "0322"):
        with pytest.raises(PinRefused) as caught:
            validate_pin(short, for_creation=True)
        assert caught.value.code == "pin-length"
        assert str(caught.value) == "PIN must be 6 digits"


@pytest.mark.parametrize(
    "pin",
    [
        # repeated
        "111111", "000000", "999999",
        # ascending and descending runs, wrapping past 9 too
        "123456", "234567", "456789", "567890", "890123",
        "654321", "987654", "543210", "109876",
        # a two- or three-digit pattern repeated
        "121212", "696969", "101010", "123123", "456456", "520520",
        # the denylist
        "112233", "123321", "159753", "147258",
    ],
)
def test_a_new_pin_refuses_easy_guesses(pin: str) -> None:
    with pytest.raises(PinRefused) as caught:
        validate_pin(pin, for_creation=True)
    assert caught.value.code == "pin-too-easy"
    assert str(caught.value) == "That PIN is too easy to guess"
    # A legacy driver may still SIGN IN with one (re-issue replaces it).
    assert validate_pin(pin, for_creation=False) == pin


def test_every_denylisted_pin_is_six_digits_and_refused() -> None:
    assert PIN_DENYLIST, "the denylist is empty"
    for pin in PIN_DENYLIST:
        assert len(pin) == 6 and pin.isascii() and pin.isdigit()
        with pytest.raises(PinRefused):
            validate_pin(pin, for_creation=True)


@pytest.mark.parametrize("pin", ["482915", "730184", "102938", "120000", "135790"])
def test_ordinary_six_digit_pins_are_accepted(pin: str) -> None:
    assert validate_pin(pin, for_creation=True) == pin


# --- the driver code (D2) -----------------------------------------------------


@pytest.mark.parametrize(
    "typed",
    ["GFA07K1Q", "gfa07k1q", "GFA0-7K1Q", "gfa0 7k1q", " gfaO-7kIq ", "GFAO7KLQ"],
)
def test_a_typed_driver_code_normalises_to_its_stored_form(typed: str) -> None:
    assert normalise_driver_code(typed) == "GFA07K1Q"
    assert is_driver_code(normalise_driver_code(typed))


@pytest.mark.parametrize("value", ["", "GFA-001", "GFA07K1", "GFA07K1QX", "GFA07K1U", "GFA07K1*"])
def test_only_eight_symbols_of_the_alphabet_make_a_driver_code(value: str) -> None:
    assert not is_driver_code(normalise_driver_code(value))
