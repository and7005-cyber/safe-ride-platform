"""Position rules: fix validation now (GPS plan U7), geometry classification
later (U9 adds the accuracy-cap → plausibility → distance-less-accuracy chain
to this same module; U10 the absent arms).

Pure: no I/O and no clock of its own — ``normalise_fix`` takes ``now`` — so
the whole boundary is unit-testable and the DAO calls it inside the action
transaction with whatever cap the school resolves to (U11).

The boundary is lenient by design (R2, R40). A tap's ``fix`` is whatever the
client sent: a usable fix, a coarse one with its coordinates attached, a
reason alone, something malformed, or nothing at all. None of those may fail
the tap, so the outcome is always a value: the stored fix or None, one
``fix_reason`` from ``FIX_REASONS`` and zero or more ``flags`` — never an
exception, never a 422.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

# fix_reason vocabulary on the trail row (DAO-owned, no CHECK — see 016).
FIX_REASON_NONE = "none"          # the body carried a usable fix, or no fix key at all
FIX_REASON_COARSE = "coarse"      # coordinates kept, accuracy above the cap
FIX_REASON_INVALID = "invalid"    # malformed or out of range; nothing stored
CLIENT_FIX_REASONS = frozenset({"denied", "unavailable", "timeout", "coarse"})
FIX_REASONS = ("none", "denied", "unavailable", "timeout", "coarse", "invalid")

# flags vocabulary on the trail row.
FLAG_CLOCK_SKEW = "clock-skew"                    # capture time ahead of receipt
FLAG_CLASSIFICATION_FAILED = "classification-failed"  # U9/U10's savepoint failed

DEFAULT_CLOCK_SKEW_TOLERANCE_S = 30


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
