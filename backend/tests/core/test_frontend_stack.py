"""Frontend stack invariants (hardening plan U5, U16) — headers, probes, budget.

Text inspection of infra/frontend/template.yaml and the deploy scripts, like
test_backend_stack.py: the content-security policy is one text in both CspMode
branches, fits CloudFront's limit and names the API origin; the response-headers
policy is on the default behaviour with HSTS on subdomains and no preload; the
two Route 53 probes and their alarms (missing data breaching) report ALARM and OK
to the us-east-1 topic; the budget mails the alert addresses; and
deploy-frontend.sh builds with the production Maps values from the parameter
store and passes CspMode on every deploy. `cfn-lint` and `aws cloudformation
validate-template` check the shapes; these pin the decisions.
"""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
TEMPLATE = (REPO / "infra" / "frontend" / "template.yaml").read_text(encoding="utf-8")
SCRIPTS = REPO / "infra" / "scripts"
DEPLOY_FRONTEND = (SCRIPTS / "deploy-frontend.sh").read_text(encoding="utf-8")
DEPLOY_BACKEND = (SCRIPTS / "deploy-backend.sh").read_text(encoding="utf-8")
CONFIG = (SCRIPTS / "config.sh").read_text(encoding="utf-8")

RESOURCES_SECTION = TEMPLATE.split("\nResources:\n", 1)[1].split("\nOutputs:\n", 1)[0]
_parts = re.split(r"^  (\w+):\n", RESOURCES_SECTION, flags=re.M)
RESOURCES = dict(zip(_parts[1::2], _parts[2::2]))  # logical id -> block text
OUTPUTS = TEMPLATE.split("\nOutputs:\n", 1)[1]

# CloudFront's documented maximum for a content-security policy value.
CLOUDFRONT_CSP_LIMIT = 1783


def _type(logical_id: str) -> str:
    match = re.search(r"^    Type: (\S+)$", RESOURCES[logical_id], re.M)
    assert match, f"{logical_id} has no Type"
    return match.group(1)


def _of_type(resource_type: str) -> list[str]:
    return [lid for lid in RESOURCES if _type(lid) == resource_type]


def _properties(logical_id: str) -> dict[str, str]:
    """The scalar `Key: value` lines directly under Properties."""
    return dict(re.findall(r"^      (\w+): (.+?)\s*$", RESOURCES[logical_id], re.M))


def _parameter(name: str) -> str:
    entry = re.search(rf"^  {name}:\n((?:    .*\n)+)", TEMPLATE, re.M)
    assert entry, f"no parameter {name}"
    return entry.group(1)


def _folded(key: str, text: str) -> list[str]:
    """Each `key: !Sub >-` folded scalar in text, joined the way YAML folds it.

    Only lines at the block's first indentation belong to it; with no blank or
    more-indented lines inside, YAML folds each line break into one space.
    """
    pattern = rf"{re.escape(key)}: !Sub >-\n((?P<indent> +)\S.*\n(?:(?P=indent)\S.*\n)*)"
    return [
        " ".join(line.strip() for line in match.group(1).splitlines())
        for match in re.finditer(pattern, text)
    ]


# --- security headers ------------------------------------------------------------

POLICY = RESOURCES["SecurityHeadersPolicy"]
ENFORCING = _folded("ContentSecurityPolicy", POLICY)
REPORT_ONLY = _folded("Value", POLICY)


def test_the_policy_text_is_identical_in_both_modes():
    assert len(ENFORCING) == 1 and len(REPORT_ONLY) == 1
    assert ENFORCING[0] == REPORT_ONLY[0]


def test_the_policy_fits_cloudfronts_limit_with_the_longest_allowed_api_origin():
    max_origin = int(re.search(r"^    MaxLength: (\d+)$", _parameter("ApiOrigin"), re.M).group(1))
    longest = ENFORCING[0].replace("${ApiOrigin}", "x" * max_origin)
    assert len(longest) < CLOUDFRONT_CSP_LIMIT, len(longest)


