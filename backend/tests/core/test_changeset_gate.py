"""The change-set gate (hardening decisions D7, E3) and the deploys around it.

infra/scripts/changeset_gate.py runs under the system python3 on table-driven
describe-change-set and list-change-sets payloads. Then deploy-backend.sh
runs end to end against fake `aws`, `sam` and `git` (tests/core/fake_cli.py):
an aborted gate deletes the change set and never executes it; "No changes to
deploy" skips execution; CONFIRM_CHANGESET=1 answered "n" discards; a stack in
REVIEW_IN_PROGRESS waits for stack-create-complete; an open run refuses the
deploy before anything is built and again just before execution (E2;
ALLOW_OPEN_RUNS=1 overrides both); a verify function that does not read the
stack's database refuses it (E5; ALLOW_VERIFY_TARGET_MISMATCH=1 overrides);
the alert address is required (E1); sam deploy gets the stack and region the
gate reviews. deploy-frontend.sh runs end to end the same way (E3): its change
set is reviewed, confirmed and executed before anything is uploaded, and a
replaced CloudFront distribution or bucket aborts even with ALLOW_REPLACE.
No AWS call is ever made.
"""

import json
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.core.fake_cli import (
    BACKEND_REGION,
    CHANGESET_ARN,
    DB_ENDPOINT,
    FRONTEND_CHANGESET_ARN,
    FRONTEND_REGION,
    HEAD,
    HELD_ALERT,
    MIGRATE_FN,
    SCRATCH_ENDPOINT,
    SECRETS,
    VERIFY_FN,
    VERIFY_URL_PASSWORD,
    FakeCli,
    backend_deploy_world,
    change_set,
    database_url,
    frontend_deploy_world,
    preflight,
    resource_change,
    verify_answer,
)

REPO = Path(__file__).resolve().parents[3]
GATE = REPO / "infra" / "scripts" / "changeset_gate.py"
# The deploy runs the gate with the operator's system python3 (3.9 on macOS).
PYTHON = "/usr/bin/python3" if Path("/usr/bin/python3").exists() else shutil.which("python3")
DEPLOY = "infra/scripts/deploy-backend.sh"
STACK = "saferide-backend"


def _gate(*args, stdin):
    return subprocess.run(
        [PYTHON, str(GATE), *args], input=stdin if isinstance(stdin, str) else json.dumps(stdin),
        capture_output=True, text=True, timeout=30,
    )


# --- review ----------------------------------------------------------------------

DB = ("Database", "AWS::RDS::DBInstance")

REVIEW_CASES = [
    # id, changes, allow_replace, exit code, stderr fragments
    ("master-password-on-database-aborts-without-replacement",
     [resource_change(*DB, names=["MasterUserPassword"])], "", 2,
     ["ABORT: Database lists a MasterUserPassword change"]),
    ("master-password-is-not-waived-by-allow-replace",
     [resource_change(*DB, names=["MasterUserPassword"])], "Database", 2,
     ["ABORT: Database lists a MasterUserPassword change"]),
    ("master-password-on-another-resource-passes",
     [resource_change("ReportingDb", "AWS::RDS::DBInstance", names=["MasterUserPassword"])], "", 0, []),
    ("database-modify-without-password-passes",
     [resource_change(*DB, names=["BackupRetentionPeriod", "PreferredMaintenanceWindow"])], "", 0, []),
    ("replacement-true-aborts",
     [resource_change("ApiFunction", "AWS::Lambda::Function", replacement="True", names=["FunctionName"])], "", 2,
     ["ABORT: ApiFunction (AWS::Lambda::Function) would be REPLACED"]),
    ("database-replacement-aborts",
     [resource_change(*DB, replacement="True", names=["DBSnapshotIdentifier"])], "", 2,
     ["ABORT: Database (AWS::RDS::DBInstance) would be REPLACED"]),
    ("allow-replace-never-applies-to-the-database",
     [resource_change(*DB, replacement="True", names=["DBSnapshotIdentifier"])], "Database", 2,
     ["ABORT: Database (AWS::RDS::DBInstance) would be REPLACED, and ALLOW_REPLACE never applies to a database"]),
    ("replaced-sns-subscription-warns-and-passes",
     [resource_change("AlertSubscriptionPrimary", "AWS::SNS::Subscription", replacement="True", names=["Endpoint"])],
     "", 0, ["WARN: AlertSubscriptionPrimary (AWS::SNS::Subscription) will be replaced: a stateless type"]),
    ("replaced-alarm-warns-and-passes",
     [resource_change("ApiErrorsAlarm", "AWS::CloudWatch::Alarm", replacement="True", names=["AlarmName"])],
     "", 0, ["WARN: ApiErrorsAlarm (AWS::CloudWatch::Alarm) will be replaced"]),
    ("replaced-metric-filter-warns-and-passes",
     [resource_change("PinFailuresFilter", "AWS::Logs::MetricFilter", replacement="True", names=["LogGroupName"])],
     "", 0, ["WARN: PinFailuresFilter (AWS::Logs::MetricFilter) will be replaced"]),
    ("allow-replace-downgrades-the-named-id",
     [resource_change("MailConfigurationSet", "AWS::SES::ConfigurationSet", replacement="True", names=["Name"])],
     " MailConfigurationSet ,", 0,
     ["!!!!", "WARN: MailConfigurationSet (AWS::SES::ConfigurationSet) WILL BE REPLACED: allowed by ALLOW_REPLACE"]),
    ("allow-replace-leaves-other-ids-aborting",
     [resource_change("MailConfigurationSet", "AWS::SES::ConfigurationSet", replacement="True"),
      resource_change("ApiFunction", "AWS::Lambda::Function", replacement="True")],
     "MailConfigurationSet", 2,
     ["WILL BE REPLACED: allowed by ALLOW_REPLACE", "ABORT: ApiFunction (AWS::Lambda::Function) would be REPLACED"]),
    ("allow-replace-never-applies-to-the-distribution",
     [resource_change("Distribution", "AWS::CloudFront::Distribution", replacement="True", names=["DistributionConfig"])],
     "Distribution", 2,
     ["ABORT: Distribution (AWS::CloudFront::Distribution) would be REPLACED, and ALLOW_REPLACE never applies"
      " to a CloudFront distribution"]),
    ("allow-replace-never-applies-to-a-bucket",
     [resource_change("TrailBucket", "AWS::S3::Bucket", replacement="True", names=["BucketName"])],
     "TrailBucket", 2,
     ["ABORT: TrailBucket (AWS::S3::Bucket) would be REPLACED, and ALLOW_REPLACE never applies to an S3 bucket"]),
    ("allow-replace-naming-an-unreplaced-id-warns",
     [resource_change("ApiFunction", "AWS::Lambda::Function", names=["Code"])], "Databse", 0,
     ["WARN: ALLOW_REPLACE names Databse, which this change set does not replace"]),
    ("conditional-warns-and-passes",
     [resource_change("ApiFunction", "AWS::Lambda::Function", replacement="Conditional", names=["Role"])], "", 0,
     ["WARN: ApiFunction (AWS::Lambda::Function) may be replaced (Replacement: Conditional)"]),
    ("adds-and-removes-pass",
     [resource_change("OpsTopic", "AWS::SNS::Topic", action="Add", replacement=None),
      resource_change("OldAlarm", "AWS::CloudWatch::Alarm", action="Remove", replacement=None)], "", 0, []),
    ("empty-changes-pass", [], "", 0, []),
]


