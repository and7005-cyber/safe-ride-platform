"""Tenancy post-steps of the migrate handler (U2): bootstrap parsing and
gating, role-sync skip; the local-only seed gate (hardening U3); the nightly
retention action's control flow (hardening U12: step order, the budget stop,
a held purge lock, a failing step, the embedded-metric line). Pure unit
tests — the fake connection records SQL. The deletes themselves run against
the local database in tests/integration/test_retention.py."""

import base64
import contextlib
import json
import logging
import time
from pathlib import Path

import psycopg
import pytest
from psycopg.pq import ExecStatus

from app import migrate_handler as mh
from app.core.config import get_settings
from app.dao import position_dao, retention_dao
from tests.core.template_text import load
from tests.lambda_json_log import JsonLines


def _payload(version=1):
    return {
        "version": version,
        "providers": [
            {"email": "Ops1@kuumbai.co.ke", "full_name": "Ops One",
             "password_hash": "pbkdf2_sha256$200000$aa$bb"},
            {"email": "ops2@kuumbai.co.ke", "full_name": "Ops Two",
             "password_hash": "pbkdf2_sha256$200000$cc$dd"},
        ],
    }


class TestParseBootstrap:
    def test_plain_json(self):
        assert mh._parse_bootstrap(json.dumps(_payload()))["version"] == 1

    def test_base64url_json_without_padding(self):
        raw = base64.urlsafe_b64encode(json.dumps(_payload(3)).encode()).decode().rstrip("=")
        assert mh._parse_bootstrap(raw)["version"] == 3

    @pytest.mark.parametrize("mutate", [
        lambda d: d.update(version="one"),
        lambda d: d.update(version=0),
        lambda d: d.update(providers=[]),
        lambda d: d["providers"][0].pop("email"),
        lambda d: d["providers"][0].update(password_hash="plaintext"),
    ])
    def test_invalid_payloads_raise(self, mutate):
        data = _payload()
        mutate(data)
        with pytest.raises((RuntimeError, json.JSONDecodeError)):
            mh._parse_bootstrap(json.dumps(data))


class FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class FakeConn:
    """Answers by SQL substring; records every non-select statement."""

    def __init__(self, *, has_table=True, marker_applied=False, existing=None):
        self.has_table = has_table
        self.marker_applied = marker_applied
        # email -> (user_id, is_provider)
        self.existing = existing or {}
        self.statements: list[tuple[str, tuple]] = []

    def execute(self, sql, params=()):
        self.statements.append((sql, tuple(params)))
        if "to_regclass" in sql:
            return FakeResult(("provider_accounts",) if self.has_table else (None,))
        if "from public.saferide_migrations" in sql:
            return FakeResult((1,) if self.marker_applied else None)
        if "from app_users u left join provider_accounts" in sql:
            return FakeResult(self.existing.get(params[0]))
        if "insert into app_users" in sql:
            return FakeResult(("new-user-id",))
        return FakeResult(None)


class TestBootstrapProviders:
    def test_skips_before_tenancy_schema(self):
        conn = FakeConn(has_table=False)
        assert "skipped" in mh._bootstrap_providers(conn, _payload())

    def test_skips_an_already_applied_version(self):
        conn = FakeConn(marker_applied=True)
        result = mh._bootstrap_providers(conn, _payload(2))
        assert result == {"skipped": "provider-bootstrap:v2 already applied"}

    def test_creates_accounts_and_marks_the_version(self):
        conn = FakeConn()
        result = mh._bootstrap_providers(conn, _payload())
        assert result["providers"] == [
            "created:ops1@kuumbai.co.ke", "created:ops2@kuumbai.co.ke",
        ]
        inserts = [s for s, _ in conn.statements if s.startswith("insert into provider_accounts")]
        assert len(inserts) == 2
        assert any("insert into public.saferide_migrations" in s for s, _ in conn.statements)

    def test_rekeys_an_existing_provider_on_version_bump(self):
        conn = FakeConn(existing={"ops1@kuumbai.co.ke": ("uid-1", True)})
        result = mh._bootstrap_providers(conn, _payload(2))
        assert "rekeyed:ops1@kuumbai.co.ke" in result["providers"]
        rekeys = [p for s, p in conn.statements if s.startswith("update app_users")]
        assert rekeys == [("pbkdf2_sha256$200000$aa$bb", "uid-1")]

    def test_refuses_an_email_held_by_a_non_provider_identity(self):
        conn = FakeConn(existing={"ops1@kuumbai.co.ke": ("uid-1", False)})
        with pytest.raises(RuntimeError, match="ops1@kuumbai.co.ke"):
            mh._bootstrap_providers(conn, _payload())


