import pytest

from app.core.errors import BadRequestError
from app.core.validation import (
    clean_email,
    clean_phone,
    is_valid_email,
    normalize_kenyan_phone,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("0712345678", "+254712345678"),
        ("0112345678", "+254112345678"),
        ("+254712345678", "+254712345678"),
        ("254712345678", "+254712345678"),
        ("0712 345 678", "+254712345678"),
        ("+254 712-345-678", "+254712345678"),
    ],
)
def test_normalizes_valid_kenyan_mobiles(raw, expected):
    assert normalize_kenyan_phone(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "123", "07123", "0812345678", "+1234567890", "notaphone", "0204440000"],
)
def test_rejects_non_mobiles(raw):
    assert normalize_kenyan_phone(raw) is None


def test_landline_only_when_allowed():
    assert normalize_kenyan_phone("0204440000", allow_landline=True) == "+254204440000"


def test_clean_phone_empty_is_none():
    assert clean_phone("") is None
    assert clean_phone(None) is None


def test_clean_phone_raises_on_invalid():
    with pytest.raises(BadRequestError):
        clean_phone("0812345678")


@pytest.mark.parametrize("raw", ["a@b.com", "first.last@school.co.ke"])
def test_valid_emails(raw):
    assert is_valid_email(raw)
    assert clean_email(raw) == raw


@pytest.mark.parametrize("raw", ["nope", "a@b", "a b@c.com", "@b.com"])
def test_invalid_emails(raw):
    assert not is_valid_email(raw)
    with pytest.raises(BadRequestError):
        clean_email(raw)


def test_clean_email_required():
    with pytest.raises(BadRequestError):
        clean_email("", required=True)
    assert clean_email("", required=False) is None
