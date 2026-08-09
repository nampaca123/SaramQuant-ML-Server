import uuid
from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from app.db import lake_writer
from app.db.athena_runner import run_query
from app.db.lake_reader import invalidate_metadata_cache
from app.db.repositories.benchmark import BenchmarkRepository
from app.db.repositories.daily_price import DailyPriceRepository
from app.db.repositories.exchange_rate import ExchangeRateRepository, ExchangeRateRow
from app.db.repositories.risk_free_rate import RiskFreeRateRepository
from app.schema import Benchmark, BenchmarkPrice, Country, Market, Maturity, RiskFreeRate
from tests.lake_guard import skip_destructive

pytestmark = pytest.mark.integration

KR_ID, US_ID = 999901, 999902
DATES = [date(1990, 1, 2), date(1990, 1, 3), date(1990, 1, 4)]
PAIR = "USD/KRW"


def _run_id() -> str:
    return f"test-{uuid.uuid4().hex[:8]}"


def _seed_stocks() -> None:
    now = pd.Timestamp.now(tz="UTC")
    rows = pd.DataFrame(
        [
            {"id": KR_ID, "symbol": "_T10A", "name": "task10 kr", "market": "KR_KOSPI"},
            {"id": US_ID, "symbol": "_T10B", "name": "task10 us", "market": "US_NYSE"},
        ]
    )
    rows["is_active"] = True
    rows["dart_corp_code"] = None
    rows["sector"] = "IT"
    rows["created_at"] = now
    rows["updated_at"] = now
    lake_writer.merge("stocks", rows, _run_id())


def _price_rows() -> list[tuple]:
    rows = []
    for stock_id, base in ((KR_ID, 10000), (US_ID, 200)):
        for offset, day in enumerate(DATES):
            close = Decimal(str(base + offset))
            rows.append((stock_id, day, close, close, close, close, 1000 + offset))
    return rows


@pytest.fixture
def price_cleanup():
    yield
    run_query(f"DELETE FROM saramquant.daily_prices WHERE stock_id IN ({KR_ID}, {US_ID})")
    run_query(f"DELETE FROM saramquant.stocks WHERE id IN ({KR_ID}, {US_ID})")
    invalidate_metadata_cache("daily_prices")
    invalidate_metadata_cache("stocks")


@pytest.fixture
def benchmark_cleanup():
    yield
    run_query(
        "DELETE FROM saramquant.benchmark_daily_prices"
        f" WHERE benchmark = 'KR_KOSPI' AND date <= DATE '{DATES[-1].isoformat()}'"
    )
    invalidate_metadata_cache("benchmark_daily_prices")


@pytest.fixture
def rate_cleanup():
    yield
    run_query(
        "DELETE FROM saramquant.risk_free_rates WHERE country = 'KR' AND maturity = '91D'"
        f" AND date <= DATE '{DATES[-1].isoformat()}'"
    )
    invalidate_metadata_cache("risk_free_rates")


@pytest.fixture
def fx_cleanup():
    yield
    run_query(f"DELETE FROM saramquant.exchange_rates WHERE pair = '{PAIR}'")
    invalidate_metadata_cache("exchange_rates")


def test_daily_price_roundtrip_partitions_by_market_group(price_cleanup):
    _seed_stocks()
    repo = DailyPriceRepository()

    written = repo.bulk_upsert(_price_rows(), run_id=_run_id())

    assert written == 6
    assert repo.get_latest_date(KR_ID) == DATES[-1]
    assert repo.get_latest_date_by_market(Market.KR_KOSPI) >= DATES[-1]

    prices = repo.get_prices(KR_ID, start_date=DATES[0], end_date=DATES[-1])
    assert [p.date for p in prices] == sorted(DATES, reverse=True)
    assert prices[0].symbol == "_T10A"
    assert prices[0].close == Decimal("10002")
    assert prices[0].volume == 1002

    kr_map = repo.get_prices_by_market(Market.KR_KOSPI, limit_per_stock=2)
    us_map = repo.get_prices_by_market(Market.US_NYSE, limit_per_stock=2)
    assert US_ID not in kr_map
    assert KR_ID not in us_map
    assert [row[0] for row in kr_map[KR_ID]] == DATES[1:]
    assert kr_map[KR_ID][-1][4] == Decimal("10002")
    assert us_map[US_ID][-1][4] == Decimal("202")

    closes = repo.get_close_prices_batch([KR_ID, US_ID], limit=3)
    assert closes[KR_ID][DATES[0]] == 10000.0
    assert len(closes[US_ID]) == 3


def test_daily_price_upsert_overwrites_existing_key(price_cleanup):
    _seed_stocks()
    repo = DailyPriceRepository()
    repo.bulk_upsert(_price_rows(), run_id=_run_id())

    updated = (KR_ID, DATES[0], Decimal("1"), Decimal("1"), Decimal("1"), Decimal("99"), 7)
    repo.bulk_upsert([updated], run_id=_run_id())

    prices = repo.get_prices(KR_ID, start_date=DATES[0], end_date=DATES[0])
    assert len(prices) == 1
    assert prices[0].close == Decimal("99")
    assert prices[0].volume == 7


def test_benchmark_roundtrip(benchmark_cleanup):
    repo = BenchmarkRepository()
    prices = [
        BenchmarkPrice(benchmark=Benchmark.KR_KOSPI, date=day, close=Decimal(f"250{index}.5"))
        for index, day in enumerate(DATES)
    ]

    written = repo.upsert_batch(prices, run_id=_run_id())
    stored = repo.get_prices(Benchmark.KR_KOSPI, start_date=DATES[0], end_date=DATES[-1])

    assert written == 3
    assert [p.date for p in stored] == sorted(DATES, reverse=True)
    assert stored[-1].close == Decimal("2500.5")
    assert repo.get_latest_date(Benchmark.KR_KOSPI) >= DATES[-1]


def test_risk_free_rate_roundtrip(rate_cleanup):
    repo = RiskFreeRateRepository()
    rates = [
        RiskFreeRate(
            country=Country.KR, maturity=Maturity.D91, date=day, rate=Decimal(f"3.2{index}")
        )
        for index, day in enumerate(DATES)
    ]

    written = repo.upsert_batch(rates, run_id=_run_id())
    stored = repo.get_rates(Country.KR, Maturity.D91, start_date=DATES[0], end_date=DATES[-1])

    assert written == 3
    assert stored[0].rate == Decimal("3.22")
    assert stored[0].country == Country.KR
    assert stored[0].maturity == Maturity.D91
    assert repo.get_latest_date(Country.KR, Maturity.D91) >= DATES[-1]
    assert repo.get_latest_rate(Country.KR, Maturity.D91) is not None


@skip_destructive
def test_exchange_rate_roundtrip_batch_and_single(fx_cleanup):
    repo = ExchangeRateRepository()
    rows = [
        ExchangeRateRow(pair=PAIR, date=day, rate=Decimal(f"130{index}.5"))
        for index, day in enumerate(DATES)
    ]

    written = repo.upsert_batch(rows, run_id=_run_id())

    assert written == 3
    assert repo.get_latest_date(PAIR) == DATES[-1]
    assert repo.get_latest_rate(PAIR) == Decimal("1302.5")
    assert repo.get_rate_on_or_before(PAIR, DATES[1]) == Decimal("1301.5")

    repo.upsert_one(ExchangeRateRow(pair=PAIR, date=DATES[1], rate=Decimal("1999.0")))

    assert repo.get_rate_on_or_before(PAIR, DATES[1]) == Decimal("1999")
