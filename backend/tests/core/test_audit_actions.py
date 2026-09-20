"""The audit action vocabulary stays in lockstep with the migration that last
widened live_admin_audit_action_check (013 defined it; 016 recreated it as
013's list plus 'exception-reviewed')."""

import re
from pathlib import Path

import pytest

from app.dao.audit_dao import ALLOWED_ACTIONS, actor_display, record_audit

MIGRATION = Path(__file__).resolve().parents[2] / "db" / "migrations" / "016_gps_tracking.sql"


def test_allowed_actions_equal_the_migration_check():
    sql = MIGRATION.read_text()
    match = re.search(
        r"live_admin_audit_action_check check \(\s*action in \((.*?)\)\s*\);",
        sql,
        re.S,
    )
    assert match, "016's action CHECK not found"
    from_sql = set(re.findall(r"'([a-z0-9-]+)'", match.group(1)))
    assert from_sql == set(ALLOWED_ACTIONS)


def test_record_audit_refuses_unknown_actions_before_any_sql():
    with pytest.raises(ValueError, match="not in the audit action vocabulary"):
        record_audit(None, action="tables-dropped", actor={"id": "x"})


def test_actor_display_masks_providers_as_saferide():
    staff = {"full_name": "Dora Director", "email": "d@x", "provider": None}
    provider = {"full_name": "Kuumbai Support", "provider": {"totp_enrolled": True}}
    assert actor_display(staff) == "Dora Director"
    assert actor_display(provider) == "SafeRide"
    assert actor_display(None) == "unknown"
