import json
from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

import app.db.repositories.factor as factor
import app.db.repositories.financial_statement as financial_statement
import app.db.repositories.fundamental as fundamental
import app.db.repositories.indicator as indicator
import app.db.repositories.lake_rows as lake_rows
import app.db.repositories.risk_badge as risk_badge
from app.db import lake_reader, lake_writer
from app.schema import FinancialStatement, Market, ReportType

D1 = date(1990, 1, 2)


@pytest.fixture
def lake(monkeypatch):
    state = {
        "stocks": pd.DataFrame(
            [{"id": 1, "market": "KR_KOSPI"}, {"id": 2, "market": "US_NYSE"}]
        ),
        "rows": pd.DataFrame(),
        "queries": [],
        "merged": [],
        "snapshots": [],
        "athena": [],
    }

    def fake_query_df(sql, params=None):
        state["queries"].append((sql, params))
        if "SELECT id, market" in sql:
            return state["stocks"].copy()
        return state["rows"].copy()

    def fake_merge(table, df, run_id):
        state["merged"].append((table, df.copy(), run_id))
        return len(df)

    def fake_snapshot_replace(table, df, run_id):
        state["snapshots"].append((table, df.copy(), run_id))
        return len(df)

    monkeypatch.setattr(lake_reader, "scan", lambda table: f"scan_{table}")
    monkeypatch.setattr(lake_reader, "query_df", fake_query_df)
    monkeypatch.setattr(lake_reader, "invalidate_metadata_cache", lambda t=None: None)
    monkeypatch.setattr(lake_writer, "merge", fake_merge)
    monkeypatch.setattr(lake_writer, "snapshot_replace", fake_snapshot_replace)
    monkeypatch.setattr(lake_rows, "run_query", lambda sql: state["athena"].append(sql))
    return state


def _statement(stock_id: int, fiscal_year: int, report_type: ReportType) -> FinancialStatement:
    return FinancialStatement(
        stock_id=stock_id, fiscal_year=fiscal_year, report_type=report_type,
        revenue=Decimal("100"), operating_income=Decimal("20"), net_income=Decimal("10"),
        total_assets=Decimal("500"), total_liabilities=Decimal("200"),
        total_equity=Decimal("300"), shares_outstanding=1000,
    )


# ── financial_statements ──


def test_ttm_order_keeps_the_postgres_case_ranking():
    assert financial_statement.TTM_ORDER == (
        "CASE report_type WHEN 'FY' THEN 4 WHEN 'Q3' THEN 3"
        " WHEN 'Q2' THEN 2 WHEN 'Q1' THEN 1 END DESC"
    )


def test_fs_upsert_batch_merges_with_market_group_and_created_at(lake):
    statements = [_statement(1, 2024, ReportType.FY), _statement(2, 2024, ReportType.Q1)]

    count = financial_statement.FinancialStatementRepository().upsert_batch(
        statements, run_id="run-fs"
    )

    table, df, run_id = lake["merged"][0]
    assert count == 2
    assert (table, run_id) == ("financial_statements", "run-fs")
    assert list(df.columns) == financial_statement.STATEMENT_COLUMNS
    assert list(df["market"]) == ["KR", "US"]
    assert list(df["report_type"]) == ["FY", "Q1"]
    assert df["created_at"].notna().all()


def test_fs_upsert_batch_drops_statements_with_unknown_stock(lake):
    assert financial_statement.FinancialStatementRepository().upsert_batch(
        [_statement(77, 2024, ReportType.FY)]
    ) == 0
    assert lake["merged"] == []


def test_fs_upsert_batch_is_noop_for_empty_input(lake):
    assert financial_statement.FinancialStatementRepository().upsert_batch([]) == 0


