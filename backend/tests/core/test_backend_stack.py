"""Backend stack invariants (hardening plan U4, U12) — the template watches itself.

Text inspection of infra/backend/template.yaml through template_text.py: one
retained 90-day log group per function with JSON logging (application and
platform INFO) pointed at it; every alarm's metric, statistic, window and
threshold, reporting ALARM and OK to the ops topic — Release 1b adds the
retention heartbeat (the one alarm whose missing data breaches), the
Scheduler's target errors and the metric-filter alarms, and moves the 4xx
burst onto a log count without 404s; the nightly schedule, its role and the
migrate function's timeout and async retries; the topic policy; the alert
addresses reach the deploy script; the database property set (no log export,
D21; maintenance clear of the backup window, D22); the mail DNS records and
the retained trail. `sam validate --lint` checks the shapes; these pin the
decisions. The metric filters' patterns are matched against real log lines
in test_metric_filters.py.
"""

import json

import re
from typing import NamedTuple

import pytest

from tests.core.template_text import REPO, Template, load

T = load("backend")
# The parity maps deploy-backend.sh sources (test_deploy_params.py runs them).
PARITY_SCRIPT = (REPO / "infra" / "scripts" / "parity-params.sh").read_text(encoding="utf-8")


def _retained(logical_id: str, deletion: str = "Retain") -> bool:
    """Kept on stack delete and replacement. Fixed-name resources use
    RetainExceptOnCreate so a rolled-back create cleans up after itself."""
    block = T.block(logical_id)
    return (
        re.search(rf"^    DeletionPolicy: {deletion}$", block, re.M) is not None
        and re.search(r"^    UpdateReplacePolicy: Retain$", block, re.M) is not None
    )


def _statements(logical_id: str) -> dict[str, str]:
    """Sid -> statement text of a policy resource's PolicyDocument, in order."""
    parts = re.split(r"^          - Sid: (\w+)\n", T.block(logical_id), flags=re.M)
    return dict(zip(parts[1::2], parts[2::2]))


def test_the_template_reader_refuses_a_split_that_misses_a_resource():
    """A logical id the split cannot see folds into the resource above it, so a
    negative assertion on either block would pass vacuously; the reader stops."""
    with pytest.raises(AssertionError, match="First holds 2 Type lines"):
        Template("Resources:\n  First:\n    Type: AWS::SNS::Topic\n  Second: # x\n    Type: AWS::SNS::Topic\n")
    assert list(Template("Resources:\n  Only:\n    Type: AWS::SNS::Topic\n").resources) == ["Only"]


# --- logging -------------------------------------------------------------------

LOG_GROUPS = {
    "ApiFunction": ("ApiLogGroup", "/saferide/api"),
    "MigrateFunction": ("MigrateLogGroup", "/saferide/migrate"),
    "VerifyFunction": ("VerifyLogGroup", "/saferide/verify"),
}


@pytest.mark.parametrize("function,group,name", [(f, *v) for f, v in LOG_GROUPS.items()])
def test_each_function_logs_json_to_its_own_retained_group(function, group, name):
    assert T.type_of(group) == "AWS::Logs::LogGroup"
    assert T.properties(group) == {"LogGroupName": name, "RetentionInDays": "90"}
    assert _retained(group, "RetainExceptOnCreate"), group
    config = re.search(r"^      LoggingConfig:\n((?:        .*\n)+)", T.block(function), re.M)
    assert config, f"{function} has no LoggingConfig"
    assert dict(re.findall(r"^        (\w+): (.+?)\s*$", config.group(1), re.M)) == {
        "LogFormat": "JSON",
        "ApplicationLogLevel": "INFO",
        # The platform's START, END and REPORT lines (duration, memory, init,
        # timeouts) are INFO; WARN would drop what the alarms point at.
        "SystemLogLevel": "INFO",
        "LogGroup": f"!Ref {group}",
    }


def test_there_is_one_log_group_per_function():
    functions = T.of_type("AWS::Serverless::Function")
    assert sorted(functions) == sorted(LOG_GROUPS)
    assert sorted(T.of_type("AWS::Logs::LogGroup")) == sorted(g for g, _ in LOG_GROUPS.values())


# --- alarms --------------------------------------------------------------------


