import json
import logging
from datetime import datetime, timedelta, timezone

import pytest
from botocore.exceptions import ClientError

import app.log.run_record as run_record
from app.log.service import audit_log_service
from app.schema import PipelineMetadata, StepResult

SCHEMA_KEYS = {
    "run_id", "service", "command", "status",
    "started_at_utc", "written_at_utc", "duration_ms", "counts", "cause",
}


class StubS3Client:
    def __init__(self, get_body=None, error=None):
        self.puts = []
        self.gets = []
        self._get_body = get_body
        self._error = error

    def put_object(self, Bucket, Key, Body):
        if self._error is not None:
            raise self._error
        self.puts.append((Bucket, Key, Body))
        return {}

    def get_object(self, Bucket, Key):
        self.gets.append((Bucket, Key))
        if self._error is not None:
            raise self._error
        return {"Body": _StubBody(self._get_body)}


class _StubBody:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return self._payload


def _no_such_key():
    return ClientError({"Error": {"Code": "NoSuchKey", "Message": "not found"}}, "GetObject")


@pytest.fixture
def s3_env(monkeypatch):
    monkeypatch.setenv("LAKE_BUCKET", "saramquant-bucket")
    monkeypatch.delenv("RUN_SUMMARY_PREFIX", raising=False)

    def _install(**kwargs):
        stub = StubS3Client(**kwargs)
        monkeypatch.setattr(run_record, "_s3_client", stub)
        return stub

    return _install


# ── write_run_record ──

def test_put_object_payload_matches_spec_schema(s3_env):
    stub = s3_env()
    started = datetime.now(timezone.utc) - timedelta(seconds=3)

    run_record.write_run_record(
        service="calc", command="kr", status="ok", started_at=started,
        counts={"collection": {"ok": 1, "failed": 0}}, cause=None, run_id="exec-1",
    )

    assert len(stub.puts) == 1
    bucket, key, body = stub.puts[0]
    assert bucket == "saramquant-bucket"
    assert key == "run-summary/calc_kr.json"

    record = json.loads(body)
    assert set(record) == SCHEMA_KEYS
    assert record["run_id"] == "exec-1"
    assert record["service"] == "calc"
    assert record["command"] == "kr"
    assert record["status"] == "ok"
    assert record["counts"] == {"collection": {"ok": 1, "failed": 0}}
    assert record["cause"] is None
    assert record["duration_ms"] >= 0
    assert datetime.fromisoformat(record["started_at_utc"]).tzinfo is not None
    assert datetime.fromisoformat(record["written_at_utc"]).tzinfo is not None


def test_duration_ms_measured_from_started_at(s3_env):
    stub = s3_env()
    started = datetime.now(timezone.utc) - timedelta(seconds=5)

    run_record.write_run_record(
        service="calc", command="us", status="ok", started_at=started,
        counts={}, cause=None, run_id="exec-2",
    )

    record = json.loads(stub.puts[0][2])
    assert 4000 <= record["duration_ms"] <= 7000


def test_naive_started_at_is_treated_as_utc(s3_env):
    stub = s3_env()
    started = (datetime.now(timezone.utc) - timedelta(seconds=2)).replace(tzinfo=None)

    run_record.write_run_record(
        service="calc", command="kr", status="ok", started_at=started,
        counts={}, cause=None, run_id="exec-3",
    )

    record = json.loads(stub.puts[0][2])
    assert record["duration_ms"] >= 0
    assert datetime.fromisoformat(record["started_at_utc"]).tzinfo is not None


def test_prefix_env_overrides_key(monkeypatch, s3_env):
    stub = s3_env()
    monkeypatch.setenv("RUN_SUMMARY_PREFIX", "custom/")

    run_record.write_run_record(
        service="calc", command="kr-fs", status="error", started_at=datetime.now(timezone.utc),
        counts={}, cause="boom", run_id="exec-4",
    )

    assert stub.puts[0][1] == "custom/calc_kr-fs.json"


def test_record_is_also_logged_as_one_json_line(s3_env, caplog):
    s3_env()
    with caplog.at_level(logging.INFO, logger="app.log.run_record"):
        run_record.write_run_record(
            service="calc", command="kr", status="partial", started_at=datetime.now(timezone.utc),
            counts={"factors": {"ok": 0, "failed": 1}}, cause="factors: boom", run_id="exec-5",
        )

    lines = [json.loads(r.message) for r in caplog.records if r.message.startswith("{")]
    assert any(line["status"] == "partial" and line["run_id"] == "exec-5" for line in lines)


def test_s3_failure_never_raises(s3_env, caplog):
    s3_env(error=RuntimeError("s3 down"))

    with caplog.at_level(logging.ERROR, logger="app.log.run_record"):
        result = run_record.write_run_record(
            service="calc", command="kr", status="ok", started_at=datetime.now(timezone.utc),
            counts={}, cause=None, run_id="exec-6",
        )

    assert result is None
    assert any(r.levelno >= logging.ERROR for r in caplog.records)


def test_non_ascii_cause_is_not_escaped(s3_env):
    stub = s3_env()

    run_record.write_run_record(
        service="calc", command="kr", status="error", started_at=datetime.now(timezone.utc),
        counts={}, cause="수집 실패", run_id="exec-7",
    )

    body = stub.puts[0][2]
    text = body.decode("utf-8") if isinstance(body, bytes) else body
    assert "수집 실패" in text


