import logging
import os
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
from psycopg2.extensions import connection

from app.db import DailyPriceRepository
from app.db.repositories.indicator import IndicatorRepository
from app.schema import Market, Benchmark, Country
from app.schema.enums.market import market_to_benchmark, market_to_country
from app.services import IndicatorService
from app.services.factor_model_service import FactorModelService
from app.utils import load_benchmark_returns, load_risk_free_rates

logger = logging.getLogger(__name__)

_MAX_WORKERS = min(8, os.cpu_count() or 8)
_CHUNK_SIZE = 50


def _compute_chunk(
    stock_batch: list[tuple[int, list[tuple]]],
    bench_ret: pd.Series | None,
    rf_rate: float,
    factor_betas: dict[int, float | None],
) -> tuple[list[tuple], list[int]]:
    rows, failed = [], []
    for stock_id, raw_prices in stock_batch:
        try:
            df = IndicatorService.build_dataframe(raw_prices)
            if df is not None:
                rows.append(IndicatorService.compute(
                    stock_id, df, bench_ret, rf_rate, factor_betas.get(stock_id),
                ))
        except Exception:
            failed.append(stock_id)
    return rows, failed


class IndicatorComputeEngine:
    def __init__(self, conn: connection):
        self._conn = conn
        self._price_repo = DailyPriceRepository(conn)
        self._indicator_repo = IndicatorRepository(conn)
        self._factor_service = FactorModelService(conn)

    def run(
        self,
        markets: list[Market],
        price_maps: dict[Market, dict[int, list[tuple]]] | None = None,
    ) -> int:
        benchmark_returns = load_benchmark_returns(self._conn, markets)
        rf_rates = load_risk_free_rates(self._conn, markets)

        all_chunks: list[tuple[list, pd.Series | None, float, dict]] = []
        for market in markets:
            pm = price_maps.get(market) if price_maps else None
            chunks = self._prepare_market(market, benchmark_returns, rf_rates, pm)
            all_chunks.extend(chunks)

        all_rows: list[tuple] = []
        all_failed: list[int] = []
        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
            futures = [
                pool.submit(_compute_chunk, chunk, bench_ret, rf_rate, fb)
                for chunk, bench_ret, rf_rate, fb in all_chunks
            ]
            for fut in futures:
                batch_rows, batch_failed = fut.result()
                all_rows.extend(batch_rows)
                all_failed.extend(batch_failed)

        if all_failed:
            logger.warning(f"[Compute] {len(all_failed)} stocks failed: {all_failed[:20]}")

        count = self._indicator_repo.upsert_batch(all_rows)
        self._conn.commit()
        logger.info(f"[Compute] Upserted {count} indicator rows")
        return count

    def _prepare_market(
        self,
        market: Market,
        benchmark_returns: dict[Benchmark, pd.Series],
        rf_rates: dict[Country, float],
        price_map: dict[int, list[tuple]] | None = None,
    ) -> list[tuple[list, pd.Series | None, float, dict]]:
        if price_map is None:
            price_map = self._price_repo.get_prices_by_market(market, limit_per_stock=300)
        if not price_map:
            logger.warning(f"[Compute] No price data for {market.value}")
            return []

        bench_ret = benchmark_returns.get(market_to_benchmark(market))
        rf_rate = rf_rates.get(market_to_country(market), 3.0)
        factor_betas = self._factor_service.get_betas(market)

        fb_used = sum(1 for sid in price_map if sid in factor_betas)
        logger.info(
            f"[Compute] {market.value}: preparing {len(price_map)} stocks "
            f"({fb_used} factor betas, {_MAX_WORKERS} workers)"
        )

        items = list(price_map.items())
        return [
            (items[i:i + _CHUNK_SIZE], bench_ret, rf_rate, factor_betas)
            for i in range(0, len(items), _CHUNK_SIZE)
        ]
