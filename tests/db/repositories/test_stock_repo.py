from datetime import datetime, timezone

import pandas as pd
import pytest

import app.db.lake_writer as lake_writer
import app.db.repositories.stock as stock
import app.db.repositories.stock_deactivation as deact
from app.schema import Market, StockInfo

CREATED = datetime(2026, 1, 1, tzinfo=timezone.utc)
NOW = datetime(2026, 8, 9, 12, 0, 0, tzinfo=timezone.utc)


def _stock_row(**overrides) -> dict:
    row = {
        "id": 1,
        "symbol": "005930",
        "name": "삼성전자",
        "market": "KR_KOSPI",
        "is_active": True,
        "dart_corp_code": None,
        "sector": "IT",
        "created_at": CREATED,
        "updated_at": CREATED,
    }
    row.update(overrides)
    return row


def _stocks_df(*rows: dict) -> pd.DataFrame:
    return pd.DataFrame(list(rows), columns=stock.STOCK_COLUMNS)


def _candidate(**overrides) -> dict:
    row = _stock_row()
    row.update({"has_price": True, "has_fs": True, "relisted": False})
    row.update(overrides)
    return row


def _candidates_df(*rows: dict) -> pd.DataFrame:
    return pd.DataFrame(list(rows), columns=deact.CANDIDATE_COLUMNS)


@pytest.fixture
def lake(monkeypatch):
    state = {"rows": _stocks_df(), "queries": [], "merged": []}

    def fake_query_df(sql, params=None):
        state["queries"].append((sql, params))
        return state["rows"].copy()

    def fake_merge(table, df, run_id):
        state["merged"].append((table, df.copy(), run_id))
        return len(df)

    monkeypatch.setattr(stock.lake_reader, "scan", lambda table: f"scan_{table}")
    monkeypatch.setattr(stock.lake_reader, "query_df", fake_query_df)
    monkeypatch.setattr(stock.lake_writer, "merge", fake_merge)
    return state


# ── id 채번 (pure) ──


def test_assign_ids_starts_at_one_on_empty_table():
    incoming = pd.DataFrame([{"symbol": "AAA", "name": "a", "market": "KR_KOSPI"}])

    out = stock.assign_stock_ids(incoming, _stocks_df(), NOW)

    assert list(out["id"]) == [1]


def test_assign_ids_continues_from_max_existing_id():
    existing = _stocks_df(_stock_row(id=7, symbol="005930"), _stock_row(id=3, symbol="000660"))
    incoming = pd.DataFrame(
        [
            {"symbol": "AAA", "name": "a", "market": "KR_KOSPI"},
            {"symbol": "BBB", "name": "b", "market": "KR_KOSPI"},
        ]
    )

    out = stock.assign_stock_ids(incoming, existing, NOW)

    assert list(out["id"]) == [8, 9]


def test_assign_ids_keeps_existing_id_and_created_at():
    existing = _stocks_df(_stock_row(id=42, symbol="005930", market="KR_KOSPI"))
    incoming = pd.DataFrame([{"symbol": "005930", "name": "삼성전자우", "market": "KR_KOSPI"}])

    out = stock.assign_stock_ids(incoming, existing, NOW)

    assert list(out["id"]) == [42]
    assert out["created_at"].iloc[0] == CREATED
    assert out["name"].iloc[0] == "삼성전자우"
    assert out["updated_at"].iloc[0] == NOW


def test_assign_ids_preserves_sector_and_active_flag_of_existing_rows():
    existing = _stocks_df(_stock_row(id=5, sector="Finance", is_active=False, dart_corp_code="X1"))
    incoming = pd.DataFrame([{"symbol": "005930", "name": "new", "market": "KR_KOSPI"}])

    out = stock.assign_stock_ids(incoming, existing, NOW)

    assert out["sector"].iloc[0] == "Finance"
    assert bool(out["is_active"].iloc[0]) is False
    assert out["dart_corp_code"].iloc[0] == "X1"


def test_assign_ids_treats_same_symbol_in_other_market_as_new_row():
    existing = _stocks_df(_stock_row(id=5, symbol="AAA", market="KR_KOSPI"))
    incoming = pd.DataFrame([{"symbol": "AAA", "name": "a", "market": "KR_KOSDAQ"}])

    out = stock.assign_stock_ids(incoming, existing, NOW)

    assert list(out["id"]) == [6]
    assert list(out["market"]) == ["KR_KOSDAQ"]


