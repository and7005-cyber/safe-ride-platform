import pytest

from app.core.security import create_session_token, hash_pin, hash_session_token, verify_pin


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
