import logging
from decimal import Decimal

import pandas as pd
from psycopg2.extensions import connection

from app.db import BenchmarkRepository, RiskFreeRateRepository
from app.schema import Market, Benchmark, Country, Maturity
from app.schema.enums.market import market_to_benchmark, market_to_country
from app.quant.indicators import daily_returns

logger = logging.getLogger(__name__)


def load_benchmark_returns(
    conn: connection, markets: list[Market], limit: int = 300
) -> dict[Benchmark, pd.Series]:
    repo = BenchmarkRepository(conn)
    benchmarks = {market_to_benchmark(m) for m in markets}
    result: dict[Benchmark, pd.Series] = {}

    for bench in benchmarks:
        prices = repo.get_prices(bench, limit=limit)
        if not prices:
            logger.warning(f"[ReferenceData] No benchmark data for {bench.value}")
            continue

        data = [
            {"date": p.date, "close": float(p.close) if isinstance(p.close, Decimal) else p.close}
            for p in prices
        ]
        df = pd.DataFrame(data).set_index("date").sort_index()
        result[bench] = daily_returns(df["close"])

    return result


def load_risk_free_rates(
    conn: connection, markets: list[Market]
) -> dict[Country, float]:
    repo = RiskFreeRateRepository(conn)
    countries = {market_to_country(m) for m in markets}
    result: dict[Country, float] = {}

    for country in countries:
        rate = repo.get_latest_rate(country, Maturity.D91)
        if rate is not None:
            result[country] = float(rate)
        else:
            default = 3.0 if country == Country.KR else 4.0
            logger.warning(
                f"[ReferenceData] No risk-free rate for {country.value}, using default {default}%"
            )
            result[country] = default

    return result
