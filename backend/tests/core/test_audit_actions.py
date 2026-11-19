"""The audit action vocabulary stays in lockstep with the migration that last
widened live_admin_audit_action_check (013 defined it; 016 recreated it as
013's list plus 'exception-reviewed'; 017 as 016's list plus the four
account-hardening actions). The newest migration that recreates the CHECK is
found by scanning, so the next widening is checked without editing this file."""

import re
from pathlib import Path

import pytest

from app.dao.audit_dao import ALLOWED_ACTIONS, actor_display, record_audit

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "db" / "migrations"
CHECK_PATTERN = re.compile(
    r"live_admin_audit_action_check check \(\s*action in \((.*?)\)\s*\);",
    re.S,
)


def _latest_widening() -> tuple[str, set[str]]:
    for path in sorted(MIGRATIONS_DIR.glob("*.sql"), reverse=True):
        match = CHECK_PATTERN.search(path.read_text())
        if match:
            return path.name, set(re.findall(r"'([a-z0-9-]+)'", match.group(1)))
    raise AssertionError("no migration recreates live_admin_audit_action_check")


def test_allowed_actions_equal_the_latest_migration_check():
    name, from_sql = _latest_widening()
    assert name >= "017_account_hardening.sql", name
    assert from_sql == set(ALLOWED_ACTIONS)


def test_each_widening_keeps_every_earlier_value():
    # "Never copy the list from an older migration": a widening that dropped
    # a value would refuse rows the live table already holds.
    lists = [
        set(re.findall(r"'([a-z0-9-]+)'", match.group(1)))
        for path in sorted(MIGRATIONS_DIR.glob("*.sql"))
        if path.name >= "016" and (match := CHECK_PATTERN.search(path.read_text()))
    ]
    assert len(lists) >= 2
    for older, newer in zip(lists, lists[1:]):
        assert older <= newer, older - newer


def test_record_audit_refuses_unknown_actions_before_any_sql():
    with pytest.raises(ValueError, match="not in the audit action vocabulary"):
        record_audit(None, action="tables-dropped", actor={"id": "x"})


def test_actor_display_masks_providers_as_saferide():
    staff = {"full_name": "Dora Director", "email": "d@x", "provider": None}
    provider = {"full_name": "Kuumbai Support", "provider": {"totp_enrolled": True}}
    assert actor_display(staff) == "Dora Director"
    assert actor_display(provider) == "SafeRide"
    assert actor_display(None) == "unknown"
