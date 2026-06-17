"""One-shot database migration/seed Lambda for the SafeRide backend.

Mirrors the local ``scripts/start-local.sh`` flow: it applies every file in
``db/migrations`` then every file in ``db/seeds``, in filename order, tracking
what has run in a ``saferide_migrations`` marker table so re-invocation is
idempotent. Invoke it manually (or from the deploy script) after the stack is
up; it is not wired to the API.

SQL files are executed with libpq's simple-query protocol
(``pgconn.exec_``) so multi-statement scripts and dollar-quoted ``do $$``
blocks run exactly as ``psql`` would — naive semicolon splitting would break on
the enum/function blocks in the migrations.
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
from psycopg.pq import ExecStatus

DB_DIR = Path(__file__).resolve().parent.parent / "db"
MIGRATIONS_DIR = DB_DIR / "migrations"
SEEDS_DIR = DB_DIR / "seeds"
# Schema-qualified: a loaded pg_dump can empty the session search_path
# (set_config('search_path','',...)), which would break unqualified lookups.
MARKER_TABLE = "public.saferide_migrations"

_OK_STATUSES = {ExecStatus.COMMAND_OK, ExecStatus.TUPLES_OK, ExecStatus.EMPTY_QUERY}


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return url


def _strip_psql_meta(sql_text: str) -> str:
    """Drop psql-only backslash meta-commands (e.g. \\restrict/\\unrestrict that
    pg_dump emits on PG16+). libpq's simple-query protocol can't parse them, and
    they never carry data (INSERT/COPY lines don't start with a backslash)."""
    return "\n".join(
        line for line in sql_text.splitlines() if not line.lstrip().startswith("\\")
    )


def _run_script(conn: psycopg.Connection, sql_text: str) -> None:
    """Execute a whole .sql file as one implicit transaction (psql-style)."""
    result = conn.pgconn.exec_(_strip_psql_meta(sql_text).encode("utf-8"))
    if result.status not in _OK_STATUSES:
        message = result.error_message.decode("utf-8", "replace")
        raise RuntimeError(message.strip() or "SQL execution failed")


def _ensure_marker_table(conn: psycopg.Connection) -> None:
    conn.execute(
        f"create table if not exists {MARKER_TABLE} "
        "(id text primary key, applied_at timestamptz not null default now())"
    )


def _already_applied(conn: psycopg.Connection, marker_id: str) -> bool:
    row = conn.execute(
        f"select 1 from {MARKER_TABLE} where id = %s", (marker_id,)
    ).fetchone()
    return row is not None


def _mark_applied(conn: psycopg.Connection, marker_id: str) -> None:
    conn.execute(
        f"insert into {MARKER_TABLE} (id) values (%s) on conflict (id) do nothing",
        (marker_id,),
    )


def _apply_dir(conn: psycopg.Connection, directory: Path, prefix: str) -> dict[str, list[str]]:
    applied: list[str] = []
    skipped: list[str] = []
    if not directory.is_dir():
        raise RuntimeError(f"missing directory: {directory}")

    for path in sorted(directory.glob("*.sql")):
        marker_id = f"{prefix}{path.stem}"
        if _already_applied(conn, marker_id):
            skipped.append(marker_id)
            continue
        _run_script(conn, path.read_text(encoding="utf-8"))
        _mark_applied(conn, marker_id)
        applied.append(marker_id)

    return {"applied": applied, "skipped": skipped}


def handler(event=None, context=None) -> dict:
    """Apply migrations then seeds. Idempotent; safe to re-invoke."""
    with psycopg.connect(_database_url(), autocommit=True) as conn:
        _ensure_marker_table(conn)
        migrations = _apply_dir(conn, MIGRATIONS_DIR, prefix="")
        seeds = _apply_dir(conn, SEEDS_DIR, prefix="seed:")

    return {
        "status": "ok",
        "migrations": migrations,
        "seeds": seeds,
    }


if __name__ == "__main__":  # local manual run against DATABASE_URL
    import json

    print(json.dumps(handler(), indent=2))
