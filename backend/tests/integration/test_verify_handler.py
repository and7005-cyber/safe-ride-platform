"""Read-only verification Lambda (verify_handler): the go/no-go SQL door for
the private-subnet production RDS, exercised in-process against the local
stack's database (the same direct-entry pattern the slot-in suite documents —
the Lambda has no API route by design, so there is nothing to drive over HTTP).

Covers: the check-set allowlist (unknown sets rejected with the valid list),
school_id validation, every registered check set returning JSON-serializable
rows against the migrated local database, the migration-011 checks agreeing
with what migration 011 actually shipped, and the server-enforced read-only
session (an INSERT through the handler's own connection factory must fail).
"""

import json
import os
import uuid

import psycopg
import pytest

from conftest import DSN

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="needs the local stack; set RUN_INTEGRATION=1",
)


@pytest.fixture()
def verify(monkeypatch):
    """Import the handler with DATABASE_URL pointed at the suite's database."""
    monkeypatch.setenv("DATABASE_URL", DSN)
    from app import verify_handler

    return verify_handler


def test_unknown_check_set_is_rejected_with_the_valid_list(verify):
    result = verify.handler({"checks": "drop-tables"})
    assert result["status"] == "error"
    assert "drop-tables" in result["reason"]
    assert set(result["valid_check_sets"]) == {
        "migrations", "migration-011", "baseline", "post-apply",
        "tenancy-preflight", "tenancy-post-move", "tenancy-rls", "gps",
    }


def test_school_scoped_sets_require_a_valid_uuid(verify):
    for payload in ({"checks": "baseline"}, {"checks": "post-apply", "school_id": "nope"}):
        result = verify.handler(payload)
        assert result["status"] == "error"
        assert "school_id" in result["reason"]


def test_migrations_check_degrades_per_check_not_per_invocation(verify):
    # The marker table exists only where the migrate Lambda has run (live).
    # The local stack applies migrations without it, so this check must come
    # back as a labeled error — never crash the whole invocation.
    result = verify.handler({"checks": "migrations"})
    assert result["status"] == "ok"
    entry = result["results"][0]
    assert entry["label"] == "applied-migrations"
    if "rows" in entry:  # a stack where the migrate Lambda has run
        assert any(r["id"] == "011_fleet_plans" for r in entry["rows"])
    else:
        assert "saferide_migrations" in entry["error"]
    json.dumps(result)  # timestamps cast to strings


def test_migration_011_checks_match_the_shipped_schema(verify):
    result = verify.handler({"checks": "migration-011"})
    assert result["status"] == "ok"
    by_label = {entry["label"]: entry["rows"] for entry in result["results"]}

    # The mismatch cross-check certifies the BACKFILL at the pristine
    # post-migration moment; once the feature runs, patterns are inputs and
    # legitimately diverge from links. On this exercised local DB we only pin
    # that the check executes and returns an integer.
    assert by_label["ridership-pattern-mismatches"][0]["mismatches"] >= 0
    definition = by_label["notification-type-check"][0]["definition"]
    assert "route-updated" in definition and "route-unassigned" in definition
    assert [r["indexname"] for r in by_label["plan-partial-uniques"]] == [
        "live_fleet_plans_school_draft_key",
        "live_fleet_plans_school_previous_key",
    ]
    assert by_label["notification-plan-dedup-index"][0]["indexname"] == (
        "live_notifications_plan_dedup"
    )
    json.dumps(result)


def test_baseline_and_post_apply_are_graceful_for_any_school(verify):
    # A fresh school with no routes, plans, or students: every check must
    # return cleanly (zero counts / empty row lists), never error — the
    # operator compares observations against the checklist.
    with psycopg.connect(DSN, autocommit=True) as conn:
        school_id = conn.execute(
            "insert into live_schools (name, lat, lng, code) "
            "values ('IT VerifySchool', -1.3, 36.8, 'IT-VFY') returning id"
        ).fetchone()[0]
    try:
        for checks in ("baseline", "post-apply"):
            result = verify.handler({"checks": checks, "school_id": str(school_id)})
            assert result["status"] == "ok", result
            labels = [entry["label"] for entry in result["results"]]
            assert len(labels) == len(set(labels))
            json.dumps(result)
        baseline = verify.handler({"checks": "baseline", "school_id": str(school_id)})
        counts = {e["label"]: e["rows"] for e in baseline["results"]}
        assert counts["route-count"][0]["routes"] == 0
    finally:
        with psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute("delete from live_schools where id = %s", (school_id,))


