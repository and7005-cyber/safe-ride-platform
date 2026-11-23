"""The nightly retention job through the migrate handler (hardening U12, AE17).

Run with the stack up:

    RUN_INTEGRATION=1 ../.venv/bin/python -m pytest tests/integration/test_retention.py -q

The ``retention`` action runs in-process against the local database as the
master role, exactly as the migrate Lambda runs it (the gps-purge precedent in
test_gps_actions.py): no API route exists, by design. It is global by nature —
it deletes every aged row in the database, not only this suite's — so every
assertion counts this suite's own rows by id, and the counts reported are
lower bounds. The seed's own feed rows are older than 90 days and the run
deletes them; ``keep_the_seeds_aged_feed`` puts them back afterwards, because
test_parent_feeds.py reads them.

Covers: an aged trail row, notification, token, session, pre-auth token,
PIN-failure row and idempotency key are gone after one run while audit and
incident rows of the same age stay and a notification 89 days old stays;
the job-runs row and the embedded-metric line; ``last-retention-run`` in the
verify set reads that row. Unverified parents: 31 days with nothing holding
them is deleted with its own private rows; a pending link, a membership, a
provider account, an audit or incident reference, 29 days, a grandfathered or
a verified account, or a non-parent role is kept; so is an old account the
office made unverified by changing its email. A held purge lock skips the
school and the run still succeeds. Out of budget, the run stops after the
current batch, records ``stopped`` and still emits success. A delete that hits
the two-second statement timeout writes a ``failed`` row and raises. Every
foreign key to app_users is classified in retention_dao.
"""

import json
import os
import uuid

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from conftest import DSN, purge_accounts, school_sandbox

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="needs the local stack; set RUN_INTEGRATION=1",
)

MARKER = uuid.uuid4().hex[:8]
FIX_LAT, FIX_LNG = -1.2921, 36.8219


def db():
    return psycopg.connect(DSN, autocommit=True, row_factory=dict_row)


@pytest.fixture(scope="module", autouse=True)
def keep_the_seeds_aged_feed():
    """The seed's notifications are dated months back, so a retention run
    deletes them; test_parent_feeds.py relies on them. Saved before this
    module, restored after it (rows whose account no longer exists are not).
    The job-runs rows this module's runs write are removed too, so a local
    `last-retention-run` never reports this suite's deliberate failure."""
    with db() as conn:
        saved = conn.execute(
            "select coalesce(jsonb_agg(to_jsonb(n)), '[]'::jsonb) as rows from live_notifications n"
            " where created_at < now() - interval '89 days'"
        ).fetchone()["rows"]
        last_job = conn.execute("select coalesce(max(id), 0) as id from ops_job_runs").fetchone()["id"]
    yield
    with db() as conn:
        conn.execute(
            "insert into live_notifications"
            " select r.* from jsonb_populate_recordset(null::live_notifications, %s) r"
            " where exists (select 1 from app_users u where u.id = r.user_id)"
            " on conflict do nothing",
            (Jsonb(saved),),
        )
        conn.execute("delete from ops_job_runs where id > %s and job = 'retention'", (last_job,))


@pytest.fixture()
def run_retention(monkeypatch, capsys):
    """Invoke the action as the Scheduler does; returns (report, EMF lines)."""
    monkeypatch.setenv("DATABASE_URL", DSN)
    from app import migrate_handler

    def run(context=None):
        capsys.readouterr()
        report = migrate_handler.handler({"action": "retention"}, context)
        out = capsys.readouterr().out
        metrics = [json.loads(line) for line in out.splitlines() if line.startswith('{"_aws"')]
        return report, metrics

    return run


class Context:
    def __init__(self, remaining_ms: int):
        self.remaining_ms = remaining_ms

    def get_remaining_time_in_millis(self) -> int:
        return self.remaining_ms


# --- the world -------------------------------------------------------------------


def _account(conn, label: str, *, days_old: int, required: bool = True,
             verified: bool = False, role: str = "parent") -> str:
    user_id = conn.execute(
        """
        insert into app_users (email, password_hash, full_name, created_at,
                               email_verification_required, email_verified_at)
        values (%s, 'pbkdf2_sha256$200000$x$y', %s, now() - make_interval(days => %s), %s,
                case when %s then now() end)
        returning id
        """,
        (f"it-retention-{MARKER}-{label}@saferide.test", f"IT Retention {label}", days_old,
         required, verified),
    ).fetchone()["id"]
    conn.execute("insert into app_user_roles (user_id, role) values (%s, %s)", (user_id, role))
    return str(user_id)


