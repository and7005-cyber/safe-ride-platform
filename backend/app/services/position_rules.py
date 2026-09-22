"""Position rules: fix validation (GPS plan U7), the custody geometry (U9),
the absent classification (U10) and the plausibility safeguard (U12).

Pure: no I/O and no clock of its own — ``normalise_fix`` takes ``now``,
``plausibility_flags`` takes the previous fix and the planned stops — so the
whole boundary is unit-testable and the DAO calls it inside the action
transaction with whatever cap and threshold the school resolves to (U11).

The boundary is lenient by design (R2, R40). A tap's ``fix`` is whatever the
client sent: a usable fix, a coarse one with its coordinates attached, a
reason alone, something malformed, or nothing at all. None of those may fail
the tap, so the outcome is always a value: the stored fix or None, one
``fix_reason`` from ``FIX_REASONS`` and zero or more ``flags`` — never an
exception, never a 422.

The custody check (``classify_custody``) is a value too: ``within``, ``away``
or ``unverified`` with one reason. Its order is the plan's "check ordering
and geometry" decision — the accuracy cap before any subtraction, so a 900 m
fix can never fake "within"; plausibility before distance, so a flagged fix
never clears a check (R32); and among the reasons a check cannot run for,
the fixed precedence stop-unverified > implausible > no-fix > too-coarse
(R20), so the office reads the cause it can act on first (a pin to fix).

The absent classification (``classify_absent``, R16) shares the unverified
reasons and their precedence, then asks what corroborates the mark: an
absence a parent or the office recorded before the tap, the phone at the
child's stop, or — on an afternoon run — the phone at the school while the
child's stop is still ahead. Anything else is ``remote``, and the driver's
attestation decides it (R17).

The plausibility safeguard (``plausibility_flags``, R32) is the second
opinion on the fix itself: compared with the run's previous fix and the
run's planned stops, does this fix look like something a phone on a bus
would report? Its flags are stored on the trail row and make every check
``unverified`` / ``implausible`` — a flagged fix neither raises nor clears a
custody or vicinity check, corroborates nothing and is never "bus seen at
stop" — while the office gets one ``implausible-movement`` exception per run
listing the flagged fixes. Flags never block the tap.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.geo_service import haversine_m

# fix_reason vocabulary on the trail row (DAO-owned, no CHECK — see 016).
FIX_REASON_NONE = "none"          # the body carried a usable fix, or no fix key at all
FIX_REASON_COARSE = "coarse"      # coordinates kept, accuracy above the cap
FIX_REASON_INVALID = "invalid"    # malformed or out of range; nothing stored
CLIENT_FIX_REASONS = frozenset({"denied", "unavailable", "timeout", "coarse"})
FIX_REASONS = ("none", "denied", "unavailable", "timeout", "coarse", "invalid")

# flags vocabulary on the trail row (DAO-owned, no CHECK — see 016).
#
# The plausibility flags (U12, R32) — stable names, one per rule, in the
# order they are reported. Any of these on a fix makes every check on it
# `unverified` / `implausible`; the office reads them comma-joined as the
# `implausible-movement` exception's reason, and the staff panel's REASON_LABEL
# map (RunExceptionsPanel.tsx) must cover every name here.
#
# The plan's "identical accuracy across consecutive fixes" arm is deliberately
# left out (decided during U12). iOS reports horizontal accuracy in quantised
# steps (5, 10, 35, 65 m ...), so identical consecutive accuracies are the
# normal case on an iPhone, and a flag on them would classify most iPhone
# Boards and Absents `implausible` — switching the custody and absent checks
# off for those drivers, which is worse than the fabrication it targets.
# `repeat-coordinates` still catches a frozen or replayed feed: a genuinely
# stuck feed repeats both.
FLAG_CLOCK_SKEW = "clock-skew"                    # capture time ahead of receipt beyond tolerance (U7)
FLAG_JUMP = "jump"                                # over 1 km from the previous fix in under 30 s
FLAG_SPEED = "speed"                              # implied speed from the previous fix above 40 m/s
FLAG_ACCURACY_ZERO = "accuracy-zero"              # accuracy reported as exactly zero
FLAG_REPEAT_COORDINATES = "repeat-coordinates"    # the previous distinct capture had the same coordinates
FLAG_AT_PLANNED_STOP = "at-planned-stop"          # within two metres of a planned stop's pin
PLAUSIBILITY_FLAGS = (
    FLAG_CLOCK_SKEW, FLAG_JUMP, FLAG_SPEED, FLAG_ACCURACY_ZERO,
    FLAG_REPEAT_COORDINATES, FLAG_AT_PLANNED_STOP,
)
# Bookkeeping, not plausibility: the savepoint tier failed on this tap (R40).
FLAG_CLASSIFICATION_FAILED = "classification-failed"

DEFAULT_CLOCK_SKEW_TOLERANCE_S = 30

# The plausibility thresholds (U12). System-wide, not per school: they
# describe what a bus can do, not what a school prefers.
PLAUSIBLE_MAX_SPEED_MPS = 40.0     # 144 km/h; above it the fix is `speed`
JUMP_DISTANCE_M = 1000.0           # further than this ...
JUMP_WINDOW_S = 30.0               # ... in less than this is `jump`
PLANNED_STOP_RADIUS_M = 2.0        # a fix this close to a stop's pin is `at-planned-stop`


@dataclass(frozen=True)
class StoredFix:
    """The four columns a fix-bearing trail row stores. ``captured_at`` is
    always timezone-aware."""

    lat: float
    lng: float
    accuracy_m: float
    captured_at: datetime


@dataclass(frozen=True)
class NormalisedFix:
    """What the boundary made of the body's ``fix``.

    ``fix`` is None whenever nothing is stored — no fix sent, a browser
    reason, or ``invalid``. ``reason`` is one of ``FIX_REASONS``. ``flags``
    is what the row carries beyond the reason (only ``clock-skew`` here;
    U9/U12 append theirs on the row itself).
    """

    fix: StoredFix | None
    reason: str
    flags: tuple[str, ...] = ()

    @property
    def servable(self) -> bool:
        """May this fix become the bus's served position? A stored fix that
        is not clock-skewed — coarse included: the served pair is "where the
        phone said it was", and U8 labels it with the accuracy that travels
        beside it. Start Run overrides this to "never" on its own (R36)."""
        return self.fix is not None and FLAG_CLOCK_SKEW not in self.flags

    def fingerprint_material(self) -> dict[str, Any]:
        """The fix as the idempotency fingerprint sees it (R33): the stored
        values, or the reason alone. Flags are excluded — they depend on the
        receipt time, and a retry must fingerprint the same."""
        if self.fix is None:
            return {"reason": self.reason}
        return {
            "lat": self.fix.lat,
            "lng": self.fix.lng,
            "accuracy_m": self.fix.accuracy_m,
            "captured_at": self.fix.captured_at.astimezone(timezone.utc).isoformat(),
            "reason": self.reason,
        }


_NO_FIX = NormalisedFix(fix=None, reason=FIX_REASON_NONE)
_INVALID = NormalisedFix(fix=None, reason=FIX_REASON_INVALID)


def _finite_number(value: Any) -> float | None:
    # bool is an int subclass; a JSON true is not a coordinate.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _aware_datetime(value: Any) -> datetime | None:
    """ISO 8601 with an offset (``+03:00`` or ``Z``). A naive string is
    rejected: without the device's offset the capture time is meaningless
    across the phone, the server and Nairobi."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        return None
    return parsed


