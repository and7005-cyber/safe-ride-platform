"""Tenancy post-steps of the migrate handler (U2): bootstrap parsing and
gating, role-sync skip. Pure unit tests — the fake connection records SQL."""

import base64
import json

import pytest

from app import migrate_handler as mh


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
