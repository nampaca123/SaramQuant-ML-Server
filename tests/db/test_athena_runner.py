import pytest

import app.db.athena_runner as athena_runner
from app.db.athena_runner import AthenaQueryError, run_query


class StubAthenaClient:
    def __init__(self, states):
        self.states = list(states)
        self.start_calls = []
        self.get_calls = 0

    def start_query_execution(self, **kwargs):
        self.start_calls.append(kwargs)
        return {"QueryExecutionId": "qid-123"}

    def get_query_execution(self, QueryExecutionId):
        self.get_calls += 1
        state = self.states.pop(0)
        status = {"State": state} if isinstance(state, str) else dict(state)
        return {"QueryExecution": {"Status": status}}


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(athena_runner.time, "sleep", lambda _: None)


def _install(monkeypatch, client):
    monkeypatch.setattr(athena_runner, "_client", client)
    return client


def test_returns_query_execution_id_on_success(monkeypatch, no_sleep):
    client = _install(monkeypatch, StubAthenaClient(["QUEUED", "RUNNING", "SUCCEEDED"]))

    assert run_query("SELECT 1") == "qid-123"
    assert client.get_calls == 3


def test_start_query_uses_workgroup_and_database(monkeypatch, no_sleep):
    monkeypatch.setenv("ATHENA_WORKGROUP", "wg-test")
    monkeypatch.setenv("GLUE_DATABASE", "db-test")
    client = _install(monkeypatch, StubAthenaClient(["SUCCEEDED"]))

    run_query("SELECT 1")

    call = client.start_calls[0]
    assert call["QueryString"] == "SELECT 1"
    assert call["WorkGroup"] == "wg-test"
    assert call["QueryExecutionContext"] == {"Database": "db-test"}


def test_defaults_to_saramquant_workgroup_and_database(monkeypatch, no_sleep):
    monkeypatch.delenv("ATHENA_WORKGROUP", raising=False)
    monkeypatch.delenv("GLUE_DATABASE", raising=False)
    client = _install(monkeypatch, StubAthenaClient(["SUCCEEDED"]))

    run_query("SELECT 1")

    assert client.start_calls[0]["WorkGroup"] == "saramquant"
    assert client.start_calls[0]["QueryExecutionContext"] == {"Database": "saramquant"}


def test_failed_query_raises_with_state_change_reason(monkeypatch, no_sleep):
    _install(monkeypatch, StubAthenaClient([
        "RUNNING",
        {"State": "FAILED", "StateChangeReason": "COLUMN_NOT_FOUND: line 1:8: Column 'x'"},
    ]))

    with pytest.raises(AthenaQueryError) as exc:
        run_query("SELECT x")

    assert "FAILED" in str(exc.value)
    assert "COLUMN_NOT_FOUND: line 1:8: Column 'x'" in str(exc.value)
    assert "qid-123" in str(exc.value)


def test_cancelled_query_raises(monkeypatch, no_sleep):
    _install(monkeypatch, StubAthenaClient([{"State": "CANCELLED", "StateChangeReason": "by user"}]))

    with pytest.raises(AthenaQueryError, match="CANCELLED"):
        run_query("SELECT 1")


def test_timeout_raises_with_timeout_message(monkeypatch):
    _install(monkeypatch, StubAthenaClient(["RUNNING"] * 50))
    clock = iter([0.0, 0.0, 7.0])
    monkeypatch.setattr(athena_runner.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(athena_runner.time, "sleep", lambda _: None)

    with pytest.raises(AthenaQueryError, match="Query timed out after 5s"):
        run_query("SELECT 1", timeout_s=5)


def test_polls_once_per_second(monkeypatch):
    _install(monkeypatch, StubAthenaClient(["RUNNING", "RUNNING", "SUCCEEDED"]))
    slept = []
    monkeypatch.setattr(athena_runner.time, "sleep", slept.append)

    run_query("SELECT 1")

    assert slept == [1.0, 1.0]
