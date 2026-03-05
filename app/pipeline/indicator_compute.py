import logging
import os
from concurrent.futures import ProcessPoolExecutor

import pandas as pd
from psycopg2.extensions import connection

from app.db import DailyPriceRepository
from app.db.repositories.indicator import IndicatorRepository
from app.schema import Market
from app.schema.enums.market import market_to_benchmark, market_to_country
from app.services import IndicatorService
from app.services.factor_model_service import FactorModelService
from app.utils import load_benchmark_returns, load_risk_free_rates

logger = logging.getLogger(__name__)

_MAX_WORKERS = min(16, os.cpu_count() or 8)
_CHUNK_SIZE = 200

_shared: dict = {}


def _init_worker(bench_ret_map, rf_rate_map, factor_betas, stock_market_map):
    _shared["bench_ret_map"] = bench_ret_map
    _shared["rf_rate_map"] = rf_rate_map
    _shared["factor_betas"] = factor_betas
    _shared["stock_market_map"] = stock_market_map


def _compute_chunk(stock_batch: list[tuple[int, list[tuple]]]) -> tuple[list[tuple], list[int]]:
    rows, failed = [], []
    for stock_id, raw_prices in stock_batch:
        try:
            df = IndicatorService.build_dataframe(raw_prices)
            if df is not None:
                mkt = _shared["stock_market_map"][stock_id]
                rows.append(IndicatorService.compute(
                    stock_id, df,
                    _shared["bench_ret_map"].get(mkt),
                    _shared["rf_rate_map"].get(mkt, 3.0),
                    _shared["factor_betas"].get(stock_id),
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
        rows, _ = self.compute(markets, price_maps)
        return self.persist(rows, markets)

    def compute(
        self,
        markets: list[Market],
        price_maps: dict[Market, dict[int, list[tuple]]] | None = None,
    ) -> tuple[list[tuple], dict[int, str]]:
        benchmark_returns = load_benchmark_returns(self._conn, markets)
        rf_rates = load_risk_free_rates(self._conn, markets)

        bench_ret_map = {m.value: benchmark_returns.get(market_to_benchmark(m)) for m in markets}
        rf_rate_map = {m.value: rf_rates.get(market_to_country(m), 3.0) for m in markets}

        all_items: list[tuple[int, list[tuple]]] = []
        stock_market_map: dict[int, str] = {}
        factor_betas: dict[int, float] = {}

        for market in markets:
            pm = price_maps.get(market) if price_maps else None
            if pm is None:
                pm = self._price_repo.get_prices_by_market(market, limit_per_stock=300)
            if not pm:
                logger.warning(f"[Compute] No price data for {market.value}")
                continue

            factor_betas.update(self._factor_service.get_betas(market))
            for stock_id in pm:
                stock_market_map[stock_id] = market.value
            all_items.extend(pm.items())

        if not all_items:
            return [], {}

        chunks = [all_items[i:i + _CHUNK_SIZE] for i in range(0, len(all_items), _CHUNK_SIZE)]

        rows: list[tuple] = []
        failed: list[int] = []
        with ProcessPoolExecutor(
            max_workers=_MAX_WORKERS,
            initializer=_init_worker,
            initargs=(bench_ret_map, rf_rate_map, factor_betas, stock_market_map),
        ) as pool:
            for batch_rows, batch_failed in pool.map(_compute_chunk, chunks):
                rows.extend(batch_rows)
                failed.extend(batch_failed)

        fb_used = sum(1 for sid in stock_market_map if sid in factor_betas)
        logger.info(
            f"[Compute] {len(rows)}/{len(stock_market_map)} stocks computed "
            f"({fb_used} factor betas, {_MAX_WORKERS} workers)"
        )

        if failed:
            logger.warning(f"[Compute] {len(failed)} stocks failed: {failed[:20]}")

        return rows, stock_market_map

    def persist(self, rows: list[tuple], markets: list[Market]) -> int:
        deleted = self._indicator_repo.delete_by_markets(markets)
        logger.info(f"[Compute] Deleted {deleted} old indicator rows")

        inserted = self._indicator_repo.insert_batch(rows)
        self._conn.commit()
        logger.info(f"[Compute] Inserted {inserted} indicator rows")
        return inserted