@pytest.mark.parametrize("changes,allow,code,fragments", [case[1:] for case in REVIEW_CASES],
                         ids=[case[0] for case in REVIEW_CASES])
def test_review(changes, allow, code, fragments):
    args = ["review"] + (["--allow-replace", allow] if allow else [])
    result = _gate(*args, stdin=change_set(*changes))
    assert result.returncode == code, result.stderr
    for fragment in fragments:
        assert fragment in result.stderr
    if code == 0:
        assert "ABORT" not in result.stderr
    if not fragments:
        assert result.stderr == ""
    # The table always prints: header, then one row per resource change.
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert lines[0].split() == ["Action", "LogicalResourceId", "ResourceType", "Replacement", "Changed"]
    assert len(lines) == 1 + len(changes)


def test_the_table_names_each_change():
    result = _gate("review", stdin=change_set(
        resource_change(*DB, names=["BackupRetentionPeriod", "DeletionProtection"]),
        resource_change("OpsTopic", "AWS::SNS::Topic", action="Add", replacement=None),
    ))
    assert result.returncode == 0
    rows = [line.split(None, 4) for line in result.stdout.splitlines() if line.strip()][1:]
    assert rows == [
        ["Modify", "Database", "AWS::RDS::DBInstance", "False", "BackupRetentionPeriod, DeletionProtection"],
        ["Add", "OpsTopic", "AWS::SNS::Topic", "-", "-"],
    ]


@pytest.mark.parametrize("status,execution", [
    ("FAILED", "UNAVAILABLE"), ("CREATE_PENDING", "UNAVAILABLE"), ("CREATE_COMPLETE", "EXECUTE_COMPLETE"),
])
def test_a_change_set_that_is_not_executable_aborts(status, execution):
    payload = change_set(resource_change(*DB, names=["BackupRetentionPeriod"]), status=status,
                         execution=execution, reason="The submitted information didn't contain changes.")
    result = _gate("review", stdin=payload)
    assert result.returncode == 2
    assert f"ABORT: change set is {status}/{execution}: The submitted information" in result.stderr
    assert result.stdout == ""


def test_unreadable_input_aborts():
    result = _gate("review", stdin="An error occurred (AccessDenied)")
    assert result.returncode == 2
    assert "ABORT: could not read the describe-change-set JSON" in result.stderr


# --- newest-since ----------------------------------------------------------------

SINCE = datetime(2026, 11, 18, 10, 0, 0, tzinfo=timezone.utc)


def _summary(name, at, status="CREATE_COMPLETE", execution="AVAILABLE", stamp=None):
    return {"ChangeSetName": name, "ChangeSetId": f"arn:aws:cloudformation:af-south-1:0:changeSet/{name}/id",
            "Status": status, "ExecutionStatus": execution,
            "CreationTime": stamp or (SINCE + timedelta(seconds=at)).isoformat()}


