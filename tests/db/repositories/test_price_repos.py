from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

import app.db.repositories.benchmark as benchmark_repo
import app.db.repositories.daily_price as daily_price
import app.db.repositories.exchange_rate as exchange_rate
import app.db.repositories.lake_rows as lake_rows
import app.db.repositories.risk_free_rate as risk_free_rate
from app.schema import Benchmark, BenchmarkPrice, Country, Market, Maturity, RiskFreeRate

D1 = date(1990, 1, 2)
D2 = date(1990, 1, 3)


def _stocks_df(*rows: dict) -> pd.DataFrame:
    return pd.DataFrame(list(rows), columns=["id", "market"])


@pytest.fixture
def lake(monkeypatch):
    state = {
        "stocks": _stocks_df({"id": 1, "market": "KR_KOSPI"}, {"id": 2, "market": "US_NYSE"}),
        "rows": pd.DataFrame(),
        "queries": [],
        "merged": [],
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

    monkeypatch.setattr(daily_price.lake_reader, "scan", lambda table: f"scan_{table}")
    monkeypatch.setattr(daily_price.lake_reader, "query_df", fake_query_df)
    monkeypatch.setattr(daily_price.lake_reader, "invalidate_metadata_cache", lambda t=None: None)
    monkeypatch.setattr(daily_price.lake_writer, "merge", fake_merge)
    monkeypatch.setattr(lake_rows, "run_query", lambda sql: state["athena"].append(sql))
    return state


# ── market_group 매핑 (pure) ──


@pytest.mark.parametrize(
    "market, expected",
    [
        ("KR_KOSPI", "KR"),
        ("KR_KOSDAQ", "KR"),
        ("US_NYSE", "US"),
        ("US_NASDAQ", "US"),
        (Market.KR_KOSDAQ, "KR"),
        (Market.US_NASDAQ, "US"),
    ],
)
def test_to_market_group_reduces_market_to_partition_value(market, expected):
    assert daily_price.to_market_group(market) == expected


def test_to_market_group_rejects_unknown_market():
    with pytest.raises(ValueError):
        daily_price.to_market_group("JP_TSE")


def test_attach_market_fills_partition_column_per_stock():
    rows = pd.DataFrame(
        [
            {"stock_id": 1, "date": D1, "close": 10},
            {"stock_id": 2, "date": D1, "close": 20},
        ]
    )

    out = daily_price.attach_market(rows, {1: "KR", 2: "US"})

    assert list(out["market"]) == ["KR", "US"]


def test_attach_market_drops_rows_whose_stock_is_unknown():
    rows = pd.DataFrame(
        [
            {"stock_id": 1, "date": D1, "close": 10},
            {"stock_id": 99, "date": D1, "close": 20},
        ]
    )

    out = daily_price.attach_market(rows, {1: "KR"})

    assert list(out["stock_id"]) == [1]


def test_attach_market_returns_empty_frame_when_nothing_resolves():
    rows = pd.DataFrame([{"stock_id": 99, "date": D1, "close": 10}])

    assert daily_price.attach_market(rows, {}).empty


# ── daily_prices 배선 ──


def test_bulk_upsert_merges_with_market_and_created_at(lake):
    rows = [
        (1, D1, Decimal("10"), Decimal("12"), Decimal("9"), Decimal("11"), 100),
        (2, D1, Decimal("20"), Decimal("22"), Decimal("19"), Decimal("21"), 200),
    ]

    count = daily_price.DailyPriceRepository().bulk_upsert(rows, run_id="run-1")

    table, df, run_id = lake["merged"][0]
    assert count == 2
    assert (table, run_id) == ("daily_prices", "run-1")
    assert list(df.columns) == daily_price.PRICE_COLUMNS
    assert list(df["market"]) == ["KR", "US"]
    assert df["created_at"].notna().all()


def test_bulk_upsert_skips_rows_without_a_known_stock(lake):
    rows = [(77, D1, Decimal("1"), Decimal("1"), Decimal("1"), Decimal("1"), 1)]

    assert daily_price.DailyPriceRepository().bulk_upsert(rows) == 0
    assert lake["merged"] == []


def test_bulk_upsert_is_noop_for_empty_input(lake):
    assert daily_price.DailyPriceRepository().bulk_upsert([]) == 0
    assert lake["merged"] == []


def test_upsert_batch_writes_one_stock_worth_of_prices(lake):
    prices = [
        daily_price.DailyPrice(
            symbol="AAA", date=D1, open=Decimal("1"), high=Decimal("2"),
            low=Decimal("1"), close=Decimal("2"), volume=5,
        )
    ]

    count = daily_price.DailyPriceRepository().upsert_batch(1, prices, run_id="run-2")

    _, df, _ = lake["merged"][0]
    assert count == 1
    assert list(df["stock_id"]) == [1]
    assert list(df["market"]) == ["KR"]


def test_upsert_batch_is_noop_for_empty_input(lake):
    assert daily_price.DailyPriceRepository().upsert_batch(1, []) == 0
    assert lake["merged"] == []


def test_get_prices_by_market_groups_rows_and_keeps_decimals(lake):
    lake["rows"] = pd.DataFrame(
        [
            {"stock_id": 1, "date": pd.Timestamp(D1), "open": 10.0, "high": 12.0,
             "low": 9.0, "close": 11.0, "volume": 100},
            {"stock_id": 1, "date": pd.Timestamp(D2), "open": 11.0, "high": 13.0,
             "low": 10.0, "close": 12.5, "volume": 110},
        ]
    )

    result = daily_price.DailyPriceRepository().get_prices_by_market(
        Market.KR_KOSPI, limit_per_stock=2
    )

    sql, params = lake["queries"][-1]
    assert "QUALIFY" in sql
    assert params[0] == "KR"
    assert params[1] == "KR_KOSPI"
    assert params[2] == 2
    assert list(result) == [1]
    assert result[1][0][0] == D1
    assert result[1][1][4] == Decimal("12.5")
    assert result[1][0][5] == 100


def test_get_close_prices_batch_returns_float_close_by_date(lake):
    lake["rows"] = pd.DataFrame(
        [
            {"stock_id": 1, "date": pd.Timestamp(D1), "close": 11.0},
            {"stock_id": 1, "date": pd.Timestamp(D2), "close": 12.5},
        ]
    )

    result = daily_price.DailyPriceRepository().get_close_prices_batch([1], limit=2)

    assert result == {1: {D1: 11.0, D2: 12.5}}


def test_get_close_prices_batch_is_noop_without_ids(lake):
    assert daily_price.DailyPriceRepository().get_close_prices_batch([]) == {}


def test_get_prices_returns_dtos_with_symbol_and_decimals(lake):
    lake["rows"] = pd.DataFrame(
        [
            {"symbol": "AAA", "date": pd.Timestamp(D2), "open": 11.0, "high": 13.0,
             "low": 10.0, "close": 12.5, "volume": 110},
        ]
    )

    prices = daily_price.DailyPriceRepository().get_prices(1, limit=1)

    assert prices[0].symbol == "AAA"
    assert prices[0].date == D2
    assert prices[0].close == Decimal("12.5")
    assert prices[0].volume == 110


def test_get_latest_date_returns_python_date(lake):
    lake["rows"] = pd.DataFrame([{"latest": pd.Timestamp(D2)}])

    assert daily_price.DailyPriceRepository().get_latest_date(1) == D2


def test_get_latest_date_returns_none_when_table_has_no_rows(lake):
    lake["rows"] = pd.DataFrame([{"latest": pd.NaT}])

    assert daily_price.DailyPriceRepository().get_latest_date(1) is None


def test_delete_before_counts_rows_then_issues_athena_delete(lake):
    lake["rows"] = pd.DataFrame([{"n": 3}])

    deleted = daily_price.DailyPriceRepository().delete_before(D2)

    assert deleted == 3
    assert "DELETE FROM" in lake["athena"][0]
    assert "1990-01-03" in lake["athena"][0]


def test_delete_skips_athena_when_nothing_matches(lake):
    lake["rows"] = pd.DataFrame([{"n": 0}])

    assert daily_price.DailyPriceRepository().delete_all() == 0
    assert lake["athena"] == []


# ── benchmark / risk_free / exchange 배선 ──


def test_benchmark_upsert_batch_merges_expected_columns(lake):
    prices = [BenchmarkPrice(benchmark=Benchmark.KR_KOSPI, date=D1, close=Decimal("2500.5"))]

    count = benchmark_repo.BenchmarkRepository().upsert_batch(prices, run_id="run-3")

    table, df, run_id = lake["merged"][0]
    assert count == 1
    assert (table, run_id) == ("benchmark_daily_prices", "run-3")
    assert list(df.columns) == benchmark_repo.BENCHMARK_COLUMNS
    assert list(df["benchmark"]) == ["KR_KOSPI"]


def test_benchmark_get_prices_returns_dtos(lake):
    lake["rows"] = pd.DataFrame(
        [{"benchmark": "KR_KOSPI", "date": pd.Timestamp(D1), "close": 2500.5}]
    )

    prices = benchmark_repo.BenchmarkRepository().get_prices(Benchmark.KR_KOSPI, limit=1)

    assert prices[0].benchmark == Benchmark.KR_KOSPI
    assert prices[0].date == D1
    assert prices[0].close == Decimal("2500.5")


def test_benchmark_upsert_batch_is_noop_for_empty_input(lake):
    assert benchmark_repo.BenchmarkRepository().upsert_batch([]) == 0
    assert lake["merged"] == []


def test_risk_free_upsert_batch_merges_expected_columns(lake):
    rates = [
        RiskFreeRate(country=Country.KR, maturity=Maturity.D91, date=D1, rate=Decimal("3.25"))
    ]

    count = risk_free_rate.RiskFreeRateRepository().upsert_batch(rates, run_id="run-4")

    table, df, run_id = lake["merged"][0]
    assert count == 1
    assert (table, run_id) == ("risk_free_rates", "run-4")
    assert list(df.columns) == risk_free_rate.RATE_COLUMNS
    assert list(df["maturity"]) == ["91D"]


def test_risk_free_get_latest_rate_returns_decimal(lake):
    lake["rows"] = pd.DataFrame([{"rate": 3.25}])

    rate = risk_free_rate.RiskFreeRateRepository().get_latest_rate(Country.KR, Maturity.D91)

    assert rate == Decimal("3.25")


def test_risk_free_get_latest_rate_returns_none_when_absent(lake):
    lake["rows"] = pd.DataFrame(columns=["rate"])

    assert risk_free_rate.RiskFreeRateRepository().get_latest_rate(Country.KR, Maturity.D91) is None


def test_risk_free_get_rates_returns_dtos(lake):
    lake["rows"] = pd.DataFrame(
        [{"country": "KR", "maturity": "91D", "date": pd.Timestamp(D1), "rate": 3.25}]
    )

    rates = risk_free_rate.RiskFreeRateRepository().get_rates(Country.KR, Maturity.D91)

    assert rates[0].country == Country.KR
    assert rates[0].maturity == Maturity.D91
    assert rates[0].rate == Decimal("3.25")


def test_exchange_upsert_batch_merges_pair_rows(lake):
    rows = [exchange_rate.ExchangeRateRow(pair="USDKRW", date=D1, rate=Decimal("1300.5"))]

    count = exchange_rate.ExchangeRateRepository().upsert_batch(rows, run_id="run-5")

    table, df, run_id = lake["merged"][0]
    assert count == 1
    assert (table, run_id) == ("exchange_rates", "run-5")
    assert list(df.columns) == exchange_rate.FX_COLUMNS


def test_exchange_upsert_one_merges_single_row(lake):
    row = exchange_rate.ExchangeRateRow(pair="USDKRW", date=D1, rate=Decimal("1300.5"))

    assert exchange_rate.ExchangeRateRepository().upsert_one(row) is None
    _, df, _ = lake["merged"][0]
    assert len(df) == 1
    assert list(df["pair"]) == ["USDKRW"]


def test_exchange_get_rate_on_or_before_returns_decimal(lake):
    lake["rows"] = pd.DataFrame([{"rate": 1300.5}])

    rate = exchange_rate.ExchangeRateRepository().get_rate_on_or_before("USDKRW", D2)

    assert rate == Decimal("1300.5")


def test_exchange_get_latest_rate_returns_none_when_absent(lake):
    lake["rows"] = pd.DataFrame(columns=["rate"])

    assert exchange_rate.ExchangeRateRepository().get_latest_rate("USDKRW") is None


def test_repositories_still_accept_a_legacy_connection_argument(lake):
    assert daily_price.DailyPriceRepository(object()) is not None
    assert benchmark_repo.BenchmarkRepository(object()) is not None
    assert risk_free_rate.RiskFreeRateRepository(object()) is not None
    assert exchange_rate.ExchangeRateRepository(object()) is not None


def test_run_id_falls_back_to_environment(lake, monkeypatch):
    monkeypatch.setenv("RUN_ID", "env-run")
    rows = [(1, D1, Decimal("1"), Decimal("1"), Decimal("1"), Decimal("1"), 1)]

    daily_price.DailyPriceRepository().bulk_upsert(rows)

    assert lake["merged"][0][2] == "env-run"
