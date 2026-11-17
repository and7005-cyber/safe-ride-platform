"""The verify Lambda's registry and parameter kinds (hardening U2, decision
D14). Pure unit tests: a fake connection records what would be sent, and the
validation paths must answer before any connection opens."""

import datetime as dt

import pytest

from app import verify_handler as vh


class _FakeResult:
    def fetchall(self):
        return []


class _FakeConnection:
    def __init__(self):
        self.calls: list[tuple[str, object]] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        self.calls.append((sql, params))
        return _FakeResult()


@pytest.fixture()
def fake_conn(monkeypatch):
    conn = _FakeConnection()
    monkeypatch.setattr(vh, "_connect", lambda: conn)
    return conn


@pytest.fixture()
def no_conn(monkeypatch):
    def refuse():
        raise AssertionError("validation must answer before any connection opens")

    monkeypatch.setattr(vh, "_connect", refuse)


def test_every_entry_declares_a_known_kind_and_binds_only_with_a_placeholder():
    assert vh.PARAM_KINDS == ("none", "school", "timestamp")
    for name, entries in vh._CHECK_SETS.items():
        labels = [label for label, _, _ in entries]
        assert len(labels) == len(set(labels)), name
        for label, sql, kind in entries:
            assert kind in vh.PARAM_KINDS, (name, label)
            assert (kind == vh.NO_PARAM) == ("%s" not in sql), (name, label)


def test_the_existing_sets_keep_their_parameter_kinds():
    kinds = {
        name: sorted({kind for _, _, kind in entries})
        for name, entries in vh._CHECK_SETS.items()
    }
    assert kinds == {
        "migrations": ["none"],
        "migration-011": ["none"],
        "baseline": ["school"],
        "post-apply": ["school"],
        "tenancy-preflight": ["none"],
        "tenancy-post-move": ["none"],
        "tenancy-rls": ["none", "school"],
        "gps": ["none"],
        "hardening": ["none"],
        "restore-compare": ["timestamp"],
    }


def test_restore_compare_covers_the_append_only_tables_by_their_time_column():
    entries = vh._CHECK_SETS["restore-compare"]
    assert [label for label, _, _ in entries] == [
        "saferide_migrations", "live_admin_audit", "run_positions",
        "live_runs", "live_incidents", "live_notifications",
    ]
    columns = {"saferide_migrations": "applied_at", "run_positions": "received_at"}
    for label, sql, _ in entries:
        column = columns.get(label, "created_at")
        assert f"where {column} <= %s" in sql, label
        assert f"max({column})" in sql, label
        assert f"from public.{label}" in sql, label


@pytest.mark.parametrize("raw", [
    None, "", "  ", "nope", 1763359200, ["2026-11-17T06:00:00Z"],
    "2026-13-01T00:00:00+00:00", "2026-11-17T25:00:00+00:00",
    "2026-11-17T06:00:00", "2026-11-17",
])
def test_a_malformed_or_naive_timestamp_is_refused_before_connecting(no_conn, raw):
    payload = {"checks": "restore-compare"}
    if raw is not None:
        payload["timestamp"] = raw
    result = vh.handler(payload)
    assert result == {
        "status": "error",
        "reason": (
            "check set 'restore-compare' requires a valid timestamp "
            "(ISO 8601 with a UTC offset, e.g. 2026-11-17T06:00:00+03:00)"
        ),
    }


@pytest.mark.parametrize("raw, expected", [
    ("2026-11-17T06:00:00+03:00", dt.datetime(2026, 11, 17, 3, tzinfo=dt.timezone.utc)),
    ("2026-11-17T03:00:00Z", dt.datetime(2026, 11, 17, 3, tzinfo=dt.timezone.utc)),
    ("2026-11-17 03:00:00.5+00:00",
     dt.datetime(2026, 11, 17, 3, 0, 0, 500000, tzinfo=dt.timezone.utc)),
])
def test_a_valid_timestamp_is_bound_once_per_placeholder(fake_conn, raw, expected):
    result = vh.handler({"checks": "restore-compare", "timestamp": raw})
    assert result["status"] == "ok"
    assert len(fake_conn.calls) == len(vh._CHECK_SETS["restore-compare"])
    for sql, params in fake_conn.calls:
        assert params == (expected,) * sql.count("%s")
        assert all(p.tzinfo is not None for p in params)


def test_a_timestamp_set_ignores_school_id_and_a_school_set_ignores_timestamp(no_conn):
    # Each set reads only the event key its kind names.
    assert vh.handler(
        {"checks": "restore-compare", "school_id": "5cae0000-0000-0000-0000-000000000001"}
    )["status"] == "error"
    result = vh.handler({"checks": "baseline", "timestamp": "2026-11-17T06:00:00Z"})
    assert result["status"] == "error"
    assert "school_id" in result["reason"]


def test_no_param_entries_are_sent_verbatim_and_school_entries_bound(fake_conn):
    school = "5CAE0000-0000-0000-0000-000000000001"
    result = vh.handler({"checks": "tenancy-rls", "school_id": school})
    assert result["status"] == "ok"
    kinds = [kind for _, _, kind in vh._CHECK_SETS["tenancy-rls"]]
    for kind, (sql, params) in zip(kinds, fake_conn.calls):
        if kind == vh.NO_PARAM:
            # None, not (): psycopg parses placeholders in any non-None
            # params, so a literal % (LIKE 'v2:%') would be refused.
            assert params is None
        else:
            assert params == (school.lower(),) * sql.count("%s")


def test_the_hardening_set_runs_without_any_parameter(fake_conn):
    result = vh.handler({"checks": "hardening"})
    assert result["status"] == "ok"
    assert [params for _, params in fake_conn.calls] == [None] * 18
    assert [entry["label"] for entry in result["results"]][:2] == [
        "017-objects-present", "audit-check-widening-017",
    ]
