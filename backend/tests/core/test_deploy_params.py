"""Live-parity parameters reach the stacks on every deploy (hardening Release 1a).

SAM CLI (1.162.1) and `aws cloudformation deploy` send UsePreviousValue for
every parameter a deploy does not pass, so on an existing stack a changed
template Default never arrived and a one-off exported override stuck until
another export. deploy-backend.sh and deploy-frontend.sh source
infra/scripts/parity-params.sh, whose build_parity_params passes every pair
of its list (PARITY_PAIRS for the backend, FRONTEND_PARAMS for the frontend)
with a non-empty template Default explicitly: the exported variable when set,
otherwise the Default read from the template. Empty-Default parameters (the
alert addresses, MailReplyTo) are passed only when set. These tests run the
real shell function in a subshell with a controlled environment, and
deploy-frontend.sh end to end against fake `aws`, `git` and `npm`
(tests/core/fake_cli.py); no AWS call is involved.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.core.fake_cli import FakeCli

REPO = Path(__file__).resolve().parents[3]
SCRIPTS = REPO / "infra" / "scripts"
PARITY = SCRIPTS / "parity-params.sh"
TEMPLATE_PATH = REPO / "infra" / "backend" / "template.yaml"
TEMPLATE = TEMPLATE_PATH.read_text(encoding="utf-8")
FRONTEND_TEMPLATE_PATH = REPO / "infra" / "frontend" / "template.yaml"
FRONTEND_TEMPLATE = FRONTEND_TEMPLATE_PATH.read_text(encoding="utf-8")
DEPLOY_BACKEND = (SCRIPTS / "deploy-backend.sh").read_text(encoding="utf-8")
DEPLOY_FRONTEND = (SCRIPTS / "deploy-frontend.sh").read_text(encoding="utf-8")

# The operator's shell on macOS is /bin/bash 3.2; pin it where it exists.
BASH = "/bin/bash" if Path("/bin/bash").exists() else shutil.which("bash")
# The parameters whose template Default is empty: passed only when exported.
EMPTY_DEFAULT = {"AlertEmailPrimary", "AlertEmailSecondary", "MailReplyTo"}
FRONTEND_EMPTY_DEFAULT = {"AlertEmailPrimary", "AlertEmailSecondary"}


def _bash(script: str, *args: str, env: dict[str, str] | None = None):
    return subprocess.run(
        [BASH, "-c", script, "bash", *args],
        env=env, capture_output=True, text=True, timeout=60,
    )


def _pairs(array: str) -> list[tuple[str, str]]:
    """(template parameter, env name) for every entry of one list, in order."""
    result = _bash(f'set -euo pipefail; source "$1"; printf "%s\\n" "${{{array}[@]}}"', str(PARITY))
    assert result.returncode == 0, result.stderr
    return [tuple(line.split(":", 1)) for line in result.stdout.splitlines()]


PAIRS = _pairs("PARITY_PAIRS")
FRONTEND_PAIRS = _pairs("FRONTEND_PARAMS")
ENV_NAMES = {env for _, env in PAIRS + FRONTEND_PAIRS}


def _clean_env(**exported: str) -> dict[str, str]:
    """The test process's environment without any list variable, plus these."""
    env = {key: value for key, value in os.environ.items() if key not in ENV_NAMES}
    env.update(exported)
    return env


def _build(template: Path = TEMPLATE_PATH, array: str = "PARITY_PAIRS", **exported: str):
    """Run build_parity_params; return (Name -> value in order, its log, result)."""
    script = (
        'set -euo pipefail; source "$1"; DEPLOY_PARAMS=();'
        f' build_parity_params "$2" "${{{array}[@]}}" >&2; printf "%s\\n" "${{DEPLOY_PARAMS[@]}}"'
    )
    result = _bash(script, str(PARITY), str(template), env=_clean_env(**exported))
    params = {}
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            name, _, value = line.partition("=")
            assert name not in params, f"{name} passed twice"
            params[name] = value
    return params, result.stderr, result