def test_fs_get_ttm_by_stock_orders_by_case_and_returns_dtos(lake):
    lake["rows"] = pd.DataFrame(
        [
            {"stock_id": 1, "fiscal_year": 2024, "report_type": "FY", "revenue": 100.0,
             "operating_income": 20.0, "net_income": 10.0, "total_assets": 500.0,
             "total_liabilities": 200.0, "total_equity": 300.0, "shares_outstanding": 1000},
        ]
    )

    result = financial_statement.FinancialStatementRepository().get_ttm_by_stock(1)

    sql, params = lake["queries"][-1]
    assert financial_statement.TTM_ORDER in sql
    assert "LIMIT 10" in sql
    assert params == [1]
    assert result[0].report_type is ReportType.FY
    assert result[0].revenue == Decimal("100")
    assert result[0].shares_outstanding == 1000


def test_fs_get_ttm_by_market_groups_by_stock_and_caps_at_ten(lake):
    lake["rows"] = pd.DataFrame(
        [
            {"stock_id": 1, "fiscal_year": 2024 - (index // 2), "report_type": "FY",
             "revenue": 1.0, "operating_income": None, "net_income": None,
             "total_assets": None, "total_liabilities": None, "total_equity": None,
             "shares_outstanding": None}
            for index in range(24)
        ]
    )

    result = financial_statement.FinancialStatementRepository().get_ttm_by_market(
        Market.KR_KOSPI
    )

    assert len(result[1]) == 10
    assert result[1][0].operating_income is None
    assert result[1][0].shares_outstanding is None


# ── stock_fundamentals ──


def test_fundamental_upsert_batch_merges_expected_columns(lake):
    rows = [(1, D1, 10.0, 1.2, 500.0, 4000.0, 0.12, 0.5, 0.08, "FULL")]

    count = fundamental.FundamentalRepository().upsert_batch(rows, run_id="run-fund")

    table, df, run_id = lake["merged"][0]
    assert count == 1
    assert (table, run_id) == ("stock_fundamentals", "run-fund")
    assert list(df.columns) == lake_rows.columns_of("stock_fundamentals")
    assert list(df["data_coverage"]) == ["FULL"]


def test_fundamental_get_with_shares_returns_decimals_and_int_shares(lake):
    lake["rows"] = pd.DataFrame(
        [{"stock_id": 1, "pbr": 1.2, "roe": 0.12, "operating_margin": 0.08,
          "debt_ratio": 0.5, "shares_outstanding": 1000.0}]
    )

    rows = fundamental.FundamentalRepository().get_with_shares([1])

    assert rows == [(1, Decimal("1.2"), Decimal("0.12"), Decimal("0.08"), Decimal("0.5"), 1000)]


def test_fundamental_get_with_shares_is_noop_without_ids(lake):
    assert fundamental.FundamentalRepository().get_with_shares([]) == []


def test_fundamental_get_latest_by_stock_maps_nan_to_none(lake):
    lake["rows"] = pd.DataFrame(
        [{"stock_id": 1, "date": pd.Timestamp(D1), "per": float("nan"), "pbr": 1.2,
          "data_coverage": "FULL", "sector": "IT", "market": "KR_KOSPI"}]
    )

    row = fundamental.FundamentalRepository().get_latest_by_stock(1)

    assert row["per"] is None
    assert row["pbr"] == Decimal("1.2")
    assert row["date"] == D1
    assert row["sector"] == "IT"


def test_fundamental_get_latest_by_stock_returns_none_when_absent(lake):
    lake["rows"] = pd.DataFrame(columns=["stock_id"])

    assert fundamental.FundamentalRepository().get_latest_by_stock(1) is None


def test_fundamental_get_all_by_market_keys_by_stock_id(lake):
    lake["rows"] = pd.DataFrame(
        [{"stock_id": 1, "date": pd.Timestamp(D1), "roe": 0.12, "sector": "IT",
          "market": "KR_KOSPI", "symbol": "AAA"}]
    )

    result = fundamental.FundamentalRepository().get_all_by_market(Market.KR_KOSPI)

    assert list(result) == [1]
    assert result[1]["symbol"] == "AAA"
    assert result[1]["roe"] == Decimal("0.12")


# ── stock_indicators (snapshot) ──


def test_indicator_insert_batch_replaces_the_whole_snapshot(lake):
    rows = [tuple([1, D1] + [1.0] * 16 + [10, 20] + [1.0] * 4)]

    count = indicator.IndicatorRepository().insert_batch(rows, run_id="run-ind")

    table, df, run_id = lake["snapshots"][0]
    assert count == 1
    assert (table, run_id) == ("stock_indicators", "run-ind")
    assert list(df.columns) == lake_rows.columns_of("stock_indicators")
    assert lake["merged"] == []


def test_indicator_insert_batch_is_noop_for_empty_input(lake):
    assert indicator.IndicatorRepository().insert_batch([]) == 0
    assert lake["snapshots"] == []


def test_indicator_delete_by_markets_is_superseded_by_the_snapshot_write(lake):
    assert indicator.IndicatorRepository().delete_by_markets([Market.KR_KOSPI]) == 0
    assert lake["athena"] == []


def test_indicator_get_latest_by_stock_maps_nan_to_none(lake):
    lake["rows"] = pd.DataFrame(
        [{"stock_id": 1, "date": pd.Timestamp(D1), "rsi_14": 55.0, "beta": float("nan"),
          "obv": 100, "close": 1000.0}]
    )

    row = indicator.IndicatorRepository().get_latest_by_stock(1)

    assert row["rsi_14"] == Decimal("55.0")
    assert row["beta"] is None
    assert row["obv"] == 100
    assert row["close"] == Decimal("1000.0")


def test_indicator_get_all_by_market_keys_by_stock_id(lake):
    lake["rows"] = pd.DataFrame(
        [{"stock_id": 1, "date": pd.Timestamp(D1), "rsi_14": 55.0, "close": 1000.0,
          "sector": "IT"}]
    )

    result = indicator.IndicatorRepository().get_all_by_market(Market.KR_KOSPI)

    assert list(result) == [1]
    assert result[1]["sector"] == "IT"


# ── factor_* / sector_aggregates ──


def test_factor_upsert_covariance_serializes_the_matrix_as_json(lake):
    matrix = [[1.0, 0.5], [0.5, 2.0]]

    factor.FactorRepository().upsert_covariance(Market.KR_KOSPI, D1, matrix)

    table, df, _ = lake["merged"][0]
    assert table == "factor_covariance"
    assert json.loads(df.iloc[0]["matrix"]) == matrix
    assert df.iloc[0]["market"] == "KR_KOSPI"


def test_factor_get_latest_covariance_deserializes_the_matrix(lake):
    matrix = [[1.0, 0.5], [0.5, 2.0]]
    lake["rows"] = pd.DataFrame(
        [{"date": pd.Timestamp(D1), "matrix": json.dumps(matrix)}]
    )

    result = factor.FactorRepository().get_latest_covariance(Market.KR_KOSPI)

    assert result == (D1, matrix)


def test_factor_get_latest_covariance_returns_none_when_absent(lake):
    lake["rows"] = pd.DataFrame(columns=["date", "matrix"])

    assert factor.FactorRepository().get_latest_covariance(Market.KR_KOSPI) is None


def test_factor_upsert_exposures_merges_expected_columns(lake):
    rows = [(1, D1, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6)]

    count = factor.FactorRepository().upsert_exposures(rows, run_id="run-exp")

    table, df, run_id = lake["merged"][0]
    assert count == 1
    assert (table, run_id) == ("factor_exposures", "run-exp")
    assert list(df.columns) == lake_rows.columns_of("factor_exposures")


def test_factor_get_latest_exposures_returns_tuples(lake):
    lake["rows"] = pd.DataFrame(
        [{"stock_id": 1, "size_z": 0.1, "value_z": 0.2, "momentum_z": 0.3,
          "volatility_z": float("nan"), "quality_z": 0.5, "leverage_z": 0.6}]
    )

    rows = factor.FactorRepository().get_latest_exposures(Market.KR_KOSPI)

    assert rows[0][0] == 1
    assert rows[0][1] == Decimal("0.1")
    assert rows[0][4] is None


def test_factor_upsert_factor_returns_merges_expected_columns(lake):
    rows = [("KR_KOSPI", D1, "market", 0.001)]

    count = factor.FactorRepository().upsert_factor_returns(rows, run_id="run-ret")

    table, df, run_id = lake["merged"][0]
    assert count == 1
    assert (table, run_id) == ("factor_returns", "run-ret")
    assert list(df.columns) == lake_rows.columns_of("factor_returns")


def test_factor_returns_history_returns_date_name_value_tuples(lake):
    lake["rows"] = pd.DataFrame(
        [{"date": pd.Timestamp(D1), "factor_name": "market", "return_value": 0.001}]
    )

    rows = factor.FactorRepository().get_factor_returns_history(Market.KR_KOSPI, limit=2)

    assert rows == [(D1, "market", Decimal("0.001"))]


def test_factor_count_factor_return_dates_returns_int(lake):
    lake["rows"] = pd.DataFrame([{"n": 120}])

    assert factor.FactorRepository().count_factor_return_dates(Market.KR_KOSPI) == 120


def test_factor_get_all_exposures_by_market_returns_floats(lake):
    lake["rows"] = pd.DataFrame(
        [{"stock_id": 1, "volatility_z": 1.5}, {"stock_id": 2, "volatility_z": float("nan")}]
    )

    result = factor.FactorRepository().get_all_exposures_by_market(Market.KR_KOSPI)

    assert result == {1: 1.5, 2: None}


def test_factor_get_volatility_z_by_stock_returns_float(lake):
    lake["rows"] = pd.DataFrame([{"volatility_z": 1.5}])

    assert factor.FactorRepository().get_volatility_z_by_stock(1, Market.KR_KOSPI) == 1.5


def test_factor_get_volatility_z_by_stock_returns_none_when_absent(lake):
    lake["rows"] = pd.DataFrame(columns=["volatility_z"])

    assert factor.FactorRepository().get_volatility_z_by_stock(1, Market.KR_KOSPI) is None


def test_factor_get_all_sector_aggregates_keys_by_sector(lake):
    lake["rows"] = pd.DataFrame(
        [{"market": "KR_KOSPI", "sector": "IT", "date": pd.Timestamp(D1), "stock_count": 12,
          "median_per": 10.0, "median_pbr": 1.0, "median_roe": 0.1,
          "median_operating_margin": 0.05, "median_debt_ratio": 0.4}]
    )

    result = factor.FactorRepository().get_all_sector_aggregates(Market.KR_KOSPI)

    assert list(result) == ["IT"]
    assert result["IT"]["stock_count"] == 12
    assert result["IT"]["median_per"] == Decimal("10.0")


def test_factor_get_sector_aggregate_single_returns_dict(lake):
    lake["rows"] = pd.DataFrame(
        [{"market": "KR_KOSPI", "sector": "IT", "date": pd.Timestamp(D1), "stock_count": 12,
          "median_per": 10.0}]
    )

    row = factor.FactorRepository().get_sector_aggregate_single(Market.KR_KOSPI, "IT")

    assert row["sector"] == "IT"
    assert row["date"] == D1


def test_factor_get_market_aggregate_returns_none_when_no_stocks(lake):
    lake["rows"] = pd.DataFrame([{"stock_count": 0, "median_per": None}])

    assert factor.FactorRepository().get_market_aggregate(Market.KR_KOSPI) is None


def test_factor_get_market_aggregate_returns_medians(lake):
    lake["rows"] = pd.DataFrame(
        [{"stock_count": 3, "median_per": 10.0, "median_pbr": 1.0, "median_roe": 0.1,
          "median_operating_margin": 0.05, "median_debt_ratio": 0.4}]
    )

    row = factor.FactorRepository().get_market_aggregate(Market.KR_KOSPI)

    assert row["stock_count"] == 3
    assert row["median_per"] == Decimal("10.0")


def test_factor_get_sector_aggregates_returns_group_tuples(lake):
    lake["rows"] = pd.DataFrame(
        [{"market": "KR_KOSPI", "sector": "IT", "stock_count": 12, "median_per": 10.0,
          "median_pbr": 1.0, "median_roe": 0.1, "median_op_margin": 0.05,
          "median_debt_ratio": 0.4}]
    )

    rows = factor.FactorRepository().get_sector_aggregates([Market.KR_KOSPI])

    assert rows[0][0] == "KR_KOSPI"
    assert rows[0][1] == "IT"
    assert rows[0][2] == 12


def test_factor_upsert_sector_aggregates_merges_expected_columns(lake):
    rows = [("KR_KOSPI", "IT", D1, 12, 10.0, 1.0, 0.1, 0.05, 0.4)]

    count = factor.FactorRepository().upsert_sector_aggregates(rows, run_id="run-sec")

    table, df, run_id = lake["merged"][0]
    assert count == 1
    assert (table, run_id) == ("sector_aggregates", "run-sec")
    assert list(df.columns) == lake_rows.columns_of("sector_aggregates")


# ── risk_badges (snapshot + json) ──


def _badge(stock_id: int) -> dict:
    return {
        "stock_id": stock_id,
        "market": "KR_KOSPI",
        "date": "1990-01-02",
        "summary_tier": "STABLE",
        "dimensions": {"dims": [{"name": "trend", "score": 10}]},
    }


def test_risk_badge_upsert_batch_replaces_snapshot_with_json_dimensions(lake):
    count = risk_badge.RiskBadgeRepository().upsert_batch([_badge(1)], run_id="run-badge")

    table, df, run_id = lake["snapshots"][0]
    assert count == 1
    assert (table, run_id) == ("risk_badges", "run-badge")
    assert list(df.columns) == lake_rows.columns_of("risk_badges")
    assert json.loads(df.iloc[0]["dimensions"]) == _badge(1)["dimensions"]
    assert df["updated_at"].notna().all()


def test_risk_badge_upsert_batch_is_noop_for_empty_input(lake):
    assert risk_badge.RiskBadgeRepository().upsert_batch([]) == 0
    assert lake["snapshots"] == []


def test_risk_badge_get_by_stock_deserializes_dimensions(lake):
    dimensions = _badge(1)["dimensions"]
    lake["rows"] = pd.DataFrame(
        [{"stock_id": 1, "market": "KR_KOSPI", "date": pd.Timestamp(D1),
          "summary_tier": "STABLE", "dimensions": json.dumps(dimensions),
          "updated_at": pd.Timestamp("1990-01-02", tz="UTC")}]
    )

    row = risk_badge.RiskBadgeRepository().get_by_stock(1)

    assert row["dimensions"] == dimensions
    assert row["date"] == D1
    assert row["summary_tier"] == "STABLE"


def test_risk_badge_get_by_stock_returns_none_when_absent(lake):
    lake["rows"] = pd.DataFrame(columns=["stock_id"])

    assert risk_badge.RiskBadgeRepository().get_by_stock(1) is None


def test_risk_badge_get_by_stocks_keys_by_stock_id(lake):
    dimensions = _badge(1)["dimensions"]
    lake["rows"] = pd.DataFrame(
        [{"stock_id": 1, "market": "KR_KOSPI", "date": pd.Timestamp(D1),
          "summary_tier": "STABLE", "dimensions": json.dumps(dimensions),
          "updated_at": pd.Timestamp("1990-01-02", tz="UTC")}]
    )

    result = risk_badge.RiskBadgeRepository().get_by_stocks([1])

    assert list(result) == [1]
    assert result[1]["dimensions"] == dimensions


def test_risk_badge_get_by_stocks_is_noop_without_ids(lake):
    assert risk_badge.RiskBadgeRepository().get_by_stocks([]) == {}


# ── legacy 호출 계약 ──


def test_repositories_still_accept_a_legacy_connection_argument(lake):
    assert financial_statement.FinancialStatementRepository(object()) is not None
    assert fundamental.FundamentalRepository(object()) is not None
    assert indicator.IndicatorRepository(object()) is not None
    assert factor.FactorRepository(object()) is not None
    assert risk_badge.RiskBadgeRepository(object()) is not None


def test_run_id_falls_back_to_environment(lake, monkeypatch):
    monkeypatch.setenv("RUN_ID", "env-run")

    risk_badge.RiskBadgeRepository().upsert_batch([_badge(1)])

    assert lake["snapshots"][0][2] == "env-run"