class Alarm(NamedTuple):
    namespace: str
    metric: str
    dimensions: tuple[tuple[str, str], ...]
    statistic: str  # a "p.." value is an ExtendedStatistic percentile
    period: int
    evaluation_periods: int
    datapoints_to_alarm: int
    threshold: str
    comparison: str
    # Quiet days (December) stay OK; a dead stack is the external probes' job.
    missing_data: str = "notBreaching"


API = (("ApiId", "!Ref HttpApi"),)
DB = (("DBInstanceIdentifier", "!Ref Database"),)
GTE, GT, LT = "GreaterThanOrEqualToThreshold", "GreaterThanThreshold", "LessThanThreshold"
# Release 1b's own metrics: the retention run's EMF line and the API log
# metric filters. No dimensions.
APP = "SafeRide"

ALARMS = {
    "ApiServerErrors": Alarm("AWS/ApiGateway", "5xx", API, "Sum", 300, 1, 1, "1", GTE),
    # The user's decision (2026-11-23): the app's non-404 4xx from its access
    # lines, same threshold; the logical id is kept so the alarm updates in
    # place.
    "ApiClientErrorsBurst": Alarm(APP, "ApiClientErrorsNon404", (), "Sum", 300, 1, 1, "50", GTE),
    "ApiLatencyP95": Alarm("AWS/ApiGateway", "Latency", API, "p95", 300, 5, 3, "3000", GT),
    "ApiFunctionErrors": Alarm(
        "AWS/Lambda", "Errors", (("FunctionName", "!Ref ApiFunction"),), "Sum", 300, 1, 1, "1", GTE
    ),
    "ApiFunctionThrottles": Alarm(
        "AWS/Lambda", "Throttles", (("FunctionName", "!Ref ApiFunction"),), "Sum", 60, 1, 1, "1", GTE
    ),
    "MigrateFunctionErrors": Alarm(
        "AWS/Lambda", "Errors", (("FunctionName", "!Ref MigrateFunction"),), "Sum", 300, 1, 1, "1", GTE
    ),
    # No dimensions: the account-level series for this region.
    "AccountConcurrency": Alarm(
        "AWS/Lambda", "ConcurrentExecutions", (), "Maximum", 60, 1, 1, "!Ref ConcurrencyAlarmThreshold", GTE
    ),
    "DbFreeStorageLow": Alarm("AWS/RDS", "FreeStorageSpace", DB, "Minimum", 300, 1, 1, str(2 * 1024**3), LT),
    "DbFreeableMemoryLow": Alarm("AWS/RDS", "FreeableMemory", DB, "Minimum", 300, 3, 3, str(100 * 1024**2), LT),
    "DbCpuHigh": Alarm("AWS/RDS", "CPUUtilization", DB, "Average", 300, 3, 3, "80", GT),
    "DbCpuCreditsLow": Alarm("AWS/RDS", "CPUCreditBalance", DB, "Minimum", 300, 3, 3, "30", LT),
    "ApiCertificateExpiry": Alarm(
        "AWS/CertificateManager", "DaysToExpiry", (("CertificateArn", "!Ref ApiCertificateArn"),),
        "Minimum", 86400, 1, 1, "20", LT,
    ),
    # --- Release 1b (U12) ---
    # 26 hourly periods with no success, missing data breaching: silence is
    # the failure this alarm exists for.
    "RetentionHeartbeat": Alarm(
        APP, "RetentionRunSucceeded", (), "Sum", 3600, 26, 26, "1", LT, missing_data="breaching",
    ),
    "RetentionScheduleTargetErrors": Alarm(
        "AWS/Scheduler", "TargetErrorCount", (("ScheduleGroup", "!Ref OpsScheduleGroup"),),
        "Sum", 300, 1, 1, "1", GTE,
    ),
    "MailSendFailed": Alarm(APP, "MailSendFailed", (), "Sum", 300, 1, 1, "1", GTE),
    "PinFailureBurst": Alarm(APP, "PinFailures", (), "Sum", 300, 1, 1, "30", GTE),
    "PinLocked": Alarm(APP, "PinLocked", (), "Sum", 300, 1, 1, "1", GTE),
    "SchoolRequiredBurst": Alarm(APP, "SchoolRequired", (), "Sum", 300, 1, 1, "20", GTE),
}

