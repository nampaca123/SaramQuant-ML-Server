import json
import logging
from typing import Optional

from psycopg2.extras import execute_values

from app.db.connection import get_connection

logger = logging.getLogger(__name__)


def insert_audit_log(
    server: str,
    action: str,
    method: Optional[str] = None,
    path: Optional[str] = None,
    status_code: Optional[int] = None,
    duration_ms: Optional[int] = None,
    metadata: Optional[dict] = None,
) -> None:
    meta_json = json.dumps(metadata) if metadata else None
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO audit_log (server, action, method, path, status_code, duration_ms, metadata)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    (server, action, method, path, status_code, duration_ms, meta_json),
                )
            conn.commit()
    except Exception:
        logger.exception("Failed to insert audit log")