def normalise_fix(
    raw: Any,
    *,
    now: datetime,
    accuracy_cap_m: float,
    skew_tolerance_s: float = DEFAULT_CLOCK_SKEW_TOLERANCE_S,
) -> NormalisedFix:
    """Turn the body's ``fix`` into what the trail row stores (R2, R40).

    Rules, in order:

    - no ``fix`` at all (None) → nothing stored, reason ``none`` — an older
      client, or a body that simply omitted it;
    - not an object → ``invalid``;
    - an object with no coordinate fields and a browser reason (``denied``,
      ``unavailable``, ``timeout``) → nothing stored, that reason; any other
      such object → ``invalid``;
    - an object with coordinate fields: ``lat`` and ``lng`` must be finite
      numbers within ±90 / ±180, ``accuracy_m`` a finite number ≥ 0 and
      ``captured_at`` an aware ISO 8601 string — anything else → ``invalid``
      (nothing stored; the tap still completes);
    - a valid fix's reason is decided here from ``accuracy_cap_m``, never
      taken from the client's label: above the cap → ``coarse``, else
      ``none``;
    - a capture time later than ``now`` by more than ``skew_tolerance_s`` is
      stored as sent and flagged ``clock-skew`` (excluded from the served
      position by ``servable``); a capture time in the past is kept as
      captured, however old.

    ``now`` must be timezone-aware.
    """
    if raw is None:
        return _NO_FIX
    if not isinstance(raw, dict):
        return _INVALID

    coordinate_keys = ("lat", "lng", "accuracy_m", "captured_at")
    if not any(key in raw for key in coordinate_keys):
        reason = raw.get("reason")
        if isinstance(reason, str) and reason in CLIENT_FIX_REASONS and reason != FIX_REASON_COARSE:
            return NormalisedFix(fix=None, reason=reason)
        return _INVALID

    lat = _finite_number(raw.get("lat"))
    lng = _finite_number(raw.get("lng"))
    accuracy = _finite_number(raw.get("accuracy_m"))
    captured_at = _aware_datetime(raw.get("captured_at"))
    if lat is None or lng is None or accuracy is None or captured_at is None:
        return _INVALID
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lng <= 180.0) or accuracy < 0:
        return _INVALID

    reason = FIX_REASON_COARSE if accuracy > accuracy_cap_m else FIX_REASON_NONE
    flags: tuple[str, ...] = ()
    if captured_at > now + timedelta(seconds=skew_tolerance_s):
        flags = (FLAG_CLOCK_SKEW,)
    return NormalisedFix(
        fix=StoredFix(lat=lat, lng=lng, accuracy_m=accuracy, captured_at=captured_at),
        reason=reason,
        flags=flags,
    )


