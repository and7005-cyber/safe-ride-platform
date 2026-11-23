"""The nightly retention deletes and their witness (hardening plan U12; Release 1b).

The migrate Lambda's ``retention`` action (``migrate_handler``) runs these at
01:30 Africa/Nairobi from EventBridge Scheduler, as the master role: no row
level security applies, so every statement here names exactly the rows it may
touch.

Each step is ONE bounded batch on the caller's transaction: at most its batch
size of victims, oldest first, ``for update skip locked`` (a row another
transaction holds is left for the next night, never waited on), under the
two-second statement timeout the GPS purge uses. The caller loops a step until
a batch comes back short. Cutoffs are absolute instants — ``now()`` minus N ×
86,400 seconds (``position_dao.RETENTION_CUTOFF_SQL``) — so the session time
zone never moves them.

What goes, and after how long:

- ``live_notifications`` older than 90 days (the parent app shows 7 days;
  the feed keeps 90 days of history);
- ``password_reset_tokens`` (reset and verify) used, or expired, more than a
  day ago;
- ``auth_sessions`` and ``auth_preauth_tokens`` expired more than 7 days ago;
- ``pin_login_failures`` older than a day (the sign-in lock reads 15 minutes);
- ``driver_action_keys`` past the GPS key TTL (seven days,
  ``GPS_ACTION_KEY_TTL_DAYS`` — the cutoff the post-Start-Run purge uses);
- parent accounts still unverified (D1: ``email_verification_required`` and no
  ``email_verified_at`` — grandfathered accounts, flag false, never qualify)
  30 days after creation, holding no student link of any status, no school
  membership and no provider account, and referenced by nothing else (see
  ``USER_REFERENCES``).

Kept for the life of the school and never touched here: ``live_incidents``
and ``live_admin_audit``. The position trail is ``position_dao.purge_batch``'s
job, per school, after these steps.
"""

from __future__ import annotations

from typing import Any, Callable

from psycopg import sql
from psycopg.types.json import Jsonb

from app.core.config import (
    GPS_ACTION_KEY_TTL_DAYS,
    GPS_PURGE_BATCH_ROWS,
    GPS_PURGE_STATEMENT_TIMEOUT_MS,
)
from app.dao import idempotency_dao
from app.dao.position_dao import RETENTION_CUTOFF_SQL
from app.dao.verification_sql import unverified_sql

JOB = "retention"

NOTIFICATION_RETENTION_DAYS = 90
SPENT_TOKEN_GRACE_DAYS = 1
EXPIRED_SESSION_GRACE_DAYS = 7
PIN_FAILURE_RETENTION_DAYS = 1
UNVERIFIED_PARENT_GRACE_DAYS = 30

BATCH_ROWS = GPS_PURGE_BATCH_ROWS
STATEMENT_TIMEOUT_MS = GPS_PURGE_STATEMENT_TIMEOUT_MS
# Deleting an account fires a foreign-key action on every table that points
# at app_users, most of them unindexed SET NULL columns (a scan each): a small
# batch keeps one inside the statement timeout even after a sign-up flood.
ACCOUNT_BATCH_ROWS = 50

