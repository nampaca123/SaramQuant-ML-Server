from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from app.log.service import audit_log_service
from app.pipeline import orchestrator as orch
from app.schema import Market
from app.services import fundamental_collection_service as fcs


# ── fixtures ──

def _changes(count: int = 3) -> pd.DataFrame:
    return pd.DataFrame({
        "id": range(1, count + 1),
        "symbol": [f"{index:06d}" for index in range(1, count + 1)],
        "market": [Market.KR_KOSPI.value] * count,
        "is_active": [False] * count,
    })


def _summary(total: int, active_after: int) -> dict:
    return {
        "total": total,
        "active_before": total,
        "active_after": active_after,
        "deactivated": total - active_after,
        "reactivated": 0,
    }


class _StubStockRepo:
    def __init__(self, changes: pd.DataFrame, summary: dict):
        self._changes = changes
        self._summary = summary
        self.calls: list[tuple] = []

    def compute_deactivation(self, market_group, active_symbols=None):
        self.calls.append((market_group, active_symbols))
        return self._changes, self._summary


@pytest.fixture
def deactivation(monkeypatch):
    merges: list[tuple] = []
    monkeypatch.setattr(
        orch.lake_writer, "merge",
        lambda table, df, run_id: merges.append((table, df, run_id)) or len(df),
    )

    def _install(total: int, active_after: int, changes: pd.DataFrame | None = None):
        repo = _StubStockRepo(_changes() if changes is None else changes,
                              _summary(total, active_after))
        monkeypatch.setattr(orch, "StockRepository", lambda *a, **kw: repo)
        return repo, merges

    return _install


# ── (a) 안전 임계 게이트 ──

def test_deactivation_aborts_when_active_ratio_below_threshold(deactivation):
    repo, merges = deactivation(total=100, active_after=9)

    step = orch.PipelineOrchestrator()._progressive_deactivate("kr")

    assert step.success is False
    assert step.error == "safety_check_failed"
    assert merges == []


def test_deactivation_merges_when_active_ratio_at_threshold(deactivation):
    repo, merges = deactivation(total=100, active_after=10)

    step = orch.PipelineOrchestrator()._progressive_deactivate("kr")

    assert step.success is True
    assert len(merges) == 1
    table, df, run_id = merges[0]
    assert table == "stocks"
    assert len(df) == 3
    assert run_id
    assert step.output_count == 3
    assert step.input_count == 100


def test_deactivation_aborts_when_universe_is_empty(deactivation):
    repo, merges = deactivation(total=0, active_after=0, changes=_changes(0))

    step = orch.PipelineOrchestrator()._progressive_deactivate("kr")

    assert step.success is False
    assert merges == []


def test_deactivation_passes_collector_active_symbols(deactivation):
    repo, _ = deactivation(total=100, active_after=90)
    pipeline = orch.PipelineOrchestrator()
    pipeline._collector.active_symbols = {Market.KR_KOSPI: {"005930"}}

    pipeline._progressive_deactivate("kr")

    assert repo.calls == [("kr", {Market.KR_KOSPI: {"005930"}})]


# ── (b) US 재무 신선도 게이트 ──

def _run_summary(status: str = "ok", age_hours: float = 1.0) -> dict:
    written = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    return {"status": status, "written_at_utc": written.isoformat(), "run_id": "usa-1"}


def test_us_collection_no_longer_uses_http(monkeypatch):
    assert not hasattr(fcs, "requests")


def test_us_gate_passes_on_fresh_ok_summary(monkeypatch):
    keys: list[str] = []
    monkeypatch.setattr(
        fcs, "read_run_summary",
        lambda key: keys.append(key) or _run_summary(age_hours=1),
    )

    result = fcs.FundamentalCollectionService().collect_all("us")

    assert result == {}
    assert keys == ["run-summary/usa_fstatements.json"]


@pytest.mark.parametrize("summary", [
    None,
    _run_summary(status="error"),
    _run_summary(status="ok", age_hours=73),
])
def test_us_gate_soft_fails_on_unusable_summary(monkeypatch, summary):
    monkeypatch.setattr(fcs, "read_run_summary", lambda key: summary)

    with pytest.raises(fcs.FreshnessGateError):
        fcs.FundamentalCollectionService().collect_all("us")


