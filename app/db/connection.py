import logging
import os
import time
from contextlib import contextmanager
from typing import Generator

import psycopg2
from psycopg2 import pool
from psycopg2.extensions import connection
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

_pool: pool.ThreadedConnectionPool | None = None

_MAX_CONN = int(os.getenv("DB_POOL_MAX_CONN", "2"))
_RETRY_ATTEMPTS = 3
_RETRY_BASE_DELAY = 1.0


def _get_pool() -> pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        db_url = os.getenv("SUPABASE_DB_TRANSACTION_POOLER_URL")
        if not db_url:
            raise ValueError("SUPABASE_DB_TRANSACTION_POOLER_URL not set")
        _pool = pool.ThreadedConnectionPool(minconn=1, maxconn=_MAX_CONN, dsn=db_url)
    return _pool


def _getconn_with_retry() -> connection:
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            return _get_pool().getconn()
        except psycopg2.OperationalError as e:
            if attempt == _RETRY_ATTEMPTS - 1:
                raise
            delay = _RETRY_BASE_DELAY * (2 ** attempt)
            logger.warning("DB connect failed (attempt %d/%d), retrying in %.1fs: %s",
                           attempt + 1, _RETRY_ATTEMPTS, delay, e)
            time.sleep(delay)
    raise psycopg2.OperationalError("unreachable")


@contextmanager
def get_connection() -> Generator[connection, None, None]:
    conn = _getconn_with_retry()
    try:
        yield conn
    except BaseException:
        conn.rollback()
        raise
    finally:
        closed = getattr(conn, "closed", 0)
        _get_pool().putconn(conn, close=(closed != 0))


def close_pool() -> None:
    global _pool
    if _pool:
        _pool.closeall()
        _pool = None