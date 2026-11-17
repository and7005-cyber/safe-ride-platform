"""Tenancy post-steps of the migrate handler (U2): bootstrap parsing and
gating, role-sync skip; the local-only seed gate (hardening U3). Pure unit
tests — the fake connection records SQL."""

import base64
import json
from pathlib import Path

import pytest
from psycopg.pq import ExecStatus

from app import migrate_handler as mh
from app.core.config import get_settings


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
