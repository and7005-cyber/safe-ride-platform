"""Fix validation at the action boundary (GPS plan U7: R2, R40). Pure tests
over ``position_rules.normalise_fix``: every shape a client can send becomes
a value — stored fix or None, one reason, flags — and never an exception.
U9 adds the geometry classification cases to this file."""

from datetime import datetime, timedelta, timezone

import pytest

from app.services.position_rules import (
    FIX_REASONS,
    FLAG_CLOCK_SKEW,
    NormalisedFix,
    normalise_fix,
)

NOW = datetime(2026, 9, 20, 6, 30, tzinfo=timezone.utc)
CAP = 200.0
NAIROBI = (-1.2921, 36.8219)


def usable(**overrides):
    fix = {
        "lat": NAIROBI[0], "lng": NAIROBI[1], "accuracy_m": 12.5,
        # The device's own offset, as the client sends it (Nairobi, +03:00).
        "captured_at": "2026-09-20T09:29:50.250+03:00",
    }
    fix.update(overrides)
    return fix


def norm(raw, **kwargs):
    return normalise_fix(raw, now=NOW, accuracy_cap_m=CAP, **kwargs)


# --- the happy path ------------------------------------------------------------


def test_a_usable_fix_is_stored_with_reason_none_and_no_flags():
    result = norm(usable())
    assert result.reason == "none"
    assert result.flags == ()
    assert result.servable
    stored = result.fix
    assert (stored.lat, stored.lng, stored.accuracy_m) == (NAIROBI[0], NAIROBI[1], 12.5)
    # Kept aware; the instant is 06:29:50.250 UTC.
    assert stored.captured_at == datetime(2026, 9, 20, 6, 29, 50, 250000, tzinfo=timezone.utc)


def test_a_zulu_capture_time_is_accepted():
    result = norm(usable(captured_at="2026-09-20T06:29:50Z"))
    assert result.fix is not None
    assert result.fix.captured_at == datetime(2026, 9, 20, 6, 29, 50, tzinfo=timezone.utc)


def test_no_fix_at_all_is_reason_none_with_nothing_stored():
    assert norm(None) == NormalisedFix(fix=None, reason="none")
    assert not norm(None).servable


@pytest.mark.parametrize("reason", ["denied", "unavailable", "timeout"])
def test_a_browser_reason_alone_is_kept_as_the_reason(reason):
    result = norm({"reason": reason})
    assert result == NormalisedFix(fix=None, reason=reason)
    assert not result.servable


# --- coarse: the server's cap decides, the client's label does not -----------


def test_accuracy_above_the_cap_is_coarse_but_still_stored_and_servable():
    result = norm(usable(accuracy_m=1500, reason="coarse"))
    assert result.reason == "coarse"
    assert result.fix is not None and result.fix.accuracy_m == 1500
    # Served with its accuracy beside it (U8 labels it); Start Run alone
    # never serves a fix, and that rule lives in the DAO, not here.
    assert result.servable


def test_the_clients_coarse_label_is_overridden_by_the_cap():
    # A client with a stale (lower) cap says coarse; the server's cap says no.
    assert norm(usable(accuracy_m=150, reason="coarse")).reason == "none"
    # And the reverse: a client that thought 250 m was fine.
    assert norm(usable(accuracy_m=250)).reason == "coarse"


def test_exactly_the_cap_is_not_coarse():
    assert norm(usable(accuracy_m=CAP)).reason == "none"


def test_a_per_school_cap_changes_the_verdict():
    assert normalise_fix(usable(accuracy_m=250), now=NOW, accuracy_cap_m=300).reason == "none"


# --- invalid: malformed or out of range, never a 422 ---------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "garbage",
        42,
        [],
        {},                                   # an object saying nothing
        {"reason": "coarse"},                 # coarse without coordinates
        {"reason": "lost"},                   # unknown reason, no coordinates
        {"reason": None},
        usable(lat=91),
        usable(lat=-90.0001),
        usable(lng=180.5),
        usable(lng=-181),
        usable(lat="−1.29"),                  # strings are not coordinates
        usable(lat=True),                     # nor booleans
        usable(lat=float("nan")),
        usable(lng=float("inf")),
        usable(accuracy_m=-1),
        usable(accuracy_m=None),
        usable(accuracy_m="12"),
        usable(captured_at="2026-09-20T09:29:50"),   # naive: no offset
        usable(captured_at="yesterday"),
        usable(captured_at=1758349790),
        usable(captured_at=""),
        usable(captured_at=None),
        {"lat": NAIROBI[0]},                  # partial coordinates
        {"lat": NAIROBI[0], "lng": NAIROBI[1], "captured_at": "2026-09-20T06:29:50Z"},
    ],
)
def test_malformed_or_out_of_range_input_is_reason_invalid(raw):
    result = norm(raw)
    assert result.reason == "invalid"
    assert result.fix is None
    assert result.flags == ()
    assert not result.servable


