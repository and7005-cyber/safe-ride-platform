"""The deploy guards in infra/scripts/config.sh, deploy-backend.sh and
scripts/provider-bootstrap.sh (hardening Release 1a, U1/U2/U3).

- ssm_lookup / ssm_required against a fake `aws`: only $BACKEND_REGION is
  read; a miss, a read error, "None" and empty output all fail without
  printing a value, and a failed `VAR="$(ssm_required x)"` aborts `set -e`.
- require_clean_checkout in a real temporary repository with a local bare
  origin: modified and staged files fail, origin/main and release/* tags
  pass, anything else fails unless ALLOW_UNRELEASED_DEPLOY=1 (which never
  waives a dirty tree).
- deploy-backend.sh never mints a secret, reads every secret before
  `sam build`, seeds a missing push parameter from backend/.env through a
  0600 file (never an argv), and aborts on any read error that is not
  ParameterNotFound.
- provider-bootstrap.sh refuses to mint a TOTP pepper while a default-region
  copy exists or while either read fails for another reason.
No AWS call is ever made (tests/core/fake_cli.py).
"""

import base64
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.core.fake_cli import (
    BACKEND_REGION,
    BASH,
    MIGRATE_FN,
    SECRETS,
    FakeCli,
    backend_deploy_world,
)

REPO = Path(__file__).resolve().parents[3]
SCRIPTS = REPO / "infra" / "scripts"
DEPLOY_BACKEND = (SCRIPTS / "deploy-backend.sh").read_text(encoding="utf-8")
NAME = "/saferide/db-password"
VALUE = "va'l\"ue $HOME `id` $(echo no)  spaced"


@pytest.fixture
def fake(tmp_path):
    return FakeCli(tmp_path)


def _config(fake: FakeCli) -> str:
    return str(fake.repo / "infra" / "scripts" / "config.sh")


def _ssm_required(fake: FakeCli, *args: str, **env: str):
    result = fake.bash('set -euo pipefail; source "$1"; shift; ssm_required "$@"', _config(fake), *args, **env)
    assert list(fake.tmpdir.iterdir()) == []  # its stderr capture is removed
    return result


def _get_parameter_regions(fake: FakeCli) -> list[str | None]:
    calls = [call for call in fake.calls("aws") if call[:2] == ["ssm", "get-parameter"]]
    return [call[call.index("--region") + 1] if "--region" in call else None for call in calls]


# --- ssm_required / ssm_lookup ----------------------------------------------------


def test_ssm_required_prints_exactly_the_value_and_nothing_else(fake):
    fake.put(BACKEND_REGION, NAME, VALUE)
    result = _ssm_required(fake, NAME)
    assert result.returncode == 0, result.stderr
    assert result.stdout == VALUE
    assert result.stderr == ""


@pytest.mark.parametrize("stored,message", [
    (None, f"ERROR: SSM {NAME} (af-south-1) is missing — copy or create it in af-south-1"),
    ({"error": "AccessDeniedException"}, f"ERROR: could not read SSM {NAME} (af-south-1): "),
    ("None", f"ERROR: SSM {NAME} (af-south-1) is missing"),
    ("", f"ERROR: SSM {NAME} (af-south-1) is missing"),
], ids=["parameter-not-found", "access-denied", "none-output", "empty-output"])
def test_ssm_required_fails_without_printing_a_value(fake, stored, message):
    if stored is not None:
        fake.put(BACKEND_REGION, NAME, stored)
    fake.put("eu-central-1", NAME, VALUE)  # a default-region copy is never consulted
    result = _ssm_required(fake, NAME)
    assert result.returncode == 1
    assert result.stdout == ""
    assert message in result.stderr
    if isinstance(stored, dict):
        assert "AccessDeniedException" in result.stderr
    assert VALUE not in result.stderr
    assert _get_parameter_regions(fake) == [BACKEND_REGION]