# Metric filter -> (the metric it counts into, the alarm that watches it).
METRIC_FILTERS = {
    "ApiClientErrorsFilter": ("ApiClientErrorsNon404", "ApiClientErrorsBurst"),
    "MailSendFailedFilter": ("MailSendFailed", "MailSendFailed"),
    "PinFailureFilter": ("PinFailures", "PinFailureBurst"),
    "PinLockedFilter": ("PinLocked", "PinLocked"),
    "SchoolRequiredFilter": ("SchoolRequired", "SchoolRequiredBurst"),
}


def test_release_1b_carries_exactly_this_alarm_set():
    assert set(T.of_type("AWS::CloudWatch::Alarm")) == set(ALARMS)
    assert set(T.of_type("AWS::Logs::MetricFilter")) == set(METRIC_FILTERS)
    assert set(T.of_type("AWS::Scheduler::Schedule")) == {"RetentionSchedule"}
    assert set(T.of_type("AWS::Scheduler::ScheduleGroup")) == {"OpsScheduleGroup"}
    # The migrate function's errors alarm (1a, D9) is not duplicated.
    migrate_alarms = [
        lid for lid in ALARMS if "!Ref MigrateFunction" in T.block(lid)
    ]
    assert migrate_alarms == ["MigrateFunctionErrors"]


def test_only_the_heartbeat_treats_missing_data_as_breaching():
    breaching = [
        lid for lid in T.of_type("AWS::CloudWatch::Alarm")
        if T.properties(lid)["TreatMissingData"] == "breaching"
    ]
    assert breaching == ["RetentionHeartbeat"]


def test_the_heartbeat_window_is_26_hours_within_cloudwatchs_limits():
    props = T.properties("RetentionHeartbeat")
    period, periods = int(props["Period"]), int(props["EvaluationPeriods"])
    assert period * periods == 26 * 3600
    # PutMetricAlarm: Period x EvaluationPeriods at most seven days, and at
    # most one day for a period under an hour.
    assert period >= 3600 and period * periods <= 604_800
    description = " ".join(
        line.strip() for line in re.search(
            r"^      AlarmDescription: >-\n((?:        .*\n)+)", T.block("RetentionHeartbeat"), re.M
        ).group(1).splitlines()
    )
    # The latency and the post-deploy ALARM are stated where the mail lands.
    assert "about 26 hours after the last good run" in description
    assert "the morning after the first missed night" in description
    assert "in ALARM from the deploy that creates it until the first run" in description
    assert "zero Lambda errors is not proof that it ran" in description
    assert len(description) <= 1024


@pytest.mark.parametrize("metric_filter", sorted(METRIC_FILTERS))
def test_each_metric_filter_counts_the_api_log_into_the_metric_its_alarm_watches(metric_filter):
    metric, alarm = METRIC_FILTERS[metric_filter]
    props = T.properties(metric_filter)
    assert props["LogGroupName"] == "!Ref ApiLogGroup"
    transformation = re.search(
        r"^      MetricTransformations:\n        - (\w+: .+\n(?:          \w+: .+\n)*)",
        T.block(metric_filter), re.M,
    )
    assert transformation, metric_filter
    # One transformation, its keys as written.
    assert T.block(metric_filter).count("\n        - ") == 1
    fields = dict(re.findall(r"(\w+): (.+?)\s*$", transformation.group(1), re.M))
    assert fields == {
        "MetricNamespace": APP, "MetricName": metric, "MetricValue": '"1"', "Unit": "Count",
    }
    # No DefaultValue: a quiet five minutes is missing data, not a zero.
    assert "DefaultValue" not in T.block(metric_filter)
    assert (ALARMS[alarm].namespace, ALARMS[alarm].metric) == (APP, metric)


