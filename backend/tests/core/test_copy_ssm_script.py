"""infra/scripts/copy-ssm-to-backend-region.sh against a fake `aws` (hardening
plan U1 step 4, decision D15) — no AWS call is ever made.

A stub named `aws` goes first on PATH. It serves `ssm get-parameter` from a
fixture directory per region, applies `ssm put-parameter --cli-input-json
file://…` to that directory, answers `configure get region`, and records the
argv of every call plus, for each put, the input file's keys and permissions
(never its value). The tests pin the contract the operator checklist relies
on: report mode never writes; --apply creates only what the target lacks; a
mismatch or a read error stops --apply before any write; no secret value
reaches an argv or the script's output; the private temporary directory is
gone afterwards; exit 0 only when every parameter ends MATCH.
"""

import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "infra" / "scripts" / "copy-ssm-to-backend-region.sh"
# The operator's shell on macOS is /bin/bash 3.2; pin it where it exists.
BASH = "/bin/bash" if Path("/bin/bash").exists() else shutil.which("bash")

NAMES = [
    "/saferide/db-password",
    "/saferide/pin-pepper",
    "/saferide/db-app-password",
    "/saferide/totp-pepper",
    "/saferide/google-maps-api-key",
]
SOURCE = "eu-central-1"
TARGET = "af-south-1"
# Values chosen to break naive quoting: quotes, $, backticks, backslashes,
# spaces and non-ASCII. None of them may ever appear in an argv or the output.
VALUES = {
    "/saferide/db-password": "db-Pa55 'single' \"double\" $HOME `id` \\n end",
    "/saferide/pin-pepper": "pepper-ünïcødé-✓-0001",
    "/saferide/db-app-password": "app-role-password-{json:like}-0002",
    "/saferide/totp-pepper": "totp-pepper-$(echo nope)-0003\nsecond line \t ",
    "/saferide/google-maps-api-key": "AIzaFAKEKEYFORTESTS0004",
}
OTHER = "a-different-value-0005"

STUB = r'''#!/usr/bin/env python3
import json, os, signal, stat, sys

# The signal test sends SIGQUIT to the whole process group, this stub
# included. SIGQUIT's default action dumps core, which on macOS raises a
# "Python quit unexpectedly" dialog; exit with the shell's status instead.
signal.signal(signal.SIGQUIT, lambda sig, frame: os._exit(128 + sig))

root = os.environ["FAKE_AWS_ROOT"]
args = sys.argv[1:]
with open(os.path.join(root, "calls.jsonl"), "a", encoding="utf-8") as log:
    log.write(json.dumps(args) + "\n")


def opt(flag):
    return args[args.index(flag) + 1] if flag in args else None


def slot(region, name):
    return os.path.join(root, "ssm", region, name.strip("/").replace("/", "__"))


if args[:3] == ["configure", "get", "region"]:
    region = os.environ.get("FAKE_AWS_CONFIGURED_REGION", "")
    if region:
        print(region)
        sys.exit(0)
    sys.exit(1)

if args[:3] == ["configure", "get", "cli_history"]:
    if os.environ.get("FAKE_AWS_CLI_HISTORY"):
        print(os.environ["FAKE_AWS_CLI_HISTORY"])
        sys.exit(0)
    sys.exit(1)

if args[:2] == ["ssm", "get-parameter"]:
    name, region = opt("--name"), opt("--region")
    if "--with-decryption" not in args or opt("--output") != "json":
        sys.exit("fake aws: get-parameter without --with-decryption --output json")
    path = slot(region, name)
    if os.path.exists(path + ".denied"):
        sys.stderr.write("\nAn error occurred (AccessDeniedException) when calling the GetParameter"
                         " operation: not authorized to perform ssm:GetParameter\n")
        sys.exit(254)
    if not os.path.exists(path):
        sys.stderr.write("\nAn error occurred (ParameterNotFound) when calling the GetParameter"
                         " operation: \n")
        sys.exit(254)
    with open(path, encoding="utf-8") as handle:
        value = handle.read()
    json.dump({"Parameter": {"Name": name, "Type": "SecureString", "Value": value, "Version": 1,
                             "ARN": "arn:aws:ssm:%s:000000000000:parameter%s" % (region, name),
                             "DataType": "text"}}, sys.stdout, indent=4)
    sys.exit(0)

if args[:2] == ["ssm", "put-parameter"]:
    region, source = opt("--region"), opt("--cli-input-json") or ""
    if not source.startswith("file://") or "--value" in args:
        sys.exit("fake aws: put-parameter must read its input from a file")
    path = source[len("file://"):]
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    record = {
        "region": region, "name": data.get("Name"), "keys": sorted(data), "type": data.get("Type"),
        "file": path, "file_mode": stat.S_IMODE(os.stat(path).st_mode),
        "dir_mode": stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode),
    }
    with open(os.path.join(root, "puts.jsonl"), "a", encoding="utf-8") as log:
        log.write(json.dumps(record) + "\n")
    target = slot(region, data["Name"])
    if os.environ.get("FAKE_AWS_PUT_RACE"):  # someone else created it meanwhile
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(os.environ["FAKE_AWS_PUT_RACE"])
    if os.environ.get("FAKE_AWS_PUT_SLEEP"):  # hold the put open for a signal test
        open(os.path.join(root, "put-started"), "w").close()
        import time
        time.sleep(float(os.environ["FAKE_AWS_PUT_SLEEP"]))
    if os.path.exists(target) and not data.get("Overwrite"):
        sys.stderr.write("\nAn error occurred (ParameterAlreadyExists) when calling the PutParameter"
                         " operation: The parameter already exists.\n")
        sys.exit(254)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as handle:
        handle.write(data["Value"])
    print(json.dumps({"Version": 1, "Tier": "Standard"}))
    sys.exit(0)

sys.stderr.write("fake aws: unsupported call %r\n" % (args,))
sys.exit(2)
'''


