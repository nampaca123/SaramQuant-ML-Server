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


def _derive_status(meta: PipelineMetadata) -> str:
    # 오케스트레이터가 런을 끊었으면 error, 끝까지 갔는데 실패 단계가 있으면 partial이다.
    if meta.aborted:
        return "error"
    return "ok" if all(step.success for step in meta.steps) else "partial"


def _step_counts(step: StepResult) -> dict:
    counts = {"ok": 1 if step.success else 0, "failed": 0 if step.success else 1}
    if step.input_count is not None:
        counts["in"] = step.input_count
    if step.output_count is not None:
        counts["out"] = step.output_count
    return counts


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
        status=_derive_status(meta),
        started_at=started_at,
        counts={step.name: _step_counts(step) for step in meta.steps},
        cause=_derive_cause(meta.steps),
        run_id=meta.run_id or _resolve_run_id(),
    )