def _newest(*summaries):
    result = _gate("newest-since", str(SINCE.timestamp()), stdin={"Summaries": list(summaries)})
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_newest_since_picks_the_newest_executable_sam_change_set():
    assert _newest(
        _summary("samcli-deploy1", 10), _summary("samcli-deploy2", 30), _summary("samcli-deploy3", 20),
    ).endswith("/samcli-deploy2/id")


@pytest.mark.parametrize("ignored", [
    _summary("manual-review", 60),                                    # not a SAM change set
    _summary("samcli-deploy-unavailable", 60, execution="UNAVAILABLE"),
    _summary("samcli-deploy-pending", 60, status="CREATE_PENDING"),
    _summary("samcli-deploy-failed", 60, status="FAILED"),
    _summary("samcli-deploy-stale", -6),                              # before the 5 s skew
])
def test_newest_since_ignores_what_this_deploy_cannot_have_created(ignored):
    assert _newest(_summary("samcli-deploy-mine", 1), ignored).endswith("/samcli-deploy-mine/id")
    assert _newest(ignored) == ""


def test_newest_since_tolerates_five_seconds_of_clock_skew_and_any_timestamp_spelling():
    assert _newest(_summary("samcli-deploy-skewed", -4)).endswith("/samcli-deploy-skewed/id")
    assert _newest(_summary("samcli-deploy-z", 0, stamp="2026-11-18T10:00:02.12Z")).endswith("/samcli-deploy-z/id")
    assert _newest() == ""


def test_newest_since_takes_only_the_named_tools_change_sets():
    """The frontend's `aws cloudformation deploy` names its change sets
    awscli-cloudformation-package-deploy-<epoch>; SAM's are the default."""
    listing = {"Summaries": [_summary("samcli-deploy9", 30),
                             _summary("awscli-cloudformation-package-deploy-1763460000", 20)]}
    awscli = _gate("newest-since", str(SINCE.timestamp()), "--prefix", "awscli-cloudformation-package-deploy-",
                   stdin=listing)
    assert awscli.returncode == 0, awscli.stderr
    assert awscli.stdout.strip().endswith("/awscli-cloudformation-package-deploy-1763460000/id")
    assert _gate("newest-since", str(SINCE.timestamp()), stdin=listing).stdout.strip().endswith("/samcli-deploy9/id")


# --- deploy-backend.sh end to end -------------------------------------------------


@pytest.fixture
def fake(tmp_path):
    cli = FakeCli(tmp_path)
    backend_deploy_world(cli)
    return cli


def _no_secret_printed(result):
    for value in list(SECRETS.values()) + [VERIFY_URL_PASSWORD, "postgresql://"]:
        assert value not in result.stdout and value not in result.stderr


def _deploy(fake: FakeCli, stdin=None, **env):
    result = fake.run(DEPLOY, stdin=stdin, **env)
    _no_secret_printed(result)  # no secret, and never the verify function's URL
    assert list(fake.tmpdir.iterdir()) == []  # every temporary file is removed
    return result


def _aws(fake, *prefix):
    return [call for call in fake.calls("aws") if call[:len(prefix)] == list(prefix)]


def _invoked(fake):
    return [call[call.index("--function-name") + 1] for call in _aws(fake, "lambda", "invoke")]


def _executed(fake):
    return [call[call.index("--change-set-name") + 1] for call in _aws(fake, "cloudformation", "execute-change-set")]


def _deleted(fake):
    return [call[call.index("--change-set-name") + 1] for call in _aws(fake, "cloudformation", "delete-change-set")]


def _waiters(fake):
    return [call[2] for call in _aws(fake, "cloudformation", "wait")]


def test_a_clean_deploy_checks_runs_builds_reviews_executes_and_migrates(fake):
    result = _deploy(fake)
    assert result.returncode == 0, result.stderr
    assert _executed(fake) == [CHANGESET_ARN]
    assert _deleted(fake) == []
    assert _waiters(fake) == ["stack-update-complete"]
    # The open-run check before the build, its re-check before execution, the migration.
    assert _invoked(fake) == [VERIFY_FN, VERIFY_FN, MIGRATE_FN]
    assert result.stdout.count("open-runs: 0") == 2
    assert re.search(r"^  Modify +Database +AWS::RDS::DBInstance +False +BackupRetentionPeriod$", result.stdout, re.M)
    assert "==> Stack saferide-backend deployed" in result.stdout
    # In order: the open-run check, the build, sam deploy, the review, the
    # re-check, execution.
    order = [" ".join(call[:3]) for call in fake.calls()]
    assert order.index("aws lambda invoke") < order.index("sam build --use-container")
    assert order.index("sam build --use-container") < order.index("sam deploy --no-execute-changeset") \
        < order.index("aws cloudformation describe-change-set")
    recheck = order.index("aws lambda invoke", order.index("aws cloudformation describe-change-set"))
    assert recheck < order.index("aws cloudformation execute-change-set")
    # The verify target is checked before each count is read.
    assert order.count("aws lambda get-function-configuration") == 2
    assert order.index("aws lambda get-function-configuration") < order.index("aws lambda invoke")