class FakeAws:
    def __init__(self, tmp_path: Path):
        self.root = tmp_path / "aws"
        self.bin = tmp_path / "bin"
        self.tmpdir = tmp_path / "tmp"
        for directory in (self.root, self.bin, self.tmpdir):
            directory.mkdir()
        stub = self.bin / "aws"
        stub.write_text(STUB, encoding="utf-8")
        stub.chmod(0o755)

    def _slot(self, region: str, name: str) -> Path:
        return self.root / "ssm" / region / name.strip("/").replace("/", "__")

    def put(self, region: str, name: str, value: str) -> None:
        path = self._slot(region, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")

    def deny(self, region: str, name: str) -> None:
        path = self._slot(region, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.with_name(path.name + ".denied").write_text("", encoding="utf-8")

    def value(self, region: str, name: str) -> str | None:
        path = self._slot(region, name)
        return path.read_text(encoding="utf-8") if path.exists() else None

    def calls(self) -> list[list[str]]:
        log = self.root / "calls.jsonl"
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]

    def puts(self) -> list[dict]:
        log = self.root / "puts.jsonl"
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]

    def _env(self, configured_region: str, env_extra: dict[str, str]) -> dict[str, str]:
        env = {
            key: value for key, value in os.environ.items()
            if not key.startswith("AWS_") and key not in ("BACKEND_REGION", "SOURCE_REGION")
        }
        env.update(
            PATH=f"{self.bin}{os.pathsep}{env.get('PATH', '')}",
            FAKE_AWS_ROOT=str(self.root),
            FAKE_AWS_CONFIGURED_REGION=configured_region,
            TMPDIR=str(self.tmpdir),
            **env_extra,
        )
        return env

    def run(self, *args: str, configured_region: str = SOURCE, xtrace: bool = False, **env_extra: str):
        # With xtrace, bash prints every command and expansion on stderr, so
        # the leak check below also covers shell variables and every argv.
        command = [BASH, *(["-x"] if xtrace else []), str(SCRIPT), *args]
        result = subprocess.run(
            command, env=self._env(configured_region, env_extra),
            capture_output=True, text=True, timeout=120,
        )
        self._assert_no_leak(result)
        # The private temporary directory is removed on every exit path.
        assert list(self.tmpdir.iterdir()) == [], list(self.tmpdir.iterdir())
        return result

    def start(self, *args: str, **env_extra: str) -> subprocess.Popen:
        return subprocess.Popen(
            [BASH, str(SCRIPT), *args], env=self._env(SOURCE, env_extra),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True,
            preexec_fn=_default_signal_dispositions,
        )

    def _assert_no_leak(self, result) -> None:
        argv_text = (self.root / "calls.jsonl").read_text(encoding="utf-8") \
            if (self.root / "calls.jsonl").exists() else ""
        for secret in [*VALUES.values(), OTHER]:
            assert secret not in argv_text, "a value reached an aws argv"
            assert json.dumps(secret)[1:-1] not in argv_text, "a value reached an aws argv"
            assert secret not in result.stdout and secret not in result.stderr, "a value was printed"


