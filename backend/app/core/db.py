from collections.abc import Iterator
from contextlib import contextmanager
from threading import Lock

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.core.config import get_settings
from app.core.scope import Scope, SchoolScope, current_scope, guc_value

_pool: ConnectionPool | None = None
_pool_lock = Lock()

# Sentinel: distinguishes "caller passed no scope" from an explicit None
# (None means deliberately global — no school restriction). UNSET is the
# public alias for signature-forwarding callers (PushDao/PushService thread
# their own scope parameter straight through to get_connection).
_UNSET = object()
UNSET = _UNSET

# Flipped to True in U7: every school-owned DAO on the staff/driver/
# provider surfaces passes its scope explicitly, so opening an
# implicitly-scoped connection inside a SchoolScope request is a programming
# error, not a fallback. ParentScope contexts stay on the context-var
# fallback until U11 converts the parent portal — the strictness check below
# deliberately fires for SchoolScope only.
STRICT_EXPLICIT_SCOPE = True


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = ConnectionPool(
                    conninfo=get_settings().database_url,
                    kwargs={"row_factory": dict_row},
                    open=False,
                )
                _pool.open()
    return _pool


def _arm_scope(connection: Connection, scope: Scope | None) -> None:
    """Publish the scope's school set as a transaction-local GUC.

    ``set_config(..., true)`` lives and dies with the current transaction, so
    the value can never leak across the pool: the next checkout (or the next
    implicit transaction after a commit) starts clean and must re-arm.
    """
    if scope is None:
        return  # unset ≡ '' — a fresh transaction has no GUC to clear
    connection.execute(
        "select set_config('saferide.school_ids', %s, true)", (guc_value(scope),)
    )


@contextmanager
def get_connection(scope: object = _UNSET) -> Iterator[Connection]:
    """Checkout a pooled connection with the request's school GUC armed.

    DAOs for school-owned tables pass their scope explicitly; until every one
    does (STRICT_EXPLICIT_SCOPE), an omitted argument falls back to the
    request context var so the compatibility window stays safe.
    """
    if scope is _UNSET:
        effective = current_scope()
        if isinstance(effective, SchoolScope) and STRICT_EXPLICIT_SCOPE:
            raise RuntimeError(
                "school-scoped request opened a connection without an explicit scope"
            )
    else:
        effective = scope  # type: ignore[assignment]
    with get_pool().connection() as connection:
        _arm_scope(connection, effective)
        yield connection


@contextmanager
def get_global_connection() -> Iterator[Connection]:
    """A deliberately scope-free connection for non-school tables (users,
    sessions, push tokens). Never arms the GUC, even inside a scoped request."""
    with get_pool().connection() as connection:
        yield connection


@contextmanager
def scoped_transaction(connection: Connection, scope: object = _UNSET) -> Iterator[None]:
    """An explicit transaction that keeps the GUC armed on both sides.

    The GUC is transaction-local, so a mid-connection commit silently drops
    it; this helper re-arms inside the new transaction and again after it
    commits, for any follow-up statements on the same connection.
    """
    effective = current_scope() if scope is _UNSET else scope
    with connection.transaction():
        _arm_scope(connection, effective)  # type: ignore[arg-type]
        yield
    _arm_scope(connection, effective)  # type: ignore[arg-type]


def close_pool() -> None:
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.close()
            _pool = None
