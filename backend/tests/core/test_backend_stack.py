"""Backend stack invariants (hardening plan U4) — the template watches itself.

Text inspection of infra/backend/template.yaml, like test_gps_settings.py: one
retained 90-day log group per function with JSON logging pointed at it; every
alarm reports ALARM and OK to the ops topic, and Release 1a carries no
heartbeat, metric-filter or Scheduler alarm (they ship in 1b with their
metrics); the alert addresses reach the deploy script; the database property
set; the mail DNS records and the retained trail bucket. `sam validate --lint`
checks the shapes; these pin the decisions.
"""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
TEMPLATE = (REPO / "infra" / "backend" / "template.yaml").read_text(encoding="utf-8")
DEPLOY_SCRIPT = (REPO / "infra" / "scripts" / "deploy-backend.sh").read_text(encoding="utf-8")

RESOURCES_SECTION = TEMPLATE.split("\nResources:\n", 1)[1].split("\nOutputs:\n", 1)[0]
_parts = re.split(r"^  (\w+):\n", RESOURCES_SECTION, flags=re.M)
RESOURCES = dict(zip(_parts[1::2], _parts[2::2]))  # logical id -> block text


def _type(logical_id: str) -> str:
    match = re.search(r"^    Type: (\S+)$", RESOURCES[logical_id], re.M)
    assert match, f"{logical_id} has no Type"
    return match.group(1)


def _of_type(resource_type: str) -> list[str]:
    return [lid for lid in RESOURCES if _type(lid) == resource_type]


def _properties(logical_id: str) -> dict[str, str]:
    """The scalar `Key: value` lines directly under Properties."""
    return dict(re.findall(r"^      (\w+): (.+?)\s*$", RESOURCES[logical_id], re.M))


def _retained(logical_id: str) -> bool:
    block = RESOURCES[logical_id]
    return (
        re.search(r"^    DeletionPolicy: Retain$", block, re.M) is not None
        and re.search(r"^    UpdateReplacePolicy: Retain$", block, re.M) is not None
    )


# --- logging -------------------------------------------------------------------

LOG_GROUPS = {
    "ApiFunction": ("ApiLogGroup", "/saferide/api"),
    "MigrateFunction": ("MigrateLogGroup", "/saferide/migrate"),
    "VerifyFunction": ("VerifyLogGroup", "/saferide/verify"),
}


@pytest.mark.parametrize("function,group,name", [(f, *v) for f, v in LOG_GROUPS.items()])
def test_each_function_logs_json_to_its_own_retained_group(function, group, name):
    assert _type(group) == "AWS::Logs::LogGroup"
    assert _properties(group) == {"LogGroupName": name, "RetentionInDays": "90"}
    assert _retained(group), group
    config = re.search(r"^      LoggingConfig:\n((?:        .*\n)+)", RESOURCES[function], re.M)
    assert config, f"{function} has no LoggingConfig"
    assert dict(re.findall(r"^        (\w+): (.+?)\s*$", config.group(1), re.M)) == {
        "LogFormat": "JSON",
        "ApplicationLogLevel": "INFO",
        "SystemLogLevel": "WARN",
        "LogGroup": f"!Ref {group}",
    }


def test_there_is_one_log_group_per_function():
    functions = _of_type("AWS::Serverless::Function")
    assert sorted(functions) == sorted(LOG_GROUPS)
    assert sorted(_of_type("AWS::Logs::LogGroup")) == sorted(g for g, _ in LOG_GROUPS.values())


# --- alarms --------------------------------------------------------------------

ALARMS = {
    "ApiServerErrors", "ApiClientErrorsBurst", "ApiLatencyP95", "ApiFunctionErrors",
    "ApiFunctionThrottles", "MigrateFunctionErrors", "AccountConcurrency",
    "DbFreeStorageLow", "DbFreeableMemoryLow", "DbCpuHigh", "DbCpuCreditsLow",
    "ApiCertificateExpiry",
}


def test_release_1a_carries_exactly_the_passive_alarm_set():
    assert set(_of_type("AWS::CloudWatch::Alarm")) == ALARMS
    # The heartbeat (missing data breaching), the metric-filter alarms and the
    # Scheduler arrive in 1b with the metrics that feed them.
    assert not [lid for lid in RESOURCES if "heartbeat" in lid.lower()]
    assert "TreatMissingData: breaching" not in RESOURCES_SECTION
    assert not _of_type("AWS::Logs::MetricFilter")
    assert not [lid for lid in RESOURCES if _type(lid).startswith("AWS::Scheduler::")]