def test_ssm_required_always_reads_the_backend_region(fake):
    fake.put(BACKEND_REGION, NAME, VALUE)
    fake.put("eu-west-1", NAME, "the-old-default-region-copy")
    # The CLI's own region settings and a stale second argument change nothing.
    result = _ssm_required(fake, NAME, "eu-west-1", AWS_REGION="eu-west-1", AWS_DEFAULT_REGION="eu-north-1")
    assert result.returncode == 0, result.stderr
    assert result.stdout == VALUE
    assert _get_parameter_regions(fake) == [BACKEND_REGION]
    [call] = [call for call in fake.calls("aws") if call[:2] == ["ssm", "get-parameter"]]
    assert "--with-decryption" in call


def test_a_failed_ssm_required_assignment_aborts_a_set_e_script(fake):
    script = 'set -euo pipefail; source "$1"; X="$(ssm_required "$2")"; echo "reached: $X"'
    result = fake.bash(script, _config(fake), NAME)
    assert result.returncode == 1
    assert "reached" not in result.stdout
    assert "is missing" in result.stderr


@pytest.mark.parametrize("stored,code", [(VALUE, 0), (None, 3), ({"error": "ThrottlingException"}, 1)],
                         ids=["found", "missing", "read-error"])
def test_ssm_lookup_tells_a_miss_from_a_read_error(fake, stored, code):
    if stored is not None:
        fake.put(BACKEND_REGION, NAME, stored)
    result = fake.bash('source "$1"; ssm_lookup "$2"', _config(fake), NAME)
    assert result.returncode == code
    assert result.stdout == (VALUE if code == 0 else "")
    assert ("could not read SSM" in result.stderr) == (code == 1)


# --- require_clean_checkout --------------------------------------------------------