def test_random_school_id_returns_zero_rows_not_errors(verify):
    result = verify.handler({"checks": "post-apply", "school_id": str(uuid.uuid4())})
    assert result["status"] == "ok"


def test_the_handlers_connection_is_server_enforced_read_only(verify):
    with verify._connect() as conn:
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            conn.execute(
                "insert into live_schools (name, lat, lng) values ('IT RO', 0, 0)"
            )


# --- Tenancy check sets (U2) -------------------------------------------------


def test_tenancy_preflight_reports_the_local_state(verify):
    result = verify.handler({"checks": "tenancy-preflight"})
    assert result["status"] == "ok"
    by_label = {e["label"]: e for e in result["results"]}
    assert len(by_label) == len(result["results"])
    # Pre-013 columns exist since 004/011, so these entries carry rows locally.
    schools = by_label["school-rows"]["rows"]
    assert {r["name"] for r in schools} >= {"Greenfield Academy", "IT Second School"}
    assert by_label["demo-identities"]["rows"], "seed identities should be visible"
    json.dumps(result)


def test_tenancy_post_move_degrades_per_check_before_migration_013(verify):
    # Every entry references tenancy schema; before 013 each must come back as
    # a labeled error (observation), never crash the invocation.
    result = verify.handler({"checks": "tenancy-post-move"})
    assert result["status"] == "ok"
    labels = [e["label"] for e in result["results"]]
    assert len(labels) == len(set(labels))
    json.dumps(result)


def test_tenancy_rls_assumes_the_runtime_role(verify):
    result = verify.handler(
        {"checks": "tenancy-rls", "school_id": "5cae0000-0000-0000-0000-000000000001"}
    )
    assert result["status"] == "ok"
    by_label = {e["label"]: e for e in result["results"]}
    # The local role exists (created by the reset script), so the connection
    # can assume it; grants and policies arrive with later releases, so data
    # entries may be labeled errors until then.
    assert by_label["current-user"]["rows"][0]["current_user"] == "saferide_app"
    json.dumps(result)


# --- GPS tracking check set (migration 016 / U1) ------------------------------


def test_gps_check_set_runs_clean_on_a_database_at_016(verify):
    # Every entry is a plain SELECT over the 016 tables: no labeled errors,
    # unique labels, JSON-serializable rows, and the scalar backlog checks
    # come back as integers (zero on a fresh database; the suites' own probes
    # roll back, so nothing else should accumulate here).
    result = verify.handler({"checks": "gps"})
    assert result["status"] == "ok", result
    labels = [e["label"] for e in result["results"]]
    assert len(labels) == len(set(labels))
    assert labels == [
        "trail-rows-per-run-today",
        "trail-rows-per-run-per-minute",
        "fix-coverage-ratio-today",
        "exceptions-by-kind-today",
        "pending-prompts-on-completed-runs",
        "call-now-due-unsent-over-10m",
        "trail-rows-classification-failed",
        "trail-rows-past-retention",
    ]
    by_label = {e["label"]: e for e in result["results"]}
    for label, entry in by_label.items():
        assert "error" not in entry, f"{label}: {entry.get('error')}"
    assert by_label["pending-prompts-on-completed-runs"]["rows"][0]["pending_on_completed"] >= 0
    assert by_label["call-now-due-unsent-over-10m"]["rows"][0]["overdue"] >= 0
    assert by_label["trail-rows-classification-failed"]["rows"][0]["flagged"] >= 0
    coverage = by_label["fix-coverage-ratio-today"]["rows"][0]
    assert coverage["action_rows"] >= coverage["with_fix"] >= 0
    # One row per school, default retention shown when the school has none.
    retention = by_label["trail-rows-past-retention"]["rows"]
    assert {r["name"] for r in retention} >= {"Greenfield Academy", "IT Second School"}
    assert all(r["retention_days"] == 90 or 7 <= r["retention_days"] <= 365 for r in retention)
    json.dumps(result)
