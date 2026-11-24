"""GET /api/health (hardening U11, R17): the database answers, and which commit
is deployed.

The Route 53 probe in us-east-1 calls this and expects a 200, so a database
outage now trips ApiProbeFailing — intended. The probe is bounded on every
side: one pooled connection (the shared pool, so it can never open more than
the pool allows), at most one second waiting for it, a one-second statement
timeout on the ``select 1``, one probe in flight per process, and the outcome
cached for five seconds, so a burst of probes costs one query.
"""

import logging
import time
from threading import Lock

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.db import get_global_connection

router = APIRouter(prefix="/api")
logger = logging.getLogger("saferide.health")

HEALTH_CACHE_SECONDS = 5.0
HEALTH_DB_TIMEOUT_MS = 1000
HEALTH_QUERY = "select 1"

_probe_lock = Lock()
_cached: tuple[float, bool] | None = None
_now = time.monotonic  # module-level so tests can move the clock


def _database_answers() -> bool:
    try:
        with get_global_connection(timeout=HEALTH_DB_TIMEOUT_MS / 1000) as connection:
            # Transaction-local: the pool's next checkout starts without it.
            connection.execute(
                "select set_config('statement_timeout', %s, true)", (str(HEALTH_DB_TIMEOUT_MS),)
            )
            connection.execute(HEALTH_QUERY).fetchone()
        return True
    except Exception as error:  # noqa: BLE001 — any failure is "db: error"
        # The class and SQL state only: a connection error's text names the
        # host and the role.
        logger.warning(
            "health-db-error error=%s sqlstate=%s",
            type(error).__name__,
            getattr(error, "sqlstate", None) or "-",
        )
        return False


def database_ok() -> bool:
    global _cached
    with _probe_lock:
        if _cached is not None and _now() - _cached[0] < HEALTH_CACHE_SECONDS:
            return _cached[1]
        ok = _database_answers()
        _cached = (_now(), ok)
        return ok


def reset_cache() -> None:
    """Forget the cached outcome (tests)."""
    global _cached
    with _probe_lock:
        _cached = None


@router.get("/health")
def health() -> JSONResponse:
    ok = database_ok()
    return JSONResponse(
        status_code=200 if ok else 503,
        content={
            "status": "ok" if ok else "error",
            "db": "ok" if ok else "error",
            "version": get_settings().app_version,
        },
    )