# ── read_run_summary ──

def test_read_run_summary_returns_dict(s3_env):
    stub = s3_env(get_body=b'{"status": "ok", "written_at_utc": "2026-08-10T00:00:00+00:00"}')

    result = run_record.read_run_summary("run-summary/usa_fstatements.json")

    assert result == {"status": "ok", "written_at_utc": "2026-08-10T00:00:00+00:00"}
    assert stub.gets == [("saramquant-bucket", "run-summary/usa_fstatements.json")]


def test_read_run_summary_returns_none_on_missing_key(s3_env, caplog):
    s3_env(error=_no_such_key())

    with caplog.at_level(logging.WARNING, logger="app.log.run_record"):
        result = run_record.read_run_summary("run-summary/missing.json")

    assert result is None
    assert any(r.levelno >= logging.WARNING for r in caplog.records)


def test_read_run_summary_returns_none_on_invalid_json(s3_env, caplog):
    s3_env(get_body=b"not-json{{")

    with caplog.at_level(logging.WARNING, logger="app.log.run_record"):
        result = run_record.read_run_summary("run-summary/broken.json")

    assert result is None
    assert any(r.levelno >= logging.WARNING for r in caplog.records)


# ── log_pipeline / log_api ──

@pytest.fixture
def written(monkeypatch):
    calls = []
    monkeypatch.setattr(
        audit_log_service, "write_run_record",
        lambda **kwargs: calls.append(kwargs),
    )
    return calls


def _meta(steps, command="kr", aborted=False):
    return PipelineMetadata(
        command=command, steps=steps, total_duration_ms=1234, aborted=aborted
    )


def test_log_pipeline_all_steps_ok_is_status_ok(written):
    audit_log_service.log_pipeline(_meta([
        StepResult("collection", True, 10),
        StepResult("fundamentals", True, 20),
    ]))

    assert written[0]["status"] == "ok"
    assert written[0]["cause"] is None
    assert written[0]["service"] == "calc"
    assert written[0]["command"] == "kr"


def test_log_pipeline_partial_when_run_continued_past_failure(written):
    audit_log_service.log_pipeline(_meta([
        StepResult("collection", True, 10),
        StepResult("sector_agg", False, 5, "sector boom"),
        StepResult("indicators", True, 30),
    ]))

    assert written[0]["status"] == "partial"
    assert "sector boom" in written[0]["cause"]


def test_log_pipeline_error_when_run_aborted_on_failed_step(written):
    audit_log_service.log_pipeline(_meta([
        StepResult("collection", False, 10, "collection boom"),
    ], aborted=True))

    assert written[0]["status"] == "error"
    assert "collection boom" in written[0]["cause"]


def test_log_pipeline_error_when_fundamentals_failure_skips_factors(written):
    audit_log_service.log_pipeline(_meta([
        StepResult("collection", True, 10),
        StepResult("fundamentals", False, 5, "fund boom"),
        StepResult("factors", False, 0, "skipped"),
    ], aborted=True))

    assert written[0]["status"] == "error"
    assert "fund boom" in written[0]["cause"]


def test_log_pipeline_counts_are_per_step(written):
    audit_log_service.log_pipeline(_meta([
        StepResult("collection", True, 10),
        StepResult("sector_agg", False, 5, "boom"),
        StepResult("indicators", True, 30),
    ]))

    assert written[0]["counts"] == {
        "collection": {"ok": 1, "failed": 0},
        "sector_agg": {"ok": 0, "failed": 1},
        "indicators": {"ok": 1, "failed": 0},
    }


def test_log_pipeline_started_at_reflects_total_duration(written):
    audit_log_service.log_pipeline(_meta([StepResult("collection", True, 10)]))

    started = written[0]["started_at"]
    assert started.tzinfo is not None
    elapsed_ms = (datetime.now(timezone.utc) - started).total_seconds() * 1000
    assert 1234 <= elapsed_ms <= 4000


def test_log_pipeline_uses_run_id_env(monkeypatch, written):
    monkeypatch.setenv("RUN_ID", "sfn-exec-99")

    audit_log_service.log_pipeline(_meta([StepResult("collection", True, 10)]))

    assert written[0]["run_id"] == "sfn-exec-99"


def test_log_pipeline_falls_back_to_generated_run_id(monkeypatch, written):
    monkeypatch.delenv("RUN_ID", raising=False)

    audit_log_service.log_pipeline(_meta([StepResult("collection", True, 10)]))

    assert written[0]["run_id"]


def test_log_pipeline_empty_steps_is_ok(written):
    audit_log_service.log_pipeline(_meta([]))

    assert written[0]["status"] == "ok"
    assert written[0]["counts"] == {}


def test_log_api_writes_one_json_line_without_s3(monkeypatch, caplog):
    called = []
    monkeypatch.setattr(audit_log_service, "write_run_record", lambda **kw: called.append(kw))

    with caplog.at_level(logging.INFO, logger="app.log.service.audit_log_service"):
        audit_log_service.log_api("GET", "/internal/health", 200, 12)

    assert called == []
    lines = [json.loads(r.message) for r in caplog.records if r.message.startswith("{")]
    assert lines == [{
        "event": "calc_api",
        "method": "GET",
        "path": "/internal/health",
        "status_code": 200,
        "duration_ms": 12,
    }]
