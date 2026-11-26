"""iCloud sync-conflict copies never reach the site bucket (2026-11-26).

During the Release 1b frontend deploy iCloud "Desktop & Documents" wrote
conflict copies ("index 2.html", "sw 2.js", "assets/index.esm-CDWZK7Vo 2.js",
"icons/icon-192 2.png", ...) into frontend/dist while the stack update ran,
and the upload published all 13. conflict_copy_guard (infra/scripts/config.sh)
refuses any path whose name has a space and a number before the extension;
deploy-frontend.sh runs it after the build (nothing deployed yet) and again
just before the upload (nothing uploaded). The sync keeps only its two
excludes, so `--delete` removes copies already in the bucket.
No AWS call is ever made (tests/core/fake_cli.py).
"""

import pytest

from tests.core.fake_cli import BUILD_OUTPUT, FRONTEND_CHANGESET_ARN, FakeCli, frontend_deploy_world

DEPLOY = "infra/scripts/deploy-frontend.sh"
# The four names the 2026-11-26 deploy log showed, and other shapes of a copy.
INCIDENT = ["index 2.html", "sw 2.js", "assets/index.esm-CDWZK7Vo 2.js", "icons/icon-192 2.png"]
OTHER_COPIES = ["index 12.html", "LICENSE 3", "assets/index-Cj2uO5w_ 2.css"]
# Spaces, digits or a number without its space: not a copy's name.
NOT_COPIES = ["foo 2bar.js", "index2.html", "index-2.html", "read me.txt", "icons/icon 2x.png"]


@pytest.fixture
def fake(tmp_path):
    return FakeCli(tmp_path)


def _write(root, paths):
    for rel in paths:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rel + "\n", encoding="utf-8")


def _guard(fake: FakeCli, directory):
    return fake.bash('set -euo pipefail; source "$1"; conflict_copy_guard "$2"',
                     str(fake.repo / "infra" / "scripts" / "config.sh"), str(directory))


def _listed(stderr: str) -> list[str]:
    """The paths the guard printed, in its order."""
    return [line[9:] for line in stderr.splitlines() if line.startswith(" " * 9) and line[9] != " "]


# --- conflict_copy_guard ------------------------------------------------------------


def test_a_clean_build_passes_silently(fake, tmp_path):
    dist = tmp_path / "dist"
    _write(dist, BUILD_OUTPUT + NOT_COPIES)
    result = _guard(fake, dist)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "" and result.stderr == ""


def test_every_copy_is_listed_relative_to_the_directory_and_refused(fake, tmp_path):
    dist = tmp_path / "dist"
    _write(dist, BUILD_OUTPUT + NOT_COPIES + INCIDENT + OTHER_COPIES)
    result = _guard(fake, dist)
    assert result.returncode == 1
    assert result.stdout == ""
    assert f"ERROR: sync-conflict copies in {dist} (a space and a number before the extension)" in result.stderr
    assert _listed(result.stderr) == sorted(INCIDENT + OTHER_COPIES)
    assert "Delete them and run the deploy again" in result.stderr


def test_a_copied_directory_is_listed_once(fake, tmp_path):
    dist = tmp_path / "dist"
    _write(dist, BUILD_OUTPUT + ["icons 2/icon-192.png", "icons 2/icon-512.png"])
    result = _guard(fake, dist)
    assert result.returncode == 1
    assert _listed(result.stderr) == ["icons 2"]


def test_only_names_under_the_directory_count(fake, tmp_path):
    # A checkout that is itself a copy ("safe-ride 2") builds a clean dist.
    dist = tmp_path / "safe-ride 2" / "frontend" / "dist"
    _write(dist, BUILD_OUTPUT)
    result = _guard(fake, dist)
    assert result.returncode == 0, result.stderr


def test_a_directory_that_cannot_be_listed_is_refused(fake, tmp_path):
    result = _guard(fake, tmp_path / "missing")
    assert result.returncode == 1
    assert f"ERROR: could not list {tmp_path / 'missing'} to check it for sync-conflict copies." in result.stderr


def test_an_unreadable_subdirectory_is_refused_not_passed(fake, tmp_path):
    dist = tmp_path / "dist"
    _write(dist, BUILD_OUTPUT + ["assets/index.esm-CDWZK7Vo 2.js"])
    (dist / "assets").chmod(0)
    try:
        result = _guard(fake, dist)
    finally:
        (dist / "assets").chmod(0o755)
    assert result.returncode == 1
    assert f"ERROR: could not list {dist} to check it for sync-conflict copies." in result.stderr


# --- deploy-frontend.sh end to end --------------------------------------------------------


@pytest.fixture
def front(fake):
    frontend_deploy_world(fake)
    return fake


def _deploy(fake: FakeCli, **env):
    result = fake.run(DEPLOY, **env)
    for value in ("value-of-google-maps-browser-key", "value-of-google-maps-map-id"):
        assert value not in result.stdout and value not in result.stderr
    return result


def _aws(fake: FakeCli, *prefix: str):
    return [call for call in fake.calls("aws") if call[:len(prefix)] == list(prefix)]


def _uploaded(fake: FakeCli):
    return _aws(fake, "s3") + _aws(fake, "cloudfront")


def test_a_clean_build_is_uploaded_and_the_sync_excludes_no_copies(front):
    result = _deploy(front)
    assert result.returncode == 0, result.stderr
    [sync] = _aws(front, "s3", "sync")
    assert "--delete" in sync
    # Only the two no-cache files: an --exclude also keeps matching objects
    # from deletion, so copies already in the bucket go with this sync.
    assert [sync[i + 1] for i, arg in enumerate(sync) if arg == "--exclude"] == ["index.html", "sw.js"]


def test_copies_left_by_the_build_stop_the_deploy_before_the_stack_changes(front):
    front.world["dist_copies"] = {"build": INCIDENT}
    result = _deploy(front)
    assert result.returncode == 1
    assert _listed(result.stderr) == sorted(INCIDENT)
    assert "ERROR: refused after the build; nothing was deployed or uploaded." in result.stderr
    assert front.calls("npm") == [["run", "build"]]
    assert _aws(front, "cloudformation", "deploy") == [] and _uploaded(front) == []


def test_copies_written_during_the_stack_update_stop_the_upload(front):
    # 2026-11-26: the copies appeared about 19 minutes after `vite build`.
    front.world["dist_copies"] = {"wait": INCIDENT}
    result = _deploy(front)
    assert result.returncode == 1
    assert [call[call.index("--change-set-name") + 1] for call in _aws(front, "cloudformation", "execute-change-set")] \
        == [FRONTEND_CHANGESET_ARN]
    assert _listed(result.stderr) == sorted(INCIDENT)
    assert ("ERROR: refused before the upload; the stack is deployed (or was unchanged) but nothing was uploaded."
            in result.stderr)
    assert "==> Uploading build" not in result.stdout
    assert _uploaded(front) == []


def test_copies_are_refused_before_the_upload_when_the_stack_has_no_changes(front):
    front.world["cfn_deploy"] = "no-changes"
    front.world["dist_copies"] = {"deploy": ["index 2.html"]}
    result = _deploy(front)
    assert result.returncode == 1
    assert "No stack changes: nothing to execute; continuing to the upload." in result.stdout
    assert _listed(result.stderr) == ["index 2.html"]
    assert "ERROR: refused before the upload;" in result.stderr
    assert _uploaded(front) == []
