"""Fix validation at the action boundary (GPS plan U7: R2, R40), the
custody geometry (U9: R14, R20), the absent classification (U10: R16, R20)
and the plausibility safeguard (U12: R32). Pure tests over
``position_rules``: every shape a client can send becomes a value — stored
fix or None, one reason, flags — and never an exception; every custody check
is one of ``within``, ``away`` or ``unverified`` with a reason, decided cap
first, then plausibility, then distance less accuracy; every absent mark is
``unverified``, ``corroborated`` (by an earlier absence, the stop or the
school) or ``remote``; every fix is judged against the previous one and the
planned stops, and a flagged fix clears nothing."""

from datetime import datetime, timedelta, timezone

import pytest

from app.services.position_rules import (
    ABSENT_CORROBORATED,
    ABSENT_REMOTE,
    ABSENT_UNVERIFIED,
    CORROBORATED_BY_ABSENCE,
    CORROBORATED_BY_SCHOOL,
    CORROBORATED_BY_STOP,
    CUSTODY_AWAY,
    CUSTODY_UNVERIFIED,
    CUSTODY_WITHIN,
    FIX_REASONS,
    FLAG_ACCURACY_ZERO,
    FLAG_AT_PLANNED_STOP,
    FLAG_CLASSIFICATION_FAILED,
    FLAG_CLOCK_SKEW,
    FLAG_JUMP,
    FLAG_REPEAT_COORDINATES,
    FLAG_SPEED,
    JUMP_DISTANCE_M,
    JUMP_WINDOW_S,
    PLANNED_STOP_RADIUS_M,
    PLAUSIBILITY_FLAGS,
    PLAUSIBLE_MAX_SPEED_MPS,
    REASON_IMPLAUSIBLE,
    REASON_NO_FIX,
    REASON_STOP_UNVERIFIED,
    REASON_TOO_COARSE,
    UNVERIFIED_REASONS,
    VICINITY_EXIT_FACTOR,
    VICINITY_INSIDE,
    VICINITY_LEFT,
    VICINITY_OUTSIDE,
    AbsentCheck,
    CustodyCheck,
    NormalisedFix,
    StoredFix,
    VicinityState,
    classify_absent,
    classify_custody,
    exit_radius_m,
    jump_distance_m,
    normalise_fix,
    plausibility_flags,
    vicinity_state,
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
    for fix, flags in ((fix_at(60), ()), (None, ()), (fix_at(60), (FLAG_JUMP,)),
                       (fix_at(60, accuracy=900), ())):
        result = custody(fix, stop, flags=flags)
        assert result.classification == CUSTODY_UNVERIFIED, (stop, fix, flags)
        assert result.reason == REASON_STOP_UNVERIFIED
        assert result.distance_m is None


def test_plausibility_flags_make_the_check_implausible_and_outrank_too_coarse():
    flagged = custody(fix_at(60), flags=(FLAG_JUMP,))
    assert flagged.classification == CUSTODY_UNVERIFIED
    assert flagged.reason == REASON_IMPLAUSIBLE
    # A flagged fix never clears the check, however close it reads (R32).
    assert flagged.distance_m == pytest.approx(60, abs=1)
    # Flagged and coarse: implausible is the reason the office sees.
    assert custody(fix_at(60, accuracy=900), flags=(FLAG_ACCURACY_ZERO,)).reason == REASON_IMPLAUSIBLE
    # A flagged far fix is not "away" either — it is not evidence of anything.
    assert custody(fix_at(1800), flags=(FLAG_JUMP,)).reason == REASON_IMPLAUSIBLE


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


# --- the absent classification (U10: R16, R20; AE6, AE7, AE8, AE16) ---------------
#
# The school sits 3 km north of the stop, so a fix "at the school" is
# unambiguously away from the stop and a fix at the stop is away from the school.

VICINITY = 100.0
SCHOOL = (STOP[0] + 3000 * METRE, STOP[1])


def school_fix(metres_north: float = 0.0, accuracy: float = 12.0) -> StoredFix:
    return StoredFix(
        lat=SCHOOL[0] + metres_north * METRE, lng=SCHOOL[1], accuracy_m=accuracy,
        captured_at=CAPTURED,
    )


def absent(
    fix, stop=STOP, *, school=SCHOOL, run_type="morning", stop_order=3, stops_completed=3,
    prior=False, vicinity=VICINITY, cap=CAP, flags=(),
):
    return classify_absent(
        fix, stop, school=school, run_type=run_type, stop_order=stop_order,
        stops_completed=stops_completed, prior_absence_covers=prior,
        vicinity_m=vicinity, accuracy_cap_m=cap, flags=flags,
    )


def test_ae8_an_absent_within_the_stops_vicinity_is_corroborated_by_the_stop():
    result = absent(fix_at(40))
    assert result == AbsentCheck(
        classification=ABSENT_CORROBORATED, reason=None, corroborated_by=CORROBORATED_BY_STOP,
        distance_m=pytest.approx(40, abs=1),
    )
    assert result.corroborated and not result.remote and not result.unverified
    # The same geometry as "bus seen at stop": 120 m away but 25 m wide is in.
    assert absent(fix_at(120, accuracy=25)).corroborated_by == CORROBORATED_BY_STOP
    # 120 m away and 12 m wide is not: 108 m net, past 100.
    assert absent(fix_at(120, accuracy=12)).classification == ABSENT_REMOTE


def test_ae7_an_absent_three_kilometres_from_the_stop_is_remote_with_its_distance():
    result = absent(fix_at(-3000))
    assert result.classification == ABSENT_REMOTE
    assert result.reason is None and result.corroborated_by is None
    assert result.distance_m == pytest.approx(3000, abs=3)
    assert result.remote


def test_ae6_a_remote_absent_stays_remote_here_the_attestation_is_the_ledgers():
    # Nothing in the geometry distinguishes a phoned-in absence from a skipped
    # stop: both are `remote`, and the driver's answer (told-me / not-at-stop)
    # is recorded on the exception, not decided here.
    assert absent(fix_at(-3000)).remote
    assert absent(fix_at(-3000), prior=False).remote


def test_a_parent_or_office_absence_before_the_tap_corroborates_the_mark():
    result = absent(fix_at(-3000), prior=True)
    assert result.classification == ABSENT_CORROBORATED
    assert result.corroborated_by == CORROBORATED_BY_ABSENCE
    # Still reported for the office.
    assert result.distance_m == pytest.approx(3000, abs=3)
    # ...but never ahead of the unverified reasons (R16's order): a prior
    # absence with an untestable fix is an unverified check.
    assert absent(None, prior=True) == AbsentCheck(
        classification=ABSENT_UNVERIFIED, reason=REASON_NO_FIX, corroborated_by=None, distance_m=None,
    )
    assert absent(fix_at(-3000, accuracy=900), prior=True).reason == REASON_TOO_COARSE


def test_ae16_an_afternoon_absent_at_the_school_for_a_stop_still_ahead_is_corroborated():
    # Arrived at the gate (stop 1); the child's stop is 6, further along.
    result = absent(school_fix(20), run_type="afternoon", stop_order=6, stops_completed=1)
    assert result.classification == ABSENT_CORROBORATED
    assert result.corroborated_by == CORROBORATED_BY_SCHOOL
    assert result.distance_m == pytest.approx(3000, abs=25), "distance is to the child's stop"
    # Before the gate Arrive (progress 0) the stop is still ahead: corroborated.
    assert absent(school_fix(), run_type="afternoon", stop_order=6, stops_completed=0).corroborated
    # Once the child's stop has been reached, "at the school" no longer explains
    # the mark: remote.
    assert absent(school_fix(), run_type="afternoon", stop_order=6, stops_completed=6).remote
    assert absent(school_fix(), run_type="afternoon", stop_order=1, stops_completed=1).remote
    # A morning run has no such arm: at the school, stop ahead, still remote.
    assert absent(school_fix(), run_type="morning", stop_order=6, stops_completed=1).remote
    # The school arm needs the school's coordinates and the same geometry.
    assert absent(school_fix(), run_type="afternoon", stop_order=6, stops_completed=1, school=None).remote
    assert absent(
        school_fix(300), run_type="afternoon", stop_order=6, stops_completed=1,
    ).remote, "300 m from the gate is not at the school"
    assert absent(
        school_fix(0, accuracy=900), run_type="afternoon", stop_order=6, stops_completed=1,
    ).reason == REASON_TOO_COARSE, "a coarse fix at the school vouches for nothing"


def test_the_unverified_reasons_and_their_precedence_are_the_custody_checks():
    assert absent(None).reason == REASON_NO_FIX
    assert absent(fix_at(40, accuracy=900)).reason == REASON_TOO_COARSE
    assert absent(fix_at(40), flags=(FLAG_JUMP,)).reason == REASON_IMPLAUSIBLE
    assert absent(fix_at(40, accuracy=900), flags=(FLAG_ACCURACY_ZERO,)).reason == REASON_IMPLAUSIBLE
    # A coordinate-less stop wins over every other reason and over every
    # corroboration, the fix at the school included; no distance is reported.
    for fix, kwargs in (
        (fix_at(40), {}), (None, {}), (fix_at(40, accuracy=900), {}),
        (fix_at(40), {"flags": (FLAG_JUMP,)}), (fix_at(40), {"prior": True}),
        (school_fix(), {"run_type": "afternoon", "stop_order": 6, "stops_completed": 1}),
    ):
        result = absent(fix, (None, None), **kwargs)
        assert result.classification == ABSENT_UNVERIFIED, (fix, kwargs)
        assert result.reason == REASON_STOP_UNVERIFIED
        assert result.distance_m is None
    # The other unverified verdicts keep the distance for the office.
    assert absent(fix_at(800, accuracy=900)).distance_m == pytest.approx(800, abs=1)
    assert absent(fix_at(40), flags=(FLAG_JUMP,)).distance_m == pytest.approx(40, abs=1)
    # A flagged fix never corroborates, however close (R32).
    assert absent(fix_at(10), flags=(FLAG_JUMP,)).unverified


def test_per_school_vicinity_and_cap_change_the_absent_verdict():
    # U11's knobs: a 200 m vicinity corroborates the 150 m mark a 100 m one does not.
    assert absent(fix_at(150)).remote
    assert absent(fix_at(150), vicinity=200).corroborated
    assert absent(fix_at(40, accuracy=250)).reason == REASON_TOO_COARSE
    assert absent(fix_at(40, accuracy=250), cap=300).corroborated


# --- the plausibility safeguard (U12: R32; R20's implausible reason) --------------
#
# The previous fix is at the stop; the new fix is judged against it and the
# run's planned stops. Capture times are built from CAPTURED so every
# interval below is legible.

STOPS = (STOP, (STOP[0] + 1000 * METRE, STOP[1]), SCHOOL)


def later(seconds: float, *, metres_north: float = 0.0, accuracy: float = 12.0,
          lng: float | None = None) -> StoredFix:
    """A fix ``seconds`` after CAPTURED, ``metres_north`` of the stop."""
    return StoredFix(
        lat=STOP[0] + metres_north * METRE, lng=STOP[1] if lng is None else lng,
        accuracy_m=accuracy, captured_at=CAPTURED + timedelta(seconds=seconds),
    )


def flags_of(fix, previous=None, stops=STOPS):
    return plausibility_flags(fix, previous=previous, stops=stops)


def test_the_flag_vocabulary_is_stable_and_ordered():
    assert PLAUSIBILITY_FLAGS == (
        "clock-skew", "jump", "speed", "accuracy-zero", "repeat-coordinates", "at-planned-stop",
    )
    assert (FLAG_CLOCK_SKEW, FLAG_JUMP, FLAG_SPEED, FLAG_ACCURACY_ZERO,
            FLAG_REPEAT_COORDINATES, FLAG_AT_PLANNED_STOP) == PLAUSIBILITY_FLAGS
    # Bookkeeping is not plausibility; and the plan's "identical accuracy"
    # arm was left out on purpose (see the vocabulary's note).
    assert FLAG_CLASSIFICATION_FAILED not in PLAUSIBILITY_FLAGS
    assert "repeat-accuracy" not in PLAUSIBILITY_FLAGS
    assert (PLAUSIBLE_MAX_SPEED_MPS, JUMP_DISTANCE_M, JUMP_WINDOW_S, PLANNED_STOP_RADIUS_M) == (
        40.0, 1000.0, 30.0, 2.0,
    )


def test_nothing_stored_means_nothing_to_judge():
    assert flags_of(None) == ()
    assert flags_of(norm(None)) == ()
    assert flags_of(norm({"reason": "denied"})) == ()
    assert flags_of(norm("garbage"), previous=later(0)) == ()


def test_two_fixes_five_km_apart_twenty_seconds_apart_flag_the_second_and_the_check_reads_implausible():
    previous = later(0, metres_north=40)
    far = later(20, metres_north=5040, accuracy=15)
    flags = flags_of(far, previous)
    assert flags == (FLAG_JUMP, FLAG_SPEED)
    # The custody check on that tap: unverified / implausible — neither
    # `away` (it is 5 km out) nor anything else (R32).
    verdict = custody(far, flags=flags)
    assert verdict == CustodyCheck(
        classification=CUSTODY_UNVERIFIED, reason=REASON_IMPLAUSIBLE,
        distance_m=pytest.approx(5040, abs=5),
    )
    # And the first of the two is judged on its own merits: nothing before it.
    assert flags_of(previous) == ()
    assert jump_distance_m(far, previous) == pytest.approx(5000, abs=5)
    assert jump_distance_m(previous, None) is None


def test_speed_is_distance_over_the_capture_interval_against_forty_metres_per_second():
    # No planned stops here: the pairwise rules alone (the next stop's pin
    # sits 1 km north and would add its own flag).
    def flags_of(fix, previous):
        return plausibility_flags(fix, previous=previous, stops=())

    previous = later(0)
    # 800 m in 20 s is exactly 40 m/s: not above the cap.
    assert flags_of(later(20, metres_north=800, accuracy=13), previous) == ()
    # 810 m in 20 s is above it — and under a kilometre, so speed alone.
    assert flags_of(later(20, metres_north=810, accuracy=13), previous) == (FLAG_SPEED,)
    # 1,001 m in 29 s is 34.5 m/s — a jump, not a speed.
    assert flags_of(later(29, metres_north=1001, accuracy=13), previous) == (FLAG_JUMP,)
    # 1,001 m in exactly 30 s is not "under 30 s"; 33 m/s is not a speed either.
    assert flags_of(later(30, metres_north=1001, accuracy=13), previous) == ()
    # 1,000 m in 10 s is not over a kilometre, but it is 100 m/s.
    assert flags_of(later(10, metres_north=1000, accuracy=13), previous) == (FLAG_SPEED,)
    # 5 km in a second: both, jump reported first.
    assert flags_of(later(1, metres_north=5000, accuracy=13), previous) == (FLAG_JUMP, FLAG_SPEED)
    # Along the longitude too.
    east = StoredFix(lat=STOP[0], lng=STOP[1] + 5000 * METRE, accuracy_m=13.0,
                     captured_at=CAPTURED + timedelta(seconds=10))
    assert flags_of(east, previous) == (FLAG_JUMP, FLAG_SPEED)


def test_a_zero_or_negative_capture_interval_yields_no_speed_or_jump_verdict():
    previous = later(60)
    # Captured before the previous fix (a cached fix behind a fresh one):
    # the distance says nothing about speed.
    assert flags_of(later(0, metres_north=5000, accuracy=13), previous) == ()
    # Same instant, different place: no interval to divide by either.
    assert flags_of(later(60, metres_north=5000, accuracy=13), previous) == ()


def test_the_same_capture_re_read_from_the_cache_is_not_a_second_observation():
    # Two Boards inside the client's cache window carry one fix (U6/U7):
    # identical coordinates, accuracy and capture time — nothing to flag.
    one = later(0, metres_north=40)
    two = later(0, metres_north=40)
    assert flags_of(two, one) == ()
    # The moment the capture time differs, identical coordinates are a
    # repeat (the fabricated-corroboration risk).
    assert flags_of(later(5, metres_north=40), one) == (FLAG_REPEAT_COORDINATES,)


def test_identical_consecutive_coordinates_flag_the_second_fix_and_distinct_ones_or_identical_accuracy_do_not():
    previous = later(0, metres_north=40, accuracy=12)
    # Same point, new accuracy: repeat-coordinates.
    assert flags_of(later(30, metres_north=40, accuracy=13), previous) == (FLAG_REPEAT_COORDINATES,)
    # Same point, same accuracy: still the one flag — a stuck feed repeats both.
    assert flags_of(later(30, metres_north=40, accuracy=12), previous) == (FLAG_REPEAT_COORDINATES,)
    # Moved 5 m with the same accuracy: nothing. Identical accuracy is not a
    # rule — iPhones report quantised accuracies and repeat them routinely.
    assert flags_of(later(30, metres_north=45, accuracy=12), previous) == ()
    # Same latitude only, or a distinct accuracy and point: nothing.
    assert flags_of(later(30, metres_north=40, accuracy=13, lng=STOP[1] + 5 * METRE), previous) == ()
    assert flags_of(later(30, metres_north=45, accuracy=13), previous) == ()
    # Repeats are judged against the previous fix only, not the whole trail.
    assert flags_of(later(60, metres_north=40, accuracy=12), later(30, metres_north=45, accuracy=13)) == ()


def test_accuracy_zero_flags_and_twenty_five_metres_does_not():
    assert flags_of(later(0, metres_north=40, accuracy=0)) == (FLAG_ACCURACY_ZERO,)
    assert flags_of(later(0, metres_north=40, accuracy=0.0)) == (FLAG_ACCURACY_ZERO,)
    assert flags_of(later(0, metres_north=40, accuracy=25)) == ()
    # A coarse fix is judged like any other — coarse is the cap's business.
    assert flags_of(later(0, metres_north=40, accuracy=900)) == ()
    # Anomalously good accuracy is the plausibility path, not "too coarse":
    # implausible outranks the cap and the geometry.
    assert custody(later(0, metres_north=40, accuracy=0), flags=(FLAG_ACCURACY_ZERO,)).reason == REASON_IMPLAUSIBLE


def test_a_fix_on_a_planned_stop_pin_flags_and_thirty_metres_away_does_not():
    assert flags_of(later(0, metres_north=0)) == (FLAG_AT_PLANNED_STOP,)
    assert flags_of(later(0, metres_north=1.9)) == (FLAG_AT_PLANNED_STOP,)
    assert flags_of(later(0, metres_north=2.1)) == ()
    assert flags_of(later(0, metres_north=30)) == ()
    # Any planned stop counts — the next one, the school gate — and a stop
    # without usable coordinates is skipped, not matched.
    assert flags_of(later(0, metres_north=1000)) == (FLAG_AT_PLANNED_STOP,)
    assert flags_of(later(0, metres_north=3000)) == (FLAG_AT_PLANNED_STOP,)
    assert flags_of(later(0, metres_north=0), stops=[None, (None, None), (float("nan"), 1.0)]) == ()
    assert flags_of(later(0, metres_north=0), stops=()) == ()


def test_clock_skew_is_folded_in_so_a_skewed_fix_classifies_as_implausible():
    skewed = norm(usable(captured_at=(NOW + timedelta(minutes=10)).isoformat()))
    assert skewed.flags == (FLAG_CLOCK_SKEW,)
    assert flags_of(skewed, stops=()) == (FLAG_CLOCK_SKEW,)
    assert custody(skewed.fix, flags=flags_of(skewed, stops=())).reason == REASON_IMPLAUSIBLE
    # The bare stored fix carries no skew information; only the boundary's
    # result does.
    assert flags_of(skewed.fix, stops=()) == ()
    # And a plausible, unskewed fix through the same door: nothing.
    assert flags_of(norm(usable()), stops=()) == ()


def test_flags_come_in_vocabulary_order_however_many_apply():
    previous = later(0, metres_north=-40, accuracy=0)
    # Zero accuracy, 1,040 m in a second, on the next stop's pin.
    pile = later(1, metres_north=1000, accuracy=0)
    assert flags_of(pile, previous) == (
        FLAG_JUMP, FLAG_SPEED, FLAG_ACCURACY_ZERO, FLAG_AT_PLANNED_STOP,
    )


def test_a_flagged_fix_neither_corroborates_an_absent_nor_counts_as_seen_at_stop():
    on_the_pin = later(0, metres_north=0)
    flags = flags_of(on_the_pin)
    assert flags == (FLAG_AT_PLANNED_STOP,)
    # Inside the vicinity, and still not corroborated (R32, R16's order).
    verdict = absent(on_the_pin, flags=flags)
    assert verdict.classification == ABSENT_UNVERIFIED and verdict.reason == REASON_IMPLAUSIBLE
    assert not verdict.corroborated
    # The geometry alone would have said yes — which is exactly why the
    # trail reader (position_dao.run_fixes) drops flagged fixes before
    # `within_vicinity` ever sees them.
    assert within_vicinity(on_the_pin, STOP, vicinity_m=VICINITY, accuracy_cap_m=CAP)


# --- the vicinity state machine (U15: R29, R30, R31; F7) ------------------------
#
# Derived from a run's plain pings in capture order against one stop: two
# consecutive plain pings inside the enter radius enter, two consecutive plain
# pings beyond the exit radius leave, a fix between the radii changes nothing,
# and a coarse fix is skipped — it neither counts nor breaks a pair.

ENTER = 100.0
EXIT = 150.0


def ping_at(metres_north: float, accuracy: float = 12.0, *, seconds: float = 0.0) -> StoredFix:
    return StoredFix(
        lat=STOP[0] + metres_north * METRE, lng=STOP[1], accuracy_m=accuracy,
        captured_at=CAPTURED + timedelta(seconds=seconds),
    )


def state_of(pings, stop=STOP, *, enter=ENTER, exit_=EXIT, cap=CAP) -> VicinityState:
    return vicinity_state(pings, stop, enter_m=enter, exit_m=exit_, accuracy_cap_m=cap)


def test_the_exit_radius_is_one_and_a_half_times_the_enter_radius():
    assert VICINITY_EXIT_FACTOR == 1.5
    assert exit_radius_m(100) == 150.0
    # A per-school enter radius (U11) scales the exit with it.
    assert exit_radius_m(200) == 300.0


def test_no_pings_or_a_single_fix_inside_does_not_enter():
    assert state_of([]) == VicinityState(state=VICINITY_OUTSIDE, completed_by=None)
    assert state_of([ping_at(20)]).state == VICINITY_OUTSIDE
    # Two inside pings that are not consecutive plain pings — a far one
    # between them — do not enter either.
    broken = [ping_at(20), ping_at(600, seconds=10), ping_at(25, seconds=20)]
    assert state_of(broken).state == VICINITY_OUTSIDE


def test_two_consecutive_plain_pings_inside_enter_and_the_pair_is_reported():
    entered = state_of([ping_at(400), ping_at(20, seconds=10), ping_at(30, seconds=20)])
    assert entered.state == VICINITY_INSIDE
    assert entered.completed_by == (1, 2)
    # A third inside ping keeps the state and the pair that produced it.
    more = state_of([ping_at(400), ping_at(20, seconds=10), ping_at(30, seconds=20), ping_at(10, seconds=30)])
    assert more == VicinityState(state=VICINITY_INSIDE, completed_by=(1, 2))


def test_enter_is_distance_less_accuracy_against_the_enter_radius():
    # 110 m off the pin with a 20 m radius is 90 m net: inside.
    assert state_of([ping_at(110, 20), ping_at(105, 20, seconds=5)]).state == VICINITY_INSIDE
    # 130 m with 20 m is 110 m net: not inside, however many.
    assert state_of([ping_at(130, 20), ping_at(125, 20, seconds=5)]).state == VICINITY_OUTSIDE


def test_a_fix_between_the_radii_does_not_toggle_and_one_exterior_fix_does_not_leave():
    inside = [ping_at(20), ping_at(30, seconds=5)]
    between = state_of([*inside, ping_at(120, seconds=10), ping_at(135, seconds=15)])
    assert between.state == VICINITY_INSIDE and between.completed_by == (0, 1)
    # 160 m off with a 20 m radius is 140 m net — inside the exit radius still.
    assert state_of([*inside, ping_at(160, 20, seconds=10), ping_at(165, 20, seconds=15)]).state == VICINITY_INSIDE
    # One exterior ping is not a departure.
    assert state_of([*inside, ping_at(400, seconds=10)]).state == VICINITY_INSIDE
    # An exterior ping, a fix back inside the exit radius, an exterior ping:
    # the two exterior pings are not consecutive, so still inside.
    flapping = state_of([*inside, ping_at(400, seconds=10), ping_at(120, seconds=15), ping_at(400, seconds=20)])
    assert flapping.state == VICINITY_INSIDE


def test_two_consecutive_exterior_pings_after_entering_leave_and_the_first_completing_pair_is_kept():
    trail = [ping_at(20), ping_at(30, seconds=5), ping_at(175, 20, seconds=10), ping_at(400, seconds=15)]
    left = state_of(trail)
    assert left.state == VICINITY_LEFT
    assert left.completed_by == (2, 3)
    # Further exterior pings do not move the pair: the departure is the
    # first batch that completed it, and only that batch raises.
    assert state_of([*trail, ping_at(800, seconds=20), ping_at(1200, seconds=25)]).completed_by == (2, 3)
    # Exactly at the exit radius net is not exterior: 170 m with 20 m is 150.
    assert state_of([ping_at(20), ping_at(30, seconds=5), ping_at(170, 20, seconds=10), ping_at(170, 20, seconds=15)]).state == VICINITY_INSIDE


def test_re_entry_and_a_second_exit_report_the_new_pairs():
    trail = [
        ping_at(20), ping_at(30, seconds=5),            # enter (0, 1)
        ping_at(400, seconds=10), ping_at(500, seconds=15),  # leave (2, 3)
        ping_at(40, seconds=20), ping_at(20, seconds=25),    # re-enter (4, 5)
    ]
    back = state_of(trail)
    assert back == VicinityState(state=VICINITY_INSIDE, completed_by=(4, 5))
    again = state_of([*trail, ping_at(400, seconds=30), ping_at(600, seconds=35)])
    assert again == VicinityState(state=VICINITY_LEFT, completed_by=(6, 7))
    # Leaving from `left` needs an entry first: exterior pings after an
    # exit never make a second departure on their own.
    assert state_of([*trail[:4], ping_at(900, seconds=20), ping_at(950, seconds=25)]).completed_by == (2, 3)


def test_a_coarse_ping_is_skipped_it_neither_counts_nor_breaks_a_pair():
    # Coarse right on the stop: never enters, however many.
    assert state_of([ping_at(0, 900), ping_at(5, 900, seconds=5), ping_at(3, 900, seconds=10)]).state == VICINITY_OUTSIDE
    # Between two inside pings a coarse one is ignored: the pair stands.
    skipped = state_of([ping_at(20), ping_at(10, 900, seconds=5), ping_at(30, seconds=10)])
    assert skipped == VicinityState(state=VICINITY_INSIDE, completed_by=(0, 2))
    # Coarse and far after entering does not leave.
    assert state_of([ping_at(20), ping_at(30, seconds=5), ping_at(5000, 900, seconds=10), ping_at(5100, 900, seconds=15)]).state == VICINITY_INSIDE
    # Exactly the cap is not coarse (as everywhere else).
    assert state_of([ping_at(20, CAP), ping_at(30, CAP, seconds=5)]).state == VICINITY_INSIDE


def test_a_stop_without_usable_coordinates_is_never_entered():
    inside = [ping_at(20), ping_at(30, seconds=5)]
    assert state_of(inside, stop=None).state == VICINITY_OUTSIDE
    assert state_of(inside, stop=(None, 36.7823)).state == VICINITY_OUTSIDE
    assert state_of(inside, stop=(float("nan"), 36.7823)).state == VICINITY_OUTSIDE


def test_per_school_radii_change_the_verdict():
    # 150 m off, 12 m radius: outside a 100 m enter radius, inside a 200 m one.
    far = [ping_at(150), ping_at(155, seconds=5)]
    assert state_of(far).state == VICINITY_OUTSIDE
    assert state_of(far, enter=200, exit_=300).state == VICINITY_INSIDE
    # ... and with a 300 m exit radius, 280 m is not a departure.
    trail = [*far, ping_at(280, seconds=10), ping_at(285, seconds=15)]
    assert state_of(trail, enter=200, exit_=300).state == VICINITY_INSIDE
    assert state_of([*far, ping_at(320, seconds=10), ping_at(330, seconds=15)], enter=200, exit_=300).state == VICINITY_LEFT
