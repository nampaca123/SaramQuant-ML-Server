"""Athena 쿼리 실행기 — StartQueryExecution 후 종료 상태까지 폴링한다."""
import logging
import os
import time

from app.db.aws_session import build_session

logger = logging.getLogger(__name__)

_POLL_INTERVAL_S = 1.0
_TERMINAL_FAILURES = ("FAILED", "CANCELLED")

_client = None


class AthenaQueryError(Exception):
    pass


def _get_client():
    global _client
    if _client is None:
        _client = build_session().client("athena")
    return _client


def run_query(sql: str, timeout_s: int = 300) -> str:
    client = _get_client()
    query_id = client.start_query_execution(
        QueryString=sql,
        WorkGroup=os.getenv("ATHENA_WORKGROUP", "saramquant"),
        QueryExecutionContext={"Database": os.getenv("GLUE_DATABASE", "saramquant")},
    )["QueryExecutionId"]
    logger.info("Athena query started: id=%s", query_id)

    deadline = time.monotonic() + timeout_s
    while True:
        status = client.get_query_execution(QueryExecutionId=query_id)["QueryExecution"]["Status"]
        state = status["State"]
        if state == "SUCCEEDED":
            logger.info("Athena query succeeded: id=%s", query_id)
            return query_id
        if state in _TERMINAL_FAILURES:
            reason = status.get("StateChangeReason", "no reason reported")
            raise AthenaQueryError(f"Athena query {state} (id={query_id}): {reason}")
        if time.monotonic() >= deadline:
            raise AthenaQueryError(f"Query timed out after {timeout_s}s (id={query_id})")
        time.sleep(_POLL_INTERVAL_S)