def _template_default(name: str, template: str = TEMPLATE) -> str:
    """The Default of one Parameters entry, unquoted (the parity tests' reading)."""
    entry = re.search(rf"^  {name}:\n((?:    .*\n)+)", template, re.M)
    assert entry, f"template Parameter {name} missing"
    default = re.search(r"^    Default: (.*?)\s*$", entry.group(1), re.M)
    assert default, f"template Parameter {name} has no Default"
    raw = default.group(1)
    return raw[1:-1] if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'" else raw


def _template_parameters(template: str) -> set[str]:
    section = template.split("\nParameters:\n", 1)[1].split("\nConditions:\n", 1)[0]
    return set(re.findall(r"^  (\w+):$", section, re.M))


def test_the_backend_list_covers_the_alarm_threshold_and_names_each_parameter_once():
    names = [param for param, _ in PAIRS]
    assert len(names) == len(set(names)) and len({env for _, env in PAIRS}) == len(PAIRS)
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


def test_build_parity_params_needs_a_template_and_pairs():
    result = _bash('set -euo pipefail; source "$1"; DEPLOY_PARAMS=(); build_parity_params "$2"',
                   str(PARITY), str(TEMPLATE_PATH), env=_clean_env())
    assert result.returncode != 0
    assert "needs a template and at least one Parameter:ENV pair" in result.stderr


def test_deploy_backend_passes_the_parity_parameters_to_sam():
    """deploy-backend.sh sources the helper, builds the parameters from the
    template before `sam build`, hands DEPLOY_PARAMS to `sam deploy`, and keeps
    no list or override loop of its own (test_changeset_gate.py runs it)."""
    assert re.search(r'^source "\$\(dirname "\$\{BASH_SOURCE\[0\]\}"\)/parity-params\.sh"$',
                     DEPLOY_BACKEND, re.M)
    call = DEPLOY_BACKEND.index('\nbuild_parity_params "$INFRA_DIR/backend/template.yaml" "${PARITY_PAIRS[@]}"\n')
    assert DEPLOY_BACKEND.index("\nDEPLOY_PARAMS=(") < call < DEPLOY_BACKEND.index("\nsam build --use-container\n")
    assert call < DEPLOY_BACKEND.index('--parameter-overrides "${DEPLOY_PARAMS[@]}"')
    assert "PARITY_PAIRS=(" not in DEPLOY_BACKEND
    assert "for pair in" not in DEPLOY_BACKEND


# --- frontend -----------------------------------------------------------------------


def test_the_frontend_list_covers_every_frontend_parameter_but_the_certificate():
    names = [param for param, _ in FRONTEND_PAIRS]
    assert len(names) == len(set(names))
    assert set(names) | {"CertificateArn"} == _template_parameters(FRONTEND_TEMPLATE)
    assert {param for param in names if _template_default(param, FRONTEND_TEMPLATE) == ""} == FRONTEND_EMPTY_DEFAULT
    assert dict(FRONTEND_PAIRS) == {
        "DomainName": "DOMAIN", "HostedZoneId": "HOSTED_ZONE_ID", "ApiOrigin": "API_ORIGIN",
        "CspMode": "CSP_MODE", "MonthlyBudgetUsd": "MONTHLY_BUDGET_USD",
        "AlertEmailPrimary": "ALERT_EMAIL_PRIMARY", "AlertEmailSecondary": "ALERT_EMAIL_SECONDARY",
    }


def test_with_nothing_exported_every_frontend_parameter_is_passed_at_its_template_default():
    params, log, result = _build(FRONTEND_TEMPLATE_PATH, "FRONTEND_PARAMS")
    assert result.returncode == 0, result.stderr
    assert params == {
        param: _template_default(param, FRONTEND_TEMPLATE)
        for param, _ in FRONTEND_PAIRS if param not in FRONTEND_EMPTY_DEFAULT
    }
    assert params == {
        "DomainName": "saferidelive.co.ke", "HostedZoneId": "Z0459820URO1AH08IO8T",
        "ApiOrigin": "https://api.saferidelive.co.ke", "CspMode": "report-only", "MonthlyBudgetUsd": "60",
    }
    assert "Parameter override" not in log


