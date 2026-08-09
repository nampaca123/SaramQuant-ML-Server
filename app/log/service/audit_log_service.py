"""파이프라인 런 레코드 + API 요청 로그 — DB 쓰기 없이 구조화 로그와 S3 run-summary만 남긴다."""
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.log.run_record import write_run_record
from app.schema.dto.pipeline_metadata import PipelineMetadata, StepResult

logger = logging.getLogger(__name__)


def _resolve_run_id() -> str:
    return os.environ.get("RUN_ID") or uuid4().hex[:12]


def _derive_status(steps: list[StepResult]) -> str:
    # 오케스트레이터는 치명적 실패 시 즉시 중단하므로, 마지막 단계 실패는 중단(error)을 뜻한다.
    failed = [s for s in steps if not s.success]
    if not failed:
        return "ok"
    if not steps[-1].success:
        return "error"
    return "partial"


def _derive_cause(steps: list[StepResult]) -> str | None:
    for step in steps:
        if not step.success:
            return f"{step.name}: {step.error or 'failed'}"
    return None


def log_api(method: str, path: str, status_code: int, duration_ms: int) -> None:
    try:
        logger.info(json.dumps({
            "event": "calc_api",
            "method": method,
            "path": path,
            "status_code": status_code,
            "duration_ms": duration_ms,
        }, ensure_ascii=False))
    except Exception:
        logger.exception("Failed to write API log line")


def log_pipeline(meta: PipelineMetadata) -> None:
    started_at = datetime.now(timezone.utc) - timedelta(milliseconds=meta.total_duration_ms)
    write_run_record(
        service="calc",
        command=meta.command,
        status=_derive_status(meta.steps),
        started_at=started_at,
        counts={
            s.name: {"ok": 1 if s.success else 0, "failed": 0 if s.success else 1}
            for s in meta.steps
        },
        cause=_derive_cause(meta.steps),
        run_id=_resolve_run_id(),
    )
