"""GPS tracking system defaults (GPS plan U11: R26, R31, R38) — the Settings
fields, their env aliases, the per-school resolver, and live parity.

Live parity: every GPS default the app reads is a template Parameter whose
Default equals the Settings default, wired into the API function's env under
the alias Settings reads, and passable from deploy-backend.sh under the same
name — the rule learned from the geo escape (`b68c877`), enforced here so a
new knob cannot ship live at a value nobody set. The migrate and verify
functions get the retention default too (the purge and the `gps` check set
resolve it). Pure text inspection of the template: no YAML dependency and no
CloudFormation tag handling needed.
"""

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.dao.school_thresholds import (
    KNOB_BOUNDS,
    PER_SCHOOL_KNOBS,
    SchoolThresholds,
    default_thresholds,
    system_defaults,
    thresholds_from_row,
)

REPO = Path(__file__).resolve().parents[3]
TEMPLATE = (REPO / "infra" / "backend" / "template.yaml").read_text(encoding="utf-8")
DEPLOY_SCRIPT = (REPO / "infra" / "scripts" / "deploy-backend.sh").read_text(encoding="utf-8")
MIGRATION_016 = (REPO / "backend" / "db" / "migrations" / "016_gps_tracking.sql").read_text(
    encoding="utf-8"
)
ENV_EXAMPLE = (REPO / "backend" / ".env.example").read_text(encoding="utf-8")

# Settings field -> (env alias, template parameter, default)
GPS_FIELDS = {
    "gps_custody_threshold_m": ("GPS_CUSTODY_THRESHOLD_M", "GpsCustodyThresholdM", 150),
    "gps_vicinity_radius_m": ("GPS_VICINITY_RADIUS_M", "GpsVicinityRadiusM", 100),
    "gps_fix_accuracy_cap_m": ("GPS_FIX_ACCURACY_CAP_M", "GpsFixAccuracyCapM", 200),
    "gps_position_retention_days": ("GPS_POSITION_RETENTION_DAYS", "GpsPositionRetentionDays", 90),
    "gps_ping_interval_s": ("GPS_PING_INTERVAL_S", "GpsPingIntervalS", 10),
    "gps_stale_after_s": ("GPS_STALE_AFTER_S", "GpsStaleAfterS", 90),
    "gps_fix_wait_budget_s": ("GPS_FIX_WAIT_BUDGET_S", "GpsFixWaitBudgetS", 5),
}


def _settings(**env) -> Settings:
    # No dotenv: the repo's backend/.env must not leak into these assertions.
    return Settings(_env_file=None, **env)


def _template_parameter(name: str) -> dict[str, str]:
    """The `Key: value` lines of one Parameters entry, by indentation."""
    match = re.search(rf"^  {name}:\n((?:    .*\n)+)", TEMPLATE, re.M)
    assert match, f"template Parameter {name} missing"
    block = match.group(1)
    return dict(re.findall(r"^    (\w+): (.+?)\s*$", block, re.M))


def _function_env(logical_id: str) -> dict[str, str]:
    """The Environment.Variables of one function resource, as text pairs."""
    start = TEMPLATE.index(f"\n  {logical_id}:\n")
    tail = TEMPLATE[start + 1:]
    end = re.search(r"^  \w+:\n", tail[1:], re.M)
    body = tail[: end.start() + 1] if end else tail
    variables = re.search(r"^      Environment:\n        Variables:\n((?:          .*\n)+)", body, re.M)
    assert variables, f"{logical_id} has no Environment.Variables block"
    return dict(re.findall(r"^          (\w+): (.+?)\s*$", variables.group(1), re.M))


# --- Settings fields -----------------------------------------------------------


def test_defaults_match_the_plan():
    s = _settings()
    for field, (_alias, _param, default) in GPS_FIELDS.items():
        assert getattr(s, field) == default, field