def test_boundary_coordinates_are_valid():
    for lat, lng in ((90, 180), (-90, -180), (0, 0)):
        result = norm(usable(lat=lat, lng=lng))
        assert result.reason == "none", (lat, lng)


def test_zero_accuracy_is_accepted_here():
    # Plausibility (U12) flags it; validation only refuses negatives.
    assert norm(usable(accuracy_m=0)).reason == "none"


def test_extra_keys_are_ignored():
    result = norm(usable(speed=12.0, heading=90, altitude=1700))
    assert result.reason == "none" and result.fix is not None


# --- clock skew: stored, flagged, never served ---------------------------------


def test_a_capture_time_in_the_future_is_stored_and_flagged_clock_skew():
    ahead = (NOW + timedelta(minutes=10)).isoformat()
    result = norm(usable(captured_at=ahead))
    assert result.reason == "none"
    assert result.flags == (FLAG_CLOCK_SKEW,)
    assert result.fix is not None
    assert result.fix.captured_at == NOW + timedelta(minutes=10)
    assert not result.servable


def test_the_skew_tolerance_is_thirty_seconds_by_default():
    inside = (NOW + timedelta(seconds=29)).isoformat()
    outside = (NOW + timedelta(seconds=31)).isoformat()
    assert norm(usable(captured_at=inside)).flags == ()
    assert norm(usable(captured_at=outside)).flags == (FLAG_CLOCK_SKEW,)
    assert norm(usable(captured_at=outside), skew_tolerance_s=60).flags == ()


def test_a_capture_time_minutes_before_receipt_is_kept_as_captured():
    earlier = NOW - timedelta(minutes=7)
    result = norm(usable(captured_at=earlier.isoformat()))
    assert result.flags == ()
    assert result.servable
    assert result.fix is not None and result.fix.captured_at == earlier


def test_skew_is_judged_on_the_instant_not_the_wall_clock_digits():
    # 09:35 at +03:00 is 06:35 UTC: five minutes ahead of NOW, flagged; the
    # same digits at +00:00 would be three hours ahead — also flagged; and
    # 09:35 at +04:00 is 05:35 UTC, in the past — not flagged.
    assert norm(usable(captured_at="2026-09-20T09:35:00+03:00")).flags == (FLAG_CLOCK_SKEW,)
    assert norm(usable(captured_at="2026-09-20T09:35:00+04:00")).flags == ()


# --- the fingerprint material (R33) ----------------------------------------


def test_fingerprint_material_is_the_stored_values_or_the_reason_and_never_the_flags():
    fixed = norm(usable())
    assert fixed.fingerprint_material() == {
        "lat": NAIROBI[0], "lng": NAIROBI[1], "accuracy_m": 12.5,
        "captured_at": "2026-09-20T06:29:50.250000+00:00", "reason": "none",
    }
    # The same fix received later, past its skew window, must fingerprint the
    # same: a retry is the same tap however late the network delivers it.
    ahead = usable(captured_at=(NOW + timedelta(minutes=5)).isoformat())
    flagged = norm(ahead)
    unflagged = normalise_fix(ahead, now=NOW + timedelta(hours=1), accuracy_cap_m=CAP)
    assert flagged.flags != unflagged.flags
    assert flagged.fingerprint_material() == unflagged.fingerprint_material()
    assert norm({"reason": "denied"}).fingerprint_material() == {"reason": "denied"}
    assert norm(None).fingerprint_material() == {"reason": "none"}


def test_the_reason_vocabulary_is_the_trail_rows():
    assert FIX_REASONS == ("none", "denied", "unavailable", "timeout", "coarse", "invalid")