class Checkout:
    """A real repository whose origin is a local bare repository."""

    def __init__(self, tmp_path: Path):
        self.env = {
            "PATH": f"{Path(shutil.which('git')).parent}{os.pathsep}/usr/bin:/bin",
            "HOME": str(tmp_path),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.org",
            "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.org",
        }
        self.origin = tmp_path / "origin.git"
        self.work = tmp_path / "work"
        self.git("init", "-q", "--bare", "-b", "main", str(self.origin), cwd=tmp_path)
        self.git("init", "-q", "-b", "main", str(self.work), cwd=tmp_path)
        config = self.work / "infra" / "scripts" / "config.sh"
        config.parent.mkdir(parents=True)
        shutil.copy2(SCRIPTS / "config.sh", config)
        self.commit("release one")
        self.git("remote", "add", "origin", str(self.origin))
        self.git("push", "-q", "origin", "main")

    def git(self, *args: str, cwd: Path | None = None) -> str:
        return subprocess.run(["git", *args], cwd=cwd or self.work, env=self.env, check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit(self, message: str) -> None:
        (self.work / "CHANGELOG").write_text(message + "\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def check(self, **env: str):
        return subprocess.run(
            [BASH, "-c", 'set -euo pipefail; source "$1"; require_clean_checkout',
             "bash", str(self.work / "infra" / "scripts" / "config.sh")],
            env={**self.env, **env}, capture_output=True, text=True, timeout=60,
        )

    @property
    def head(self) -> str:
        return self.git("rev-parse", "--short", "HEAD")


@pytest.fixture
def checkout(tmp_path):
    return Checkout(tmp_path)


def test_origin_main_passes_and_untracked_files_are_ignored(checkout):
    (checkout.work / "justfile").write_text("untracked on purpose\n", encoding="utf-8")
    result = checkout.check()
    assert result.returncode == 0, result.stderr
    assert f"==> Clean checkout: HEAD {checkout.head} is origin/main" in result.stdout


@pytest.mark.parametrize("stage", [False, True], ids=["modified", "staged"])
def test_a_modified_or_staged_tracked_file_fails(checkout, stage):
    (checkout.work / "CHANGELOG").write_text("edited\n", encoding="utf-8")
    if stage:
        checkout.git("add", "CHANGELOG")
    result = checkout.check()
    assert result.returncode == 1
    assert "ERROR: modified or staged tracked files" in result.stderr
    assert "CHANGELOG" in result.stderr


def test_a_release_tag_passes(checkout):
    checkout.commit("release two")
    checkout.git("tag", "release/1a")
    result = checkout.check()
    assert result.returncode == 0, result.stderr
    assert f"==> Clean checkout: HEAD {checkout.head} is tagged release/1a" in result.stdout


@pytest.mark.parametrize("tag", [None, "v2", "releases/2"])
def test_an_unreleased_commit_fails(checkout, tag):
    checkout.commit("not released")
    if tag:
        checkout.git("tag", tag)
    result = checkout.check()
    assert result.returncode == 1
    assert f"ERROR: HEAD {checkout.head} on main is neither origin/main nor a release/* tag." in result.stderr


def test_allow_unreleased_deploy_passes_with_a_warning(checkout):
    checkout.commit("hotfix")
    result = checkout.check(ALLOW_UNRELEASED_DEPLOY="1")
    assert result.returncode == 0, result.stderr
    assert f"WARNING: ALLOW_UNRELEASED_DEPLOY=1 — deploying {checkout.head} on main" in result.stderr


def test_allow_unreleased_deploy_never_waives_a_dirty_tree(checkout):
    checkout.commit("hotfix")
    (checkout.work / "CHANGELOG").write_text("edited\n", encoding="utf-8")
    result = checkout.check(ALLOW_UNRELEASED_DEPLOY="1")
    assert result.returncode == 1
    assert "ERROR: modified or staged tracked files" in result.stderr
    assert "WARNING" not in result.stderr


def test_an_unreachable_origin_fails(checkout):
    checkout.git("remote", "set-url", "origin", str(checkout.origin.parent / "gone.git"))
    result = checkout.check()
    assert result.returncode == 1
    assert "ERROR: git fetch origin failed" in result.stderr


# --- deploy-backend.sh: secrets ---------------------------------------------------


def test_deploy_backend_never_mints_and_reads_every_secret_before_the_build():
    assert "openssl rand" not in DEPLOY_BACKEND
    assert "--value" not in DEPLOY_BACKEND  # a seeded value travels in a 0600 file
    build = DEPLOY_BACKEND.index("\nsam build --use-container\n")
    reads = [match.start() for match in re.finditer(r"\$\((ssm_required|ssm_seeded) ", DEPLOY_BACKEND)]
    assert len(reads) == 12  # five secrets, the provider bootstrap, six push parameters
    assert all(read < build for read in reads)


def _deploy(fake: FakeCli, **env):
    result = fake.run("infra/scripts/deploy-backend.sh", **env)
    assert list(fake.tmpdir.iterdir()) == []  # the seeding directory is gone
    return result


@pytest.fixture
def deploy(fake):
    backend_deploy_world(fake)
    return fake


@pytest.mark.parametrize("name", list(SECRETS) + ["/saferide/provider-bootstrap"])
def test_a_missing_required_parameter_stops_the_deploy_before_anything_is_built(deploy, name):
    del deploy.world["ssm"][BACKEND_REGION][name]
    result = _deploy(deploy)
    assert result.returncode == 1
    assert f"ERROR: SSM {name} (af-south-1) is missing" in result.stderr
    if name == "/saferide/provider-bootstrap":
        assert "create it with scripts/provider-bootstrap.sh" in result.stderr
    assert deploy.calls("sam") == [] and deploy.puts() == []


def test_an_unreadable_provider_bootstrap_is_not_called_missing(deploy):
    deploy.deny(BACKEND_REGION, "/saferide/provider-bootstrap")
    result = _deploy(deploy)
    assert result.returncode == 1
    assert "ERROR: could not read SSM /saferide/provider-bootstrap (af-south-1)" in result.stderr
    assert deploy.calls("sam") == []


ENV_FILE = """\
# push secrets, provisioned once
FIREBASE_SERVICE_ACCOUNT_JSON={"type": "service_account", "private_key": "-----BEGIN KEY-----\\nabc\\n-----END KEY-----\\n"}
VAPID_PUBLIC_KEY=BPublicKeyValue0007
VAPID_PRIVATE_KEY=private-'quoted'-$HOME-`id`-0008
VAPID_PRIVATE_KEY=a-second-definition-is-ignored
VAPID_SUBJECT=
"""
SEEDED = {
    "/saferide/firebase-service-account-json":
        '{"type": "service_account", "private_key": "-----BEGIN KEY-----\\nabc\\n-----END KEY-----\\n"}',
    "/saferide/vapid-public-key": "BPublicKeyValue0007",
    "/saferide/vapid-private-key": "private-'quoted'-$HOME-`id`-0008",
}


def test_a_missing_push_parameter_is_seeded_from_env_through_a_private_file(deploy):
    (deploy.repo / "backend" / ".env").write_text(ENV_FILE, encoding="utf-8")
    result = _deploy(deploy)
    assert result.returncode == 0, result.stderr
    puts = deploy.puts()
    assert [put["name"] for put in puts] == list(SEEDED)
    for put in puts:
        assert put["via"] == "file" and put["region"] == BACKEND_REGION
        assert put["keys"] == ["Name", "Type", "Value"]  # no Overwrite
        assert put["type"] == "SecureString"
        assert put["file_mode"] == 0o600 and put["dir_mode"] == 0o700
        assert not Path(put["file"]).parent.exists()
    for name, value in SEEDED.items():
        assert deploy.ssm_value(BACKEND_REGION, name) == value
        assert f"==> Seeded SSM {name} (af-south-1) from backend/.env" in result.stderr
    # Never in an aws argv, never printed.
    aws_argv = json.dumps(deploy.calls("aws"))
    for value in SEEDED.values():
        assert value not in aws_argv and json.dumps(value)[1:-1] not in aws_argv
        assert value not in result.stdout and value not in result.stderr
    # Each seeded parameter is read back, and the stack gets what SSM holds.
    reads = [call[call.index("--name") + 1] for call in deploy.calls("aws") if call[:2] == ["ssm", "get-parameter"]]
    for name in SEEDED:
        assert reads.count(name) == 2
    [sam_deploy] = [call for call in deploy.calls("sam") if call[:1] == ["deploy"]]
    assert "VapidPublicKey=BPublicKeyValue0007" in sam_deploy
    assert f"VapidPrivateKey={SEEDED['/saferide/vapid-private-key']}" in sam_deploy
    assert not [arg for arg in sam_deploy if arg.startswith(("VapidSubject=", "FirebaseVapidKey="))]


def test_a_push_parameter_already_in_ssm_wins_over_env(deploy):
    (deploy.repo / "backend" / ".env").write_text("VAPID_PRIVATE_KEY=from-env\n", encoding="utf-8")
    deploy.put(BACKEND_REGION, "/saferide/vapid-private-key", "from-ssm")
    result = _deploy(deploy)
    assert result.returncode == 0, result.stderr
    assert deploy.puts() == []
    [sam_deploy] = [call for call in deploy.calls("sam") if call[:1] == ["deploy"]]
    assert "VapidPrivateKey=from-ssm" in sam_deploy


def test_a_push_parameter_read_error_aborts_instead_of_seeding(deploy):
    (deploy.repo / "backend" / ".env").write_text(ENV_FILE, encoding="utf-8")
    deploy.deny(BACKEND_REGION, "/saferide/vapid-public-key")
    result = _deploy(deploy)
    assert result.returncode == 1
    assert "ERROR: could not read SSM /saferide/vapid-public-key (af-south-1)" in result.stderr
    assert "/saferide/vapid-public-key" not in [put["name"] for put in deploy.puts()]
    assert deploy.calls("sam") == []
    assert MIGRATE_FN not in json.dumps(deploy.calls("aws"))


# --- provider-bootstrap.sh -----------------------------------------------------------

PEPPER = "/saferide/totp-pepper"
FAKE_VENV_PYTHON = """#!/bin/sh
case "$2" in *hash_password*) echo 'fake-password-hash'; exit 0 ;; esac
exec python3 "$@"
"""


@pytest.fixture
def bootstrap(fake):
    python = fake.repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(FAKE_VENV_PYTHON, encoding="utf-8")
    python.chmod(0o755)
    return fake


def _bootstrap(fake: FakeCli, **env):
    return fake.run("scripts/provider-bootstrap.sh", "ops-one@example.org", "Ops One",
                    "ops-two@example.org", "Ops Two", **env)


def test_bootstrap_refuses_to_mint_while_a_default_region_pepper_exists(bootstrap):
    bootstrap.put("eu-central-1", PEPPER, "the-pre-hardening-pepper")
    result = _bootstrap(bootstrap)
    assert result.returncode == 1
    assert f"ERROR: {PEPPER} exists in the default CLI region but not in af-south-1." in result.stderr
    assert "do not mint a new one" in result.stderr
    assert bootstrap.puts() == []
    assert _get_parameter_regions(bootstrap) == [BACKEND_REGION, None]  # the second read is un-regioned


def test_bootstrap_does_not_mint_when_the_backend_region_read_fails(bootstrap):
    bootstrap.deny(BACKEND_REGION, PEPPER)
    result = _bootstrap(bootstrap)
    assert result.returncode == 1
    assert f"ERROR: could not read SSM {PEPPER} (af-south-1)" in result.stderr
    assert f"cannot tell whether {PEPPER} exists in af-south-1 — not minting one" in result.stderr
    assert bootstrap.puts() == []
    assert _get_parameter_regions(bootstrap) == [BACKEND_REGION]


@pytest.mark.parametrize("default_region,fragment", [
    ("eu-central-1", "AccessDeniedException"),
    (None, "You must specify a region"),
], ids=["access-denied", "no-default-region"])
def test_bootstrap_does_not_mint_when_the_default_region_read_fails(bootstrap, default_region, fragment):
    bootstrap.world["default_region"] = default_region
    if default_region:
        bootstrap.deny(default_region, PEPPER)
    result = _bootstrap(bootstrap)
    assert result.returncode == 1
    assert f"ERROR: could not check the default CLI region for {PEPPER}" in result.stderr
    assert fragment in result.stderr and "Not minting one" in result.stderr
    assert bootstrap.puts() == []


def _bootstrap_payload(fake: FakeCli) -> dict:
    raw = fake.ssm_value(BACKEND_REGION, "/saferide/provider-bootstrap")
    return json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))