def test_the_policy_baseline():
    directives = dict(
        (part.split(" ", 1)[0], part.split(" ", 1)[1] if " " in part else "")
        for part in ENFORCING[0].split("; ")
    )
    assert directives["default-src"] == "'self'"
    assert directives["connect-src"] == "'self' ${ApiOrigin} https://maps.googleapis.com"
    assert directives["script-src"] == "'self' https://maps.googleapis.com"  # no inline, no blob: yet
    assert directives["style-src"] == "'self' 'unsafe-inline' https://fonts.googleapis.com"
    assert directives["font-src"] == "'self' https://fonts.gstatic.com data:"
    assert directives["frame-ancestors"] == "'none'"
    assert directives["object-src"] == "'none'"
    assert directives["base-uri"] == "'self'"
    assert len(directives) == ENFORCING[0].count("; ") + 1  # no directive given twice


def test_cspmode_picks_the_header_and_defaults_to_report_only():
    param = _parameter("CspMode")
    assert re.search(r"^    Default: report-only$", param, re.M)
    assert re.search(r"^    AllowedValues: \[report-only, enforce\]$", param, re.M)
    assert re.search(r"^  CspEnforce: !Equals \[!Ref CspMode, enforce\]$", TEMPLATE, re.M)
    # enforce: the CSP security header, otherwise nothing there ...
    assert re.search(
        r"ContentSecurityPolicy: !If\n +- CspEnforce\n +- ContentSecurityPolicy: !Sub >-\n(?: {16}.*\n)+"
        r" {14}Override: true\n {12}- !Ref AWS::NoValue\n",
        POLICY,
    )
    # ... report-only: the custom report-only header, otherwise none.
    assert re.search(
        r"- !If\n +- CspEnforce\n +- !Ref AWS::NoValue\n +- Header: Content-Security-Policy-Report-Only\n",
        POLICY,
    )


def test_hsts_nosniff_frame_referrer_and_permissions_policy():
    hsts = re.search(r"^          StrictTransportSecurity:\n((?:            .*\n)+)", POLICY, re.M)
    assert hsts
    assert dict(re.findall(r"^            (\w+): (.+?)\s*$", hsts.group(1), re.M)) == {
        "AccessControlMaxAgeSec": "31536000",
        "IncludeSubdomains": "true",
        "Preload": "false",
        "Override": "true",
    }
    assert re.search(r"^          ContentTypeOptions:\n            Override: true$", POLICY, re.M)
    assert re.search(r"^            FrameOption: DENY$", POLICY, re.M)
    assert re.search(r"^            ReferrerPolicy: strict-origin-when-cross-origin$", POLICY, re.M)
    assert re.search(
        r'- Header: Permissions-Policy\n +Value: "geolocation=\(self\), camera=\(\), microphone=\(\)"\n',
        POLICY,
    )


def test_the_policy_is_on_the_default_cache_behaviour():
    assert _type("SecurityHeadersPolicy") == "AWS::CloudFront::ResponseHeadersPolicy"
    behaviour = re.search(r"^        DefaultCacheBehavior:\n((?:          .*\n)+)", RESOURCES["Distribution"], re.M)
    assert behaviour
    assert re.search(r"^          ResponseHeadersPolicyId: !Ref SecurityHeadersPolicy$", behaviour.group(1), re.M)
    assert "Value: !Ref SecurityHeadersPolicy" in OUTPUTS


# --- probes and alarms -----------------------------------------------------------

HEALTH_CHECKS = {"ApiHealthCheck": "/api/health", "SiteHealthCheck": "/"}
ALARMS = {"ApiProbeFailing", "SiteProbeFailing", "SiteServerErrors", "SiteCertificateExpiry"}
PROBE_ALARMS = {"ApiProbeFailing": "ApiHealthCheck", "SiteProbeFailing": "SiteHealthCheck"}


@pytest.mark.parametrize("check,path", sorted(HEALTH_CHECKS.items()))
def test_each_health_check_probes_https_from_three_regions_every_30_seconds(check, path):
    block = RESOURCES[check]
    assert _type(check) == "AWS::Route53::HealthCheck"
    config = dict(re.findall(r"^        (\w+): (.+?)\s*$", block, re.M))
    assert config["Type"] == "HTTPS"
    assert config["ResourcePath"] == path
    assert config["Port"] == "443"
    assert config["EnableSNI"] == "true"
    assert config["RequestInterval"] == "30"
    assert config["FailureThreshold"] == "3"
    regions = re.search(r"^        Regions:\n((?:          - .*\n)+)", block, re.M)
    assert regions and re.findall(r"- (\S+)", regions.group(1)) == ["us-east-1", "eu-west-1", "ap-southeast-1"]
    assert re.search(r"^      HealthCheckTags:\n        - Key: Name\n", block, re.M)
    assert f"Value: !Ref {check}" in OUTPUTS


