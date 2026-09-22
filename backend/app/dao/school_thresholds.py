"""Per-school tracking thresholds: the school's stored value, else the system
default (GPS plan U11: R26, R31, R38).

One authority for every geometry and cadence knob the GPS feature reads —
the ``resolve_gate_anchor`` shape (override, then default), applied to
migration 016's five ``live_schools`` columns:

- ``custody_threshold_m`` — a Board or Drop-off further than this from the
  child's stop, after the fix's accuracy is subtracted, is ``away`` (U9);
- ``vicinity_radius_m`` — a fix this close to a stop, accuracy subtracted,
  places the bus *at* it: the absent corroboration and "bus seen at stop"
  (U9, U10; the Phase 2 enter radius, U15);
- ``fix_accuracy_cap_m`` — a fix wider than this is ``coarse`` and can
  neither raise nor clear a check (U7, U9);
- ``position_retention_days`` — the trail's retention by receipt time and
  the read filter every trail reader applies (U7, R12);
- ``ping_interval_s`` — the Phase 2 ping cadence the driver app is told to
  keep (U14).

Two more knobs are system defaults only — no column, so no override:
``stale_after_s`` (U8's staleness) and ``fix_wait_budget_s`` (U6's cold-fix
wait). They ride the same object so the driver context and the SQL fragments
read one shape.

Leaf module: it imports ``app.core.config`` and nothing from ``app.dao``, so
``fleet_dao``, ``run_dao``, ``exception_dao`` and ``position_dao`` can all
take it without a cycle. Callers pass their own connection; nothing here opens
one or logs a value.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.core.config import get_settings

# The five per-school knobs, in the order School Settings shows them. These
# are ``live_schools`` column names; ``SchoolPayload`` (api/fleet.py) accepts
# exactly this set and ``update_school`` writes exactly this set.
PER_SCHOOL_KNOBS: tuple[str, ...] = (
    "custody_threshold_m",
    "vicinity_radius_m",
    "fix_accuracy_cap_m",
    "position_retention_days",
    "ping_interval_s",
)

# Inclusive bounds per knob — the same numbers as migration 016's CHECK
# constraints, enforced first at the API boundary (422) so the database CHECK
# is never the first line of defence. tests/core/test_gps_settings.py keeps
# this table in lockstep with the migration.
KNOB_BOUNDS: dict[str, tuple[int, int]] = {
    "custody_threshold_m": (25, 2000),
    "vicinity_radius_m": (25, 2000),
    "fix_accuracy_cap_m": (25, 2000),
    "position_retention_days": (7, 365),
    "ping_interval_s": (5, 60),
}

# Knob -> the Settings field that holds its system default.
_SETTINGS_FIELD: dict[str, str] = {
    "custody_threshold_m": "gps_custody_threshold_m",
    "vicinity_radius_m": "gps_vicinity_radius_m",
    "fix_accuracy_cap_m": "gps_fix_accuracy_cap_m",
    "position_retention_days": "gps_position_retention_days",
    "ping_interval_s": "gps_ping_interval_s",
}


@dataclass(frozen=True)
class SchoolThresholds:
    """The resolved knobs for one school. Every value is a positive int in
    its stated bounds: the per-school five are the stored value or the
    default; the last two are the system defaults."""

    custody_threshold_m: int
    vicinity_radius_m: int
    fix_accuracy_cap_m: int
    position_retention_days: int
    ping_interval_s: int
    stale_after_s: int
    fix_wait_budget_s: int

    def driver_config(self) -> dict[str, int]:
        """The ``config`` object the driver context serves (U6 reads exactly
        these keys; U14 adds the ping consumer). The client never hard-codes
        them."""
        return {
            "fix_wait_budget_s": self.fix_wait_budget_s,
            "fix_accuracy_cap_m": self.fix_accuracy_cap_m,
            "ping_interval_s": self.ping_interval_s,
        }


def system_defaults() -> dict[str, int]:
    """The five per-school knobs' system defaults, knob name -> value — what
    ``GET /api/fleet/school`` serves as ``tracking_defaults`` so the Settings
    page can show each default beside its field."""
    settings = get_settings()
    return {knob: int(getattr(settings, field)) for knob, field in _SETTINGS_FIELD.items()}


def default_thresholds() -> SchoolThresholds:
    """Every knob at its system default (a school with nothing stored)."""
    return thresholds_from_row(None)


def thresholds_from_row(row: Mapping[str, Any] | None) -> SchoolThresholds:
    """Resolve from a ``live_schools`` row already in hand (any mapping that
    carries the five columns; missing or null means default). Override, then
    default — never a blend: a stored value is used as stored, since the
    column CHECK and the payload bounds already keep it in range."""
    settings = get_settings()
    resolved: dict[str, int] = {}
    for knob, field in _SETTINGS_FIELD.items():
        stored = row.get(knob) if row is not None else None
        resolved[knob] = int(stored) if stored is not None else int(getattr(settings, field))
    return SchoolThresholds(
        **resolved,
        stale_after_s=int(settings.gps_stale_after_s),
        fix_wait_budget_s=int(settings.gps_fix_wait_budget_s),
    )


def resolve_school_thresholds(conn, school_id: str) -> SchoolThresholds:
    """Read the school's five columns on the caller's connection and resolve.
    A school the connection cannot see (RLS) or that does not exist resolves
    to the defaults — the readers never fail a tap over a missing row."""
    row = conn.execute(
        f"select {', '.join(PER_SCHOOL_KNOBS)} from live_schools where id = %s",  # noqa: S608
        (str(school_id),),
    ).fetchone()
    return thresholds_from_row(dict(row) if row is not None else None)
