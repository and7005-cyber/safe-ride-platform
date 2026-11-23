"""One-shot database migration/seed Lambda for the SafeRide backend.

Mirrors the local ``scripts/start-local.sh`` flow: it applies every file in
``db/migrations`` then every file in ``db/seeds``, in filename order, tracking
what has run in a ``saferide_migrations`` marker table so re-invocation is
idempotent. Invoke it manually (or from the deploy script) after the stack is
up; it is not wired to the API.

Seeds are demo and snapshot data, so they apply only when ``APP_ENV`` is
``local``. Anywhere else (the deployed function sets ``APP_ENV=production``)
the seed files are listed by name in the report and never opened.

SQL files are executed with libpq's simple-query protocol
(``pgconn.exec_``) so multi-statement scripts and dollar-quoted ``do $$``
blocks run exactly as ``psql`` would — naive semicolon splitting would break on
the enum/function blocks in the migrations.

Named event actions (this Lambda holds the writable connection; the verify
Lambda is server-enforced read-only):

- ``{"action": "gps-purge", "school_id": "<uuid>", "max_batches": 100}`` —
  the on-demand GPS retention purge (GPS plan U7/R12) for a school that has
  stopped running, whose trail the post-Start-Run pass therefore never
  reaches. Loops ``position_dao.purge_batch`` — the same bounded pass the
  API runs after Start Run: trail rows older than the school's retention by
  ``received_at`` (2,000 per batch, 2 s statement timeout), coordinates
  nulled on exceptions and events older than retention, idempotency keys
  older than seven days — one transaction per batch, until a batch deletes
  nothing or ``max_batches`` (default 100, at most 1,000) is reached. Stops
  and reports ``locked`` when a Start Run pass holds the school's advisory
  lock. The ``gps`` verify set's ``trail-rows-past-retention`` says when it
  is due. Nothing is migrated or seeded on this path.
- ``{"action": "retention"}`` — the nightly retention job (hardening U12),
  invoked asynchronously by EventBridge Scheduler at 01:30 Africa/Nairobi
  (and by hand once after the deploy that creates the schedule). Opens an
  ``ops_job_runs`` row (``running``); runs the cheap bounded deletes of
  ``retention_dao.STEPS`` first, each looped until a batch comes back short,
  so a trail backlog can never starve them; then loops every school through
  ``position_dao.purge_batch`` under the invocation's remaining-time budget,
  skipping a school whose purge lock is held. Under 30 seconds left it stops
  after the current batch: ``stopped``, which is success — the next night
  carries on. Closes the row with the status and the per-table counts, then
  prints ONE CloudWatch embedded-metric-format line to stdout with
  ``RetentionRunSucceeded`` = 1 (the heartbeat alarm's metric). Any error
  closes the row ``failed`` and RAISES — the migrate function's errors alarm
  sees a raise, never a returned error status — and prints no metric.
- ``{"action": "purge-verify-tokens"}`` — before a rollback to Release 1a
  (hardening D52): deletes every email-verification token. 1a's reset
  consumes a token by its hash alone, so a live ``verify`` token would work
  as a reset link once the backend is back on ``release/1a``. Invoked on 1b
  immediately before the 1a backend deploy (Release 1b checklist, "Rollback
  rehearsal"); the 1b frontend on a 1a backend creates no verification
  tokens, so only a sign-up in the minutes of that deploy can add one, which
  the ``hardening`` set's ``token-rows-by-purpose`` shows. A parent whose
  link was deleted asks for a new one (Resend) once 1b is back, or the office
  confirms the sign-up. Reports the count only.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path

import psycopg
from psycopg.pq import ExecStatus
from psycopg.rows import dict_row

from app.core.config import get_settings

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


def _apply_seeds(conn: psycopg.Connection) -> dict:
    """Apply ``db/seeds`` locally; elsewhere name the files and leave them shut."""
    settings = get_settings()
    if not settings.is_local:
        return {
            "skipped": f"APP_ENV={settings.app_env}",
            "files": [path.name for path in sorted(SEEDS_DIR.glob("*.sql"))],
        }
    return _apply_dir(conn, SEEDS_DIR, prefix="seed:")


def handler(event=None, context=None) -> dict:
    """Apply migrations then (locally) seeds, then the tenancy post-steps.
    Idempotent. A named ``action`` in the event runs that action instead (see
    module docstring)."""
    action = (event or {}).get("action")
    if action == "gps-purge":
        return _gps_purge(event or {})
    if action == "retention":
        return _retention(context)
    if action == "purge-verify-tokens":
        return _purge_verify_tokens()
    if action is not None:
        return {"status": "error", "reason": f"unknown action {action!r}"}
    with psycopg.connect(_database_url(), autocommit=True) as conn:
        _ensure_marker_table(conn)
        migrations = _apply_dir(conn, MIGRATIONS_DIR, prefix="")
        seeds = _apply_seeds(conn)
        app_role = _sync_app_role(conn)
        bootstrap_data = _fetch_bootstrap()
        provider_bootstrap = (
            _bootstrap_providers(conn, bootstrap_data)
            if bootstrap_data is not None
            else {"skipped": "PROVIDER_BOOTSTRAP_SSM not set"}
        )

    return {
        "status": "ok",
        "migrations": migrations,
        "seeds": seeds,
        "app_role": app_role,
        "provider_bootstrap": provider_bootstrap,
    }




# --- Tenancy post-steps (U2) -------------------------------------------------
# The runtime application role and the provider bootstrap are handled here, in
# Python, because their inputs (a role password, SSM-held account material)
# cannot live in a committed SQL file. Both steps are idempotent and skip
# cleanly when their inputs are absent (local runs, pre-tenancy stacks).


def _sync_app_role(conn: psycopg.Connection) -> dict:
    """Create/refresh the ``saferide_app`` runtime role and its grants."""
    password = os.environ.get("DB_APP_PASSWORD")
    if not password:
        return {"skipped": "DB_APP_PASSWORD not set"}

    from psycopg import sql

    conn.execute(
        sql.SQL(
            """
            do $$
            begin
              if not exists (select 1 from pg_roles where rolname = 'saferide_app') then
                execute format('create role saferide_app login nobypassrls password %L', {pw});
              else
                execute format('alter role saferide_app with login nobypassrls password %L', {pw});
              end if;
            end
            $$;
            """
        ).format(pw=sql.Literal(password))
    )
    conn.execute("grant usage on schema public to saferide_app")
    conn.execute(
        "grant select, insert, update, delete on all tables in schema public to saferide_app"
    )
    conn.execute("grant usage, select on all sequences in schema public to saferide_app")
    conn.execute(
        "alter default privileges in schema public "
        "grant select, insert, update, delete on tables to saferide_app"
    )
    conn.execute(
        "alter default privileges in schema public "
        "grant usage, select on sequences to saferide_app"
    )
    # Membership so the verify Lambda (connecting as the master role) can
    # SET ROLE saferide_app and prove the policies bind (tenancy-rls set).
    conn.execute("grant saferide_app to current_user")
    return {"role": "saferide_app", "synced": True}


def _parse_bootstrap(raw: str) -> dict:
    """Decode the provider-bootstrap SSM value: JSON, or base64url(JSON)."""
    import base64
    import json as _json

    text = raw.strip()
    if not text.startswith("{"):
        pad = "=" * (-len(text) % 4)
        text = base64.urlsafe_b64decode(text + pad).decode("utf-8")
    data = _json.loads(text)
    version = data.get("version")
    providers = data.get("providers")
    if not isinstance(version, int) or version < 1:
        raise RuntimeError("provider bootstrap: 'version' must be a positive integer")
    if not isinstance(providers, list) or not providers:
        raise RuntimeError("provider bootstrap: 'providers' must be a non-empty list")
    for entry in providers:
        for key in ("email", "full_name", "password_hash"):
            if not entry.get(key):
                raise RuntimeError(f"provider bootstrap: provider missing {key!r}")
        if not str(entry["password_hash"]).startswith("pbkdf2_sha256$"):
            raise RuntimeError(
                f"provider bootstrap: {entry['email']} password_hash is not a PBKDF2 hash"
            )
    return data


def _fetch_bootstrap() -> dict | None:
    """Read the bootstrap parameter named in the environment, if configured."""
    param_name = os.environ.get("PROVIDER_BOOTSTRAP_SSM")
    if not param_name:
        return None
    import boto3  # available in the Lambda runtime; unused locally

    value = boto3.client("ssm").get_parameter(Name=param_name, WithDecryption=True)[
        "Parameter"
    ]["Value"]
    return _parse_bootstrap(value)


def _bootstrap_providers(conn: psycopg.Connection, data: dict) -> dict:
    """Insert (or, on a version bump, re-key) the bootstrap provider accounts.

    Runs only once per bootstrap ``version`` (marker table), and only when the
    tenancy schema exists. An email that already belongs to a non-provider
    identity fails the deploy naming the address — provider identities never
    piggyback on parent or staff accounts.
    """
    if conn.execute("select to_regclass('public.provider_accounts')").fetchone()[0] is None:
        return {"skipped": "provider_accounts table not present yet"}

    marker = f"provider-bootstrap:v{data['version']}"
    if _already_applied(conn, marker):
        return {"skipped": f"{marker} already applied"}

    import secrets

    applied = []
    for entry in data["providers"]:
        email = entry["email"].strip().lower()
        row = conn.execute(
            "select u.id, (p.user_id is not null) as is_provider "
            "from app_users u left join provider_accounts p on p.user_id = u.id "
            "where lower(u.email) = %s",
            (email,),
        ).fetchone()
        if row and not row[1]:
            raise RuntimeError(
                f"provider bootstrap: {email} already belongs to a non-provider identity"
            )
        if row:
            conn.execute(
                "update app_users set password_hash = %s, must_change_password = true "
                "where id = %s",
                (entry["password_hash"], row[0]),
            )
            applied.append(f"rekeyed:{email}")
            continue
        user_id = conn.execute(
            "insert into app_users (email, password_hash, full_name, must_change_password) "
            "values (%s, %s, %s, true) returning id",
            (email, entry["password_hash"], entry["full_name"]),
        ).fetchone()[0]
        conn.execute(
            "insert into provider_accounts (user_id, totp_salt) values (%s, %s)",
            (user_id, secrets.token_hex(16)),
        )
        applied.append(f"created:{email}")

    _mark_applied(conn, marker)
    return {"marker": marker, "providers": applied}



# --- On-demand GPS retention purge (GPS plan U7/R12) ---------------------------

GPS_PURGE_MAX_BATCHES_DEFAULT = 100
GPS_PURGE_MAX_BATCHES_CAP = 1000


def _parse_gps_purge_event(event: dict) -> tuple[str, int]:
    """``(school_id, max_batches)`` from the event, or raise ValueError."""
    try:
        school_id = str(uuid.UUID(str(event.get("school_id"))))
    except (TypeError, ValueError) as error:
        raise ValueError("gps-purge requires a valid school_id (uuid)") from error
    raw = event.get("max_batches", GPS_PURGE_MAX_BATCHES_DEFAULT)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        raise ValueError("gps-purge max_batches must be a positive integer")
    return school_id, min(raw, GPS_PURGE_MAX_BATCHES_CAP)


def _gps_purge(event: dict) -> dict:
    """Loop the bounded purge pass for one school until nothing is left,
    the school's lock is held elsewhere, or the batch bound is reached.

    The master role is not subject to RLS, so the pass's own ``school_id``
    predicates are what confine it here — the same statements the scoped
    API path runs. Each batch is its own transaction (advisory lock and
    statement timeout end with it). The report carries ids and counts only.
    """
    try:
        school_id, max_batches = _parse_gps_purge_event(event)
    except ValueError as error:
        return {"status": "error", "reason": str(error)}

    from app.dao.position_dao import purge_batch  # lazy: not needed to migrate

    totals = {
        "trail_rows_deleted": 0, "exceptions_nulled": 0, "events_nulled": 0, "keys_deleted": 0,
    }
    batches = 0
    stopped = "clean"
    with psycopg.connect(_database_url(), autocommit=True, row_factory=dict_row) as conn:
        while batches < max_batches:
            with conn.transaction():
                outcome = purge_batch(conn, school_id)
            if outcome is None:
                stopped = "locked"
                break
            batches += 1
            for key in totals:
                totals[key] += outcome[key]
            if not any(outcome[key] for key in totals):
                break
        else:
            stopped = "batch-bound"
    return {
        "status": "ok",
        "action": "gps-purge",
        "school_id": school_id,
        "batches": batches,
        "stopped": stopped,
        **totals,
    }


# --- Before a rollback to Release 1a (hardening D52) ------------------------------

# Verification tokens only: reset tokens are what 1a itself writes and reads.
PURGE_VERIFY_TOKENS_SQL = "delete from password_reset_tokens where purpose = 'verify'"


def _purge_verify_tokens() -> dict:
    """Delete every email-verification token (see the module docstring)."""
    with psycopg.connect(_database_url(), autocommit=True) as conn:
        deleted = conn.execute(PURGE_VERIFY_TOKENS_SQL).rowcount
    return {"status": "ok", "action": "purge-verify-tokens", "deleted": deleted}


# --- The nightly retention job (hardening U12) -----------------------------------

logger = logging.getLogger("saferide.retention")

# Stop when less than this is left of the invocation (the function's timeout
# is 600 s): the batch in hand finishes — four statements of at most 2 s —
# and the row is closed well before Lambda would kill the run.
RETENTION_BUDGET_FLOOR_MS = 30_000
# The heartbeat alarm's metric (infra/backend/template.yaml, RetentionHeartbeat).
RETENTION_METRIC_NAMESPACE = "SafeRide"
RETENTION_METRIC_NAME = "RetentionRunSucceeded"


class RetentionFailed(RuntimeError):
    """Raised after the failed status is written. Its text names the run,
    the step, the error class and SQL state only: a database error's own
    message can quote a row, and Lambda prints the raised error's text."""


