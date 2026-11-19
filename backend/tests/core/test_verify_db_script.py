"""infra/scripts/verify-db.sh exit codes (hardening decision D24) against a
fake `aws` (tests/core/fake_cli.py) — no AWS call is ever made.

After printing the verify Lambda's answer the script exits 0 when its status
is "ok", 3 when it is not, and with --strict (first argument) 4 when a check
returned an `error` row; a FunctionError or a missing verify function exits
1, usage errors 2. The second argument travels as both `school_id` and
`timestamp`: the handler reads the one the set binds.

Hardening E4: the invoke passes --cli-read-timeout 0, and a failed `aws lambda
invoke` exits with the CLI's own code. E5: a verify function whose
DATABASE_URL host is not the stack's DbEndpoint (a scratch restore) exits 5
before anything is invoked, unless ALLOW_VERIFY_TARGET_MISMATCH=1; the URL is
never printed.
"""

import json

import pytest

from tests.core.fake_cli import (
    DB_ENDPOINT,
    SCRATCH_ENDPOINT,
    VERIFY_FN,
    VERIFY_URL_PASSWORD,
    FakeCli,
    database_url,
    verify_answer,
)

SCRIPT = "infra/scripts/verify-db.sh"
SCHOOL = "5cae0000-0000-0000-0000-000000000001"
OK_ROWS = [{"label": "migrations", "rows": [{"name": "017_account_hardening"}]}]
ERROR_ROWS = OK_ROWS + [{"label": "open-runs-and-today", "error": 'relation "live_runs" does not exist'}]


@pytest.fixture
def fake(tmp_path):
    cli = FakeCli(tmp_path, stubs=("aws",))
    cli.world["stack"] = {"status": "UPDATE_COMPLETE",
                          "outputs": {"VerifyFunctionName": VERIFY_FN, "DbEndpoint": DB_ENDPOINT}}
    cli.world["lambda_config"] = {VERIFY_FN: {"DATABASE_URL": database_url()}}
    return cli


def _answer(fake, answer):
    fake.world["lambda"] = {VERIFY_FN: answer}


def _run(fake, *args, **env):
    result = fake.run(SCRIPT, *args, **env)
    assert list(fake.tmpdir.iterdir()) == []  # the answer file is removed
    for text in (VERIFY_URL_PASSWORD, "postgresql://"):  # the verify function's URL is never printed
        assert text not in result.stdout and text not in result.stderr
    return result


def _invokes(fake):
    return [call for call in fake.calls("aws") if call[:2] == ["lambda", "invoke"]]


def _sent(fake) -> dict:
    [invoke] = [call for call in fake.calls("aws") if call[:2] == ["lambda", "invoke"]]
    assert invoke[invoke.index("--function-name") + 1] == VERIFY_FN
    assert invoke[invoke.index("--region") + 1] == "af-south-1"
    return json.loads(invoke[invoke.index("--payload") + 1])


def test_status_ok_exits_zero_and_prints_the_answer(fake):
    _answer(fake, verify_answer(OK_ROWS, checks="migrations"))
    result = _run(fake, "migrations")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"status": "ok", "checks": "migrations", "results": OK_ROWS}
    assert result.stdout.startswith('{\n    "status": "ok"')  # json.tool's layout, as before
    assert _sent(fake) == {"checks": "migrations"}


def test_a_status_other_than_ok_exits_three_after_printing(fake):
    _answer(fake, {"payload": {"status": "error", "reason": "unknown check set 'nope'",
                               "valid_check_sets": ["gps", "migrations"]}, "function_error": None})
    result = _run(fake, "nope")
    assert result.returncode == 3
    assert json.loads(result.stdout)["reason"] == "unknown check set 'nope'"
    assert "status is 'error', not 'ok': unknown check set 'nope'" in result.stderr


def test_strict_exits_four_naming_the_checks_that_returned_an_error(fake):
    _answer(fake, verify_answer(ERROR_ROWS, checks="hardening"))
    lenient = _run(fake, "hardening")
    assert lenient.returncode == 0, lenient.stderr
    strict = _run(fake, "--strict", "hardening")
    assert strict.returncode == 4
    assert json.loads(strict.stdout)["results"] == ERROR_ROWS
    assert "1 check(s) returned an error: open-runs-and-today" in strict.stderr


def test_strict_with_clean_rows_exits_zero(fake):
    _answer(fake, verify_answer(OK_ROWS, checks="migrations"))
    result = _run(fake, "--strict", "migrations")
    assert result.returncode == 0, result.stderr


def test_strict_still_reports_a_bad_status_as_three(fake):
    _answer(fake, {"payload": {"status": "error", "reason": "check set 'baseline' requires a valid school_id (uuid)"},
                   "function_error": None})
    result = _run(fake, "--strict", "baseline")
    assert result.returncode == 3


def test_a_function_error_exits_non_zero(fake):
    _answer(fake, {"payload": {"errorMessage": "boom", "errorType": "RuntimeError"}, "function_error": "Unhandled"})
    result = _run(fake, "migrations")
    assert result.returncode == 1
    assert "ERROR: verify Lambda failed (Unhandled)" in result.stderr
    assert "boom" in result.stderr


def test_a_stack_without_the_verify_function_exits_one(fake):
    del fake.world["stack"]
    result = _run(fake, "migrations")
    assert result.returncode == 1
    assert "VerifyFunctionName not found on stack saferide-backend" in result.stderr
    assert not [call for call in fake.calls("aws") if call[:2] == ["lambda", "invoke"]]