@pytest.mark.parametrize("alarm", sorted(ALARMS))
def test_each_alarm_reports_alarm_and_ok_to_the_ops_topic(alarm):
    block = RESOURCES[alarm]
    for actions in ("AlarmActions", "OKActions"):
        assert re.search(rf"^      {actions}:\n        - !Ref OpsTopic\n", block, re.M), (alarm, actions)
    assert _properties(alarm)["TreatMissingData"] == "notBreaching"
    # What it means, then the first response, in one sentence.
    description = re.search(r"^      AlarmDescription: >-\n((?:        .*\n)+)", block, re.M)
    assert description, f"{alarm} has no folded AlarmDescription"
    text = " ".join(line.strip() for line in description.group(1).splitlines())
    assert "; first, " in text and text.endswith(".") and text.count(". ") == 0, text


def test_the_certificate_alarm_exists_only_with_the_custom_domain():
    assert re.search(r"^    Condition: HasApiCert$", RESOURCES["ApiCertificateExpiry"], re.M)


def test_the_concurrency_alarm_threshold_is_a_parameter_at_eight():
    assert _properties("AccountConcurrency")["Threshold"] == "!Ref ConcurrencyAlarmThreshold"
    param = re.search(r"^  ConcurrencyAlarmThreshold:\n((?:    .*\n)+)", TEMPLATE, re.M)
    assert param and re.search(r"^    Default: 8$", param.group(1), re.M)


@pytest.mark.parametrize(
    "param,env,condition,subscription",
    [
        ("AlertEmailPrimary", "ALERT_EMAIL_PRIMARY", "HasAlertPrimary", "OpsSubscriptionPrimary"),
        ("AlertEmailSecondary", "ALERT_EMAIL_SECONDARY", "HasAlertSecondary", "OpsSubscriptionSecondary"),
    ],
)
def test_each_alert_address_is_an_optional_parameter_with_a_conditional_subscription(
    param, env, condition, subscription
):
    entry = re.search(rf"^  {param}:\n((?:    .*\n)+)", TEMPLATE, re.M)
    assert entry and re.search(r'^    Default: ""$', entry.group(1), re.M)
    assert re.search(rf'^  {condition}: !Not \[!Equals \[!Ref {param}, ""\]\]$', TEMPLATE, re.M)
    block = RESOURCES[subscription]
    assert re.search(rf"^    Condition: {condition}$", block, re.M)
    assert _properties(subscription) == {
        "TopicArn": "!Ref OpsTopic", "Protocol": "email", "Endpoint": f"!Ref {param}",
    }
    assert f"{param}:{env}" in DEPLOY_SCRIPT
    assert re.search(r'^for pair in .*"\$\{ALERT_PARAMS\[@\]\}"', DEPLOY_SCRIPT, re.M)


# --- database ------------------------------------------------------------------


def test_database_hardening_properties():
    block = RESOURCES["Database"]
    assert re.search(r"^    DeletionPolicy: Snapshot$", block, re.M)
    assert re.search(r"^    UpdateReplacePolicy: Snapshot$", block, re.M)
    props = _properties("Database")
    assert props["DeletionProtection"] == "true"
    assert props["BackupRetentionPeriod"] == "35"
    assert props["MaxAllocatedStorage"] == "100"
    assert props["PreferredBackupWindow"] == '"23:00-23:30"'
    assert props["PreferredMaintenanceWindow"] == '"sat:23:30-sun:00:30"'
    assert re.search(r"^      EnableCloudwatchLogsExports:\n        - postgresql\n", block, re.M)
    assert "EnablePerformanceInsights" not in block  # not offered on db.t4g.micro (D18)


# --- mail DNS and the audit trail ------------------------------------------------


def test_the_mail_records_are_dkim_mail_from_and_dmarc_without_rua():
    records = {lid: _properties(lid) for lid in _of_type("AWS::Route53::RecordSet")}
    dkim = [p for p in records.values() if p.get("Type") == "CNAME" and "DkimDNSToken" in p["Name"]]
    assert len(dkim) == 3
    dmarc = RESOURCES["DmarcRecord"]
    assert '"v=DMARC1; p=none"' in dmarc and "rua" not in dmarc  # D11
    assert '"v=spf1 include:amazonses.com ~all"' in RESOURCES["SesMailFromSpf"]
    assert "feedback-smtp.${AWS::Region}.amazonses.com" in RESOURCES["SesMailFromMx"]
    assert _retained("SesIdentity")


def test_the_trail_bucket_is_retained_private_and_expires_after_90_days():
    assert _retained("TrailBucket")
    block = RESOURCES["TrailBucket"]
    assert "BucketName" not in block  # CloudFormation-named (D10)
    for flag in ("BlockPublicAcls", "BlockPublicPolicy", "IgnorePublicAcls", "RestrictPublicBuckets"):
        assert re.search(rf"^        {flag}: true$", block, re.M), flag
    assert "ExpirationInDays: 90" in block
    trail = _properties("AuditTrail")
    assert trail["IsMultiRegionTrail"] == "true"
    assert trail["EnableLogFileValidation"] == "true"