@pytest.fixture(scope="module")
def world():
    """A sandbox school with a bus and a completed run, and a grandfathered
    parent who owns this suite's feed rows, tokens and sessions."""
    with school_sandbox("IT Retention School", lat=FIX_LAT, lng=FIX_LNG) as sandbox:
        school = sandbox["id"]
        with db() as conn:
            owner = _account(conn, "owner", days_old=400, required=False)
            bus = conn.execute(
                "insert into live_buses (name, school_id) values (%s, %s) returning id",
                (f"IT Retention Bus {MARKER}", school),
            ).fetchone()["id"]
            run = conn.execute(
                "insert into live_runs (school_id, bus_id, date, status, total_stops)"
                " values (%s, %s, current_date - 100, 'completed', 3) returning id",
                (school, bus),
            ).fetchone()["id"]
            student = conn.execute(
                "insert into live_students (name, school_id) values (%s, %s) returning id",
                (f"IT Retention Student {MARKER}", school),
            ).fetchone()["id"]
        created = [owner]
        try:
            yield {
                "school": school, "owner": owner, "bus": str(bus), "run": str(run),
                "student": str(student), "accounts": created,
            }
        finally:
            purge_accounts(*created)


def _trail_row(conn, world, days_ago: int) -> str:
    return str(conn.execute(
        """
        insert into run_positions
            (school_id, run_id, bus_id, source, action_kind, action_key, lat, lng,
             accuracy_m, captured_at, received_at, fix_reason)
        values (%s, %s, %s, 'action', 'arrive', gen_random_uuid(), %s, %s, 10,
                now() - make_interval(days => %s), now() - make_interval(days => %s), 'none')
        returning id
        """,
        (world["school"], world["run"], world["bus"], FIX_LAT, FIX_LNG, days_ago, days_ago),
    ).fetchone()["id"])


def _notification(conn, user_id: str, days_ago: int) -> str:
    return str(conn.execute(
        "insert into live_notifications (user_id, type, title, body, created_at)"
        " values (%s, 'custom', 'IT retention', '', now() - make_interval(days => %s))"
        " returning id",
        (user_id, days_ago),
    ).fetchone()["id"])


def _exists(conn, table: str, column: str, value) -> bool:
    return conn.execute(
        f"select exists (select 1 from {table} where {column} = %s) as e",  # noqa: S608
        (value,),
    ).fetchone()["e"]


def _job_row(conn, run_id: int) -> dict:
    return conn.execute("select * from ops_job_runs where id = %s", (run_id,)).fetchone()


# --- AE17 ------------------------------------------------------------------------


