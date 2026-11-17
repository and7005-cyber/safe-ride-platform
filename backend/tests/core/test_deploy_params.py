"""Live-parity parameters reach the stack on every deploy (hardening Release 1a).

SAM CLI (1.162.1) sends UsePreviousValue for every parameter a deploy does
not pass, so on an existing stack a changed template Default never arrived
and a one-off exported override stuck until another export. deploy-backend.sh
now sources infra/scripts/parity-params.sh, whose build_parity_params passes
every parity parameter with a non-empty template Default explicitly: the
exported variable when set, otherwise the Default read from the template.
Empty-Default parameters (the alert addresses, MailReplyTo) are passed only
when set. These tests run the real shell function in a subshell with a
controlled environment; no AWS call is involved.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPTS = REPO / "infra" / "scripts"
PARITY = SCRIPTS / "parity-params.sh"
TEMPLATE_PATH = REPO / "infra" / "backend" / "template.yaml"
TEMPLATE = TEMPLATE_PATH.read_text(encoding="utf-8")
DEPLOY_BACKEND = (SCRIPTS / "deploy-backend.sh").read_text(encoding="utf-8")

MAPS = ("GPS_PARAMS", "API_THROTTLE_PARAMS", "MAIL_PARAMS", "ALERT_PARAMS")
# The operator's shell on macOS is /bin/bash 3.2; pin it where it exists.
BASH = "/bin/bash" if Path("/bin/bash").exists() else shutil.which("bash")
# The parameters whose template Default is empty: passed only when exported.
EMPTY_DEFAULT = {"AlertEmailPrimary", "AlertEmailSecondary", "MailReplyTo"}


def _bash(script: str, *args: str, env: dict[str, str] | None = None):
    return subprocess.run(
        [BASH, "-c", script, "bash", *args],
        env=env, capture_output=True, text=True, timeout=60,
    )


def _pairs() -> list[tuple[str, str]]:
    """(template parameter, env name) for every map entry, in map order."""
    arrays = " ".join(f'"${{{name}[@]}}"' for name in MAPS)
    result = _bash(f'set -euo pipefail; source "$1"; printf "%s\\n" {arrays}', str(PARITY))
    assert result.returncode == 0, result.stderr
    return [tuple(line.split(":", 1)) for line in result.stdout.splitlines()]


PAIRS = _pairs()
ENV_NAMES = {env for _, env in PAIRS}


def _clean_env(**exported: str) -> dict[str, str]:
    """The test process's environment without any map variable, plus these."""
    env = {key: value for key, value in os.environ.items() if key not in ENV_NAMES}
    env.update(exported)
    return env


def _build(template: Path = TEMPLATE_PATH, **exported: str):
    """Run build_parity_params; return (Name -> value in order, its log, result)."""
    script = (
        'set -euo pipefail; source "$1"; DEPLOY_PARAMS=();'
        ' build_parity_params "$2" >&2; printf "%s\\n" "${DEPLOY_PARAMS[@]}"'
    )
    result = _bash(script, str(PARITY), str(template), env=_clean_env(**exported))
    params = {}
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            name, _, value = line.partition("=")
            assert name not in params, f"{name} passed twice"
            params[name] = value
    return params, result.stderr, result


def _template_default(name: str) -> str:
    """The Default of one Parameters entry, unquoted (the parity tests' reading)."""
    entry = re.search(rf"^  {name}:\n((?:    .*\n)+)", TEMPLATE, re.M)
    assert entry, f"template Parameter {name} missing"
    default = re.search(r"^    Default: (.*?)\s*$", entry.group(1), re.M)
    assert default, f"template Parameter {name} has no Default"
    raw = default.group(1)
    return raw[1:-1] if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'" else raw


def test_the_maps_cover_the_alarm_threshold_and_name_each_parameter_once():
    names = [param for param, _ in PAIRS]
    assert len(names) == len(set(names)) and len(ENV_NAMES) == len(PAIRS)
    assert ("ConcurrencyAlarmThreshold", "CONCURRENCY_ALARM_THRESHOLD") in PAIRS
    assert {param for param in names if _template_default(param) == ""} == EMPTY_DEFAULT


def test_with_nothing_exported_every_parity_parameter_is_passed_at_its_template_default():
    params, log, result = _build()
    assert result.returncode == 0, result.stderr
    expected = {param: _template_default(param) for param, _ in PAIRS if param not in EMPTY_DEFAULT}
    assert params == expected
    # Spot checks against the values the other parity tests pin.
    assert params["GpsCustodyThresholdM"] == "150"
    assert params["PinLoginRouteRateLimit"] == "20"
    assert params["MailFromAddress"] == "no-reply@saferidelive.co.ke"  # quoted in the template
    assert params["MailEnabled"] == "false"
    assert params["ConcurrencyAlarmThreshold"] == "8"
    assert "Parameter override" not in log
    assert "Live-parity parameters at their template Default: GpsCustodyThresholdM=150" in log


def test_one_exported_variable_overrides_only_its_parameter():
    baseline, _, _ = _build()
    params, log, result = _build(GPS_CUSTODY_THRESHOLD_M="200")
    assert result.returncode == 0, result.stderr
    assert params["GpsCustodyThresholdM"] == "200"
    assert {k: v for k, v in params.items() if k != "GpsCustodyThresholdM"} == {
        k: v for k, v in baseline.items() if k != "GpsCustodyThresholdM"
    }
    assert "Parameter override: GpsCustodyThresholdM=200 (from $GPS_CUSTODY_THRESHOLD_M)" in log


def test_the_concurrency_threshold_follows_its_export():
    params, _, result = _build(CONCURRENCY_ALARM_THRESHOLD="800")
    assert result.returncode == 0, result.stderr
    assert params["ConcurrencyAlarmThreshold"] == "800"