def test_bootstrap_mints_the_pepper_only_when_both_regions_answer_not_found(bootstrap):
    result = _bootstrap(bootstrap)
    assert result.returncode == 0, result.stderr
    assert f"==> Created SSM {PEPPER} (af-south-1)" in result.stdout
    puts = bootstrap.puts()
    assert [put["name"] for put in puts] == [
        PEPPER,
        "/saferide/provider-initial-password/ops-one-example.org",
        "/saferide/provider-initial-password/ops-two-example.org",
        "/saferide/provider-bootstrap",
    ]
    assert {put["region"] for put in puts} == {BACKEND_REGION}
    assert len(bootstrap.ssm_value(BACKEND_REGION, PEPPER)) == 64
    payload = _bootstrap_payload(bootstrap)
    assert payload["version"] == 1
    assert [p["email"] for p in payload["providers"]] == ["ops-one@example.org", "ops-two@example.org"]


def test_bootstrap_keeps_an_existing_pepper_and_bumps_the_version(bootstrap):
    backend_deploy_world(bootstrap)  # pepper and a version-2 bootstrap in af-south-1
    result = _bootstrap(bootstrap)
    assert result.returncode == 0, result.stderr
    assert f"==> SSM {PEPPER} (af-south-1) already exists (unchanged)" in result.stdout
    assert PEPPER not in [put["name"] for put in bootstrap.puts()]
    assert bootstrap.ssm_value(BACKEND_REGION, PEPPER) == SECRETS[PEPPER]
    assert _get_parameter_regions(bootstrap)[0] == BACKEND_REGION
    assert None not in _get_parameter_regions(bootstrap)  # no default-region read needed
    assert _bootstrap_payload(bootstrap)["version"] == 3