def test_one_run_deletes_every_aged_row_and_keeps_audit_incidents_and_recent_rows(
    world, run_retention, monkeypatch,
):
    owner, school = world["owner"], world["school"]
    source = f"it-retention-{MARKER}"
    with db() as conn:
        trail_old, trail_new = _trail_row(conn, world, 100), _trail_row(conn, world, 1)
        exception = conn.execute(
            """
            insert into run_exceptions (school_id, run_id, stop_order, kind, fix_lat, fix_lng,
                                        fix_accuracy_m, fix_captured_at, distance_m,
                                        seen_at_stop, created_at)
            values (%s, %s, 2, 'custody-away', %s, %s, 12, now() - interval '100 days', 1800,
                    false, now() - interval '100 days')
            returning id
            """,
            (school, world["run"], FIX_LAT, FIX_LNG),
        ).fetchone()["id"]
        n_old, n_89, n_new = (_notification(conn, owner, d) for d in (91, 89, 0))
        tokens = {
            name: conn.execute(
                "insert into password_reset_tokens (user_id, token_hash, purpose, expires_at,"
                " used_at, created_at) values (%s, %s, %s, now() + make_interval(mins => %s),"
                " case when %s then now() - make_interval(mins => %s) end,"
                " now() - interval '3 days') returning id",
                (owner, f"it-retention-{MARKER}-{name}", purpose, expires_in, used, used_ago),
            ).fetchone()["id"]
            for name, purpose, expires_in, used, used_ago in (
                ("used-2d", "reset", -60 * 24 * 2 + 30, True, 60 * 24 * 2),
                ("expired-2d", "verify", -60 * 24 * 2, False, 0),
                ("expired-12h", "verify", -60 * 12, False, 0),
                ("used-1h", "reset", 30, True, 60),
                ("live", "reset", 20, False, 0),
            )
        }
        sessions = {
            days: conn.execute(
                "insert into auth_sessions (user_id, token_hash, expires_at)"
                " values (%s, %s, now() - make_interval(days => %s)) returning id",
                (owner, f"it-retention-{MARKER}-session-{days}", days),
            ).fetchone()["id"]
            for days in (8, 6)
        }
        preauth = {
            days: conn.execute(
                "insert into auth_preauth_tokens (user_id, token_hash, expires_at)"
                " values (%s, %s, now() - make_interval(days => %s)) returning id",
                (owner, f"it-retention-{MARKER}-preauth-{days}", days),
            ).fetchone()["id"]
            for days in (8, 6)
        }
        for label, age in (("old", "2 days"), ("new", "1 hour")):
            conn.execute(
                "insert into pin_login_failures (school_id, source, attempted_at)"
                f" values (%s, %s, now() - interval '{age}')",  # noqa: S608
                (school, f"{source}-{label}"),
            )
        driver = str(uuid.uuid4())
        keys = {}
        for days in (8, 2):
            keys[days] = str(uuid.uuid4())
            conn.execute(
                "insert into driver_action_keys (school_id, driver_id, key, action,"
                " request_fingerprint, response, created_at)"
                " values (%s, %s, %s, 'board', 'fp', '{}'::jsonb, now() - make_interval(days => %s))",
                (school, driver, keys[days], days),
            )
        audit = conn.execute(
            "insert into live_admin_audit (actor_name, actor_email, action, school_id, created_at)"
            " values ('IT Retention', %s, 'school-updated', %s, now() - interval '100 days')"
            " returning id",
            (f"it-retention-{MARKER}-actor@saferide.test", school),
        ).fetchone()["id"]
        incident = conn.execute(
            "insert into live_incidents (type, school_id, description, created_at)"
            " values ('other', %s, 'IT retention', now() - interval '100 days') returning id",
            (school,),
        ).fetchone()["id"]

    report, metrics = run_retention()

    assert report["status"] == "ok" and report["job_status"] == "ok", report
    with db() as conn:
        assert not _exists(conn, "run_positions", "id", trail_old)
        assert _exists(conn, "run_positions", "id", trail_new)
        coords = conn.execute(
            "select fix_lat, fix_lng, distance_m, kind from run_exceptions where id = %s", (exception,)
        ).fetchone()
        assert (coords["fix_lat"], coords["fix_lng"]) == (None, None)
        assert (coords["distance_m"], coords["kind"]) == (1800, "custody-away")
        assert not _exists(conn, "live_notifications", "id", n_old)
        assert _exists(conn, "live_notifications", "id", n_89)  # 89 days: kept
        assert _exists(conn, "live_notifications", "id", n_new)
        for name, token_id in tokens.items():
            expected = name in ("expired-12h", "used-1h", "live")
            assert _exists(conn, "password_reset_tokens", "id", token_id) is expected, name
        assert not _exists(conn, "auth_sessions", "id", sessions[8])
        assert _exists(conn, "auth_sessions", "id", sessions[6])
        assert not _exists(conn, "auth_preauth_tokens", "id", preauth[8])
        assert _exists(conn, "auth_preauth_tokens", "id", preauth[6])
        assert not _exists(conn, "pin_login_failures", "source", f"{source}-old")
        assert _exists(conn, "pin_login_failures", "source", f"{source}-new")
        assert not _exists(conn, "driver_action_keys", "key", keys[8])
        assert _exists(conn, "driver_action_keys", "key", keys[2])
        # Kept for the life of the school, untouched.
        assert _exists(conn, "live_admin_audit", "id", audit)
        assert _exists(conn, "live_incidents", "id", incident)

        # The witness: closed ok, with the counts the report carries.
        row = _job_row(conn, report["run_id"])
    assert row["job"] == "retention" and row["status"] == "ok"
    assert row["finished_at"] is not None and row["finished_at"] >= row["started_at"]
    assert row["counts"] == report["counts"]
    counts = report["counts"]
    for key, at_least in (
        ("run_positions", 1), ("live_notifications", 1), ("password_reset_tokens", 2),
        ("auth_sessions", 1), ("auth_preauth_tokens", 1), ("pin_login_failures", 1),
        ("driver_action_keys", 1), ("run_exceptions_nulled", 1),
    ):
        assert counts[key] >= at_least, (key, counts)
    assert counts["trail_batches"] >= 1 and counts["schools_locked"] == 0

    # Exactly one embedded-metric line, success, naming this run.
    (metric,) = metrics
    assert metric["RetentionRunSucceeded"] == 1
    assert metric["run_id"] == report["run_id"] and metric["status"] == "ok"
    assert metric["_aws"]["CloudWatchMetrics"][0]["Namespace"] == "SafeRide"

    # The verify set's last-retention-run reads this row.
    from app import verify_handler

    result = verify_handler.handler({"checks": "hardening"})
    (last,) = next(e for e in result["results"] if e["label"] == "last-retention-run")["rows"]
    assert last["id"] == report["run_id"] and last["status"] == "ok"
    assert float(last["age_hours"]) < 1