def test_an_empty_export_means_the_template_default():
    params, _, result = _build(GPS_CUSTODY_THRESHOLD_M="", MAIL_ENABLED="")
    assert result.returncode == 0, result.stderr
    assert params["GpsCustodyThresholdM"] == "150"
    assert params["MailEnabled"] == "false"


def test_with_everything_exported_no_default_is_used():
    exported = {env: f"value-{index}" for index, (_, env) in enumerate(PAIRS)}
    params, log, result = _build(**exported)
    assert result.returncode == 0, result.stderr
    assert params == {param: exported[env] for param, env in PAIRS}
    assert "template Default" not in log


def test_empty_default_parameters_are_omitted_unless_exported():
    params, _, _ = _build()
    assert not EMPTY_DEFAULT & set(params)
    params, _, result = _build(
        ALERT_EMAIL_PRIMARY="ops-one@example.org", MAIL_REPLY_TO="office@example.org"
    )
    assert result.returncode == 0, result.stderr
    assert params["AlertEmailPrimary"] == "ops-one@example.org"
    assert params["MailReplyTo"] == "office@example.org"
    assert "AlertEmailSecondary" not in params


def _synthetic_template(tmp_path: Path, defaults: dict[str, str]) -> Path:
    """A template whose Parameters hold every parity name with the given
    Default spelling (verbatim YAML), followed by a Resources section."""
    lines = ["AWSTemplateFormatVersion: \"2010-09-09\"", "Parameters:"]
    for param, _ in PAIRS:
        lines += [
            "  # a comment line between parameters",
            f"  {param}:",
            "    Type: String",
            f"    Default: {defaults.get(param, '1')}",
            "    Description: >",
            "      Default: not-this-one (a folded description line)",
        ]
    lines += ["Resources:", "  Thing:", "    Type: AWS::SNS::Topic", ""]
    path = tmp_path / "template.yaml"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def test_the_reader_unquotes_and_strips_comments_like_yaml(tmp_path):
    template = _synthetic_template(tmp_path, {
        "GpsCustodyThresholdM": "175   # comment",
        "MailFromAddress": '"quoted@example.org"',
        "MailConfigurationSet": "'it''s-a-set'",
        "MailEnabled": "true",
        "AlertEmailPrimary": '""',
        "AlertEmailSecondary": "''",
        "MailReplyTo": '""',
    })
    params, _, result = _build(template)
    assert result.returncode == 0, result.stderr
    assert params["GpsCustodyThresholdM"] == "175"
    assert params["MailFromAddress"] == "quoted@example.org"
    assert params["MailConfigurationSet"] == "it's-a-set"
    assert params["MailEnabled"] == "true"
    assert params["GpsVicinityRadiusM"] == "1"
    assert not EMPTY_DEFAULT & set(params)


def test_a_parameter_missing_from_the_template_stops_the_build_naming_it(tmp_path):
    text = re.sub(r"^  MailEnabled:\n(?:    .*\n)+", "", TEMPLATE, count=1, flags=re.M)
    assert "  MailEnabled:\n" not in text
    template = tmp_path / "template.yaml"
    template.write_text(text, encoding="utf-8")
    _, _, result = _build(template)
    assert result.returncode != 0
    assert "MailEnabled is not in" in result.stderr
    assert result.stdout == ""  # nothing was appended


def test_a_parameter_without_a_default_stops_the_build_naming_it(tmp_path):
    text = TEMPLATE.replace('    Default: "false"\n    AllowedValues: ["true", "false"]\n',
                            '    AllowedValues: ["true", "false"]\n', 1)
    assert text != TEMPLATE
    template = tmp_path / "template.yaml"
    template.write_text(text, encoding="utf-8")
    _, _, result = _build(template)
    assert result.returncode != 0
    assert "MailEnabled has no Default" in result.stderr


@pytest.mark.parametrize(
    "raw",
    [
        '"unterminated', "'bad'quote'", '"back\\slash"',
        # empty or null, or the value on the next line: never read as "empty"
        "", "~", "null", "# only a comment",
        # block, flow, tag, anchor and alias forms
        ">", "|", "[1, 2]", "{a: b}", "!Ref Other", "&anchor 150", "*anchor",
    ],
)
def test_an_unparseable_default_stops_the_build(tmp_path, raw):
    template = _synthetic_template(tmp_path, {"MailFromAddress": raw})
    _, _, result = _build(template)
    assert result.returncode != 0
    assert "MailFromAddress has a Default this reader cannot parse" in result.stderr


def test_deploy_backend_passes_the_parity_parameters_to_sam():
    """deploy-backend.sh sources the helper, builds the parameters from the
    template before `sam build`, hands DEPLOY_PARAMS to `sam deploy`, and keeps
    no map or override loop of its own."""
    assert re.search(r'^source "\$\(dirname "\$\{BASH_SOURCE\[0\]\}"\)/parity-params\.sh"$',
                     DEPLOY_BACKEND, re.M)
    call = DEPLOY_BACKEND.index('\nbuild_parity_params "$INFRA_DIR/backend/template.yaml"\n')
    assert DEPLOY_BACKEND.index("\nDEPLOY_PARAMS=(") < call < DEPLOY_BACKEND.index("\nsam build --use-container\n")
    assert call < DEPLOY_BACKEND.index('sam deploy --no-execute-changeset --parameter-overrides "${DEPLOY_PARAMS[@]}"')
    for name in MAPS:
        assert f"{name}=(" not in DEPLOY_BACKEND
    assert "for pair in" not in DEPLOY_BACKEND