def test_one_frontend_export_overrides_only_its_parameter():
    baseline, _, _ = _build(FRONTEND_TEMPLATE_PATH, "FRONTEND_PARAMS")
    params, log, result = _build(FRONTEND_TEMPLATE_PATH, "FRONTEND_PARAMS", MONTHLY_BUDGET_USD="120")
    assert result.returncode == 0, result.stderr
    assert params == {**baseline, "MonthlyBudgetUsd": "120"}
    assert "Parameter override: MonthlyBudgetUsd=120 (from $MONTHLY_BUDGET_USD)" in log


def _deploy_frontend(tmp_path: Path, **env: str):
    fake = FakeCli(tmp_path)
    for name in ("/saferide/google-maps-browser-key", "/saferide/google-maps-map-id"):
        fake.put("af-south-1", name, f"value-of-{name.rsplit('/', 1)[1]}")
    fake.world["stack"] = {"status": "UPDATE_COMPLETE", "outputs": {
        "BucketName": "saferide-frontend-000000000000", "DistributionId": "E2FAKE", "SiteUrl": "https://example.org"}}
    result = fake.run("infra/scripts/deploy-frontend.sh", **env)
    deploys = [call for call in fake.calls("aws") if call[:2] == ["cloudformation", "deploy"]]
    params = None
    if deploys:
        [deploy] = deploys
        overrides = deploy[deploy.index("--parameter-overrides") + 1:deploy.index("--no-fail-on-empty-changeset")]
        params = dict(item.split("=", 1) for item in overrides)
    return fake, result, params


def test_deploy_frontend_passes_every_parameter_on_every_deploy(tmp_path):
    fake, result, params = _deploy_frontend(tmp_path)
    assert result.returncode == 0, result.stderr
    assert params == {
        "DomainName": "saferidelive.co.ke", "HostedZoneId": "Z0459820URO1AH08IO8T",
        "ApiOrigin": "https://api.saferidelive.co.ke", "CspMode": "report-only", "MonthlyBudgetUsd": "60",
    }
    # Assembled before the build, like the backend's.
    tools = [call[0] for call in fake.calls() if call[0] in ("npm", "aws") and call[1:3] != ["ssm", "get-parameter"]]
    assert tools[0] == "npm"


def test_deploy_frontend_takes_exports_and_derives_the_api_origin(tmp_path):
    _, result, params = _deploy_frontend(
        tmp_path, CSP_MODE="enforce", MONTHLY_BUDGET_USD="90", ALERT_EMAIL_PRIMARY="ops-one@example.org",
        VITE_API_BASE_URL="https://api.staging.example.org/", CF_CERT_ARN="arn:aws:acm:us-east-1:0:certificate/x",
    )
    assert result.returncode == 0, result.stderr
    assert params == {
        "CertificateArn": "arn:aws:acm:us-east-1:0:certificate/x",
        "DomainName": "saferidelive.co.ke", "HostedZoneId": "Z0459820URO1AH08IO8T",
        "ApiOrigin": "https://api.staging.example.org", "CspMode": "enforce", "MonthlyBudgetUsd": "90",
        "AlertEmailPrimary": "ops-one@example.org",
    }


def test_deploy_frontend_refuses_an_unknown_csp_mode_before_anything_else(tmp_path):
    fake, result, params = _deploy_frontend(tmp_path, CSP_MODE="enforced")
    assert result.returncode == 1
    assert "CSP_MODE must be report-only or enforce (got 'enforced')" in result.stderr
    assert params is None
    assert fake.calls("aws") == [] and fake.calls("npm") == []
