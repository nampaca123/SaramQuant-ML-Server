"""DB insert 대신 JSON 한 줄 로깅 — Task 12에서 run record 로거로 교체된다."""
import json
import logging

from app.schema.dto.pipeline_metadata import PipelineMetadata

logger = logging.getLogger(__name__)


def _log_audit(payload: dict) -> None:
    try:
        logger.info(json.dumps(payload, default=str))
    except Exception:
        logger.exception("Failed to write audit log line")


def log_api(method: str, path: str, status_code: int, duration_ms: int) -> None:
    _log_audit({
        "server": "calc",
        "action": "API",
        "method": method,
        "path": path,
        "status_code": status_code,
        "duration_ms": duration_ms,
    })


def log_pipeline(meta: PipelineMetadata) -> None:
    _log_audit({
        "server": "calc",
        "action": "PIPELINE",
        "method": meta.command,
        "path": f"pipeline/{meta.command}",
        "duration_ms": meta.total_duration_ms,
        "metadata": meta.to_dict(),
    })
