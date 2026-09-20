"""Idempotent driver actions: the ``driver_action_keys`` ledger (GPS plan
U7/R33; the client half is U6's action envelope).

Module functions on the caller's connection, like ``participation_dao``: the
key is claimed *inside* the action transaction, right after the ownership
checks, and the response is written onto the same row as the last statement
before commit. That ordering is the whole guarantee — a key can never be
committed ahead of its action, a response never after it, and an action that
fails on its own guards rolls its key back with it, so the retry re-executes
against those guards rather than replaying a refusal.

Keyed (school, driver, key): a key from another driver at the same school is
a different row and never replays — wider scoping would make the table a
per-school cache of children's data. RLS confines every read and write here
to the scope's school like any other school-owned table.

The request fingerprint covers the driver, the run, the action, the action's
own identity fields (student, note, expected stop order, ...) and the
normalised fix. The client freezes the fix with the key at the first attempt
(R3), so a retry fingerprints identically; a different payload under the same
key is a client bug and answers ``idempotency-mismatch`` without revealing
what was stored.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

import psycopg
from fastapi.encoders import jsonable_encoder
from psycopg import sql
from psycopg.types.json import Jsonb

from app.core.config import GPS_KEY_LOCK_TIMEOUT_MS
from app.core.errors import ActionReplayed, IdempotencyConflictError
from app.services.position_rules import NormalisedFix

logger = logging.getLogger("saferide.idempotency")

IN_FLIGHT = "idempotency-in-flight"
MISMATCH = "idempotency-mismatch"

# The seven envelope actions, as `driver_action_keys.action` and the trail
# row's action_kind spell them.
ACTIONS = ("start", "arrive", "board", "dropoff", "absent", "handover", "end")


@dataclass(frozen=True)
class ActionEnvelope:
    """What the router hands the DAO from one tapped action's request: the
    normalised key (None when the header was absent or not a UUID), the
    body's ``fix`` exactly as sent (validated later, never here) and the
    client's diagnostic device id."""

    key: str | None = None
    fix: Any = None
    device_id: Any = None


def parse_key(header: str | None) -> str | None:
    """The ``Idempotency-Key`` header as a canonical uuid string, or None.

    Absent, blank, malformed or not a UUID all mean "no key": the action
    executes normally and writes no key row (R33's fallback for older
    clients). Never a 4xx.
    """
    if not header or not isinstance(header, str):
        return None
    try:
        return str(uuid.UUID(header.strip()))
    except (ValueError, AttributeError):
        return None


def device_id_of(value: Any) -> str | None:
    """The client-minted device id, stored as an opaque short string and never
    trusted for anything (R28). Anything but a non-empty string is dropped."""
    if isinstance(value, str) and value.strip():
        return value.strip()[:80]
    return None


def fingerprint(
    *,
    action: str,
    driver_id: str,
    run_id: str | None,
    identity: dict[str, Any],
    fix: NormalisedFix,
) -> str:
    """SHA-256 over the canonical JSON of what makes two requests the same
    tap. ``identity`` is the action's own fields (student, note, expected stop
    order, whole_day, on_bus, route); the device id and any prompt hint are
    not identity and stay out."""
    material = {
        "action": action,
        "driver_id": str(driver_id),
        "run_id": str(run_id) if run_id else None,
        "identity": {k: identity[k] for k in sorted(identity)},
        "fix": fix.fingerprint_material(),
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def claim(
    conn,
    *,
    school_id: str,
    driver_id: str,
    key: str,
    run_id: str | None,
    action: str,
    request_fingerprint: str,
    lock_timeout_ms: int = GPS_KEY_LOCK_TIMEOUT_MS,
) -> None:
    """Insert the key row, or decide what a repeat means.

    ``insert … on conflict do nothing returning key`` under a short lock
    timeout, in a savepoint so a timeout leaves the action transaction
    healthy. A returned row means the key is ours: return, and the action
    proceeds. No row means the key exists: read it — same fingerprint with a
    response → raise ``ActionReplayed`` with that body (the router returns
    it, no side effects); a different fingerprint → 409
    ``idempotency-mismatch``; a lock timeout (the first attempt is still
    executing, so the conflict check had to wait on its transaction) or a row
    without a response → 409 ``idempotency-in-flight``.
    """
    try:
        with conn.transaction():
            conn.execute(
                sql.SQL("set local lock_timeout = {}").format(sql.Literal(f"{lock_timeout_ms}ms"))
            )
            claimed = conn.execute(
                """
                insert into driver_action_keys
                    (school_id, driver_id, key, run_id, action, request_fingerprint)
                values (%s, %s, %s, %s, %s, %s)
                on conflict (school_id, driver_id, key) do nothing
                returning key
                """,
                (school_id, driver_id, key, run_id, action, request_fingerprint),
            ).fetchone()
    except psycopg.errors.LockNotAvailable as error:
        raise IdempotencyConflictError(
            "This action is still being processed; try again in a moment",
            code=IN_FLIGHT,
        ) from error
    # The savepoint released with the SET LOCAL inside it: reset the wait for
    # the rest of the action transaction, whose own row locks keep their
    # normal behaviour.
    conn.execute("set local lock_timeout = default")
    if claimed is not None:
        return
    stored = conn.execute(
        """
        select request_fingerprint, response from driver_action_keys
        where school_id = %s and driver_id = %s and key = %s
        """,
        (school_id, driver_id, key),
    ).fetchone()
    if stored is None:
        # Inserted and gone between two statements (a purge racing a retry):
        # tell the client to try again rather than guess.
        raise IdempotencyConflictError(
            "This action is still being processed; try again in a moment", code=IN_FLIGHT
        )
    if stored["request_fingerprint"] != request_fingerprint:
        logger.info(
            "idempotency mismatch (driver=%s action=%s): key reused for a different request",
            driver_id, action,
        )
        raise IdempotencyConflictError(
            "This key was already used for a different action", code=MISMATCH
        )
    if stored["response"] is None:
        raise IdempotencyConflictError(
            "This action is still being processed; try again in a moment", code=IN_FLIGHT
        )
    raise ActionReplayed(stored["response"], action=action)


def seal(
    conn, *, school_id: str, driver_id: str, key: str, run_id: str | None, body: Any
) -> None:
    """Write the response onto the key row — the caller's LAST statement
    before commit. ``body`` is encoded with FastAPI's own encoder so the
    replay serialises byte-for-byte like the original. A Start Run's run id,
    unknown at claim time, lands here."""
    conn.execute(
        """
        update driver_action_keys
        set response = %s, run_id = coalesce(%s, run_id)
        where school_id = %s and driver_id = %s and key = %s
        """,
        (Jsonb(jsonable_encoder(body)), run_id, school_id, driver_id, key),
    )


def purge_expired(conn, school_id: str, *, ttl_days: int, limit: int) -> int:
    """Delete up to ``limit`` of the school's keys older than ``ttl_days`` by
    ``created_at`` (server time). Returns the count. Part of the bounded
    purge pass (position_dao); RLS confines it to the school."""
    # Seconds, not an interval's day field, so the cutoff is the same instant
    # under any session time zone (position_dao.RETENTION_CUTOFF_SQL).
    return conn.execute(
        """
        with victims as (
            select school_id, driver_id, key from driver_action_keys
            where school_id = %s and created_at < now() - make_interval(secs => %s * 86400)
            order by created_at
            limit %s
            for update skip locked
        )
        delete from driver_action_keys k
        using victims v
        where k.school_id = v.school_id and k.driver_id = v.driver_id and k.key = v.key
        """,
        (school_id, ttl_days, limit),
    ).rowcount