# --- the custody check (U9: R14, R20; R32's hook) ------------------------------

CUSTODY_WITHIN = "within"
CUSTODY_AWAY = "away"
CUSTODY_UNVERIFIED = "unverified"

# The `reason` an `unverified` exception carries, in precedence order (R20):
# when several apply to one tap, the first wins.
REASON_STOP_UNVERIFIED = "stop-unverified"   # the stop has no usable coordinates
REASON_IMPLAUSIBLE = "implausible"           # the fix carries a plausibility flag (U12)
REASON_NO_FIX = "no-fix"                     # nothing stored: denied, unavailable, timeout, invalid, omitted
REASON_TOO_COARSE = "too-coarse"             # accuracy above the cap
UNVERIFIED_REASONS = (
    REASON_STOP_UNVERIFIED, REASON_IMPLAUSIBLE, REASON_NO_FIX, REASON_TOO_COARSE,
)


@dataclass(frozen=True)
class CustodyCheck:
    """The verdict on one Board or Drop-off. ``distance_m`` is the great-circle
    distance from the fix to the stop whenever both exist — reported even
    when the verdict is ``unverified``, for the office; None otherwise."""

    classification: str
    reason: str | None
    distance_m: float | None

    @property
    def away(self) -> bool:
        return self.classification == CUSTODY_AWAY

    @property
    def unverified(self) -> bool:
        return self.classification == CUSTODY_UNVERIFIED