def test_sam_deploy_is_pinned_to_the_reviewed_stack_and_region(fake):
    result = _deploy(fake)
    assert result.returncode == 0, result.stderr
    [deploy] = [call for call in fake.calls("sam") if call[:1] == ["deploy"]]
    assert deploy[deploy.index("--stack-name") + 1] == STACK
    assert deploy[deploy.index("--region") + 1] == BACKEND_REGION
    overrides = deploy[deploy.index("--parameter-overrides") + 1:]
    assert f"AppVersion={HEAD[:7]}" in overrides
    assert "GpsCustodyThresholdM=150" in overrides  # a parity parameter at its template Default
    assert f"DbMasterPassword={SECRETS['/saferide/db-password']}" in overrides
    # Every CloudFormation call — the review, the execution, the waiter — names
    # the same stack and region.
    for call in _aws(fake, "cloudformation"):
        assert call[call.index("--stack-name") + 1] == STACK
        assert call[call.index("--region") + 1] == BACKEND_REGION


@pytest.mark.parametrize("change", [
    resource_change("Database", "AWS::RDS::DBInstance", names=["MasterUserPassword"]),
    resource_change("Database", "AWS::RDS::DBInstance", replacement="True", names=["DBInstanceClass"]),
], ids=["master-password", "replacement"])
def test_an_aborted_gate_deletes_the_change_set_and_never_executes_it(fake, change):
    fake.world["change_set"] = change_set(change)
    result = _deploy(fake)
    assert result.returncode == 1
    assert "ABORT: " in result.stderr and "change-set gate refused this deploy" in result.stderr
    assert _deleted(fake) == [CHANGESET_ARN]
    assert _executed(fake) == []
    assert _waiters(fake) == []
    assert MIGRATE_FN not in _invoked(fake)


def test_allow_replace_reaches_the_gate(fake):
    fake.world["change_set"] = change_set(
        resource_change("MailConfigurationSet", "AWS::SES::ConfigurationSet", replacement="True", names=["Name"]))
    refused = _deploy(fake)
    assert refused.returncode == 1 and _executed(fake) == []
    allowed = _deploy(fake, ALLOW_REPLACE="MailConfigurationSet")
    assert allowed.returncode == 0, allowed.stderr
    assert "WILL BE REPLACED: allowed by ALLOW_REPLACE" in allowed.stderr
    assert _executed(fake) == [CHANGESET_ARN]


def test_no_changes_skips_execution_and_still_migrates(fake):
    fake.world["sam_deploy"] = "no-changes"
    result = _deploy(fake)
    assert result.returncode == 0, result.stderr
    assert "No stack changes: nothing to execute" in result.stdout
    assert _aws(fake, "cloudformation", "describe-change-set") == []
    assert _executed(fake) == [] and _deleted(fake) == []
    assert _invoked(fake) == [VERIFY_FN, MIGRATE_FN]


def test_confirm_changeset_answered_no_discards_it(fake):
    result = _deploy(fake, stdin="n\n", CONFIRM_CHANGESET="1")
    assert result.returncode == 1
    assert "Deploy cancelled by the operator." in result.stderr
    assert _deleted(fake) == [CHANGESET_ARN]
    assert _executed(fake) == []
    assert MIGRATE_FN not in _invoked(fake)


def test_confirm_changeset_answered_yes_executes(fake):
    result = _deploy(fake, stdin="y\n", CONFIRM_CHANGESET="1")
    assert result.returncode == 0, result.stderr
    assert _executed(fake) == [CHANGESET_ARN] and _deleted(fake) == []


def test_a_stack_in_review_in_progress_waits_for_create_complete(fake):
    fake.world["stack"]["status"] = "REVIEW_IN_PROGRESS"
    result = _deploy(fake)
    assert result.returncode == 0, result.stderr
    assert _waiters(fake) == ["stack-create-complete"]


def test_a_change_set_sam_did_not_name_is_found_by_creation_time(fake):
    now = datetime.now(timezone.utc)
    newest = "arn:aws:cloudformation:af-south-1:0:changeSet/samcli-deploy-new/id"
    fake.world["sam_deploy"] = "unnamed"
    fake.world["change_sets"] = {"Summaries": [
        {"ChangeSetName": "samcli-deploy-old", "ChangeSetId": "arn:old", "Status": "CREATE_COMPLETE",
         "ExecutionStatus": "AVAILABLE", "CreationTime": (now - timedelta(hours=1)).isoformat()},
        {"ChangeSetName": "samcli-deploy-new", "ChangeSetId": newest, "Status": "CREATE_COMPLETE",
         "ExecutionStatus": "AVAILABLE", "CreationTime": (now + timedelta(minutes=5)).isoformat()},
    ]}
    result = _deploy(fake)
    assert result.returncode == 0, result.stderr
    assert _executed(fake) == [newest]