def test_the_api_probe_targets_the_api_origins_host():
    config = dict(re.findall(r"^        (\w+): (.+?)\s*$", RESOURCES["ApiHealthCheck"], re.M))
    assert config["FullyQualifiedDomainName"] == '!Select [2, !Split ["/", !Ref ApiOrigin]]'
    origin = _parameter("ApiOrigin")
    assert re.search(r"^    Default: https://api\.saferidelive\.co\.ke$", origin, re.M)
    assert re.search(r"^    AllowedPattern: '\^https://\[a-z0-9\.-\]\+\$'$", origin, re.M)


def test_the_stack_carries_exactly_the_us_east_1_alarm_set():
    assert set(_of_type("AWS::CloudWatch::Alarm")) == ALARMS
    assert re.search(r"^    Condition: HasCert$", RESOURCES["SiteCertificateExpiry"], re.M)


@pytest.mark.parametrize("alarm", sorted(ALARMS))
def test_each_alarm_reports_alarm_and_ok_to_the_us_east_1_topic(alarm):
    block = RESOURCES[alarm]
    for actions in ("AlarmActions", "OKActions"):
        assert re.search(rf"^      {actions}:\n        - !Ref OpsTopicUsEast1\n", block, re.M), (alarm, actions)
    expected = "breaching" if alarm in PROBE_ALARMS else "notBreaching"
    assert _properties(alarm)["TreatMissingData"] == expected
    # What it means, then the first response, in one sentence.
    description = re.search(r"^      AlarmDescription: >-\n((?:        .*\n)+)", block, re.M)
    assert description, f"{alarm} has no folded AlarmDescription"
    text = " ".join(line.strip() for line in description.group(1).splitlines())
    assert "; first, " in text and text.endswith(".") and text.count(". ") == 0, text


@pytest.mark.parametrize("alarm,check", sorted(PROBE_ALARMS.items()))
def test_probe_alarms_fire_after_three_unhealthy_minutes(alarm, check):
    props = _properties(alarm)
    assert props["Namespace"] == "AWS/Route53" and props["MetricName"] == "HealthCheckStatus"
    assert props["Statistic"] == "Minimum"
    assert (props["Period"], props["EvaluationPeriods"], props["DatapointsToAlarm"]) == ("60", "3", "3")
    assert (props["Threshold"], props["ComparisonOperator"]) == ("1", "LessThanThreshold")
    assert re.search(
        rf"^        - Name: HealthCheckId\n          Value: !Ref {check}$", RESOURCES[alarm], re.M
    )


def test_the_cloudfront_error_rate_alarm():
    props = _properties("SiteServerErrors")
    assert (props["Namespace"], props["MetricName"], props["Statistic"]) == ("AWS/CloudFront", "5xxErrorRate", "Average")
    assert (props["Threshold"], props["ComparisonOperator"]) == ("5", "GreaterThanThreshold")
    assert re.search(r"- Name: DistributionId\n +Value: !Ref Distribution\n +- Name: Region\n +Value: Global\n",
                     RESOURCES["SiteServerErrors"])


def test_the_us_east_1_topic_and_its_conditional_subscriptions():
    assert _properties("OpsTopicUsEast1") == {"TopicName": "saferide-ops-us-east-1"}
    policy = RESOURCES["OpsTopicUsEast1Policy"]
    assert "Service: cloudwatch.amazonaws.com" in policy and "aws:SourceAccount: !Ref AWS::AccountId" in policy
    assert "ses.amazonaws.com" not in policy
    for suffix, param in (("Primary", "AlertEmailPrimary"), ("Secondary", "AlertEmailSecondary")):
        entry = _parameter(param)
        assert re.search(r'^    Default: ""$', entry, re.M)
        assert re.search(r"^    AllowedPattern: '\^\$\|\^\[\^@\\s\]\+@\[\^@\\s\]\+\\\.\[\^@\\s\]\+\$'$", entry, re.M)
        assert re.search(rf'^  HasAlert{suffix}: !Not \[!Equals \[!Ref {param}, ""\]\]$', TEMPLATE, re.M)
        subscription = f"OpsUsEast1Subscription{suffix}"
        assert re.search(rf"^    Condition: HasAlert{suffix}$", RESOURCES[subscription], re.M)
        assert _properties(subscription) == {
            "TopicArn": "!Ref OpsTopicUsEast1", "Protocol": "email", "Endpoint": f"!Ref {param}",
        }
    assert "Value: !Ref OpsTopicUsEast1" in OUTPUTS