@pytest.mark.parametrize("field,alias", [(f, v[0]) for f, v in GPS_FIELDS.items()])
def test_each_field_is_overridable_from_its_env_alias(field, alias):
    lo, hi = {
        "gps_custody_threshold_m": (25, 2000),
        "gps_vicinity_radius_m": (25, 2000),
        "gps_fix_accuracy_cap_m": (25, 2000),
        "gps_position_retention_days": (7, 365),
        "gps_ping_interval_s": (5, 60),
        "gps_stale_after_s": (10, 3600),
        "gps_fix_wait_budget_s": (1, 30),
    }[field]
    assert getattr(_settings(**{alias: str(hi)}), field) == hi
    assert getattr(_settings(**{alias: str(lo)}), field) == lo
    # Out of bounds is a loud boot failure, not a silent clamp.
    with pytest.raises(ValidationError):
        _settings(**{alias: str(hi + 1)})
    with pytest.raises(ValidationError):
        _settings(**{alias: str(lo - 1)})


def test_per_school_knob_bounds_equal_migration_016_checks():
    """The API's 422 bounds and the column CHECKs are one table."""
    for knob, (lo, hi) in KNOB_BOUNDS.items():
        pattern = rf"add column if not exists {knob} integer\s+check \({knob} between (\d+) and (\d+)\)"
        match = re.search(pattern, MIGRATION_016)
        assert match, f"016 has no CHECK for {knob}"
        assert (int(match.group(1)), int(match.group(2))) == (lo, hi), knob
    assert set(KNOB_BOUNDS) == set(PER_SCHOOL_KNOBS)


def test_settings_bounds_equal_the_per_school_bounds():
    """A system default outside the per-school bounds would be a value no
    school could set for itself; the Settings fields carry the same limits."""
    fields = Settings.model_fields
    knob_to_field = {
        "custody_threshold_m": "gps_custody_threshold_m",
        "vicinity_radius_m": "gps_vicinity_radius_m",
        "fix_accuracy_cap_m": "gps_fix_accuracy_cap_m",
        "position_retention_days": "gps_position_retention_days",
        "ping_interval_s": "gps_ping_interval_s",
    }
    for knob, field in knob_to_field.items():
        meta = {type(m).__name__: m for m in fields[field].metadata}
        assert (meta["Ge"].ge, meta["Le"].le) == KNOB_BOUNDS[knob], knob


# --- the resolver ---------------------------------------------------------------


def test_thresholds_from_row_prefers_the_stored_value_and_defaults_the_rest():
    resolved = thresholds_from_row({"custody_threshold_m": 300, "ping_interval_s": None})
    assert resolved.custody_threshold_m == 300
    assert resolved.vicinity_radius_m == 100
    assert resolved.fix_accuracy_cap_m == 200
    assert resolved.position_retention_days == 90
    assert resolved.ping_interval_s == 10
    assert resolved.stale_after_s == 90
    assert resolved.fix_wait_budget_s == 5
    assert isinstance(resolved, SchoolThresholds)


def test_default_thresholds_and_system_defaults_agree():
    defaults = default_thresholds()
    assert system_defaults() == {
        "custody_threshold_m": 150,
        "vicinity_radius_m": 100,
        "fix_accuracy_cap_m": 200,
        "position_retention_days": 90,
        "ping_interval_s": 10,
    }
    assert tuple(system_defaults()) == PER_SCHOOL_KNOBS
    for knob, value in system_defaults().items():
        assert getattr(defaults, knob) == value


def test_driver_config_serves_exactly_the_three_client_keys():
    config = thresholds_from_row({"fix_accuracy_cap_m": 500, "ping_interval_s": 20}).driver_config()
    assert config == {"fix_wait_budget_s": 5, "fix_accuracy_cap_m": 500, "ping_interval_s": 20}


# --- the payload's three-way semantics --------------------------------------------