def test_us_gate_soft_fail_skips_fundamentals_and_records_the_failed_step(monkeypatch, written):
    monkeypatch.setattr(fcs, "read_run_summary", lambda key: None)
    pipeline = _quiet_pipeline(monkeypatch)
    fundamental_calls: list[str] = []
    monkeypatch.setattr(
        pipeline, "_compute_fundamentals",
        lambda region: fundamental_calls.append(region) or 5,
    )

    pipeline.run_collect_fs_us()

    assert fundamental_calls == []
    assert len(written) == 1
    assert written[0]["status"] == "error"
    assert written[0]["counts"]["fs_collection"]["failed"] == 1
    assert "fundamentals" not in written[0]["counts"]
    assert "fs_collection" in written[0]["cause"]


# ── (c) 런 레코드 1회 기록 + counts ──

@pytest.fixture
def written(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(
        audit_log_service, "write_run_record", lambda **kwargs: calls.append(kwargs)
    )
    return calls


def _quiet_pipeline(monkeypatch) -> orch.PipelineOrchestrator:
    monkeypatch.setattr(orch.lake_writer, "optimize_and_vacuum", lambda tables: None)
    return orch.PipelineOrchestrator()


def test_run_record_written_once_on_success_with_counts(monkeypatch, written):
    pipeline = _quiet_pipeline(monkeypatch)
    monkeypatch.setattr(
        pipeline._fund_collector, "collect_all", lambda region: {"success": 42, "failed": 0}
    )
    monkeypatch.setattr(pipeline, "_compute_fundamentals", lambda region: 11)

    pipeline.run_collect_fs_kr()

    assert len(written) == 1
    assert written[0]["command"] == "kr-fs"
    assert written[0]["service"] == "calc"
    assert written[0]["status"] == "ok"
    assert written[0]["counts"] == {
        "fs_collection": {"ok": 1, "failed": 0, "out": 42},
        "fundamentals": {"ok": 1, "failed": 0, "out": 11},
    }


def test_run_record_written_once_when_step_fails(monkeypatch, written):
    pipeline = _quiet_pipeline(monkeypatch)
    monkeypatch.setattr(
        pipeline._fund_collector, "collect_all", lambda region: {"success": 3, "failed": 0}
    )

    def _boom(region):
        raise RuntimeError("fundamentals boom")

    monkeypatch.setattr(pipeline, "_compute_fundamentals", _boom)

    pipeline.run_collect_fs_kr()

    assert len(written) == 1
    assert written[0]["status"] == "error"
    assert "fundamentals boom" in written[0]["cause"]
    assert written[0]["counts"]["fundamentals"] == {"ok": 0, "failed": 1}


def test_run_record_written_once_when_body_raises(monkeypatch, written):
    pipeline = _quiet_pipeline(monkeypatch)

    def _boom(steps):
        steps.append(orch.StepResult("collection", True, 1, output_count=9))
        raise RuntimeError("unexpected boom")

    with pytest.raises(RuntimeError):
        pipeline._run_command("kr", _boom, ["stocks"])

    assert len(written) == 1
    assert written[0]["status"] == "error"
    assert "unexpected boom" in written[0]["cause"]
    assert written[0]["counts"]["collection"] == {"ok": 1, "failed": 0, "out": 9}


def test_run_record_uses_env_run_id(monkeypatch, written):
    monkeypatch.setenv("RUN_ID", "sfn-exec-13")
    pipeline = _quiet_pipeline(monkeypatch)
    monkeypatch.setattr(pipeline._fund_collector, "collect_all", lambda region: {"success": 1})
    monkeypatch.setattr(pipeline, "_compute_fundamentals", lambda region: 1)

    pipeline.run_collect_fs_kr()

    assert written[0]["run_id"] == "sfn-exec-13"


def test_maintenance_runs_after_the_pipeline(monkeypatch, written):
    maintained: list[list[str]] = []
    monkeypatch.setattr(orch.lake_writer, "optimize_and_vacuum", maintained.append)
    pipeline = orch.PipelineOrchestrator()
    monkeypatch.setattr(pipeline._fund_collector, "collect_all", lambda region: {"success": 1})
    monkeypatch.setattr(pipeline, "_compute_fundamentals", lambda region: 1)

    pipeline.run_collect_fs_kr()

    assert maintained == [orch.FS_TABLES]