def test_assign_ids_deduplicates_incoming_keys():
    incoming = pd.DataFrame(
        [
            {"symbol": "AAA", "name": "old", "market": "KR_KOSPI"},
            {"symbol": "AAA", "name": "new", "market": "KR_KOSPI"},
        ]
    )

    out = stock.assign_stock_ids(incoming, _stocks_df(), NOW)

    assert len(out) == 1
    assert out["name"].iloc[0] == "new"


def test_assign_ids_defaults_new_rows_to_active_without_sector():
    incoming = pd.DataFrame([{"symbol": "AAA", "name": "a", "market": "US_NYSE"}])

    out = stock.assign_stock_ids(incoming, _stocks_df(), NOW)

    assert bool(out["is_active"].iloc[0]) is True
    assert out["sector"].iloc[0] is None
    assert out["created_at"].iloc[0] == NOW


def test_assign_ids_output_casts_to_the_iceberg_arrow_schema():
    existing = _stocks_df(_stock_row(id=42, symbol="005930")).astype({"id": "int64"})
    incoming = pd.DataFrame(
        [
            {"symbol": "005930", "name": "renamed", "market": "KR_KOSPI"},
            {"symbol": "AAA", "name": "a", "market": "KR_KOSPI"},
        ]
    )

    out = stock.assign_stock_ids(incoming, existing, NOW)
    table = lake_writer._to_arrow_table("stocks", out)

    assert table.column("id").to_pylist() == [42, 43]
    assert table.column("sector").to_pylist() == ["IT", None]


def test_assign_ids_returns_empty_frame_for_empty_input():
    out = stock.assign_stock_ids(pd.DataFrame(columns=["symbol", "name", "market"]), _stocks_df(), NOW)

    assert out.empty
    assert list(out.columns) == stock.STOCK_COLUMNS


# ── deactivation 결정 (pure) ──


def test_healthy_active_stock_is_not_changed():
    changes = deact.decide_activation(_candidates_df(_candidate()), NOW)

    assert changes.empty


def test_active_stock_without_price_is_deactivated():
    changes = deact.decide_activation(_candidates_df(_candidate(has_price=False)), NOW)

    assert list(changes["id"]) == [1]
    assert bool(changes["is_active"].iloc[0]) is False


def test_active_stock_without_sector_is_deactivated():
    changes = deact.decide_activation(_candidates_df(_candidate(sector=None)), NOW)

    assert bool(changes["is_active"].iloc[0]) is False


def test_active_stock_with_na_sector_is_deactivated():
    changes = deact.decide_activation(_candidates_df(_candidate(sector="N/A")), NOW)

    assert bool(changes["is_active"].iloc[0]) is False


def test_active_stock_without_financial_statement_is_deactivated():
    changes = deact.decide_activation(_candidates_df(_candidate(has_fs=False)), NOW)

    assert bool(changes["is_active"].iloc[0]) is False


def test_relisted_inactive_stock_with_full_data_is_reactivated():
    row = _candidate(is_active=False, relisted=True)

    changes = deact.decide_activation(_candidates_df(row), NOW)

    assert list(changes["id"]) == [1]
    assert bool(changes["is_active"].iloc[0]) is True


def test_relisted_inactive_stock_missing_price_stays_inactive():
    row = _candidate(is_active=False, relisted=True, has_price=False)

    changes = deact.decide_activation(_candidates_df(row), NOW)

    assert changes.empty


def test_not_relisted_inactive_stock_is_left_alone():
    row = _candidate(is_active=False, relisted=False)

    changes = deact.decide_activation(_candidates_df(row), NOW)

    assert changes.empty


def test_changes_carry_full_row_and_bumped_updated_at():
    row = _candidate(id=9, name="종목", sector=None, dart_corp_code="D1")

    changes = deact.decide_activation(_candidates_df(row), NOW)

    assert list(changes.columns) == stock.STOCK_COLUMNS
    assert changes["name"].iloc[0] == "종목"
    assert changes["dart_corp_code"].iloc[0] == "D1"
    assert changes["created_at"].iloc[0] == CREATED
    assert changes["updated_at"].iloc[0] == NOW


def test_decide_activation_handles_empty_candidates():
    changes = deact.decide_activation(_candidates_df(), NOW)

    assert changes.empty
    assert list(changes.columns) == stock.STOCK_COLUMNS


# ── 안전 임계 통계 (pure) ──


def test_summarize_counts_active_before_and_after():
    candidates = _candidates_df(
        _candidate(id=1),
        _candidate(id=2, symbol="B", has_price=False),
        _candidate(id=3, symbol="C", is_active=False, relisted=True),
    )
    changes = deact.decide_activation(candidates, NOW)

    stats = deact.summarize_activation(candidates, changes)

    assert stats["total"] == 3
    assert stats["active_before"] == 2
    assert stats["active_after"] == 2
    assert stats["deactivated"] == 1
    assert stats["reactivated"] == 1


