import logging
import time

from flask import Flask, g, request

from app.log.service.audit_log_service import log_api

logger = logging.getLogger(__name__)


def register_audit_middleware(app: Flask) -> None:

    @app.before_request
    def _audit_start():
        if request.path.startswith("/internal"):
            g.audit_start = time.monotonic()

    @app.after_request
    def _audit_after(response):
        _record_log(response.status_code)
        return response

    @app.teardown_request
    def _audit_teardown(exc):
        if exc is not None and not getattr(g, "_audit_logged", False):
            _record_log(500)


def _record_log(status_code: int) -> None:
    start = getattr(g, "audit_start", None)
    if start is None:
        return
    g._audit_logged = True
    duration_ms = int((time.monotonic() - start) * 1000)
    try:
        log_api(request.method, request.path, status_code, duration_ms)
    except Exception:
        logger.exception("Audit log recording failed")