def _usable_point(point: tuple[Any, Any] | None) -> tuple[float, float] | None:
    """A (lat, lng) pair with both finite and in range, else None. Stops are
    geocoded, not surveyed: a missing or low-confidence pin degrades the
    check to unverified rather than classifying against a wrong place."""
    if point is None:
        return None
    lat = _finite_number(point[0])
    lng = _finite_number(point[1])
    if lat is None or lng is None:
        return None
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lng <= 180.0):
        return None
    return lat, lng


def plausibility_flags(
    fix: NormalisedFix | StoredFix | None,
    *,
    previous: StoredFix | None = None,
    stops: Iterable[tuple[Any, Any] | None] = (),
    **context: Any,
) -> tuple[str, ...]:
    """The plausibility safeguard's flags for this fix (U12, R32), in
    ``PLAUSIBILITY_FLAGS`` order; ``()`` when nothing is stored or nothing
    looks wrong.

    ``previous`` is the run's previous fix-bearing trail row inside the
    school's retention, by receipt order (the DAO chooses it; None for the
    first fix of a run); ``stops`` the run's planned stop coordinates
    (``run_stops`` for this run, gate included). The rules:

    - ``clock-skew``: the boundary's own flag (U7) folded in, so a skewed fix
      classifies as implausible too — only a ``NormalisedFix`` carries it;
    - ``accuracy-zero``: accuracy of exactly 0 (non-finite is already
      ``invalid`` at the boundary and never reaches here);
    - ``at-planned-stop``: within ``PLANNED_STOP_RADIUS_M`` of any planned
      stop's pin — a phone does not land on a geocoded pin by chance;
    - against ``previous``, only when its capture time differs from this
      one's — an identical capture time is the same fix re-read from the
      client's cache (U6's 15 s window; two Boards legitimately share one),
      not a second observation: ``repeat-coordinates`` (identical lat and
      lng), and, when the capture interval is positive, ``speed`` (distance
      ÷ interval above ``PLAUSIBLE_MAX_SPEED_MPS``) and ``jump`` (over
      ``JUMP_DISTANCE_M`` in under ``JUMP_WINDOW_S``). A zero or negative
      interval yields no speed or jump verdict: a cached fix may
      legitimately predate the previous tap's fresh one.

    Identical accuracy is not a rule (see the vocabulary's note). Coarse
    fixes are judged like any other: a wide fix can still be a teleport.
    ``context`` keeps the signature open for later inputs.
    """
    inherited: tuple[str, ...] = ()
    if isinstance(fix, NormalisedFix):
        inherited = fix.flags
        fix = fix.fix
    if fix is None:
        return ()

    found: set[str] = set()
    if FLAG_CLOCK_SKEW in inherited:
        found.add(FLAG_CLOCK_SKEW)
    if fix.accuracy_m == 0:
        found.add(FLAG_ACCURACY_ZERO)
    here = (fix.lat, fix.lng)
    for stop in stops:
        point = _usable_point(stop)
        if point is not None and haversine_m(here, point) <= PLANNED_STOP_RADIUS_M:
            found.add(FLAG_AT_PLANNED_STOP)
            break
    if previous is not None and previous.captured_at != fix.captured_at:
        if previous.lat == fix.lat and previous.lng == fix.lng:
            found.add(FLAG_REPEAT_COORDINATES)
        interval = (fix.captured_at - previous.captured_at).total_seconds()
        if interval > 0:
            distance = haversine_m(here, (previous.lat, previous.lng))
            if distance / interval > PLAUSIBLE_MAX_SPEED_MPS:
                found.add(FLAG_SPEED)
            if distance > JUMP_DISTANCE_M and interval < JUMP_WINDOW_S:
                found.add(FLAG_JUMP)
    return tuple(flag for flag in PLAUSIBILITY_FLAGS if flag in found)