def _time_left(context) -> bool:
    """True while more than the floor remains. A manual run outside Lambda
    (no context) has no budget."""
    remaining = getattr(context, "get_remaining_time_in_millis", None)
    if remaining is None:
        return True
    return remaining() > RETENTION_BUDGET_FLOOR_MS


def _retention_steps(conn, counts: dict, context, progress: dict) -> bool:
    """The cheap bounded deletes, before any trail batch. Each step loops
    until a batch comes back short. Once the budget is gone every remaining
    step still gets its one batch (bounded by the statement timeout), so one
    table's backlog never starves the next. Returns True when the budget
    stopped a loop."""
    from app.dao import retention_dao

    stopped = False
    for key, delete_batch, batch_rows in retention_dao.STEPS:
        progress["step"] = key
        while True:
            with conn.transaction():
                retention_dao.set_statement_timeout(conn)
                deleted = delete_batch(conn, batch_rows)
            counts[key] += deleted
            if deleted < batch_rows:
                break
            if stopped or not _time_left(context):
                stopped = True
                break
    return stopped


def _retention_trail(conn, counts: dict, context, progress: dict, locked: list[str]) -> bool:
    """Every school through the bounded trail purge, oldest backlog first,
    each until a batch leaves nothing past retention. A held purge lock (a
    Start Run's pass) skips the school. Returns True when the budget stopped
    the loop — after the batch in hand."""
    from app.dao import retention_dao
    from app.dao.position_dao import purge_batch

    progress["step"] = "run_positions"
    for index, school_id in enumerate(retention_dao.schools_oldest_trail_first(conn)):
        if index and not _time_left(context):
            return True
        while True:
            with conn.transaction():
                outcome = purge_batch(conn, school_id)
            if outcome is None:
                counts["schools_locked"] += 1
                locked.append(school_id)
                break
            counts["trail_batches"] += 1
            counts["run_positions"] += outcome["trail_rows_deleted"]
            counts["run_exceptions_nulled"] += outcome["exceptions_nulled"]
            counts["run_exception_events_nulled"] += outcome["events_nulled"]
            counts["driver_action_keys"] += outcome["keys_deleted"]
            if (
                outcome["trail_rows_deleted"] < outcome["batch_rows"]
                and outcome["keys_deleted"] < outcome["batch_rows"]
            ):
                break
            if not _time_left(context):
                return True
    return False