# --- unverified parents ------------------------------------------------------------


def test_only_an_unverified_parent_nothing_holds_is_deleted_after_30_days(world, run_retention):
    school = world["school"]
    with db() as conn:
        accounts = {
            "lone-31d": _account(conn, "lone", days_old=31),
            "pending-link": _account(conn, "pending", days_old=31),
            "membership": _account(conn, "member", days_old=31),
            "provider": _account(conn, "provider", days_old=31),
            "audit-actor": _account(conn, "auditor", days_old=31),
            "incident-ack": _account(conn, "acker", days_old=31),
            "young-29d": _account(conn, "young", days_old=29),
            "grandfathered": _account(conn, "grandfathered", days_old=31, required=False),
            "verified": _account(conn, "verified", days_old=31, verified=True),
            "not-a-parent": _account(conn, "staff", days_old=31, role="admin"),
        }
        world["accounts"].extend(accounts.values())
        lone = accounts["lone-31d"]
        # The lone account's own private rows, which go with it.
        own = {
            "auth_sessions": conn.execute(
                "insert into auth_sessions (user_id, token_hash, expires_at)"
                " values (%s, %s, now() + interval '1 hour') returning id",
                (lone, f"it-retention-{MARKER}-lone-session"),
            ).fetchone()["id"],
            "password_reset_tokens": conn.execute(
                "insert into password_reset_tokens (user_id, token_hash, purpose, expires_at)"
                " values (%s, %s, 'verify', now() + interval '1 hour') returning id",
                (lone, f"it-retention-{MARKER}-lone-verify"),
            ).fetchone()["id"],
            "live_push_subscriptions": conn.execute(
                "insert into live_push_subscriptions (user_id, endpoint)"
                " values (%s, %s) returning id",
                (lone, f"https://push.example.test/it-retention-{MARKER}"),
            ).fetchone()["id"],
            "live_notifications": _notification(conn, lone, 0),
        }
        conn.execute(
            "insert into live_parent_students (parent_id, student_id, status, school_id)"
            " values (%s, %s, 'pending', %s)",
            (accounts["pending-link"], world["student"], school),
        )
        conn.execute(
            "insert into school_memberships (user_id, school_id, role, state)"
            " values (%s, %s, 'driver', 'offered')",
            (accounts["membership"], school),
        )
        conn.execute(
            "insert into provider_accounts (user_id, totp_salt) values (%s, 'it-salt')",
            (accounts["provider"],),
        )
        conn.execute(
            "insert into live_admin_audit (actor_id, actor_name, actor_email, action, school_id)"
            " values (%s, 'IT Retention', %s, 'school-updated', %s)",
            (accounts["audit-actor"], f"it-retention-{MARKER}-a@saferide.test", school),
        )
        conn.execute(
            "insert into live_incidents (type, school_id, acknowledged, acknowledged_by)"
            " values ('other', %s, true, %s)",
            (school, accounts["incident-ack"]),
        )

    report, _ = run_retention()

    assert report["counts"]["unverified_parent_accounts"] >= 1
    with db() as conn:
        survivors = {
            label: _exists(conn, "app_users", "id", user_id) for label, user_id in accounts.items()
        }
        assert survivors == {label: label != "lone-31d" for label in accounts}, survivors
        for table, row_id in own.items():
            assert not _exists(conn, table, "id", row_id), table
        # The audit and incident rows still name their account (no SET NULL).
        assert conn.execute(
            "select count(*) as n from live_admin_audit where actor_id = %s",
            (accounts["audit-actor"],),
        ).fetchone()["n"] == 1
        assert conn.execute(
            "select count(*) as n from live_incidents where acknowledged_by = %s",
            (accounts["incident-ack"],),
        ).fetchone()["n"] == 1