def test_regex_filter_patterns_stay_inside_the_log_groups_quota():
    # CloudWatch Logs: at most 5 metric-filter patterns with regex per log
    # group, at most 2 regexes per pattern.
    patterns = [T.properties(lid)["FilterPattern"] for lid in METRIC_FILTERS]
    with_regex = [p for p in patterns if "%" in p]
    assert len(with_regex) <= 5
    assert all(p.count("%") // 2 <= 2 for p in with_regex)


# --- the nightly retention schedule (U12) ---------------------------------------


def test_the_migrate_function_runs_ten_minutes_and_is_never_retried_asynchronously():
    block = T.block("MigrateFunction")
    assert T.properties("MigrateFunction")["Timeout"] == "600"
    config = re.search(r"^      EventInvokeConfig:\n((?:        .*\n)+)", block, re.M)
    assert config, "MigrateFunction has no EventInvokeConfig"
    assert dict(re.findall(r"^        (\w+): (.+?)\s*$", config.group(1), re.M)) == {
        "MaximumRetryAttempts": "0", "MaximumEventAgeInSeconds": "3600",
    }
    # The deploy's synchronous migrate invoke waits without a client timeout.
    deploy = (REPO / "infra" / "scripts" / "deploy-backend.sh").read_text(encoding="utf-8")
    invoke = re.search(r'aws lambda invoke [^\n]*--function-name "\$MIGRATE_FN" \\\n[^\n]*', deploy)
    assert invoke and "--cli-read-timeout 0" in invoke.group(0)


def test_the_schedule_invokes_retention_at_half_past_one_nairobi_time():
    props = T.properties("RetentionSchedule")
    assert props["ScheduleExpression"] == "cron(30 1 * * ? *)"
    assert props["ScheduleExpressionTimezone"] == "Africa/Nairobi"
    assert props["GroupName"] == "!Ref OpsScheduleGroup"
    assert props["State"] == "ENABLED"
    block = T.block("RetentionSchedule")
    assert re.search(r'^      FlexibleTimeWindow:\n        Mode: "OFF"\n', block, re.M)
    target = re.search(r"^      Target:\n((?:        .*\n)+)", block, re.M).group(1)
    fields = dict(re.findall(r"^        (\w+): (.+?)\s*$", target, re.M))
    assert fields["Arn"] == "!GetAtt MigrateFunction.Arn"
    assert fields["RoleArn"] == "!GetAtt RetentionSchedulerRole.Arn"
    assert json.loads(fields["Input"].strip("'")) == {"action": "retention"}
    retry = dict(re.findall(r"^          (\w+): (.+?)\s*$", target, re.M))
    assert retry == {"MaximumRetryAttempts": "2", "MaximumEventAgeInSeconds": "3600"}


def test_the_scheduler_role_may_only_invoke_the_migrate_function():
    block = T.block("RetentionSchedulerRole")
    assert T.type_of("RetentionSchedulerRole") == "AWS::IAM::Role"
    trust = re.search(r"^      AssumeRolePolicyDocument:\n((?:        .*\n)+)", block, re.M).group(1)
    assert re.findall(r"Service: (\S+)", trust) == ["scheduler.amazonaws.com"]
    assert "aws:SourceAccount: !Ref AWS::AccountId" in trust
    actions = re.findall(r"^                Action: (.+?)\s*$", block, re.M)
    resources = re.findall(r"^                Resource: (.+?)\s*$", block, re.M)
    assert actions == ["lambda:InvokeFunction"]
    assert resources == ["!GetAtt MigrateFunction.Arn"]
    # No fixed RoleName: the deploy keeps CAPABILITY_IAM, not NAMED_IAM.
    assert "RoleName" not in block


@pytest.mark.parametrize("alarm", sorted(ALARMS))
def test_each_alarm_watches_its_metric_window_and_threshold(alarm):
    expected = ALARMS[alarm]
    block = T.block(alarm)
    props = T.properties(alarm)
    assert (props["Namespace"], props["MetricName"]) == (expected.namespace, expected.metric)
    dimensions = re.findall(r"^        - Name: (\w+)\n          Value: (.+?)\s*$", block, re.M)
    assert tuple(dimensions) == expected.dimensions
    assert ("\n      Dimensions:\n" in block) == bool(expected.dimensions)
    if expected.statistic.startswith("p"):
        assert props["ExtendedStatistic"] == expected.statistic and "Statistic" not in props
        assert props["EvaluateLowSampleCountPercentile"] == "ignore"
    else:
        assert props["Statistic"] == expected.statistic and "ExtendedStatistic" not in props
    assert (int(props["Period"]), int(props["EvaluationPeriods"]), int(props["DatapointsToAlarm"])) == (
        expected.period, expected.evaluation_periods, expected.datapoints_to_alarm,
    )
    assert (props["Threshold"], props["ComparisonOperator"]) == (expected.threshold, expected.comparison)
    assert props["TreatMissingData"] == expected.missing_data


@pytest.mark.parametrize("alarm", sorted(ALARMS))
def test_each_alarm_reports_alarm_and_ok_to_the_ops_topic(alarm):
    block = T.block(alarm)
    for actions in ("AlarmActions", "OKActions"):
        assert re.search(rf"^      {actions}:\n        - !Ref OpsTopic\n", block, re.M), (alarm, actions)
    # What it means, then what to do first.
    description = re.search(r"^      AlarmDescription: >-\n((?:        .*\n)+)", block, re.M)
    assert description, f"{alarm} has no folded AlarmDescription"
    assert "; first, " in " ".join(line.strip() for line in description.group(1).splitlines())


def test_the_certificate_alarm_exists_only_with_the_custom_domain():
    assert re.search(r"^    Condition: HasApiCert$", T.block("ApiCertificateExpiry"), re.M)


def test_the_concurrency_alarm_threshold_is_a_parameter_at_eight():
    assert T.parameter("ConcurrencyAlarmThreshold")["Default"] == "8"


def test_the_ops_topic_policy_keeps_the_owner_and_lets_this_accounts_alarms_and_ses_publish():
    assert T.type_of("OpsTopicPolicy") == "AWS::SNS::TopicPolicy"
    assert re.search(r"^      Topics:\n        - !Ref OpsTopic\n", T.block("OpsTopicPolicy"), re.M)
    statements = _statements("OpsTopicPolicy")
    assert list(statements) == ["AccountOwnerManage", "CloudWatchAlarmsPublish", "SesEventsPublish"]
    for sid, text in statements.items():
        assert re.search(r"^            Effect: Allow$", text, re.M), sid
        assert re.search(r"^            Resource: !Ref OpsTopic$", text, re.M), sid
    # The default owner statement, restated because the policy replaces it.
    owner = statements["AccountOwnerManage"]
    assert re.search(r'^              AWS: !Sub "arn:aws:iam::\$\{AWS::AccountId\}:root"$', owner, re.M)
    assert re.findall(r"^              - (sns:\w+)$", owner, re.M) == [
        "sns:GetTopicAttributes", "sns:SetTopicAttributes", "sns:AddPermission", "sns:RemovePermission",
        "sns:DeleteTopic", "sns:Subscribe", "sns:ListSubscriptionsByTopic", "sns:Publish",
    ]
    # The two services publish only on behalf of this account.
    for sid, service in (
        ("CloudWatchAlarmsPublish", "cloudwatch.amazonaws.com"),
        ("SesEventsPublish", "ses.amazonaws.com"),
    ):
        text = statements[sid]
        assert re.search(rf"^              Service: {re.escape(service)}$", text, re.M), sid
        assert re.search(r"^            Action: sns:Publish$", text, re.M), sid
        assert re.search(
            r"^            Condition:\n              StringEquals:\n"
            r"                aws:SourceAccount: !Ref AWS::AccountId$",
            text,
            re.M,
        ), sid
    # SES checks it may publish when the event destination is created.
    assert re.search(r"^    DependsOn: OpsTopicPolicy$", T.block("SesConfigSetEvents"), re.M)


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
    assert T.parameter(param)["Default"] == '""'
    assert re.search(rf'^  {condition}: !Not \[!Equals \[!Ref {param}, ""\]\]$', T.text, re.M)
    assert re.search(rf"^    Condition: {condition}$", T.block(subscription), re.M)
    assert T.properties(subscription) == {
        "TopicArn": "!Ref OpsTopic", "Protocol": "email", "Endpoint": f"!Ref {param}",
    }
    assert f"{param}:{env}" in PARITY_SCRIPT


# --- database ------------------------------------------------------------------


def test_database_hardening_properties():
    block = T.block("Database")
    assert re.search(r"^    DeletionPolicy: Snapshot$", block, re.M)
    assert re.search(r"^    UpdateReplacePolicy: Snapshot$", block, re.M)
    props = T.properties("Database")
    assert props["DeletionProtection"] == "true"
    assert props["BackupRetentionPeriod"] == "35"
    assert props["MaxAllocatedStorage"] == "100"
    # UTC: backups 02:00-02:30 EAT daily; maintenance Sunday 03:00-04:00 EAT,
    # half an hour after Saturday night's backup ends rather than on it (D22).
    assert props["PreferredBackupWindow"] == '"23:00-23:30"'
    assert props["PreferredMaintenanceWindow"] == '"sun:00:00-sun:01:00"'
    # No PostgreSQL log export (D21): error DETAIL lines can carry emails and
    # the app-role password literal. No alarm sends the operator to one.
    assert "EnableCloudwatchLogsExports" not in block
    assert not [alarm for alarm in ALARMS if "postgresql log" in T.block(alarm)]
    assert "EnablePerformanceInsights" not in block  # not offered on db.t4g.micro (D18)


# --- mail DNS and the audit trail ------------------------------------------------


def test_the_mail_records_are_dkim_mail_from_and_dmarc_without_rua():
    records = {lid: T.properties(lid) for lid in T.of_type("AWS::Route53::RecordSet")}
    dkim = [p for p in records.values() if p.get("Type") == "CNAME" and "DkimDNSToken" in p["Name"]]
    assert len(dkim) == 3
    dmarc = T.block("DmarcRecord")
    assert '"v=DMARC1; p=none"' in dmarc and "rua" not in dmarc  # D11
    assert '"v=spf1 include:amazonses.com ~all"' in T.block("SesMailFromSpf")
    assert "feedback-smtp.${AWS::Region}.amazonses.com" in T.block("SesMailFromMx")
    assert _retained("SesIdentity", "RetainExceptOnCreate")


def test_the_trail_bucket_is_retained_private_encrypted_and_expires_after_90_days():
    assert _retained("TrailBucket")
    block = T.block("TrailBucket")
    assert "BucketName" not in block  # CloudFormation-named (D10)
    assert re.search(
        r"^      BucketEncryption:\n        ServerSideEncryptionConfiguration:\n"
        r"          - ServerSideEncryptionByDefault:\n              SSEAlgorithm: AES256\n",
        block,
        re.M,
    )
    for flag in ("BlockPublicAcls", "BlockPublicPolicy", "IgnorePublicAcls", "RestrictPublicBuckets"):
        assert re.search(rf"^        {flag}: true$", block, re.M), flag
    assert re.search(r"^          - ObjectOwnership: BucketOwnerEnforced$", block, re.M)
    rules = re.search(r"^      LifecycleConfiguration:\n        Rules:\n((?:          .*\n)+)", block, re.M)
    assert rules and rules.group(1).count("          - ") == 1
    assert dict(re.findall(r"^          (?:- |  )(\w+): (.+?)\s*$", rules.group(1), re.M)) == {
        "Id": "expire-after-90-days", "Status": "Enabled", "ExpirationInDays": "90",
    }


def test_the_trail_logs_every_region_with_validation_into_the_bucket_its_policy_names():
    assert T.type_of("AuditTrail") == "AWS::CloudTrail::Trail"
    trail = T.properties("AuditTrail")
    assert trail["IsLogging"] == "true"
    assert trail["IsMultiRegionTrail"] == "true"
    assert trail["IncludeGlobalServiceEvents"] == "true"
    assert trail["EnableLogFileValidation"] == "true"
    assert trail["S3BucketName"] == "!Ref TrailBucket"
    # CloudTrail checks it can write when the trail is created.
    assert re.search(r"^    DependsOn: TrailBucketPolicy$", T.block("AuditTrail"), re.M)
    assert T.properties("TrailBucketPolicy")["Bucket"] == "!Ref TrailBucket"
    # Both delivery statements are bound to this trail, by its name.
    statements = _statements("TrailBucketPolicy")
    assert list(statements) == ["AWSCloudTrailAclCheck", "AWSCloudTrailWrite"]
    trail_arn = f'!Sub "arn:aws:cloudtrail:${{AWS::Region}}:${{AWS::AccountId}}:trail/{trail["TrailName"]}"'
    for sid, text in statements.items():
        assert re.search(r"^              Service: cloudtrail\.amazonaws\.com$", text, re.M), sid
        assert re.findall(r"^                aws:SourceArn: (.+?)\s*$", text, re.M) == [trail_arn], sid
    assert re.search(r"^            Action: s3:GetBucketAcl$", statements["AWSCloudTrailAclCheck"], re.M)
    write = statements["AWSCloudTrailWrite"]
    assert re.search(r"^            Action: s3:PutObject$", write, re.M)
    assert re.search(r"^                s3:x-amz-acl: bucket-owner-full-control$", write, re.M)