def test_a_failed_stack_update_prints_events_and_does_not_migrate(fake):
    fake.world["wait_fails"] = True
    result = _deploy(fake)
    assert result.returncode == 1
    assert "did not reach update-complete" in result.stderr
    assert _aws(fake, "cloudformation", "describe-stack-events")
    assert MIGRATE_FN not in _invoked(fake)


# --- open-run guard ---------------------------------------------------------------


def _nothing_built_or_deployed(fake):
    assert fake.calls("sam") == []
    assert _aws(fake, "cloudformation", "execute-change-set") == []
    assert _invoked(fake) == [VERIFY_FN] or _invoked(fake) == []


def test_an_open_run_refuses_the_deploy_before_anything_is_built(fake):
    fake.world["lambda"][VERIFY_FN] = preflight(1)
    result = _deploy(fake)
    assert result.returncode == 1
    assert "ERROR: 1 run(s) are open" in result.stderr
    assert "ALLOW_OPEN_RUNS=1" in result.stderr
    _nothing_built_or_deployed(fake)
    [invoke] = _aws(fake, "lambda", "invoke")
    assert json.loads(invoke[invoke.index("--payload") + 1]) == {"checks": "tenancy-preflight"}


def test_allow_open_runs_deploys_anyway_with_a_loud_warning(fake):
    fake.world["lambda"][VERIFY_FN] = preflight(2)
    result = _deploy(fake, ALLOW_OPEN_RUNS="1")
    assert result.returncode == 0, result.stderr
    # Once for the check before the build, once for the re-check.
    assert result.stderr.count("WARNING: ALLOW_OPEN_RUNS=1 — deploying although open-runs is 2.") == 2
    assert result.stderr.count("!" * 76) == 4
    assert _executed(fake) == [CHANGESET_ARN]


# --- open-run re-check before execution (E2) -----------------------------------------


@pytest.mark.parametrize("confirm", [False, True], ids=["no-prompt", "after-a-yes-at-the-prompt"])
def test_a_run_opened_during_the_build_refuses_before_executing(fake, confirm):
    fake.world["lambda"][VERIFY_FN] = [preflight(0), preflight(1)]
    env = {"CONFIRM_CHANGESET": "1"} if confirm else {}
    result = _deploy(fake, stdin="y\n" if confirm else None, **env)
    assert result.returncode == 1
    assert "ERROR: 1 run(s) are open" in result.stderr
    assert "the open-run re-check refused this deploy; the change set was not executed" in result.stderr
    assert "change set deleted; nothing was executed." in result.stderr
    assert _deleted(fake) == [CHANGESET_ARN]
    assert _executed(fake) == [] and _waiters(fake) == []
    assert _invoked(fake) == [VERIFY_FN, VERIFY_FN]  # never the migration
    order = [" ".join(call[:3]) for call in fake.calls()]
    assert order.index("aws cloudformation describe-change-set") \
        < order.index("aws lambda invoke", order.index("sam deploy --no-execute-changeset")) \
        < order.index("aws cloudformation delete-change-set")


def test_a_recheck_that_reads_no_count_refuses_too(fake):
    fake.world["lambda"][VERIFY_FN] = [preflight(0), verify_answer([], function_error="Unhandled")]
    result = _deploy(fake)
    assert result.returncode == 1
    assert "cannot confirm that no run is open: saferide-verify failed (Unhandled)" in result.stderr
    assert _deleted(fake) == [CHANGESET_ARN] and _executed(fake) == []


def test_allow_open_runs_covers_the_recheck(fake):
    fake.world["lambda"][VERIFY_FN] = [preflight(0), preflight(3)]
    result = _deploy(fake, ALLOW_OPEN_RUNS="1")
    assert result.returncode == 0, result.stderr
    assert "WARNING: ALLOW_OPEN_RUNS=1 — deploying although open-runs is 3." in result.stderr
    assert _executed(fake) == [CHANGESET_ARN] and _deleted(fake) == []
    assert _invoked(fake) == [VERIFY_FN, VERIFY_FN, MIGRATE_FN]


# --- verify target (E5) ------------------------------------------------------------


def test_the_verify_target_is_read_as_its_url_alone_in_the_backend_region(fake):
    result = _deploy(fake)
    assert result.returncode == 0, result.stderr
    for call in _aws(fake, "lambda", "get-function-configuration"):
        assert call[call.index("--function-name") + 1] == VERIFY_FN
        assert call[call.index("--region") + 1] == BACKEND_REGION
        assert call[call.index("--query") + 1] == "Environment.Variables.DATABASE_URL"


def test_a_verify_function_on_a_scratch_restore_refuses_before_anything_is_built(fake):
    fake.world["lambda_config"][VERIFY_FN]["DATABASE_URL"] = database_url(SCRATCH_ENDPOINT)
    result = _deploy(fake)
    assert result.returncode == 1
    assert "ERROR: the verify function does not read saferide-backend's database: " \
        f"{VERIFY_FN} reads database host {SCRATCH_ENDPOINT}, not the stack's DbEndpoint {DB_ENDPOINT}" \
        " (it still points at a scratch restore)." in result.stderr
    assert "infra/RESTORE.md, step C" in result.stderr and "ALLOW_VERIFY_TARGET_MISMATCH=1" in result.stderr
    _nothing_built_or_deployed(fake)
    assert _invoked(fake) == []  # no count is read from the wrong database