def test_an_account_the_office_made_unverified_is_kept_once_its_links_are_gone(
    world, run_retention,
):
    """D35: the office's email change leaves a long-standing parent
    unverified while its created_at stays old. With its last link gone it
    must survive the run — only accounts that never verified qualify. Its
    twin, the same in every way but the audit row, shows the run would
    otherwise delete it."""
    from app.dao.audit_dao import record_audit

    with db() as conn:
        changed = _account(conn, "email-changed", days_old=400)
        twin = _account(conn, "never-verified", days_old=400)
        world["accounts"].extend((changed, twin))
        # The row account_dao.update_parent writes after _restart_verification
        # (017's action: deleted below, for the 013/016 double-apply checks).
        audit = record_audit(
            conn, action="parent-email-changed",
            actor={"full_name": "IT Retention",
                   "email": f"it-retention-{MARKER}-office@saferide.test"},
            school_id=world["school"], resource_type="parent", resource_id=uuid.UUID(changed),
            detail={"tokens_voided": 0, "sessions_revoked": 0, "push_registrations_removed": 0},
        )
    try:
        report, _ = run_retention()

        assert report["job_status"] == "ok", report
        with db() as conn:
            assert _exists(conn, "app_users", "id", changed)
            assert not _exists(conn, "app_users", "id", twin)
    finally:
        with db() as conn:
            conn.execute("delete from live_admin_audit where id = %s", (audit,))


# --- the lock, the budget, a failure ---------------------------------------------


def test_a_held_purge_lock_skips_that_school_and_the_run_still_succeeds(world, run_retention):
    school = world["school"]
    with db() as conn:
        trail_old = _trail_row(conn, world, 120)
        n_old = _notification(conn, world["owner"], 95)
    with psycopg.connect(DSN) as holder:
        holder.execute("select pg_advisory_xact_lock(16, hashtext(%s))", (school,))
        report, metrics = run_retention()
        holder.rollback()

    assert report["job_status"] == "ok"
    assert school in report["locked_school_ids"]
    assert report["counts"]["schools_locked"] >= 1
    assert len(metrics) == 1
    with db() as conn:
        assert _exists(conn, "run_positions", "id", trail_old)  # skipped tonight
        assert not _exists(conn, "live_notifications", "id", n_old)  # the cheap deletes ran

    report, _ = run_retention()
    assert school not in report["locked_school_ids"]
    with db() as conn:
        assert not _exists(conn, "run_positions", "id", trail_old)


def test_out_of_budget_the_run_stops_after_the_current_batch_and_still_succeeds(
    world, run_retention,
):
    run_retention()  # clear any backlog, so every cheap step comes back short
    with db() as conn:
        # The oldest trail row anywhere: this school's batch comes first.
        trail_old = _trail_row(conn, world, 130)
        schools = conn.execute("select count(*) as n from live_schools").fetchone()["n"]
    assert schools >= 2  # so there is a next school to stop before

    report, metrics = run_retention(Context(1_000))

    assert report["job_status"] == "stopped" and report["stopped"] == "budget"
    assert report["counts"]["trail_batches"] == 1  # the batch in hand, then stop
    with db() as conn:
        assert not _exists(conn, "run_positions", "id", trail_old)  # that batch completed
        row = _job_row(conn, report["run_id"])
    assert row["status"] == "stopped" and row["finished_at"] is not None
    assert row["counts"] == report["counts"]
    (metric,) = metrics
    assert metric["RetentionRunSucceeded"] == 1 and metric["status"] == "stopped"


