from collections.abc import Iterator
from contextlib import contextmanager
from threading import Lock

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.core.config import get_settings

_pool: ConnectionPool | None = None
_pool_lock = Lock()


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


@contextmanager
def get_connection() -> Iterator[Connection]:
    with get_pool().connection() as connection:
        yield connection


def close_pool() -> None:
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.close()
            _pool = None
