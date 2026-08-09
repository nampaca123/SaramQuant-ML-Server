"""런 레코드 — 스펙 §6.1 JSON을 CloudWatch 1줄 + S3 run-summary에 기록한다."""
import json
import logging
import os
from datetime import datetime, timezone

from app.db.aws_session import build_session

logger = logging.getLogger(__name__)

_s3_client = None


def _get_s3_client():
    global _s3_client
    if _s3_client is None:
        _s3_client = build_session().client("s3")
    return _s3_client


def _bucket() -> str:
    return os.getenv("LAKE_BUCKET", "saramquant-bucket")


def _prefix() -> str:
    return os.getenv("RUN_SUMMARY_PREFIX", "run-summary/")


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def write_run_record(
    service: str,
    command: str,
    status: str,
    started_at: datetime,
    counts: dict,
    cause: str | None,
    run_id: str,
) -> None:
    # 감사 기록 실패가 파이프라인을 중단시켜서는 안 되므로 전체를 삼킨다.
    try:
        started = _as_utc(started_at)
        written = datetime.now(timezone.utc)
        record = {
            "run_id": run_id,
            "service": service,
            "command": command,
            "status": status,
            "started_at_utc": started.isoformat(),
            "written_at_utc": written.isoformat(),
            "duration_ms": max(int((written - started).total_seconds() * 1000), 0),
            "counts": counts,
            "cause": cause,
        }
        body = json.dumps(record, ensure_ascii=False, default=str)
        logger.info(body)
        _get_s3_client().put_object(
            Bucket=_bucket(),
            Key=f"{_prefix()}{service}_{command}.json",
            Body=body.encode("utf-8"),
        )
    except Exception:
        logger.exception("Failed to write run record")


def read_run_summary(key: str) -> dict | None:
    try:
        response = _get_s3_client().get_object(Bucket=_bucket(), Key=key)
        return json.loads(response["Body"].read())
    except Exception:
        logger.warning("Failed to read run summary: %s", key, exc_info=True)
        return None