def jump_distance_m(fix: StoredFix | None, previous: StoredFix | None) -> float | None:
    """How far this fix sits from the previous distinct capture — the
    ``distance_m`` an ``implausible-movement`` ledger event carries ("moved
    N m between fixes"). None without a previous fix, or when the two are
    the same capture."""
    if fix is None or previous is None or previous.captured_at == fix.captured_at:
        return None
    return haversine_m((fix.lat, fix.lng), (previous.lat, previous.lng))


def classify_custody(
    fix: StoredFix | None,
    stop: tuple[Any, Any] | None,
    *,
    custody_threshold_m: float,
    accuracy_cap_m: float,
    flags: Iterable[str] = (),
) -> CustodyCheck:
    """Classify a Board or Drop-off tap against the child's stop (R14, R20).

    In order:

    - the stop has no usable coordinates → ``unverified`` / ``stop-unverified``,
      whatever the fix (the pin is what the office must fix first);
    - no stored fix → ``unverified`` / ``no-fix``;
    - the fix carries a plausibility flag → ``unverified`` / ``implausible``,
      even when it is also coarse: a flagged fix is not evidence either way;
    - accuracy above the cap → ``unverified`` / ``too-coarse``. The cap runs
      before any subtraction: 800 m away with a 900 m radius is not "within";
    - otherwise ``distance − accuracy`` against the threshold: past it is
      ``away``, at or inside it is ``within``.

    The distance travels on the verdict whenever fix and stop both exist.
    """
    point = _usable_point(stop)
    distance = haversine_m((fix.lat, fix.lng), point) if fix is not None and point else None
    reason = _unverified_reason(fix, point, accuracy_cap_m=accuracy_cap_m, flags=flags)
    if reason is not None:
        return CustodyCheck(
            classification=CUSTODY_UNVERIFIED, reason=reason,
            distance_m=distance if reason != REASON_STOP_UNVERIFIED else None,
        )
    assert fix is not None and distance is not None
    if distance - fix.accuracy_m > custody_threshold_m:
        return CustodyCheck(classification=CUSTODY_AWAY, reason=None, distance_m=distance)
    return CustodyCheck(classification=CUSTODY_WITHIN, reason=None, distance_m=distance)


def _unverified_reason(
    fix: StoredFix | None,
    point: tuple[float, float] | None,
    *,
    accuracy_cap_m: float,
    flags: Iterable[str],
) -> str | None:
    """The one reason a check cannot run for, in R20's precedence — or None
    when it can. Shared by the custody check and the absent classification so
    the two can never rank the same tap differently."""
    reasons: list[str] = []
    if point is None:
        reasons.append(REASON_STOP_UNVERIFIED)
    if fix is None:
        reasons.append(REASON_NO_FIX)
    else:
        if any(True for _ in flags):
            reasons.append(REASON_IMPLAUSIBLE)
        if fix.accuracy_m > accuracy_cap_m:
            reasons.append(REASON_TOO_COARSE)
    if not reasons:
        return None
    return next(r for r in UNVERIFIED_REASONS if r in reasons)


def within_vicinity(
    fix: StoredFix | None,
    stop: tuple[Any, Any] | None,
    *,
    vicinity_m: float,
    accuracy_cap_m: float,
) -> bool:
    """Did this fix place the phone at the stop? The same geometry as the
    custody check — cap first, then ``distance − accuracy`` inside the
    vicinity radius — so "bus seen at stop" (R13) and, later, U15's arrival
    nudge read one rule. A coarse fix on the stop vouches for nothing; a stop
    without coordinates is never seen."""
    point = _usable_point(stop)
    if fix is None or point is None or fix.accuracy_m > accuracy_cap_m:
        return False
    return haversine_m((fix.lat, fix.lng), point) - fix.accuracy_m <= vicinity_m


# --- the absent classification (U10: R16, R17, R20) -----------------------------

ABSENT_CORROBORATED = "corroborated"
ABSENT_REMOTE = "remote"
ABSENT_UNVERIFIED = "unverified"