def _default_signal_dispositions() -> None:
    """Give the script default handlers for the signals under test.

    A signal that is ignored when a shell starts cannot be trapped, and that
    disposition is inherited: under `nohup` (HUP ignored) or as a background
    job of a non-interactive shell (INT and QUIT ignored) the script's trap
    would never run and the signal tests would fail for reasons unrelated to
    the script. Resetting them in the child keeps the tests about the script,
    however the suite itself was launched.
    """
    for sig in (signal.SIGHUP, signal.SIGINT, signal.SIGQUIT, signal.SIGTERM):
        signal.signal(sig, signal.SIG_DFL)


def _status_lines(stdout: str) -> dict[str, str]:
    """The last status line printed per parameter name."""
    last = {}
    for line in stdout.splitlines():
        name, sep, status = line.partition(": ")
        if sep and name in NAMES:
            last[name] = status
    return last


@pytest.fixture
def aws(tmp_path):
    return FakeAws(tmp_path)


def _seed(aws: FakeAws, target_names=NAMES, source_names=NAMES):
    for name in source_names:
        aws.put(SOURCE, name, VALUES[name])
    for name in target_names:
        aws.put(TARGET, name, VALUES[name])


def test_all_matching_parameters_exit_zero_with_five_match_lines(aws):
    _seed(aws)
    result = aws.run()
    assert result.returncode == 0, result.stderr
    assert _status_lines(result.stdout) == {name: "present in both, values MATCH" for name in NAMES}
    assert f"source {SOURCE} -> target {TARGET}" in result.stdout
    assert "All 5 parameters MATCH" in result.stdout
    assert aws.puts() == []


def test_report_mode_is_read_only(aws):
    _seed(aws, target_names=NAMES[:3])
    result = aws.run()
    assert result.returncode == 1
    statuses = _status_lines(result.stdout)
    assert statuses == {
        **{name: "present in both, values MATCH" for name in NAMES[:3]},
        **{name: f"missing in {TARGET}" for name in NAMES[3:]},
    }
    assert "--apply" in result.stderr
    assert aws.puts() == []
    assert {tuple(call[:2]) for call in aws.calls()} <= {("configure", "get"), ("ssm", "get-parameter")}
    for name in NAMES[3:]:
        assert aws.value(TARGET, name) is None


def test_apply_copies_only_the_parameters_missing_in_the_target(aws):
    _seed(aws, target_names=NAMES[:3])
    result = aws.run("--apply")
    assert result.returncode == 0, result.stderr
    puts = aws.puts()
    assert [put["name"] for put in puts] == NAMES[3:]
    for put in puts:
        assert put["region"] == TARGET
        assert put["keys"] == ["Name", "Type", "Value"]  # no Overwrite
        assert put["type"] == "SecureString"
        assert put["file_mode"] == 0o600
        assert put["dir_mode"] == 0o700
        assert not Path(put["file"]).exists()
        assert not Path(put["file"]).parent.exists()
    put_calls = [call for call in aws.calls() if call[:2] == ["ssm", "put-parameter"]]
    assert len(put_calls) == 2
    for call in put_calls:
        assert call[call.index("--cli-input-json") + 1].startswith("file://")
        assert "--overwrite" not in call
    for name in NAMES:
        assert aws.value(TARGET, name) == VALUES[name]
    assert _status_lines(result.stdout) == {name: "present in both, values MATCH" for name in NAMES}
    for name in NAMES[3:]:
        assert f"Copied {name} to {TARGET}" in result.stdout