def test_summarize_handles_no_changes():
    candidates = _candidates_df(_candidate())

    stats = deact.summarize_activation(candidates, deact.decide_activation(candidates, NOW))

    assert stats == {
        "total": 1,
        "active_before": 1,
        "active_after": 1,
        "deactivated": 0,
        "reactivated": 0,
    }


# ── 레포지토리 I/O 배선 ──


def test_upsert_batch_merges_full_rows_and_returns_count(lake):
    repo = stock.StockRepository()

    count = repo.upsert_batch(
        [
            StockInfo(symbol="AAA", name="a", market=Market.KR_KOSPI),
            StockInfo(symbol="BBB", name="b", market=Market.KR_KOSPI),
        ],
        run_id="run-1",
    )

    table, df, run_id = lake["merged"][0]
    assert count == 2
    assert (table, run_id) == ("stocks", "run-1")
    assert list(df.columns) == stock.STOCK_COLUMNS
    assert list(df["market"]) == ["KR_KOSPI", "KR_KOSPI"]


def test_upsert_batch_is_noop_for_empty_input(lake):
    assert stock.StockRepository().upsert_batch([]) == 0
    assert lake["merged"] == []


def test_update_sectors_only_touches_known_keys(lake):
    lake["rows"] = _stocks_df(
        _stock_row(id=1, symbol="AAA", market="KR_KOSPI", sector=None),
        _stock_row(id=2, symbol="BBB", market="KR_KOSPI", sector=None),
    )
    repo = stock.StockRepository()

    count = repo.update_sectors([("AAA", "KR_KOSPI", "IT"), ("ZZZ", "KR_KOSPI", "Bio")])

    _, df, _ = lake["merged"][0]
    assert count == 1
    assert list(df["symbol"]) == ["AAA"]
    assert list(df["sector"]) == ["IT"]
    assert df["updated_at"].iloc[0] != CREATED


def test_deactivate_unlisted_flags_only_missing_symbols(lake):
    lake["rows"] = _stocks_df(
        _stock_row(id=1, symbol="AAA"),
        _stock_row(id=2, symbol="BBB"),
    )
    repo = stock.StockRepository()

    count = repo.deactivate_unlisted(Market.KR_KOSPI, {"AAA"})

    _, df, _ = lake["merged"][0]
    assert count == 1
    assert list(df["symbol"]) == ["BBB"]
    assert bool(df["is_active"].iloc[0]) is False


def test_deactivate_unlisted_is_noop_without_symbols(lake):
    lake["rows"] = _stocks_df(_stock_row(id=1, symbol="AAA"))

    assert stock.StockRepository().deactivate_unlisted(Market.KR_KOSPI, set()) == 0
    assert lake["merged"] == []


def test_reactivate_listed_stocks_flags_matching_symbols(lake):
    lake["rows"] = _stocks_df(
        _stock_row(id=1, symbol="AAA", is_active=False),
        _stock_row(id=2, symbol="BBB", is_active=False),
    )

    count = stock.StockRepository().reactivate_listed_stocks(Market.KR_KOSPI, {"BBB"})

    _, df, _ = lake["merged"][0]
    assert count == 1
    assert list(df["symbol"]) == ["BBB"]
    assert bool(df["is_active"].iloc[0]) is True


def test_get_active_stocks_returns_market_enum(lake):
    lake["rows"] = _stocks_df(_stock_row(id=3, symbol="AAA", market="US_NYSE"))

    rows = stock.StockRepository().get_active_stocks(Market.US_NYSE)

    assert rows == [(3, "AAA", Market.US_NYSE)]


def test_get_stocks_without_sector_returns_id_symbol_pairs(lake):
    lake["rows"] = _stocks_df(_stock_row(id=4, symbol="AAA", sector=None))

    assert stock.StockRepository().get_stocks_without_sector(Market.KR_KOSPI) == [(4, "AAA")]


def test_find_by_id_returns_none_when_absent(lake):
    assert stock.StockRepository().find_by_id(999) is None


def test_run_id_falls_back_to_environment(lake, monkeypatch):
    monkeypatch.setenv("RUN_ID", "env-run")
    lake["rows"] = _stocks_df(_stock_row(id=1, symbol="AAA"))

    stock.StockRepository().deactivate_unlisted(Market.KR_KOSPI, {"ZZZ"})

    assert lake["merged"][0][2] == "env-run"