# What corroborated the mark, when something did (R16), in the order tested.
CORROBORATED_BY_ABSENCE = "absence"   # a parent or office absence already covered this trip
CORROBORATED_BY_STOP = "stop"         # the phone was within the child's stop vicinity
CORROBORATED_BY_SCHOOL = "school"     # afternoon: at the school, the child's stop still ahead


@dataclass(frozen=True)
class AbsentCheck:
    """The verdict on one Absent mark. ``distance_m`` is the great-circle
    distance from the fix to the child's stop whenever both exist — reported
    for the office and for the prompt's copy whatever the verdict."""

    classification: str
    reason: str | None
    corroborated_by: str | None
    distance_m: float | None

    @property
    def remote(self) -> bool:
        return self.classification == ABSENT_REMOTE

    @property
    def unverified(self) -> bool:
        return self.classification == ABSENT_UNVERIFIED

    @property
    def corroborated(self) -> bool:
        return self.classification == ABSENT_CORROBORATED


def classify_absent(
    fix: StoredFix | None,
    stop: tuple[Any, Any] | None,
    *,
    school: tuple[Any, Any] | None,
    run_type: str,
    stop_order: int | None,
    stops_completed: int,
    prior_absence_covers: bool,
    vicinity_m: float,
    accuracy_cap_m: float,
    flags: Iterable[str] = (),
) -> AbsentCheck:
    """Classify an Absent mark against the child's stop (R16, R20).

    In order:

    - the reasons a check cannot run for, with R20's precedence exactly as
      the custody check ranks them (stop-unverified > implausible > no-fix >
      too-coarse) → ``unverified`` with that reason;
    - a parent or office absence already covered this trip before the tap
      (``prior_absence_covers``, read by the caller before its own upsert)
      → ``corroborated`` by ``absence``;
    - the fix within the stop's vicinity — the same geometry as "bus seen at
      stop": cap first, then ``distance − accuracy`` inside ``vicinity_m``
      → ``corroborated`` by ``stop``;
    - an afternoon run, the child's stop order beyond ``stops_completed``
      and the fix within the school's vicinity (R16 as amended: the child did
      not board at the gate) → ``corroborated`` by ``school``;
    - otherwise ``remote`` — the driver's attestation decides (R17).

    ``school`` is the run's own gate coordinates, or the school's pin when the
    snapshot has none; without either the school arm simply does not apply.
    """
    point = _usable_point(stop)
    distance = haversine_m((fix.lat, fix.lng), point) if fix is not None and point else None
    reason = _unverified_reason(fix, point, accuracy_cap_m=accuracy_cap_m, flags=flags)
    if reason is not None:
        return AbsentCheck(
            classification=ABSENT_UNVERIFIED, reason=reason, corroborated_by=None,
            distance_m=distance if reason != REASON_STOP_UNVERIFIED else None,
        )
    assert fix is not None and distance is not None
    if prior_absence_covers:
        return AbsentCheck(
            classification=ABSENT_CORROBORATED, reason=None,
            corroborated_by=CORROBORATED_BY_ABSENCE, distance_m=distance,
        )
    if within_vicinity(fix, point, vicinity_m=vicinity_m, accuracy_cap_m=accuracy_cap_m):
        return AbsentCheck(
            classification=ABSENT_CORROBORATED, reason=None,
            corroborated_by=CORROBORATED_BY_STOP, distance_m=distance,
        )
    if (
        run_type == "afternoon"
        and stop_order is not None
        and stop_order > stops_completed
        and within_vicinity(fix, school, vicinity_m=vicinity_m, accuracy_cap_m=accuracy_cap_m)
    ):
        return AbsentCheck(
            classification=ABSENT_CORROBORATED, reason=None,
            corroborated_by=CORROBORATED_BY_SCHOOL, distance_m=distance,
        )
    return AbsentCheck(
        classification=ABSENT_REMOTE, reason=None, corroborated_by=None, distance_m=distance,
    )