def test_a_mismatch_aborts_apply_before_anything_is_written(aws):
    _seed(aws, target_names=NAMES[:4])
    aws.put(TARGET, NAMES[1], OTHER)
    result = aws.run("--apply")
    assert result.returncode == 1
    statuses = _status_lines(result.stdout)
    assert statuses[NAMES[1]] == "present in both, values MISMATCH"
    assert statuses[NAMES[4]] == f"missing in {TARGET}"
    assert "Resolve it by hand" in result.stderr
    assert "nothing was written" in result.stderr
    assert aws.puts() == []
    assert aws.value(TARGET, NAMES[1]) == OTHER
    assert aws.value(TARGET, NAMES[4]) is None


def test_a_mismatch_fails_report_mode_too(aws):
    _seed(aws)
    aws.put(TARGET, NAMES[0], OTHER)
    result = aws.run()
    assert result.returncode == 1
    assert _status_lines(result.stdout)[NAMES[0]] == "present in both, values MISMATCH"


def test_missing_in_the_source_is_reported_and_never_copied_backwards(aws):
    _seed(aws, source_names=NAMES[:4])
    for args in ((), ("--apply",)):
        result = aws.run(*args)
        assert result.returncode == 1
        assert _status_lines(result.stdout)[NAMES[4]] == f"missing in {SOURCE}"
    assert aws.puts() == []
    assert aws.value(SOURCE, NAMES[4]) is None


def test_missing_in_both_regions_is_named(aws):
    _seed(aws, source_names=NAMES[1:], target_names=NAMES[1:])
    result = aws.run()
    assert result.returncode == 1
    assert _status_lines(result.stdout)[NAMES[0]] == f"missing in {SOURCE} and {TARGET}"


def test_a_read_error_is_not_mistaken_for_a_missing_parameter(aws):
    _seed(aws, target_names=NAMES[:4])
    aws.deny(TARGET, NAMES[4])
    result = aws.run("--apply")
    assert result.returncode == 1
    status = _status_lines(result.stdout)[NAMES[4]]
    assert status.startswith(f"could not read in {TARGET}: ")
    assert "AccessDeniedException" in status
    assert aws.puts() == []


def test_source_region_defaults_to_the_configured_region_and_can_be_overridden(aws):
    for name in NAMES:
        aws.put("eu-west-1", name, VALUES[name])
        aws.put(TARGET, name, VALUES[name])
    configured = aws.run(configured_region="eu-west-1")
    assert configured.returncode == 0, configured.stderr
    assert "source eu-west-1 -> target af-south-1" in configured.stdout
    assert "source region from aws configure get region" in configured.stdout
    explicit = aws.run(configured_region=SOURCE, SOURCE_REGION="eu-west-1")
    assert explicit.returncode == 0, explicit.stderr
    assert "source eu-west-1 -> target af-south-1" in explicit.stdout
    assert "source region from SOURCE_REGION" in explicit.stdout


@pytest.mark.parametrize(
    "env,origin",
    [
        ({"AWS_DEFAULT_REGION": "eu-west-1"}, "AWS_DEFAULT_REGION"),
        ({"AWS_REGION": "eu-west-1", "AWS_DEFAULT_REGION": "eu-north-1"}, "AWS_REGION"),
        ({"SOURCE_REGION": "eu-west-1", "AWS_REGION": "eu-north-1"}, "SOURCE_REGION"),
    ],
)
def test_the_default_source_is_the_region_an_unregioned_cli_call_uses(aws, env, origin):
    """release/0's deploy read the five secrets with no --region, so the CLI
    resolved AWS_REGION, then AWS_DEFAULT_REGION, then the config file: the
    copy helper compares that same region unless SOURCE_REGION says otherwise."""
    for name in NAMES:
        aws.put("eu-west-1", name, VALUES[name])
        aws.put(TARGET, name, VALUES[name])
    result = aws.run(configured_region=SOURCE, **env)
    assert result.returncode == 0, result.stderr
    assert "source eu-west-1 -> target af-south-1" in result.stdout
    assert f"source region from {origin}" in result.stdout


