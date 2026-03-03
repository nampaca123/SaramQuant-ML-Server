import logging
import os
from concurrent.futures import ProcessPoolExecutor

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
_CHUNK_SIZE = 100

_shared: dict = {}


def _init_worker(bench_ret: pd.Series | None, rf_rate: float, factor_betas: dict[int, float | None]):
    _shared["bench_ret"] = bench_ret
    _shared["rf_rate"] = rf_rate
    _shared["factor_betas"] = factor_betas


def _compute_chunk(stock_batch: list[tuple[int, list[tuple]]]) -> tuple[list[tuple], list[int]]:
    rows, failed = [], []
    for stock_id, raw_prices in stock_batch:
        try:
            df = IndicatorService.build_dataframe(raw_prices)
            if df is not None:
                fb = _shared["factor_betas"].get(stock_id)
                rows.append(IndicatorService.compute(
                    stock_id, df, _shared["bench_ret"], _shared["rf_rate"], fb,
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

        all_rows: list[tuple] = []
        for market in markets:
            pm = price_maps.get(market) if price_maps else None
            rows = self._process_market(market, benchmark_returns, rf_rates, pm)
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
        price_map: dict[int, list[tuple]] | None = None,
    ) -> list[tuple]:
        if price_map is None:
            price_map = self._price_repo.get_prices_by_market(market, limit_per_stock=300)
        if not price_map:
            logger.warning(f"[Compute] No price data for {market.value}")
            return []

        bench_ret = benchmark_returns.get(market_to_benchmark(market))
        rf_rate = rf_rates.get(market_to_country(market), 3.0)
        factor_betas = self._factor_service.get_betas(market)

        items = list(price_map.items())
        chunks = [items[i:i + _CHUNK_SIZE] for i in range(0, len(items), _CHUNK_SIZE)]

        rows: list[tuple] = []
        failed: list[int] = []
        with ProcessPoolExecutor(
            max_workers=_MAX_WORKERS,
            initializer=_init_worker,
            initargs=(bench_ret, rf_rate, factor_betas),
        ) as pool:
            for batch_rows, batch_failed in pool.map(_compute_chunk, chunks):
                rows.extend(batch_rows)
                failed.extend(batch_failed)

        if failed:
            logger.warning(
                f"[Compute] {market.value}: {len(failed)} stocks failed: "
                f"{failed[:20]}"
            )
        fb_used = sum(1 for sid in price_map if sid in factor_betas)
        logger.info(
            f"[Compute] {market.value}: {len(rows)}/{len(price_map)} stocks computed "
            f"({fb_used} factor betas, {_MAX_WORKERS} workers)"
        )
        return rows
