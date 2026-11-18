"""infra/scripts/verify-db.sh exit codes (hardening decision D24) against a
fake `aws` (tests/core/fake_cli.py) — no AWS call is ever made.

After printing the verify Lambda's answer the script exits 0 when its status
is "ok", 3 when it is not, and with --strict (first argument) 4 when a check
returned an `error` row; a FunctionError or a missing verify function exits
1, usage errors 2. The second argument travels as both `school_id` and
`timestamp`: the handler reads the one the set binds.
"""

import json

import pytest

from tests.core.fake_cli import VERIFY_FN, FakeCli, verify_answer

SCRIPT = "infra/scripts/verify-db.sh"
SCHOOL = "5cae0000-0000-0000-0000-000000000001"
OK_ROWS = [{"label": "migrations", "rows": [{"name": "017_account_hardening"}]}]
ERROR_ROWS = OK_ROWS + [{"label": "open-runs-and-today", "error": 'relation "live_runs" does not exist'}]


@pytest.fixture
def fake(tmp_path):
    cli = FakeCli(tmp_path, stubs=("aws",))
    cli.world["stack"] = {"status": "UPDATE_COMPLETE", "outputs": {"VerifyFunctionName": VERIFY_FN}}
    return cli


def _answer(fake, answer):
    fake.world["lambda"] = {VERIFY_FN: answer}


def _run(fake, *args):
    result = fake.run(SCRIPT, *args)
    assert list(fake.tmpdir.iterdir()) == []  # the answer file is removed
    return result


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
