"""Fix validation at the action boundary (GPS plan U7: R2, R40) and the
custody geometry (U9: R14, R20, R32 hook). Pure tests over
``position_rules``: every shape a client can send becomes a value — stored
fix or None, one reason, flags — and never an exception; every custody check
is one of ``within``, ``away`` or ``unverified`` with a reason, decided cap
first, then plausibility, then distance less accuracy."""

from datetime import datetime, timedelta, timezone

import pytest

from app.services.position_rules import (
    CUSTODY_AWAY,
    CUSTODY_UNVERIFIED,
    CUSTODY_WITHIN,
    FIX_REASONS,
    FLAG_CLOCK_SKEW,
    REASON_IMPLAUSIBLE,
    REASON_NO_FIX,
    REASON_STOP_UNVERIFIED,
    REASON_TOO_COARSE,
    UNVERIFIED_REASONS,
    CustodyCheck,
    NormalisedFix,
    StoredFix,
    classify_custody,
    normalise_fix,
    plausibility_flags,
    within_vicinity,
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


# --- the custody check (U9: R14, R20, AE3, AE4, AE17) --------------------------
#
# Geometry: one degree of latitude is ~111,195 m, so a stop at (lat, lng) and a
# fix at (lat + d/111195, lng) are d metres apart along the meridian. Every
# distance below is built that way so the expected value is legible.

THRESHOLD = 150.0
STOP = (-1.2902, 36.7823)  # Kilimani
METRE = 1 / 111_195.0
CAPTURED = datetime(2026, 9, 20, 6, 29, 50, tzinfo=timezone.utc)


def fix_at(metres_north: float, accuracy: float = 12.0) -> StoredFix:
    return StoredFix(
        lat=STOP[0] + metres_north * METRE, lng=STOP[1], accuracy_m=accuracy, captured_at=CAPTURED,
    )


def custody(fix, stop=STOP, *, threshold=THRESHOLD, cap=CAP, flags=()):
    return classify_custody(
        fix, stop, custody_threshold_m=threshold, accuracy_cap_m=cap, flags=flags
    )


def test_ae4_a_board_sixty_metres_from_the_stop_is_within():
    result = custody(fix_at(60))
    assert result.classification == CUSTODY_WITHIN
    assert result.reason is None
    assert result.distance_m == pytest.approx(60, abs=1)
    assert not result.away and not result.unverified


def test_ae3_a_board_1800_metres_from_the_stop_is_away_with_its_distance():
    result = custody(fix_at(1800))
    assert result == CustodyCheck(
        classification=CUSTODY_AWAY, reason=None, distance_m=pytest.approx(1800, abs=2)
    )
    assert result.away


def test_accuracy_is_subtracted_before_the_threshold_is_applied():
    # 200 m away, 60 m wide: 140 m net, inside 150 → within.
    assert custody(fix_at(200, accuracy=60)).classification == CUSTODY_WITHIN
    # 200 m away, 40 m wide: 160 m net, past 150 → away.
    assert custody(fix_at(200, accuracy=40)).classification == CUSTODY_AWAY
    # Exactly the threshold does not exceed it.
    exact = custody(fix_at(162, accuracy=12))
    assert exact.distance_m - 12 == pytest.approx(150, abs=0.5)
    assert exact.classification == CUSTODY_WITHIN


def test_ae17_a_coarse_fix_is_unverified_too_coarse_and_the_cap_runs_first():
    # 900 m wide at 800 m: subtracting first would read "within"; the cap
    # runs first so it cannot (the plan's ordering decision).
    result = custody(fix_at(800, accuracy=900))
    assert result.classification == CUSTODY_UNVERIFIED
    assert result.reason == REASON_TOO_COARSE
    assert result.unverified and not result.away
    # The distance is still reported for the office, informational.
    assert result.distance_m == pytest.approx(800, abs=1)
    # And the same 900 m fix far away is the same verdict, never "away".
    assert custody(fix_at(5000, accuracy=900)).reason == REASON_TOO_COARSE


def test_exactly_the_cap_is_not_coarse_and_an_approximate_grant_is():
    assert custody(fix_at(60, accuracy=CAP)).classification == CUSTODY_WITHIN
    # 1000 m or more is an approximate-location grant; the client banners it,
    # the server simply cannot test it.
    assert custody(fix_at(60, accuracy=1000)).reason == REASON_TOO_COARSE


def test_no_fix_is_unverified_no_fix_with_no_distance():
    result = custody(None)
    assert result == CustodyCheck(classification=CUSTODY_UNVERIFIED, reason=REASON_NO_FIX, distance_m=None)


@pytest.mark.parametrize(
    "stop",
    [None, (None, None), (None, 36.7823), (-1.2902, None), (float("nan"), 36.7823), (91.0, 36.7823)],
)
def test_a_stop_without_usable_coordinates_is_stop_unverified_whatever_the_fix(stop):
    # With a good fix, with no fix, with an implausible fix: the stop wins the
    # precedence (stop-unverified > implausible > no-fix > too-coarse).
    for fix, flags in ((fix_at(60), ()), (None, ()), (fix_at(60), ("implausible-jump",)),
                       (fix_at(60, accuracy=900), ())):
        result = custody(fix, stop, flags=flags)
        assert result.classification == CUSTODY_UNVERIFIED, (stop, fix, flags)
        assert result.reason == REASON_STOP_UNVERIFIED
        assert result.distance_m is None


def test_plausibility_flags_make_the_check_implausible_and_outrank_too_coarse():
    flagged = custody(fix_at(60), flags=("implausible-jump",))
    assert flagged.classification == CUSTODY_UNVERIFIED
    assert flagged.reason == REASON_IMPLAUSIBLE
    # A flagged fix never clears the check, however close it reads (R32).
    assert flagged.distance_m == pytest.approx(60, abs=1)
    # Flagged and coarse: implausible is the reason the office sees.
    assert custody(fix_at(60, accuracy=900), flags=("accuracy-zero",)).reason == REASON_IMPLAUSIBLE
    # A flagged far fix is not "away" either — it is not evidence of anything.
    assert custody(fix_at(1800), flags=("implausible-jump",)).reason == REASON_IMPLAUSIBLE


def test_the_plausibility_hook_returns_no_flags_until_u12_fills_it():
    stored = norm(usable())
    assert plausibility_flags(stored) == ()
    assert plausibility_flags(stored.fix) == ()
    assert plausibility_flags(None) == ()
    # A clock-skewed fix is not a plausibility flag today either (U12 decides).
    skewed = norm(usable(captured_at=(NOW + timedelta(minutes=10)).isoformat()))
    assert skewed.flags == (FLAG_CLOCK_SKEW,)
    assert plausibility_flags(skewed) == ()


def test_a_per_school_threshold_changes_the_verdict():
    # U11's scenario: a 300 m school does not flag the 250 m tap a default one flags.
    assert custody(fix_at(250)).classification == CUSTODY_AWAY
    assert custody(fix_at(250), threshold=300).classification == CUSTODY_WITHIN
    # And a per-school cap: 250 m accuracy is coarse by default, fine at 300.
    assert custody(fix_at(60, accuracy=250)).reason == REASON_TOO_COARSE
    assert custody(fix_at(60, accuracy=250), cap=300).classification == CUSTODY_WITHIN


def test_the_unverified_reason_precedence_is_the_plans():
    assert UNVERIFIED_REASONS == ("stop-unverified", "implausible", "no-fix", "too-coarse")


def test_distance_is_symmetric_and_along_the_longitude_too():
    east = StoredFix(lat=STOP[0], lng=STOP[1] + 1800 * METRE, accuracy_m=12.0, captured_at=CAPTURED)
    result = custody(east)
    # A degree of longitude at 1.29° S is ~99.97% of one at the equator.
    assert result.distance_m == pytest.approx(1800, rel=0.01)
    assert result.classification == CUSTODY_AWAY


# --- "bus seen at stop": any fix inside the vicinity, same geometry ---------------


def test_within_vicinity_uses_the_cap_then_distance_less_accuracy():
    kwargs = {"vicinity_m": 100.0, "accuracy_cap_m": CAP}
    assert within_vicinity(fix_at(40, accuracy=25), STOP, **kwargs)
    # 120 m away but 25 m wide: 95 m net, inside 100.
    assert within_vicinity(fix_at(120, accuracy=25), STOP, **kwargs)
    # 1.8 km away: no.
    assert not within_vicinity(fix_at(1800, accuracy=25), STOP, **kwargs)
    # Coarse fix right on the stop: cannot vouch for anything (cap first).
    assert not within_vicinity(fix_at(0, accuracy=900), STOP, **kwargs)
    # A stop without coordinates is never "seen".
    assert not within_vicinity(fix_at(0), None, **kwargs)
    assert not within_vicinity(fix_at(0), (None, 36.7823), **kwargs)
