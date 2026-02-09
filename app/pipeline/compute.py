import logging

import pandas as pd
from psycopg2.extensions import connection

from app.db import DailyPriceRepository
from app.db.repositories.indicator import IndicatorRepository
from app.schema import Market, Benchmark, Country
from app.schema.enums.market import market_to_benchmark, market_to_country
from app.services import IndicatorService
from app.utils import load_benchmark_returns, load_risk_free_rates

logger = logging.getLogger(__name__)


class ComputeEngine:
    def __init__(self, conn: connection):
        self._conn = conn
        self._price_repo = DailyPriceRepository(conn)
        self._indicator_repo = IndicatorRepository(conn)

    def run(self, markets: list[Market]) -> int:
        benchmark_returns = load_benchmark_returns(self._conn, markets)
        rf_rates = load_risk_free_rates(self._conn, markets)

        all_rows: list[tuple] = []
        for market in markets:
            rows = self._process_market(market, benchmark_returns, rf_rates)
            all_rows.extend(rows)

        deleted = self._indicator_repo.delete_by_markets(markets)
        logger.info(f"[Compute] Deleted {deleted} old indicator rows")

        inserted = self._indicator_repo.insert_batch(all_rows)
        self._conn.commit()
        logger.info(f"[Compute] Inserted {inserted} indicator rows")
        return inserted

    def _process_market(
        self,
        market: Market,
        benchmark_returns: dict[Benchmark, pd.Series],
        rf_rates: dict[Country, float],
    ) -> list[tuple]:
        price_map = self._price_repo.get_prices_by_market(market, limit_per_stock=300)
        if not price_map:
            logger.warning(f"[Compute] No price data for {market.value}")
            return []

        bench_ret = benchmark_returns.get(market_to_benchmark(market))
        rf_rate = rf_rates.get(market_to_country(market), 3.0)

        rows: list[tuple] = []
        for i, (stock_id, raw_prices) in enumerate(price_map.items(), 1):
            df = IndicatorService.build_dataframe(raw_prices)
            if df is not None:
                rows.append(IndicatorService.compute(stock_id, df, bench_ret, rf_rate))
            if i % 500 == 0:
                logger.info(f"[Compute] {market.value}: {i}/{len(price_map)} stocks")

        logger.info(f"[Compute] {market.value}: {len(rows)}/{len(price_map)} stocks computed")
        return rows