def test_school_payload_distinguishes_omitted_null_and_set():
    from app.api.fleet import SchoolPayload

    body = SchoolPayload.model_validate(
        {"name": "S", "custody_threshold_m": 300, "vicinity_radius_m": None}
    )
    assert body.tracking_fields() == {"custody_threshold_m": 300, "vicinity_radius_m": None}
    assert set(body.base_fields()) == {
        "name", "address", "phone", "lat", "lng", "morning_bell", "afternoon_bell",
    }
    assert SchoolPayload.model_validate({"name": "S"}).tracking_fields() == {}


@pytest.mark.parametrize("knob", PER_SCHOOL_KNOBS)
def test_school_payload_rejects_values_outside_the_bounds(knob):
    from app.api.fleet import SchoolPayload

    lo, hi = KNOB_BOUNDS[knob]
    assert getattr(SchoolPayload.model_validate({"name": "S", knob: lo}), knob) == lo
    assert getattr(SchoolPayload.model_validate({"name": "S", knob: hi}), knob) == hi
    for bad in (lo - 1, hi + 1, lo + 0.5, "abc"):
        with pytest.raises(ValidationError):
            SchoolPayload.model_validate({"name": "S", knob: bad})


# --- live parity ----------------------------------------------------------------


@pytest.mark.parametrize("field,alias,param,default", [(f, *v) for f, v in GPS_FIELDS.items()])
def test_template_parameter_default_equals_settings_default(field, alias, param, default):
    entry = _template_parameter(param)
    assert entry["Type"] == "Number"
    assert int(entry["Default"]) == default == getattr(_settings(), field)
    meta = {type(m).__name__: m for m in Settings.model_fields[field].metadata}
    assert (int(entry["MinValue"]), int(entry["MaxValue"])) == (meta["Ge"].ge, meta["Le"].le)


@pytest.mark.parametrize("alias,param", [(v[0], v[1]) for v in GPS_FIELDS.values()])
def test_api_function_env_carries_every_alias(alias, param):
    assert _function_env("ApiFunction")[alias] == f"!Ref {param}"


def test_migrate_and_verify_functions_carry_the_retention_default():
    for logical_id in ("MigrateFunction", "VerifyFunction"):
        env = _function_env(logical_id)
        assert env["GPS_POSITION_RETENTION_DAYS"] == "!Ref GpsPositionRetentionDays", logical_id


@pytest.mark.parametrize("alias,param", [(v[0], v[1]) for v in GPS_FIELDS.values()])
def test_deploy_script_maps_each_alias_to_its_parameter(alias, param):
    assert f"{param}:{alias}" in DEPLOY_SCRIPT


@pytest.mark.parametrize("alias", [v[0] for v in GPS_FIELDS.values()])
def test_env_example_documents_each_alias_with_its_default(alias):
    default = next(v[2] for v in GPS_FIELDS.values() if v[0] == alias)
    assert re.search(rf"^# {alias}={default}$", ENV_EXAMPLE, re.M), alias


def test_no_reader_imports_a_retired_module_constant():
    """The five geometry/cadence defaults are Settings fields now; a stray
    import of the old constants would bake a value in at import time."""
    retired = (
        "GPS_POSITION_RETENTION_DAYS", "GPS_FIX_ACCURACY_CAP_M", "GPS_STALE_AFTER_S",
        "GPS_CUSTODY_THRESHOLD_M", "GPS_VICINITY_RADIUS_M",
    )
    # Imports and attribute reads only: the names live on as env aliases in
    # config.py's Field(alias=...) and in comments that cite the variable.
    for path in (REPO / "backend" / "app").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for name in retired:
            used = re.search(rf"^\s*(from [\w.]+ import[^\n]*\b{name}\b|.*\bconfig\.{name}\b)", text, re.M)
            assert used is None, f"{path.name} still reads {name}"
    # And the constants themselves are gone.
    import app.core.config as config

    for name in retired:
        assert not hasattr(config, name), name
