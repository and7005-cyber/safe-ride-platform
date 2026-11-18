"""The backend change-set gate (hardening decision D7) and the deploy around it.

infra/scripts/changeset_gate.py runs under the system python3 on table-driven
describe-change-set and list-change-sets payloads. Then deploy-backend.sh
runs end to end against fake `aws`, `sam` and `git` (tests/core/fake_cli.py):
an aborted gate deletes the change set and never executes it; "No changes to
deploy" skips execution; CONFIRM_CHANGESET=1 answered "n" discards; a stack in
REVIEW_IN_PROGRESS waits for stack-create-complete; an open run refuses the
deploy before anything is built (ALLOW_OPEN_RUNS=1 overrides); sam deploy
gets the stack and region the gate reviews. No AWS call is ever made.
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
    HEAD,
    MIGRATE_FN,
    SECRETS,
    VERIFY_FN,
    FakeCli,
    backend_deploy_world,
    change_set,
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
      resource_change("TrailBucket", "AWS::S3::Bucket", replacement="True")],
     "MailConfigurationSet", 2,
     ["WILL BE REPLACED: allowed by ALLOW_REPLACE", "ABORT: TrailBucket (AWS::S3::Bucket) would be REPLACED"]),
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


# --- deploy-backend.sh end to end -------------------------------------------------


@pytest.fixture
def fake(tmp_path):
    cli = FakeCli(tmp_path)
    backend_deploy_world(cli)
    return cli


def _deploy(fake: FakeCli, stdin=None, **env):
    result = fake.run(DEPLOY, stdin=stdin, **env)
    for value in SECRETS.values():  # no secret is ever printed
        assert value not in result.stdout and value not in result.stderr
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
    assert _invoked(fake) == [VERIFY_FN, MIGRATE_FN]
    assert "open-runs: 0" in result.stdout
    assert re.search(r"^  Modify +Database +AWS::RDS::DBInstance +False +BackupRetentionPeriod$", result.stdout, re.M)
    assert "==> Stack saferide-backend deployed" in result.stdout
    # In order: the open-run check, the build, sam deploy, the review, execution.
    order = [(call[0], call[1], call[2]) for call in fake.calls()]
    assert order.index(("aws", "lambda", "invoke")) < order.index(("sam", "build", "--use-container"))
    steps = [entry for entry in order if entry[0] == "sam" or entry[1] == "cloudformation"]
    names = [f"{tool} {a} {b}" for tool, a, b in steps]
    assert names.index("sam build --use-container") < names.index("sam deploy --no-execute-changeset") \
        < names.index("aws cloudformation describe-change-set") < names.index("aws cloudformation execute-change-set")


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
    assert "WARNING: ALLOW_OPEN_RUNS=1 — deploying although open-runs is 2." in result.stderr
    assert result.stderr.count("!" * 76) == 2
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
    refused = _deploy(fake)
    assert refused.returncode == 1
    assert f"cannot confirm that no run is open: {reason}" in refused.stderr
    _nothing_built_or_deployed(fake)
    # Every reason takes the same override branch; run it for two of them (a
    # stack without the verify function, and a verify function that fails).
    if breakage not in ("no-verify-output", "function-error"):
        return
    allowed = _deploy(fake, ALLOW_OPEN_RUNS="1")
    assert allowed.returncode == 0, allowed.stderr
    assert f"deploying although no open-run count could be read: {reason}" in allowed.stderr
    assert [call[:1] for call in fake.calls("sam")] == [["build"], ["deploy"]]