def test_the_budget_mails_the_alert_addresses_at_80_actual_and_100_forecast():
    block = RESOURCES["MonthlyCostBudget"]
    assert _type("MonthlyCostBudget") == "AWS::Budgets::Budget"
    assert re.search(r"^    Condition: HasAnyAlert$", block, re.M)
    assert re.search(r"^  HasAnyAlert: !Or \[!Condition HasAlertPrimary, !Condition HasAlertSecondary\]$", TEMPLATE, re.M)
    assert re.search(r"^    Default: 60$", _parameter("MonthlyBudgetUsd"), re.M)
    assert "Amount: !Ref MonthlyBudgetUsd" in block and "TimeUnit: MONTHLY" in block and "BudgetType: COST" in block
    notifications = re.findall(r"NotificationType: (\w+)\n +ComparisonOperator: GREATER_THAN\n +Threshold: (\d+)\n", block)
    assert notifications == [("ACTUAL", "80"), ("FORECASTED", "100")]
    assert block.count("- HasAlertPrimary\n") == 2 and block.count("- HasAlertSecondary\n") == 2


# --- deploy scripts ----------------------------------------------------------------


def test_ssm_required_is_shared_from_config_sh():
    assert re.search(r"^ssm_required\(\) \{", CONFIG, re.M)
    assert re.search(r"^export -f ssm_required$", CONFIG, re.M)
    assert 'region="${2:-$BACKEND_REGION}"' in CONFIG
    assert "ssm_required()" not in DEPLOY_BACKEND and "ssm_required()" not in DEPLOY_FRONTEND
    assert 'DB_PASSWORD="$(ssm_required /saferide/db-password)"' in DEPLOY_BACKEND


def test_the_frontend_build_takes_the_maps_values_from_the_parameter_store():
    reads = {
        "MAPS_BROWSER_KEY": "/saferide/google-maps-browser-key",
        "MAPS_MAP_ID": "/saferide/google-maps-map-id",
    }
    clean = DEPLOY_FRONTEND.index("\nrequire_clean_checkout\n")
    build = DEPLOY_FRONTEND.index("npm run build")
    for var, name in reads.items():
        line = f'{var}="$(ssm_required {name})"'
        assert line in DEPLOY_FRONTEND
        assert clean < DEPLOY_FRONTEND.index(line) < build  # read before anything is built
    build_command = DEPLOY_FRONTEND[DEPLOY_FRONTEND.rindex("\nVITE_API_BASE_URL=", 0, build):build]
    assert 'VITE_GOOGLE_MAPS_API_KEY="$MAPS_BROWSER_KEY"' in build_command
    assert 'VITE_GOOGLE_MAPS_MAP_ID="$MAPS_MAP_ID"' in build_command
    # Never printed.
    for line in DEPLOY_FRONTEND.splitlines():
        if re.search(r"\b(echo|printf)\b", line):
            assert "MAPS_BROWSER_KEY" not in line and "MAPS_MAP_ID" not in line, line


def test_the_frontend_deploy_passes_cspmode_every_time_and_alert_addresses_when_set():
    assert 'CSP_MODE="${CSP_MODE:-report-only}"' in DEPLOY_FRONTEND
    params = re.search(r"^FE_PARAMS=\(\n((?:  .*\n)+)\)$", DEPLOY_FRONTEND, re.M)
    assert params, "FE_PARAMS is not a multi-line array"
    assert '"CspMode=${CSP_MODE}"' in params.group(1)
    assert '"ApiOrigin=${API_ORIGIN}"' in params.group(1)
    for param, env in (("AlertEmailPrimary", "ALERT_EMAIL_PRIMARY"), ("AlertEmailSecondary", "ALERT_EMAIL_SECONDARY")):
        assert re.search(
            rf'^if \[ -n "\$\{{{env}:-\}}" \]; then\n  FE_PARAMS\+=\("{param}=\$\{{{env}\}}"\)$',
            DEPLOY_FRONTEND,
            re.M,
        ), param