def test_allow_verify_target_mismatch_deploys_with_a_loud_warning(fake):
    fake.world["lambda_config"][VERIFY_FN]["DATABASE_URL"] = database_url(SCRATCH_ENDPOINT)
    result = _deploy(fake, ALLOW_VERIFY_TARGET_MISMATCH="1")
    assert result.returncode == 0, result.stderr
    assert result.stderr.count(
        f"WARNING: ALLOW_VERIFY_TARGET_MISMATCH=1 — going on although {VERIFY_FN} reads database host"
        f" {SCRATCH_ENDPOINT}") == 2
    assert _executed(fake) == [CHANGESET_ARN]


def test_the_host_comparison_ignores_case(fake):
    fake.world["lambda_config"][VERIFY_FN]["DATABASE_URL"] = database_url(DB_ENDPOINT.upper())
    result = _deploy(fake)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("breakage,problem", [
    ("no-config", f"the DATABASE_URL host of {VERIFY_FN} could not be read (see the AWS error above)"),
    ("no-url", f"{VERIFY_FN} has no DATABASE_URL"),
    ("no-endpoint", "stack saferide-backend (af-south-1) has no DbEndpoint output"),
])
def test_an_unconfirmable_verify_target_refuses(fake, breakage, problem):
    if breakage == "no-config":
        del fake.world["lambda_config"][VERIFY_FN]
    elif breakage == "no-url":
        del fake.world["lambda_config"][VERIFY_FN]["DATABASE_URL"]
    else:
        del fake.world["stack"]["outputs"]["DbEndpoint"]
    result = _deploy(fake)
    assert result.returncode == 1
    assert f"does not read saferide-backend's database: {problem}." in result.stderr
    _nothing_built_or_deployed(fake)


# --- alert address (E1), end to end ---------------------------------------------------


def _sam_overrides(fake):
    [deploy] = [call for call in fake.calls("sam") if call[:1] == ["deploy"]]
    return [arg for arg in deploy[deploy.index("--parameter-overrides") + 1:] if not arg.startswith("--")]


def test_the_held_alert_address_is_passed_explicitly(fake):
    result = _deploy(fake)
    assert result.returncode == 0, result.stderr
    assert f"AlertEmailPrimary={HELD_ALERT}" in _sam_overrides(fake)
    assert f"==> Alert address kept: AlertEmailPrimary={HELD_ALERT} (held by saferide-backend;" in result.stdout


def test_an_exported_alert_address_wins(fake):
    result = _deploy(fake, ALERT_EMAIL_PRIMARY="ops-one@example.org")
    assert result.returncode == 0, result.stderr
    overrides = _sam_overrides(fake)
    assert [arg for arg in overrides if arg.startswith("AlertEmailPrimary=")] == ["AlertEmailPrimary=ops-one@example.org"]
    assert "Alert address kept" not in result.stdout


def test_no_alert_address_refuses_before_anything_is_built(fake):
    del fake.world["stack"]["parameters"]["AlertEmailPrimary"]
    result = _deploy(fake)
    assert result.returncode == 1
    assert "ERROR: no alert address: alarms would email no one — export ALERT_EMAIL_PRIMARY." in result.stderr
    assert "ALLOW_NO_ALERTS=1" in result.stderr
    assert fake.calls("sam") == [] and _invoked(fake) == []


def test_allow_no_alerts_deploys_without_one_loudly(fake):
    del fake.world["stack"]["parameters"]["AlertEmailPrimary"]
    result = _deploy(fake, ALLOW_NO_ALERTS="1")
    assert result.returncode == 0, result.stderr
    assert "WARNING: ALLOW_NO_ALERTS=1 — deploying saferide-backend (af-south-1) with no alert address" in result.stderr
    assert not [arg for arg in _sam_overrides(fake) if arg.startswith("AlertEmailPrimary=")]
    assert _executed(fake) == [CHANGESET_ARN]


