import uuid
from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from app.db import lake_reader, lake_writer
from app.db.athena_runner import run_query
from app.db.repositories.daily_price import DailyPriceRepository
from app.db.repositories.factor import FactorRepository
from app.db.repositories.financial_statement import FinancialStatementRepository
from app.db.repositories.indicator import IndicatorRepository
from app.schema import FinancialStatement, Market, ReportType

pytestmark = pytest.mark.integration

STOCK_A, STOCK_B = 999901, 999902
FISCAL_YEAR = 1990
DAY = date(1990, 1, 2)
MATRIX = [[1.25, 0.5], [0.5, 2.75]]


def _run_id() -> str:
    return f"test-{uuid.uuid4().hex[:8]}"


def _seed_stocks() -> None:
    now = pd.Timestamp.now(tz="UTC")
    rows = pd.DataFrame(
        [
            {"id": STOCK_A, "symbol": "_T11A", "name": "task11 a", "market": "KR_KOSPI"},
            {"id": STOCK_B, "symbol": "_T11B", "name": "task11 b", "market": "KR_KOSPI"},
        ]
    )
    rows["is_active"] = True
    rows["dart_corp_code"] = None
    rows["sector"] = "IT"
    rows["created_at"] = now
    rows["updated_at"] = now
    lake_writer.merge("stocks", rows, _run_id())


def _seed_prices() -> None:
    close = Decimal("10000")
    DailyPriceRepository().bulk_upsert(
        [
            (stock_id, DAY, close, close, close, close, 1000)
            for stock_id in (STOCK_A, STOCK_B)
        ],
        run_id=_run_id(),
    )


def _statement(report_type: ReportType, revenue: str) -> FinancialStatement:
    return FinancialStatement(
        stock_id=STOCK_A, fiscal_year=FISCAL_YEAR, report_type=report_type,
        revenue=Decimal(revenue), operating_income=Decimal("20"), net_income=Decimal("10"),
        total_assets=Decimal("500"), total_liabilities=Decimal("200"),
        total_equity=Decimal("300"), shares_outstanding=1234,
    )


def _indicator_row(stock_id: int) -> tuple:
    return tuple([stock_id, DAY] + [1.5] * 16 + [100, 200] + [1.5] * 4)


def _count(table: str, predicate: str = "") -> int:
    clause = f" WHERE {predicate}" if predicate else ""
    sql = f"SELECT count(*) AS n FROM {lake_reader.scan(table)}{clause}"
    return int(lake_reader.query_df(sql).iloc[0]["n"])


def _cleanup(*tables: str) -> None:
    for table in tables:
        run_query(f"DELETE FROM saramquant.{table}")
        lake_reader.invalidate_metadata_cache(table)


@pytest.fixture
def statement_cleanup():
    yield
    _cleanup("financial_statements", "stocks")


@pytest.fixture
def covariance_cleanup():
    yield
    _cleanup("factor_covariance")


@pytest.fixture
def indicator_cleanup():
    yield
    _cleanup("stock_indicators", "daily_prices", "stocks")


def test_financial_statement_merge_reads_back_in_ttm_order(statement_cleanup):
    _seed_stocks()
    repo = FinancialStatementRepository()

    written = repo.upsert_batch(
        [_statement(ReportType.Q1, "100"), _statement(ReportType.FY, "400")],
        run_id=_run_id(),
    )

    assert written == 2
    assert _count("financial_statements", f"market = 'KR' AND stock_id = {STOCK_A}") == 2

    ttm = repo.get_ttm_by_stock(STOCK_A)
    assert [s.report_type for s in ttm] == [ReportType.FY, ReportType.Q1]
    assert ttm[0].revenue == Decimal("400.00")
    assert ttm[0].shares_outstanding == 1234
    assert ttm[0].fiscal_year == FISCAL_YEAR

    by_market = repo.get_ttm_by_market(Market.KR_KOSPI)
    assert [s.report_type for s in by_market[STOCK_A]] == [ReportType.FY, ReportType.Q1]


def test_factor_covariance_roundtrips_the_matrix_through_json(covariance_cleanup):
    repo = FactorRepository()

    repo.upsert_covariance(Market.KR_KOSPI, DAY, MATRIX, run_id=_run_id())

    assert repo.get_latest_covariance(Market.KR_KOSPI) == (DAY, MATRIX)
    assert repo.get_latest_covariance(Market.US_NYSE) is None


def test_stock_indicator_snapshot_replace_keeps_only_the_last_write(indicator_cleanup):
    _seed_stocks()
    _seed_prices()
    repo = IndicatorRepository()

    assert repo.insert_batch(
        [_indicator_row(STOCK_A), _indicator_row(STOCK_B)], run_id=_run_id()
    ) == 2
    assert _count("stock_indicators") == 2

    latest = repo.get_latest_by_stock(STOCK_A)
    assert latest["rsi_14"] == Decimal("1.5000")
    assert latest["obv"] == 100
    assert latest["date"] == DAY
    assert latest["close"] == Decimal("10000.00")

    assert repo.insert_batch([_indicator_row(STOCK_B)], run_id=_run_id()) == 1
    assert _count("stock_indicators") == 1
    assert repo.get_latest_by_stock(STOCK_A) is None
    assert list(repo.get_all_by_market(Market.KR_KOSPI)) == [STOCK_B]