def _record_failure(conn, run_id: int, counts: dict, failure: dict) -> None:
    """Close the row ``failed`` — on the run's connection, or on a fresh one
    when that connection died. Never raises: the caller raises the run's own
    error either way."""
    from app.dao import retention_dao

    try:
        if conn.closed or conn.broken:
            with psycopg.connect(_database_url(), autocommit=True, row_factory=dict_row) as fresh:
                retention_dao.close_run(fresh, run_id, "failed", {**counts, **failure})
        else:
            retention_dao.close_run(conn, run_id, "failed", {**counts, **failure})
    except Exception as record_error:  # noqa: BLE001 — the run's error is what must surface
        logger.error(
            "retention-failure-unrecorded run_id=%s error=%s",
            run_id, type(record_error).__name__,
        )


def retention_metric_document(run_id: int, status: str, counts: dict) -> dict:
    """One CloudWatch embedded-metric-format document: the success count with
    no dimensions (an empty dimension set), the counts beside it as plain
    properties — searchable in Logs Insights, not billed as metrics."""
    return {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": RETENTION_METRIC_NAMESPACE,
                    "Dimensions": [[]],
                    "Metrics": [{"Name": RETENTION_METRIC_NAME, "Unit": "Count"}],
                }
            ],
        },
        RETENTION_METRIC_NAME: 1,
        "job": "retention",
        "run_id": run_id,
        "status": status,
        "counts": counts,
    }