@pytest.mark.parametrize("checks,param", [
    ("baseline", SCHOOL),
    ("restore-compare", "2026-11-17T06:00:00+03:00"),
])
def test_the_parameter_is_sent_under_both_keys(fake, checks, param):
    _answer(fake, verify_answer(OK_ROWS, checks=checks))
    result = _run(fake, checks, param)
    assert result.returncode == 0, result.stderr
    assert _sent(fake) == {"checks": checks, "school_id": param, "timestamp": param}


def test_restore_compare_without_a_timestamp_is_the_lambdas_refusal(fake):
    _answer(fake, {"payload": {"status": "error", "reason": "check set 'restore-compare' requires a valid timestamp"},
                   "function_error": None})
    result = _run(fake, "restore-compare")
    assert result.returncode == 3
    assert _sent(fake) == {"checks": "restore-compare"}


def test_an_argument_cannot_break_the_payload(fake):
    _answer(fake, verify_answer(OK_ROWS, checks="baseline"))
    tricky = 'x", "checks": "gps'
    result = _run(fake, "baseline", tricky)
    assert result.returncode == 0, result.stderr
    assert _sent(fake) == {"checks": "baseline", "school_id": tricky, "timestamp": tricky}


@pytest.mark.parametrize("args", [(), ("--strict",), ("hardening", "--strict"), ("baseline", SCHOOL, "extra")])
def test_usage_errors_exit_two_without_invoking(fake, args):
    result = _run(fake, *args)
    assert result.returncode == 2
    assert "usage:" in result.stderr
    assert not [call for call in fake.calls("aws") if call[:2] == ["lambda", "invoke"]]


# --- E4: no CLI read timeout; a failed aws call exits with the CLI's code ----------------


def test_the_invoke_has_no_cli_read_timeout(fake):
    _answer(fake, verify_answer(OK_ROWS, checks="migrations"))
    result = _run(fake, "migrations")
    assert result.returncode == 0, result.stderr
    [invoke] = _invokes(fake)
    assert invoke[invoke.index("--cli-read-timeout") + 1] == "0"


def test_a_failed_invoke_call_exits_with_the_clis_code(fake):
    fake.world["lambda"] = {}  # the fake CLI answers ResourceNotFoundException, exit 254
    result = _run(fake, "migrations")
    assert result.returncode == 254
    assert "ResourceNotFoundException" in result.stderr
    assert "ERROR: aws lambda invoke failed (aws exit 254, see the AWS error above): no answer was read." in result.stderr
    assert result.stdout == ""


# --- E5: the verify function must read the stack's database -------------------------


def test_the_target_check_reads_only_the_url_of_the_verify_function(fake):
    _answer(fake, verify_answer(OK_ROWS, checks="migrations"))
    result = _run(fake, "migrations")
    assert result.returncode == 0, result.stderr
    [read] = [call for call in fake.calls("aws") if call[:2] == ["lambda", "get-function-configuration"]]
    assert read[read.index("--function-name") + 1] == VERIFY_FN
    assert read[read.index("--region") + 1] == "af-south-1"
    assert read[read.index("--query") + 1] == "Environment.Variables.DATABASE_URL"
    order = [call[:2] for call in fake.calls("aws")]
    assert order.index(["lambda", "get-function-configuration"]) < order.index(["lambda", "invoke"])


def test_a_verify_function_on_a_scratch_restore_exits_five_without_invoking(fake):
    _answer(fake, verify_answer(OK_ROWS, checks="migrations"))
    fake.world["lambda_config"][VERIFY_FN]["DATABASE_URL"] = database_url(SCRATCH_ENDPOINT)
    result = _run(fake, "migrations")
    assert result.returncode == 5
    assert f"reads database host {SCRATCH_ENDPOINT}, not the stack's DbEndpoint {DB_ENDPOINT}" in result.stderr
    assert "ALLOW_VERIFY_TARGET_MISMATCH=1 reads it anyway (a restore rehearsal)." in result.stderr
    assert result.stdout == "" and _invokes(fake) == []


def test_allow_verify_target_mismatch_reads_the_scratch_restore_loudly(fake):
    _answer(fake, verify_answer(OK_ROWS, checks="restore-compare"))
    fake.world["lambda_config"][VERIFY_FN]["DATABASE_URL"] = database_url(SCRATCH_ENDPOINT)
    result = _run(fake, "restore-compare", "2026-11-17T06:00:00+03:00", ALLOW_VERIFY_TARGET_MISMATCH="1")
    assert result.returncode == 0, result.stderr
    assert "WARNING: ALLOW_VERIFY_TARGET_MISMATCH=1 — going on although" in result.stderr
    assert json.loads(result.stdout)["status"] == "ok"  # stdout stays the answer alone
    assert len(_invokes(fake)) == 1


@pytest.mark.parametrize("breakage", ["no-config", "no-url", "no-endpoint"])
def test_an_unconfirmable_verify_target_exits_five(fake, breakage):
    _answer(fake, verify_answer(OK_ROWS, checks="migrations"))
    if breakage == "no-config":
        del fake.world["lambda_config"][VERIFY_FN]
    elif breakage == "no-url":
        fake.world["lambda_config"][VERIFY_FN] = {"APP_ENV": "production"}
    else:
        del fake.world["stack"]["outputs"]["DbEndpoint"]
    result = _run(fake, "migrations")
    assert result.returncode == 5
    assert "ERROR: the verify function does not read saferide-backend's database" in result.stderr
    assert _invokes(fake) == []