# --- what an account deletion may and may not reach ------------------------------
# Every column that holds an app_users id, in two lists. A test
# (tests/integration/test_retention.py) reads the live foreign keys and fails
# when one is in neither, so a new reference must be classified here.
#
# The account's own private rows: they cascade with it, and nothing else reads
# them once the account is gone.
ACCOUNT_OWN_ROWS = (
    ("app_user_roles", "user_id"),
    ("auth_sessions", "user_id"),
    ("auth_preauth_tokens", "user_id"),
    ("password_reset_tokens", "user_id"),
    ("live_notifications", "user_id"),
    ("live_push_subscriptions", "user_id"),
    ("live_fcm_tokens", "user_id"),
)
# A row here keeps the account. The first three are the plan's rule (a link of
# any status, a membership of any role or state, a provider account). The
# incident and audit columns are ON DELETE SET NULL: deleting the account
# would rewrite rows that are kept for the life of the school, so the account
# stays instead. The rest are SET NULL attributions (who drove, marked, made,
# offered, contacted) or carry the id with no foreign key at all (an exception
# review, an idempotency key), which a delete would blank or leave dangling.
USER_REFERENCES = (
    ("live_parent_students", "parent_id"),
    ("school_memberships", "user_id"),
    ("provider_accounts", "user_id"),
    ("provider_support_sessions", "provider_user_id"),
    ("live_incidents", "driver_id"),
    ("live_incidents", "acknowledged_by"),
    ("live_admin_audit", "actor_id"),
    ("live_buses", "driver_id"),
    ("live_runs", "driver_id"),
    ("run_participation", "acting_driver_id"),
    ("run_participation", "contacted_by"),
    ("live_student_absences", "marked_by"),
    ("live_fleet_plans", "created_by"),
    ("school_memberships", "offered_by"),
    ("provider_accounts", "created_by"),
    ("run_exceptions", "reviewed_by"),
    ("driver_action_keys", "driver_id"),
)


def _unreferenced(alias: str) -> str:
    return "".join(
        f"\n              and not exists (select 1 from {table} x where x.{column} = {alias}.id)"
        for table, column in USER_REFERENCES
    )


# --- the steps -----------------------------------------------------------------


def delete_notifications(conn, limit: int) -> int:
    return conn.execute(
        f"""
        with victims as (
            select id from live_notifications
            where created_at < {RETENTION_CUTOFF_SQL}
            order by created_at
            limit %s
            for update skip locked
        )
        delete from live_notifications n using victims v where n.id = v.id
        """,
        (NOTIFICATION_RETENTION_DAYS, limit),
    ).rowcount


def delete_spent_tokens(conn, limit: int) -> int:
    """Reset and verify tokens alike: either one is dead a day after its use
    (or its voiding — an office email change marks them used) or expiry."""
    return conn.execute(
        f"""
        with victims as (
            select id from password_reset_tokens
            where used_at < {RETENTION_CUTOFF_SQL} or expires_at < {RETENTION_CUTOFF_SQL}
            order by created_at
            limit %s
            for update skip locked
        )
        delete from password_reset_tokens t using victims v where t.id = v.id
        """,
        (SPENT_TOKEN_GRACE_DAYS, SPENT_TOKEN_GRACE_DAYS, limit),
    ).rowcount


def delete_expired_sessions(conn, limit: int) -> int:
    return conn.execute(
        f"""
        with victims as (
            select id from auth_sessions
            where expires_at < {RETENTION_CUTOFF_SQL}
            order by expires_at
            limit %s
            for update skip locked
        )
        delete from auth_sessions s using victims v where s.id = v.id
        """,
        (EXPIRED_SESSION_GRACE_DAYS, limit),
    ).rowcount


def delete_expired_preauth_tokens(conn, limit: int) -> int:
    return conn.execute(
        f"""
        with victims as (
            select id from auth_preauth_tokens
            where expires_at < {RETENTION_CUTOFF_SQL}
            order by expires_at
            limit %s
            for update skip locked
        )
        delete from auth_preauth_tokens p using victims v where p.id = v.id
        """,
        (EXPIRED_SESSION_GRACE_DAYS, limit),
    ).rowcount


def delete_old_pin_failures(conn, limit: int) -> int:
    """The table has no key (one row per refused attempt), so the victims are
    named by ctid — stable within this one statement, whose snapshot and row
    locks it shares."""
    return conn.execute(
        f"""
        with victims as (
            select ctid from pin_login_failures
            where attempted_at < {RETENTION_CUTOFF_SQL}
            order by attempted_at
            limit %s
            for update skip locked
        )
        delete from pin_login_failures f using victims v where f.ctid = v.ctid
        """,
        (PIN_FAILURE_RETENTION_DAYS, limit),
    ).rowcount