def _emit_retention_metric(run_id: int, status: str, counts: dict) -> None:
    """Straight to stdout, one line. The function logs JSON through Lambda's
    LoggingConfig, which wraps every ``logging`` record in its own object (the
    document would arrive as a string inside ``message`` and CloudWatch would
    extract nothing); stdout lines reach the log group as written, and a JSON
    line without a ``level`` passes the INFO filter as INFO."""
    print(
        json.dumps(retention_metric_document(run_id, status, counts), separators=(",", ":")),
        file=sys.stdout,
        flush=True,
    )


def _retention(context=None) -> dict:
    from app.core.request_context import database_fields, traceback_frames
    from app.dao import retention_dao

    counts = retention_dao.new_counts()
    locked: list[str] = []
    progress = {"step": "open"}
    with psycopg.connect(_database_url(), autocommit=True, row_factory=dict_row) as conn:
        run_id = retention_dao.open_run(conn)
        try:
            stopped = _retention_steps(conn, counts, context, progress)
            if not stopped:
                stopped = _retention_trail(conn, counts, context, progress, locked)
            status = "stopped" if stopped else "ok"
            progress["step"] = "close"
            retention_dao.close_run(conn, run_id, status, counts)
        except Exception as error:
            fields = database_fields(error)
            failure = {
                "failed_step": progress["step"],
                "error": type(error).__name__,
                "sqlstate": fields["sqlstate"],
            }
            logger.error(
                "retention-failed run_id=%s step=%s error=%s sqlstate=%s table=%s",
                run_id, failure["failed_step"], failure["error"],
                fields["sqlstate"] or "-", fields["table"] or "-",
                extra={"traceback": traceback_frames(error)},
            )
            _record_failure(conn, run_id, counts, failure)
            raise RetentionFailed(
                f"retention run {run_id} failed at {failure['failed_step']}: "
                f"{failure['error']} sqlstate={fields['sqlstate'] or '-'}"
            ) from None

    _emit_retention_metric(run_id, status, counts)
    return {
        "status": "ok",
        "action": "retention",
        "run_id": run_id,
        "job_status": status,
        "stopped": "budget" if status == "stopped" else "clean",
        "locked_school_ids": locked,
        "counts": counts,
    }


if __name__ == "__main__":  # local manual run against DATABASE_URL
    print(json.dumps(handler(), indent=2))