def test_a_delete_that_overruns_the_statement_timeout_writes_failed_and_raises(
    run_retention, monkeypatch, capsys,
):
    from app import migrate_handler
    from app.dao import retention_dao

    def slow_sessions(conn, limit):
        conn.execute("select pg_sleep(3)")  # past the 2 s SET LOCAL of the batch
        return 0

    monkeypatch.setattr(retention_dao, "STEPS", tuple(
        (key, slow_sessions if key == "auth_sessions" else fn, rows)
        for key, fn, rows in retention_dao.STEPS
    ))
    with db() as conn:
        before = conn.execute("select coalesce(max(id), 0) as id from ops_job_runs").fetchone()["id"]

    capsys.readouterr()
    with pytest.raises(migrate_handler.RetentionFailed) as raised:
        migrate_handler.handler({"action": "retention"}, None)
    out = capsys.readouterr().out

    assert "failed at auth_sessions: QueryCanceled sqlstate=57014" in str(raised.value)
    with db() as conn:
        (row,) = conn.execute(
            "select * from ops_job_runs where id > %s and job = 'retention'", (before,)
        ).fetchall()
    assert row["status"] == "failed" and row["finished_at"] is not None
    assert {k: row["counts"][k] for k in ("failed_step", "error", "sqlstate")} == {
        "failed_step": "auth_sessions", "error": "QueryCanceled", "sqlstate": "57014",
    }
    # The steps before it ran and are recorded; nothing after it ran.
    assert "live_notifications" in row["counts"] and row["counts"]["trail_batches"] == 0
    assert '"_aws"' not in out  # no success metric for a failed run


# --- the account delete can neither fail on a key nor orphan a reference -----------


def test_every_foreign_key_to_app_users_is_classified():
    from app.dao import retention_dao

    with db() as conn:
        keys = conn.execute(
            """
            select c.conrelid::regclass::text as tbl, a.attname as col, c.confdeltype as action
            from pg_constraint c
            join pg_attribute a on a.attrelid = c.conrelid and a.attnum = c.conkey[1]
            where c.confrelid = 'public.app_users'::regclass and c.contype = 'f'
            """
        ).fetchall()
        columns = {
            (r["table_name"], r["column_name"])
            for r in conn.execute(
                "select table_name, column_name from information_schema.columns"
                " where table_schema = 'public'"
            ).fetchall()
        }
    own, kept = set(retention_dao.ACCOUNT_OWN_ROWS), set(retention_dao.USER_REFERENCES)
    foreign = {(k["tbl"], k["col"]): k["action"] for k in keys}
    unclassified = set(foreign) - own - kept
    assert not unclassified, f"classify these in retention_dao: {sorted(unclassified)}"
    # Own rows cascade with the account; no key can make the delete fail
    # (c = cascade, n = set null; never restrict or no action).
    assert all(foreign[pair] == "c" for pair in own), foreign
    assert set(foreign.values()) <= {"c", "n"}
    # Every listed column exists (a typo would silently weaken the guard).
    assert (own | kept) <= columns, sorted((own | kept) - columns)


# --- before a rollback to 1a: the verification tokens go (hardening D52) ---------


def test_purge_verify_tokens_deletes_live_verification_tokens_and_keeps_reset_tokens(monkeypatch):
    """1a's reset consumes a token by hash alone, so every live verify token
    must be gone before the backend goes back to release/1a; reset tokens are
    1a's own and stay."""
    monkeypatch.setenv("DATABASE_URL", DSN)
    from app import migrate_handler

    with db() as conn:
        user_id = _account(conn, "purge-verify", days_old=0)
        tokens = {}
        for purpose in ("verify", "reset"):
            tokens[purpose] = conn.execute(
                "insert into password_reset_tokens (user_id, token_hash, purpose, expires_at)"
                " values (%s, %s, %s, now() + interval '1 hour') returning id",
                (user_id, f"it-purge-{MARKER}-{purpose}", purpose),
            ).fetchone()["id"]
    try:
        report = migrate_handler.handler({"action": "purge-verify-tokens"})
        assert report["status"] == "ok" and report["action"] == "purge-verify-tokens"
        assert report["deleted"] >= 1
        with db() as conn:
            assert not _exists(conn, "password_reset_tokens", "id", tokens["verify"])
            assert _exists(conn, "password_reset_tokens", "id", tokens["reset"])
            assert conn.execute(
                "select count(*) as n from password_reset_tokens where purpose = 'verify'"
            ).fetchone()["n"] == 0
    finally:
        purge_accounts(user_id)