def test_role_sync_skips_without_a_password(monkeypatch):
    monkeypatch.delenv("DB_APP_PASSWORD", raising=False)
    assert mh._sync_app_role(None) == {"skipped": "DB_APP_PASSWORD not set"}


def test_fetch_bootstrap_skips_without_the_ssm_name(monkeypatch):
    monkeypatch.delenv("PROVIDER_BOOTSTRAP_SSM", raising=False)
    assert mh._fetch_bootstrap() is None


# --- the on-demand GPS purge action (GPS plan U7/R12) -----------------------


class TestGpsPurgeEvent:
    SCHOOL = "5cae0000-0000-0000-0000-000000000001"

    def test_defaults_and_bound(self):
        assert mh._parse_gps_purge_event({"school_id": self.SCHOOL}) == (self.SCHOOL, 100)
        assert mh._parse_gps_purge_event({"school_id": self.SCHOOL, "max_batches": 3}) == (
            self.SCHOOL, 3,
        )
        assert mh._parse_gps_purge_event({"school_id": self.SCHOOL, "max_batches": 5000}) == (
            self.SCHOOL, 1000,
        )

    @pytest.mark.parametrize("event", [
        {}, {"school_id": "nope"}, {"school_id": None},
        {"school_id": SCHOOL, "max_batches": 0},
        {"school_id": SCHOOL, "max_batches": "10"},
        {"school_id": SCHOOL, "max_batches": True},
    ])
    def test_invalid_events_are_refused_before_any_connection(self, event, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        result = mh.handler({"action": "gps-purge", **event})
        assert result["status"] == "error"
        assert "gps-purge" in result["reason"]

    def test_an_unknown_action_is_refused_without_migrating(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert mh.handler({"action": "drop-everything"}) == {
            "status": "error", "reason": "unknown action 'drop-everything'",
        }


# --- before a rollback to 1a: the verification tokens go (hardening D52) ------


def test_purge_verify_tokens_deletes_only_verification_tokens_and_reports_the_count(monkeypatch):
    statements: list[str] = []

    class Result:
        rowcount = 3

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params=()):
            statements.append(sql)
            return Result()

    monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
    monkeypatch.setattr(mh.psycopg, "connect", lambda *args, **kwargs: Conn())
    assert mh.handler({"action": "purge-verify-tokens"}) == {
        "status": "ok", "action": "purge-verify-tokens", "deleted": 3,
    }
    # One statement, scoped to the verify purpose: 1a's own reset tokens stay.
    assert statements == ["delete from password_reset_tokens where purpose = 'verify'"]


# --- seeds are local-only (hardening U3) -------------------------------------


class FakePgResult:
    status = ExecStatus.COMMAND_OK
    error_message = b""


class FakePgConn:
    """The libpq handle ``_run_script`` drives: records each script's text."""

    def __init__(self, scripts):
        self.scripts = scripts

    def exec_(self, query: bytes):
        self.scripts.append(query.decode("utf-8"))
        return FakePgResult()


class FakeMigrateConn(FakeConn):
    """FakeConn as ``psycopg.connect`` hands it to the handler: a context
    manager carrying a ``pgconn`` that records the scripts it runs."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.scripts: list[str] = []
        self.pgconn = FakePgConn(self.scripts)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestSeedsAreLocalOnly:
    @pytest.fixture
    def db_tree(self, tmp_path, monkeypatch):
        migrations = tmp_path / "migrations"
        seeds = tmp_path / "seeds"
        migrations.mkdir()
        seeds.mkdir()
        (migrations / "001_first.sql").write_text("select 'migration-one';")
        (migrations / "002_second.sql").write_text("select 'migration-two';")
        (seeds / "001_demo_seed.sql").write_text("select 'seed-demo';")
        (seeds / "002_live_demo_seed.sql").write_text("select 'seed-live';")
        monkeypatch.setattr(mh, "MIGRATIONS_DIR", migrations)
        monkeypatch.setattr(mh, "SEEDS_DIR", seeds)
        return seeds

    @pytest.fixture
    def conn(self, monkeypatch):
        fake = FakeMigrateConn()
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake/fake")
        monkeypatch.delenv("DB_APP_PASSWORD", raising=False)
        monkeypatch.delenv("PROVIDER_BOOTSTRAP_SSM", raising=False)
        monkeypatch.setattr(mh.psycopg, "connect", lambda *args, **kwargs: fake)
        return fake

    @pytest.fixture
    def app_env(self, monkeypatch):
        def _set(value):
            monkeypatch.setenv("APP_ENV", value)
            get_settings.cache_clear()

        yield _set
        get_settings.cache_clear()

    @pytest.fixture
    def opened(self, monkeypatch):
        """Every path read through ``Path.read_text`` while the handler runs."""
        paths: list[Path] = []
        real_read_text = Path.read_text

        def spy(self, *args, **kwargs):
            paths.append(self)
            return real_read_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", spy)
        return paths

    @staticmethod
    def _markers(conn):
        return [
            params[0] for sql, params in conn.statements
            if sql.startswith("insert into public.saferide_migrations")
        ]

    def test_production_applies_migrations_and_names_each_skipped_seed(
        self, db_tree, conn, app_env, opened,
    ):
        app_env("production")
        report = mh.handler()

        assert report["status"] == "ok"
        assert report["migrations"] == {"applied": ["001_first", "002_second"], "skipped": []}
        assert report["seeds"] == {
            "skipped": "APP_ENV=production",
            "files": ["001_demo_seed.sql", "002_live_demo_seed.sql"],
        }
        assert self._markers(conn) == ["001_first", "002_second"]
        assert conn.scripts == ["select 'migration-one';", "select 'migration-two';"]
        assert not [path for path in opened if path.parent == db_tree], "a seed file was opened"

    def test_local_applies_migrations_and_seeds(self, db_tree, conn, app_env):
        app_env("local")
        report = mh.handler()

        assert report["migrations"] == {"applied": ["001_first", "002_second"], "skipped": []}
        assert report["seeds"] == {
            "applied": ["seed:001_demo_seed", "seed:002_live_demo_seed"], "skipped": [],
        }
        assert self._markers(conn) == [
            "001_first", "002_second", "seed:001_demo_seed", "seed:002_live_demo_seed",
        ]
        assert conn.scripts[2:] == ["select 'seed-demo';", "select 'seed-live';"]


# --- the nightly retention action (hardening U12) -----------------------------

RUN_ID = 41


class FakeRetentionConn:
    """What ``psycopg.connect`` hands the retention action: a context manager
    whose ``transaction()`` records each batch boundary."""

    def __init__(self, journal):
        self.journal = journal
        self.closed = False
        self.broken = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @contextlib.contextmanager
    def transaction(self):
        self.journal.append("begin")
        yield
        self.journal.append("commit")


class Context:
    """A Lambda context reporting a fixed remaining time."""

    def __init__(self, remaining_ms):
        self.remaining_ms = remaining_ms

    def get_remaining_time_in_millis(self):
        return self.remaining_ms


def _step(journal, key, results):
    """A fake bounded delete answering ``results`` in turn (then 0); an
    exception in the list is raised instead."""
    answers = iter(results)

    def delete_batch(conn, limit):
        answer = next(answers, 0)
        journal.append(f"{key}:{limit}")
        if isinstance(answer, Exception):
            raise answer
        return answer

    return delete_batch


def _outcome(school_id, trail, keys=0, batch_rows=2000):
    return {
        "school_id": school_id, "retention_days": 90, "trail_rows_deleted": trail,
        "exceptions_nulled": 1, "events_nulled": 0, "keys_deleted": keys,
        "batch_rows": batch_rows,
    }


@pytest.fixture
def retention(monkeypatch):
    """The action wired to fakes: two steps (batch 2 and 3), three schools and
    a scripted purge_batch per school. Returns the knobs and the journal."""
    journal: list[str] = []
    closed: list[tuple] = []
    conns: list[FakeRetentionConn] = []
    state = {
        "steps": {"live_notifications": [1], "auth_sessions": [0]},
        "batches": {
            "school-1": [_outcome("school-1", 2000), _outcome("school-1", 7)],
            "school-2": [None],
            "school-3": [_outcome("school-3", 0)],
        },
    }

    def connect(*args, **kwargs):
        conn = FakeRetentionConn(journal)
        conns.append(conn)
        return conn

    monkeypatch.setenv("DATABASE_URL", "postgresql://fake/fake")
    monkeypatch.setattr(mh.psycopg, "connect", connect)
    monkeypatch.setattr(retention_dao, "open_run", lambda conn: journal.append("open") or RUN_ID)
    monkeypatch.setattr(
        retention_dao, "close_run",
        lambda conn, run_id, status, counts: closed.append((run_id, status, dict(counts))),
    )
    monkeypatch.setattr(retention_dao, "set_statement_timeout", lambda conn: None)
    monkeypatch.setattr(
        retention_dao, "schools_oldest_trail_first",
        lambda conn: journal.append("schools") or list(state["batches"]),
    )

    def purge_batch(conn, school_id):
        journal.append(f"trail:{school_id}")
        return state["batches"][school_id].pop(0)

    monkeypatch.setattr(position_dao, "purge_batch", purge_batch)

    def install():
        steps = state["steps"]
        sizes = {"live_notifications": 2, "auth_sessions": 3}
        monkeypatch.setattr(retention_dao, "STEPS", tuple(
            (key, _step(journal, key, steps[key]), sizes[key]) for key in steps
        ))
        monkeypatch.setattr(retention_dao, "new_counts", lambda: {
            **{key: 0 for key in steps}, **{key: 0 for key in retention_dao.TRAIL_COUNTS},
            "driver_action_keys": 0,
        })

    return {"journal": journal, "closed": closed, "conns": conns, "state": state, "install": install}


def _metric_lines(capsys) -> list[dict]:
    out = capsys.readouterr().out
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def test_retention_runs_the_cheap_deletes_before_any_trail_batch_then_closes_ok(retention, capsys):
    retention["state"]["steps"]["live_notifications"] = [2, 2, 1]
    retention["install"]()

    report = mh.handler({"action": "retention"}, None)

    journal = retention["journal"]
    trail_calls = [entry for entry in journal if entry.startswith("trail:")]
    # Each step loops until a short batch; every step precedes the trail.
    assert [e for e in journal if ":" in e and not e.startswith("trail:")] == [
        "live_notifications:2", "live_notifications:2", "live_notifications:2", "auth_sessions:3",
    ]
    assert journal.index("schools") > journal.index("auth_sessions:3")
    # school-1 until a short batch, school-2's lock is held (skipped),
    # school-3 is clean after one.
    assert trail_calls == ["trail:school-1", "trail:school-1", "trail:school-2", "trail:school-3"]
    (closed,) = retention["closed"]
    assert closed[:2] == (RUN_ID, "ok")
    assert closed[2]["live_notifications"] == 5
    assert closed[2]["run_positions"] == 2007
    assert closed[2]["trail_batches"] == 3 and closed[2]["schools_locked"] == 1
    assert report == {
        "status": "ok", "action": "retention", "run_id": RUN_ID, "job_status": "ok",
        "stopped": "clean", "locked_school_ids": ["school-2"], "counts": closed[2],
    }
    (metric,) = _metric_lines(capsys)
    assert metric[mh.RETENTION_METRIC_NAME] == 1 and metric["status"] == "ok"


def test_a_held_purge_lock_skips_that_school_and_the_run_carries_on(retention, capsys):
    retention["state"]["batches"] = {
        "school-1": [None], "school-2": [_outcome("school-2", 3)],
    }
    retention["install"]()

    report = mh.handler({"action": "retention"}, None)

    assert report["locked_school_ids"] == ["school-1"]
    assert report["counts"]["run_positions"] == 3
    assert report["job_status"] == "ok"
    assert len(_metric_lines(capsys)) == 1


def test_under_the_budget_floor_the_trail_stops_after_the_current_batch(retention, capsys):
    retention["install"]()

    report = mh.handler({"action": "retention"}, Context(mh.RETENTION_BUDGET_FLOOR_MS - 1))

    trail_calls = [e for e in retention["journal"] if e.startswith("trail:")]
    assert trail_calls == ["trail:school-1"]  # the batch in hand, then stop
    (closed,) = retention["closed"]
    assert closed[1] == "stopped"
    assert report["job_status"] == "stopped" and report["stopped"] == "budget"
    # A budget stop is success: the heartbeat's metric is still emitted.
    (metric,) = _metric_lines(capsys)
    assert metric[mh.RETENTION_METRIC_NAME] == 1 and metric["status"] == "stopped"


def test_a_clean_school_still_yields_to_the_budget_before_the_next_one(retention, capsys):
    retention["state"]["batches"] = {
        "school-1": [_outcome("school-1", 5)], "school-2": [_outcome("school-2", 5)],
    }
    retention["install"]()

    mh.handler({"action": "retention"}, Context(1_000))

    assert [e for e in retention["journal"] if e.startswith("trail:")] == ["trail:school-1"]
    assert retention["closed"][0][1] == "stopped"


def test_with_time_left_the_budget_never_stops_the_run(retention, capsys):
    retention["install"]()

    report = mh.handler({"action": "retention"}, Context(mh.RETENTION_BUDGET_FLOOR_MS + 1))

    assert report["job_status"] == "ok"
    assert retention["state"]["batches"]["school-1"] == []  # both batches ran


def test_a_full_backlog_out_of_budget_gives_every_step_one_batch_and_skips_the_trail(
    retention, capsys,
):
    retention["state"]["steps"] = {"live_notifications": [2, 2, 2], "auth_sessions": [3, 3]}
    retention["install"]()

    report = mh.handler({"action": "retention"}, Context(0))

    journal = retention["journal"]
    assert [e for e in journal if ":" in e] == ["live_notifications:2", "auth_sessions:3"]
    assert "schools" not in journal
    assert report["job_status"] == "stopped"
    assert report["counts"]["live_notifications"] == 2 and report["counts"]["auth_sessions"] == 3
    assert len(_metric_lines(capsys)) == 1


def test_a_failing_delete_writes_a_failed_row_and_raises_without_the_metric(
    retention, capsys, caplog,
):
    error = psycopg.errors.QueryCanceled("canceling statement: row (parent@saferide.test)")
    retention["state"]["steps"] = {"live_notifications": [1], "auth_sessions": [error]}
    retention["install"]()
    caplog.set_level(logging.INFO, logger="saferide.retention")

    with pytest.raises(mh.RetentionFailed) as raised:
        mh.handler({"action": "retention"}, None)

    (closed,) = retention["closed"]
    assert closed[:2] == (RUN_ID, "failed")
    assert closed[2]["live_notifications"] == 1  # what ran before is recorded
    assert {k: closed[2][k] for k in ("failed_step", "error", "sqlstate")} == {
        "failed_step": "auth_sessions", "error": "QueryCanceled", "sqlstate": "57014",
    }
    # What Lambda prints for the raise: the step, class and state only — not
    # the database's text, and no chained cause.
    message = str(raised.value)
    assert message == f"retention run {RUN_ID} failed at auth_sessions: QueryCanceled sqlstate=57014"
    assert raised.value.__cause__ is None and raised.value.__suppress_context__
    assert "saferide.test" not in caplog.text
    assert any(r.getMessage().startswith("retention-failed run_id=41 step=auth_sessions")
               for r in caplog.records)
    assert not any(r.exc_info for r in caplog.records)
    # No success metric for a failed run: the heartbeat must not be fed.
    assert _metric_lines(capsys) == []
    assert not [e for e in retention["journal"] if e.startswith("trail:")]


def test_a_failing_trail_batch_names_the_trail_step(retention, capsys, monkeypatch):
    retention["install"]()

    def broken(conn, school_id):
        raise psycopg.errors.DeadlockDetected("deadlock")

    monkeypatch.setattr(position_dao, "purge_batch", broken)
    with pytest.raises(mh.RetentionFailed, match="failed at run_positions: DeadlockDetected"):
        mh.handler({"action": "retention"}, None)
    assert retention["closed"][0][1] == "failed"
    assert retention["closed"][0][2]["failed_step"] == "run_positions"


def test_the_failed_row_goes_through_a_fresh_connection_when_the_run_connection_broke(
    retention, capsys, monkeypatch,
):
    retention["install"]()
    recorded_on = []
    fake_close = retention_dao.close_run

    def close_run(conn, run_id, status, counts):
        recorded_on.append(conn)
        fake_close(conn, run_id, status, counts)

    def first_conn_breaks(conn, limit):
        retention["conns"][0].broken = True
        raise psycopg.OperationalError("server closed the connection")

    monkeypatch.setattr(retention_dao, "STEPS", (("live_notifications", first_conn_breaks, 2),))
    monkeypatch.setattr(retention_dao, "close_run", close_run)
    with pytest.raises(mh.RetentionFailed):
        mh.handler({"action": "retention"}, None)
    assert len(retention["conns"]) == 2
    assert recorded_on == [retention["conns"][1]]
    assert retention["closed"][0][1] == "failed"


def test_a_failed_row_that_cannot_be_written_still_raises_the_runs_own_error(
    retention, capsys, caplog, monkeypatch,
):
    retention["state"]["steps"] = {"live_notifications": [ValueError("boom")], "auth_sessions": [0]}
    retention["install"]()

    def close_run(conn, run_id, status, counts):
        raise psycopg.OperationalError("gone")

    monkeypatch.setattr(retention_dao, "close_run", close_run)
    caplog.set_level(logging.INFO, logger="saferide.retention")
    with pytest.raises(mh.RetentionFailed, match="live_notifications: ValueError"):
        mh.handler({"action": "retention"}, None)
    assert "retention-failure-unrecorded run_id=41 error=OperationalError" in caplog.text


def test_the_metric_is_one_standalone_emf_document_on_stdout_never_a_log_record(retention, capsys):
    """Lambda's JSON LoggingConfig wraps every logging record in its own
    object, so an EMF document logged would arrive as a string inside
    `message` and CloudWatch would extract nothing. The document goes to
    stdout as one line, and no record carries it."""
    retention["install"]()
    runtime = JsonLines()  # the runtime's handler, on the root before the handler runs
    root = logging.getLogger()
    previous = root.level
    root.addHandler(runtime)
    root.setLevel(logging.INFO)
    try:
        before_ms = int(time.time() * 1000)
        mh.handler({"action": "retention"}, None)
        after_ms = int(time.time() * 1000)
    finally:
        root.removeHandler(runtime)
        root.setLevel(previous)

    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.strip()]
    assert len(lines) == 1
    document = json.loads(lines[0])
    assert lines[0].startswith("{") and lines[0].endswith("}")  # nothing before or after
    # The EMF specification: _aws metadata on the root, the metric a number
    # on the root, an (allowed) empty dimension set, a millisecond timestamp.
    meta = document["_aws"]
    assert before_ms <= meta["Timestamp"] <= after_ms
    assert meta["CloudWatchMetrics"] == [{
        "Namespace": "SafeRide", "Dimensions": [[]],
        "Metrics": [{"Name": "RetentionRunSucceeded", "Unit": "Count"}],
    }]
    assert document["RetentionRunSucceeded"] == 1
    assert "level" not in document  # Lambda files a level-less JSON line at INFO
    assert document["run_id"] == RUN_ID and document["counts"]["live_notifications"] == 1
    assert not [line for line in runtime.lines if "_aws" in line or "RetentionRunSucceeded" in line]


def test_the_heartbeat_alarm_watches_the_metric_the_action_emits():
    alarm = load("backend").properties("RetentionHeartbeat")
    assert (alarm["Namespace"], alarm["MetricName"]) == (
        mh.RETENTION_METRIC_NAMESPACE, mh.RETENTION_METRIC_NAME,
    )
    # The schedule's input is the action this handler dispatches.
    target = load("backend").block("RetentionSchedule")
    assert """Input: '{"action": "retention"}'""" in target


def test_a_near_miss_action_name_is_still_refused_before_connecting(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert mh.handler({"action": "retention-now"})["status"] == "error"


# --- the retention deletes' SQL (shape only; behaviour in the integration file) --


class RecordingConn:
    def __init__(self):
        self.statements: list[tuple[str, tuple]] = []

    def execute(self, sql, params=()):
        self.statements.append((sql, tuple(params)))

        class Result:
            rowcount = 0

        return Result()


@pytest.mark.parametrize("key,delete_batch,batch_rows", retention_dao.STEPS)
def test_every_step_is_bounded_oldest_first_and_skips_locked_rows(key, delete_batch, batch_rows):
    conn = RecordingConn()
    assert delete_batch(conn, batch_rows) == 0
    ((statement, params),) = conn.statements
    assert "for update" in statement and "skip locked" in statement
    assert "order by" in statement and "limit %s" in statement
    assert params[-1] == batch_rows
    # Absolute instants: seconds, never an interval's calendar day field.
    assert "make_interval(secs => %s * 86400)" in statement
    assert "interval '" not in statement
    # Never the kept tables.
    assert "delete from live_admin_audit" not in statement and "delete from live_incidents" not in statement


def test_the_steps_and_their_ages():
    by_key = {key: (fn, rows) for key, fn, rows in retention_dao.STEPS}
    assert list(by_key) == [
        "live_notifications", "password_reset_tokens", "auth_sessions", "auth_preauth_tokens",
        "pin_login_failures", "driver_action_keys", "unverified_parent_accounts",
    ]
    ages = {}
    for key, (fn, rows) in by_key.items():
        conn = RecordingConn()
        fn(conn, rows)
        ages[key] = conn.statements[0][1][0]
    assert ages == {
        "live_notifications": 90, "password_reset_tokens": 1, "auth_sessions": 7,
        "auth_preauth_tokens": 7, "pin_login_failures": 1, "driver_action_keys": 7,
        "unverified_parent_accounts": 30,
    }
    assert by_key["unverified_parent_accounts"][1] == retention_dao.ACCOUNT_BATCH_ROWS == 50
    assert {rows for key, (_, rows) in by_key.items() if key != "unverified_parent_accounts"} == {2000}
    assert set(retention_dao.new_counts()) == set(by_key) | set(retention_dao.TRAIL_COUNTS)


def test_the_unverified_parent_delete_keeps_every_referenced_or_grandfathered_account():
    from app.dao.verification_sql import unverified_sql

    conn = RecordingConn()
    retention_dao.delete_unverified_parents(conn, 50)
    ((statement, params),) = conn.statements
    assert unverified_sql("u") in statement  # D1's one predicate
    assert "r.role = 'parent'" in statement
    for table, column in retention_dao.USER_REFERENCES:
        assert f"not exists (select 1 from {table} x where x.{column} = u.id)" in statement
    # The plan's three, and the never-touched incident and audit columns.
    assert {("live_parent_students", "parent_id"), ("school_memberships", "user_id"),
            ("provider_accounts", "user_id"), ("live_incidents", "driver_id"),
            ("live_incidents", "acknowledged_by"), ("live_admin_audit", "actor_id")} <= set(
        retention_dao.USER_REFERENCES
    )
    # Never an account the office made unverified by changing its email: the
    # grace counts from creation, so only never-verified accounts qualify.
    # The match is the row account_dao.update_parent writes.
    assert (
        "and not exists (select 1 from live_admin_audit e"
        " where e.action = 'parent-email-changed'"
        " and e.resource_type = 'parent'"
        " and e.resource_id = u.id::text)"
    ) in " ".join(statement.split())
    assert "for update of u skip locked" in statement
    assert params == (30, 50)