def delete_expired_action_keys(conn, limit: int) -> int:
    return idempotency_dao.purge_expired_all_schools(
        conn, ttl_days=GPS_ACTION_KEY_TTL_DAYS, limit=limit
    )


def delete_unverified_parents(conn, limit: int) -> int:
    """Parent accounts that never verified, 30 days on, that nothing points at.

    The row lock is what keeps a concurrent rescue safe: verification (the
    owner's click, an emailed reset, the office's Confirm and reset) updates
    the account row itself, so a rescue that commits first changes the row
    and the locked re-check drops it, and one that starts later waits for
    this delete and finds no account. Their own private rows (sessions,
    tokens, push registrations, feed, role) cascade with them."""
    return conn.execute(
        f"""
        with victims as (
            select u.id from app_users u
            where {unverified_sql('u')}
              and u.created_at < {RETENTION_CUTOFF_SQL}
              and exists (select 1 from app_user_roles r
                          where r.user_id = u.id and r.role = 'parent'){_unreferenced('u')}
            order by u.created_at
            limit %s
            for update of u skip locked
        )
        delete from app_users a using victims v where a.id = v.id
        """,
        (UNVERIFIED_PARENT_GRACE_DAYS, limit),
    ).rowcount


# (counts key, one bounded batch, batch size), in run order. The keys are the
# tables the rows leave, so the job-runs row reads like the schema.
Step = tuple[str, Callable[[Any, int], int], int]
STEPS: tuple[Step, ...] = (
    ("live_notifications", delete_notifications, BATCH_ROWS),
    ("password_reset_tokens", delete_spent_tokens, BATCH_ROWS),
    ("auth_sessions", delete_expired_sessions, BATCH_ROWS),
    ("auth_preauth_tokens", delete_expired_preauth_tokens, BATCH_ROWS),
    ("pin_login_failures", delete_old_pin_failures, BATCH_ROWS),
    ("driver_action_keys", delete_expired_action_keys, BATCH_ROWS),
    ("unverified_parent_accounts", delete_unverified_parents, ACCOUNT_BATCH_ROWS),
)

# What the per-school trail loop adds (position_dao.purge_batch's counts).
TRAIL_COUNTS = (
    "run_positions",
    "run_exceptions_nulled",
    "run_exception_events_nulled",
    "trail_batches",
    "schools_locked",
)


def new_counts() -> dict[str, int]:
    return {key: 0 for key, _, _ in STEPS} | {key: 0 for key in TRAIL_COUNTS}


def set_statement_timeout(conn, timeout_ms: int = STATEMENT_TIMEOUT_MS) -> None:
    """SET LOCAL: ends with the caller's transaction (one batch)."""
    conn.execute(
        sql.SQL("set local statement_timeout = {}").format(sql.Literal(f"{timeout_ms}ms"))
    )


def schools_oldest_trail_first(conn) -> list[str]:
    """Every school, the one with the oldest trail row first, so a night that
    stops on its budget resumes where the backlog is deepest. One index probe
    per school (run_positions_school_received_idx)."""
    rows = conn.execute(
        """
        select s.id from live_schools s
        left join lateral (
            select min(p.received_at) as oldest from run_positions p where p.school_id = s.id
        ) t on true
        order by t.oldest nulls last, s.id
        """
    ).fetchall()
    return [str(row["id"]) for row in rows]


# --- the witness (ops_job_runs, D4) ---------------------------------------------


def open_run(conn) -> int:
    return conn.execute(
        "insert into ops_job_runs (job, status) values (%s, 'running') returning id", (JOB,)
    ).fetchone()["id"]


def close_run(conn, run_id: int, status: str, counts: dict[str, Any]) -> None:
    """``ok``, ``stopped`` (the budget ran out: success, the rest is the next
    night's) or ``failed``."""
    conn.execute(
        "update ops_job_runs set status = %s, finished_at = now(), counts = %s where id = %s",
        (status, Jsonb(counts), run_id),
    )