@pytest.mark.parametrize("breakage,reason", [
    ("no-verify-output", "stack saferide-backend (af-south-1) has no VerifyFunctionName output"),
    ("no-stack", "stack saferide-backend (af-south-1) has no VerifyFunctionName output"),
    ("function-missing", "could not invoke saferide-verify"),
    ("function-error", "saferide-verify failed (Unhandled)"),
    ("status-error", "tenancy-preflight answered status 'error'"),
    ("no-label", "tenancy-preflight has no open-runs row"),
    ("label-error", "the open-runs check failed: relation \"live_runs\" does not exist"),
    ("no-count", "the open-runs check returned no count"),
])
def test_an_unavailable_open_run_count_refuses_unless_allowed(tmp_path, breakage, reason):
    fake = FakeCli(tmp_path)
    backend_deploy_world(fake)
    if breakage == "no-verify-output":
        del fake.world["stack"]["outputs"]["VerifyFunctionName"]
    elif breakage == "no-stack":
        del fake.world["stack"]
    elif breakage == "function-missing":
        del fake.world["lambda"][VERIFY_FN]
    elif breakage == "function-error":
        fake.world["lambda"][VERIFY_FN] = verify_answer([], function_error="Unhandled")
    elif breakage == "status-error":
        fake.world["lambda"][VERIFY_FN] = verify_answer([], status="error", reason="unknown check set")
    elif breakage == "no-label":
        fake.world["lambda"][VERIFY_FN] = verify_answer([{"label": "school-rows", "rows": []}])
    elif breakage == "label-error":
        fake.world["lambda"][VERIFY_FN] = verify_answer(
            [{"label": "open-runs", "error": 'relation "live_runs" does not exist'}])
    else:
        fake.world["lambda"][VERIFY_FN] = verify_answer([{"label": "open-runs", "rows": []}])
    # Without a stack there is no held alert address either (E1 would refuse first).
    env = {"ALERT_EMAIL_PRIMARY": "ops-one@example.org"} if breakage == "no-stack" else {}
    refused = _deploy(fake, **env)
    assert refused.returncode == 1
    assert f"cannot confirm that no run is open: {reason}" in refused.stderr
    _nothing_built_or_deployed(fake)
    # Every reason takes the same override branch; run it for two of them (a
    # stack without the verify function, and a verify function that fails).
    if breakage not in ("no-verify-output", "function-error"):
        return
    allowed = _deploy(fake, ALLOW_OPEN_RUNS="1")
    assert allowed.returncode == 0, allowed.stderr
    # The check before the build and the re-check before execution both warn.
    assert allowed.stderr.count(f"deploying although no open-run count could be read: {reason}") == 2
    assert [call[:1] for call in fake.calls("sam")] == [["build"], ["deploy"]]


# --- deploy-frontend.sh end to end (E3) ------------------------------------------------

FRONTEND_DEPLOY = "infra/scripts/deploy-frontend.sh"
FRONTEND_STACK = "saferide-frontend"


@pytest.fixture
def front(tmp_path):
    cli = FakeCli(tmp_path)
    frontend_deploy_world(cli)
    return cli


def _deploy_frontend(fake: FakeCli, stdin=None, **env):
    result = fake.run(FRONTEND_DEPLOY, stdin=stdin, **env)
    for value in ("value-of-google-maps-browser-key", "value-of-google-maps-map-id"):
        assert value not in result.stdout and value not in result.stderr
    assert list(fake.tmpdir.iterdir()) == []
    return result


def _uploaded(fake):
    return [call[:2] for call in fake.calls("aws") if call[0] in ("s3", "cloudfront")]


# The fake build writes index.html and sw.js, each copied with no-cache.
UPLOAD = [["s3", "sync"], ["s3", "cp"], ["s3", "cp"], ["cloudfront", "create-invalidation"]]


def test_a_clean_frontend_deploy_builds_reviews_executes_then_uploads(front):
    result = _deploy_frontend(front)
    assert result.returncode == 0, result.stderr
    assert _executed(front) == [FRONTEND_CHANGESET_ARN] and _deleted(front) == []
    assert _waiters(front) == ["stack-update-complete"]
    assert _uploaded(front) == UPLOAD
    assert re.search(r"^  Modify +SecurityHeadersPolicy +AWS::CloudFront::ResponseHeadersPolicy +False",
                     result.stdout, re.M)
    assert "==> Stack saferide-frontend deployed" in result.stdout
    order = [" ".join(call[:3]) for call in front.calls()]
    assert order.index("npm run build") < order.index("aws cloudformation deploy") \
        < order.index("aws cloudformation describe-change-set") < order.index("aws cloudformation execute-change-set") \
        < order.index("aws cloudformation wait") < order.index("aws s3 sync")
    [deploy] = _aws(front, "cloudformation", "deploy")
    assert "--no-execute-changeset" in deploy and "--no-fail-on-empty-changeset" in deploy
    # Every CloudFormation call names the frontend stack and its region.
    for call in _aws(front, "cloudformation"):
        assert call[call.index("--stack-name") + 1] == FRONTEND_STACK
        assert call[call.index("--region") + 1] == FRONTEND_REGION


@pytest.mark.parametrize("change,allow,abort", [
    (resource_change("SiteHealthCheck", "AWS::Route53::HealthCheck", replacement="True", names=["HealthCheckConfig"]),
     "", "ABORT: SiteHealthCheck (AWS::Route53::HealthCheck) would be REPLACED"),
    (resource_change("Distribution", "AWS::CloudFront::Distribution", replacement="True", names=["DistributionConfig"]),
     "Distribution", "ALLOW_REPLACE never applies to a CloudFront distribution"),
    (resource_change("SiteBucket", "AWS::S3::Bucket", replacement="True", names=["BucketName"]),
     "SiteBucket", "ALLOW_REPLACE never applies to an S3 bucket"),
], ids=["replacement", "distribution-even-when-allowed", "bucket-even-when-allowed"])
def test_an_aborted_frontend_gate_deletes_the_change_set_and_uploads_nothing(front, change, allow, abort):
    front.world["change_set"] = {**change_set(change), "ChangeSetId": FRONTEND_CHANGESET_ARN}
    result = _deploy_frontend(front, **({"ALLOW_REPLACE": allow} if allow else {}))
    assert result.returncode == 1
    assert abort in result.stderr
    assert "change-set gate refused this deploy (see ABORT above); nothing was uploaded." in result.stderr
    assert _deleted(front) == [FRONTEND_CHANGESET_ARN]
    assert _executed(front) == [] and _waiters(front) == [] and _uploaded(front) == []


