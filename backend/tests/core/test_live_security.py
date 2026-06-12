from app.core.security import (
    hash_password,
    hash_pin_hmac,
    verify_password,
    verify_pin_hmac,
)

PEPPER = "test-pepper"


def test_password_round_trips_for_arbitrary_strings():
    stored = hash_password("test1234.")
    assert verify_password("test1234.", stored)
    assert not verify_password("wrong", stored)


def test_password_hash_is_salted_and_differs_per_call():
    assert hash_password("same") != hash_password("same")


def test_verify_password_rejects_garbage_hash():
    assert not verify_password("x", "not-a-valid-hash")


def test_pin_hmac_is_deterministic_for_uniqueness_index():
    # Same PIN + pepper must produce the same hash so a unique index can catch
    # duplicates; different peppers must diverge.
    assert hash_pin_hmac("1234", PEPPER) == hash_pin_hmac("1234", PEPPER)
    assert hash_pin_hmac("1234", PEPPER) != hash_pin_hmac("1234", "other")
    assert hash_pin_hmac("1234", PEPPER) != hash_pin_hmac("5678", PEPPER)


def test_pin_hmac_verifies():
    stored = hash_pin_hmac("4821", PEPPER)
    assert verify_pin_hmac("4821", stored, PEPPER)
    assert not verify_pin_hmac("0000", stored, PEPPER)