@pytest.mark.parametrize(
    "args,env,expected",
    [
        (("--force",), {}, "usage"),
        (("--apply", "extra"), {}, "usage"),
        ((), {"SOURCE_REGION": TARGET}, "nothing to compare"),
        ((), {"FAKE_AWS_CONFIGURED_REGION": ""}, "set SOURCE_REGION"),
    ],
)
def test_usage_errors_exit_two_without_calling_ssm(aws, args, env, expected):
    configured = env.pop("FAKE_AWS_CONFIGURED_REGION", SOURCE)
    result = aws.run(*args, configured_region=configured, **env)
    assert result.returncode == 2
    assert expected in result.stderr
    assert not [call for call in aws.calls() if call[:1] == ["ssm"]]


def test_xtrace_shows_no_value_in_any_variable_or_argv(aws):
    """bash -x prints every expansion and every command line: no value may
    appear there, so none passed through a shell variable or an argv."""
    _seed(aws, target_names=NAMES[:2])
    result = aws.run("--apply", xtrace=True)
    assert result.returncode == 0, result.stderr
    assert "+ " in result.stderr  # the trace really ran
    assert len(aws.puts()) == 3


def test_an_enabled_cli_history_refuses_before_any_read(aws):
    _seed(aws)
    result = aws.run("--apply", FAKE_AWS_CLI_HISTORY="enabled")
    assert result.returncode == 2
    assert "cli_history" in result.stderr
    assert not [call for call in aws.calls() if call[:1] == ["ssm"]]


def test_a_source_read_error_blocks_apply(aws):
    _seed(aws, target_names=NAMES[:4])
    aws.deny(SOURCE, NAMES[0])
    result = aws.run("--apply")
    assert result.returncode == 1
    status = _status_lines(result.stdout)[NAMES[0]]
    assert status.startswith(f"could not read in {SOURCE}: An error occurred (AccessDeniedException)")
    assert aws.puts() == []
    assert aws.value(TARGET, NAMES[4]) is None


def test_a_put_that_loses_a_race_fails_and_overwrites_nothing(aws):
    """If the name appears in the target between the check and the put,
    put-parameter (no Overwrite) refuses and the script stops."""
    _seed(aws, target_names=NAMES[:4])
    result = aws.run("--apply", FAKE_AWS_PUT_RACE=OTHER)
    assert result.returncode == 1
    assert "put-parameter /saferide/google-maps-api-key (af-south-1) failed" in result.stderr
    assert "ParameterAlreadyExists" in result.stderr
    assert aws.value(TARGET, NAMES[4]) == OTHER


def test_apply_copies_what_it_can_and_still_fails_on_a_parameter_missing_in_the_source(aws):
    _seed(aws, source_names=NAMES[:4], target_names=NAMES[1:])
    result = aws.run("--apply")
    assert result.returncode == 1
    assert [put["name"] for put in aws.puts()] == [NAMES[0]]
    statuses = _status_lines(result.stdout)
    assert statuses[NAMES[0]] == "present in both, values MATCH"
    assert statuses[NAMES[4]] == f"missing in {SOURCE}"
    assert aws.value(TARGET, NAMES[0]) == VALUES[NAMES[0]]


@pytest.mark.parametrize(
    "sig,code",
    [(signal.SIGHUP, 129), (signal.SIGINT, 130), (signal.SIGQUIT, 131), (signal.SIGTERM, 143)],
)
def test_a_signal_during_a_put_still_removes_the_temporary_directory(aws, sig, code):
    """The put input (a plaintext value) exists only while put-parameter runs;
    a signal at exactly that moment must still remove it."""
    _seed(aws, target_names=NAMES[:4])
    proc = aws.start("--apply", FAKE_AWS_PUT_SLEEP="30")
    try:
        deadline = time.monotonic() + 30
        while not (aws.root / "put-started").exists():
            assert proc.poll() is None, proc.communicate()
            assert time.monotonic() < deadline, "the put never started"
            time.sleep(0.05)
        assert list(aws.tmpdir.glob("saferide-ssm-copy.*/put.*")), "the put file should exist now"
        os.killpg(proc.pid, sig)
        stdout, stderr = proc.communicate(timeout=30)
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
    assert proc.returncode == code, stderr
    assert list(aws.tmpdir.iterdir()) == []
    aws._assert_no_leak(SimpleNamespace(stdout=stdout, stderr=stderr))