def test_a_replaced_stateless_frontend_resource_warns_and_deploys(front):
    front.world["change_set"] = {**change_set(
        resource_change("OpsUsEast1SubscriptionPrimary", "AWS::SNS::Subscription", replacement="True",
                        names=["Endpoint"])), "ChangeSetId": FRONTEND_CHANGESET_ARN}
    result = _deploy_frontend(front)
    assert result.returncode == 0, result.stderr
    assert "WARN: OpsUsEast1SubscriptionPrimary (AWS::SNS::Subscription) will be replaced: a stateless type" \
        in result.stderr
    assert _executed(front) == [FRONTEND_CHANGESET_ARN] and _uploaded(front) == UPLOAD


def test_allow_replace_reaches_the_frontend_gate(front):
    front.world["change_set"] = {**change_set(
        resource_change("SiteHealthCheck", "AWS::Route53::HealthCheck", replacement="True")),
        "ChangeSetId": FRONTEND_CHANGESET_ARN}
    result = _deploy_frontend(front, ALLOW_REPLACE="SiteHealthCheck")
    assert result.returncode == 0, result.stderr
    assert "SiteHealthCheck (AWS::Route53::HealthCheck) WILL BE REPLACED: allowed by ALLOW_REPLACE" in result.stderr
    assert _executed(front) == [FRONTEND_CHANGESET_ARN]


def test_no_frontend_changes_go_straight_to_the_upload(front):
    front.world["cfn_deploy"] = "no-changes"
    result = _deploy_frontend(front)
    assert result.returncode == 0, result.stderr
    assert "No stack changes: nothing to execute; continuing to the upload." in result.stdout
    assert _aws(front, "cloudformation", "describe-change-set") == []
    assert _executed(front) == [] and _deleted(front) == []
    assert _uploaded(front) == UPLOAD


def test_confirm_changeset_answered_no_discards_the_frontend_change_set(front):
    result = _deploy_frontend(front, stdin="n\n", CONFIRM_CHANGESET="1")
    assert result.returncode == 1
    assert "Deploy cancelled by the operator." in result.stderr
    assert _deleted(front) == [FRONTEND_CHANGESET_ARN]
    assert _executed(front) == [] and _uploaded(front) == []


def test_confirm_changeset_answered_yes_executes_the_frontend_change_set(front):
    result = _deploy_frontend(front, stdin="y\n", CONFIRM_CHANGESET="1")
    assert result.returncode == 0, result.stderr
    assert _executed(front) == [FRONTEND_CHANGESET_ARN] and _uploaded(front) == UPLOAD


def test_a_frontend_change_set_the_cli_did_not_name_is_found_by_its_prefix(front):
    now = datetime.now(timezone.utc)
    mine = "arn:aws:cloudformation:us-east-1:0:changeSet/awscli-cloudformation-package-deploy-2/id"
    front.world["cfn_deploy"] = "unnamed"
    front.world["change_sets"] = {"Summaries": [
        {"ChangeSetName": "samcli-deploy-other", "ChangeSetId": "arn:sam", "Status": "CREATE_COMPLETE",
         "ExecutionStatus": "AVAILABLE", "CreationTime": (now + timedelta(minutes=9)).isoformat()},
        {"ChangeSetName": "awscli-cloudformation-package-deploy-2", "ChangeSetId": mine,
         "Status": "CREATE_COMPLETE", "ExecutionStatus": "AVAILABLE",
         "CreationTime": (now + timedelta(minutes=5)).isoformat()},
    ]}
    result = _deploy_frontend(front)
    assert result.returncode == 0, result.stderr
    assert _executed(front) == [mine]


def test_a_failed_frontend_stack_update_prints_events_and_uploads_nothing(front):
    front.world["wait_fails"] = True
    result = _deploy_frontend(front)
    assert result.returncode == 1
    assert "saferide-frontend did not reach update-complete" in result.stderr
    assert _aws(front, "cloudformation", "describe-stack-events")
    assert _uploaded(front) == []


def test_a_new_frontend_stack_waits_for_create_complete(front):
    front.world["stack"]["status"] = "REVIEW_IN_PROGRESS"
    result = _deploy_frontend(front)
    assert result.returncode == 0, result.stderr
    assert _waiters(front) == ["stack-create-complete"]


def test_no_frontend_alert_address_refuses_before_the_build(front):
    del front.world["stack"]
    result = _deploy_frontend(front)
    assert result.returncode == 1
    assert "ERROR: no alert address: alarms would email no one — export ALERT_EMAIL_PRIMARY." in result.stderr
    assert "Stack saferide-frontend (us-east-1) holds no AlertEmailPrimary" in result.stderr
    assert front.calls("npm") == [] and _aws(front, "cloudformation", "deploy") == []
