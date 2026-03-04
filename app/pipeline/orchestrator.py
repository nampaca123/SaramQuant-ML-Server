import logging
import time
from typing import Callable, Any
from concurrent.futures import ThreadPoolExecutor

from app.db import get_connection, DailyPriceRepository
from app.db.repositories.stock import StockRepository
from app.schema import Market
from app.services import PriceCollectionService
from app.services.price_collection_service import REGION_CONFIG
from app.services.fundamental_collection_service import FundamentalCollectionService
from app.pipeline.indicator_compute import IndicatorComputeEngine
from app.pipeline.fundamental_compute import FundamentalComputeEngine
from app.pipeline.factor_compute import FactorComputeEngine
from app.pipeline.sector_aggregate_compute import SectorAggregateComputeEngine
from app.pipeline.integrity_check import IntegrityCheckEngine
from app.collectors.service.exchange_rate import ExchangeRateCollector
from app.schema import StepResult, PipelineMetadata
from app.log.service.audit_log_service import log_pipeline

logger = logging.getLogger(__name__)

PriceMaps = dict[Market, dict[int, list[tuple]]]
_SAFETY_THRESHOLD = 0.10


class PipelineOrchestrator:
    def __init__(self):
        self._collector = PriceCollectionService()
        self._fund_collector = FundamentalCollectionService()
        self._exchange_rate_collector = ExchangeRateCollector()

    # ── public entry points ──

    def run_daily_kr(self) -> None:
        logger.info("[Pipeline] Starting KR daily pipeline")
        collect_start = time.monotonic()
        with ThreadPoolExecutor(max_workers=2) as pool:
            collect_future = pool.submit(self._collector.collect_all, "kr")
            exchange_future = pool.submit(self._collect_exchange_rates)
            collect_future.result()
            exchange_future.result()
        collect_ms = int((time.monotonic() - collect_start) * 1000)
        logger.info(f"[Pipeline] KR collection done in {collect_ms}ms")
        self._run_compute_pipeline("kr", collect_ms=collect_ms)
        logger.info("[Pipeline] KR daily pipeline complete")

    def run_daily_us(self) -> None:
        logger.info("[Pipeline] Starting US daily pipeline")
        collect_start = time.monotonic()
        self._collector.collect_all("us")
        collect_ms = int((time.monotonic() - collect_start) * 1000)
        logger.info(f"[Pipeline] US collection done in {collect_ms}ms")
        self._run_compute_pipeline("us", collect_ms=collect_ms)
        logger.info("[Pipeline] US daily pipeline complete")

    def run_initial_kr(self) -> None:
        logger.info("[Pipeline] Starting KR initial pipeline")
        self._collector.collect_all("kr")
        self._fund_collector.collect_all("kr")
        self._run_compute_pipeline("kr-initial")
        logger.info("[Pipeline] KR initial pipeline complete")

    def run_initial_us(self) -> None:
        logger.info("[Pipeline] Starting US initial pipeline")
        self._collector.collect_all("us")
        self._fund_collector.collect_all("us")
        self._run_compute_pipeline("us-initial")
        logger.info("[Pipeline] US initial pipeline complete")

    def run_collect_fs_kr(self) -> None:
        logger.info("[Pipeline] Collecting KR financial statements")
        self._fund_collector.collect_all("kr")
        self._compute_fundamentals("kr")
        logger.info("[Pipeline] KR financial statement pipeline complete")

    def run_collect_fs_us(self) -> None:
        logger.info("[Pipeline] Collecting US financial statements")
        self._fund_collector.collect_all("us")
        self._compute_fundamentals("us")
        logger.info("[Pipeline] US financial statement pipeline complete")

    # ── compute pipeline ──

    def _run_compute_pipeline(self, command: str, collect_ms: int = 0) -> None:
        region = command.replace("-initial", "")
        markets = REGION_CONFIG[region]["markets"]
        steps: list[StepResult] = []
        pipeline_start = time.monotonic()

        if collect_ms > 0:
            steps.append(StepResult("collection", True, collect_ms))

        if not self._progressive_deactivate(markets):
            return

        load_start = time.monotonic()
        price_maps = self._load_prices(markets)
        steps.append(StepResult(
            "load_prices", True, int((time.monotonic() - load_start) * 1000),
        ))

        fund = self._safe_step("fundamentals", self._compute_fundamentals, region, price_maps)
        steps.append(fund)

        if fund.success:
            with ThreadPoolExecutor(max_workers=2) as pool:
                factor_future = pool.submit(self._safe_step, "factors", self._compute_factors, region, price_maps)
                sector_agg_future = pool.submit(self._safe_step, "sector_agg", self._compute_sector_aggregates, region)
                factor = factor_future.result()
                sector_agg = sector_agg_future.result()
            steps.extend([factor, sector_agg])

            if factor.success:
                steps.append(self._safe_step("indicators", self._compute_indicators, region, price_maps))
            steps.append(self._safe_step("risk_badges", self._compute_risk_badges, region))
        else:
            steps.append(StepResult("factors", False, 0, "skipped"))
            logger.error("[Pipeline] Fundamentals failed — skipping factors/indicators/risk_badges")

        self._run_integrity_check(region)

        meta = PipelineMetadata(
            command=command,
            steps=steps,
            total_duration_ms=int((time.monotonic() - pipeline_start) * 1000),
        )
        try:
            log_pipeline(meta)
        except Exception:
            logger.exception("Failed to log pipeline audit")

    # ── progressive deactivation (single transaction) ──

    def _progressive_deactivate(self, markets: list[Market]) -> bool:
        with get_connection() as conn:
            repo = StockRepository(conn)

            for market in markets:
                symbols = self._collector.active_symbols.get(market, set())
                reactivated = repo.reactivate_listed_stocks(market, symbols)
                if reactivated:
                    logger.info(f"[Pipeline] Reactivated {reactivated} stocks in {market.value}")

                no_price = repo.deactivate_no_price_stocks(market)
                if no_price:
                    logger.info(f"[Pipeline] Deactivated {no_price} no-price stocks in {market.value}")

                no_sector = repo.deactivate_no_sector_stocks(market)
                if no_sector:
                    logger.info(f"[Pipeline] Deactivated {no_sector} no-sector stocks in {market.value}")

                no_fs = repo.deactivate_no_fs_stocks(market)
                if no_fs:
                    logger.info(f"[Pipeline] Deactivated {no_fs} no-FS stocks in {market.value}")

            if not self._safety_check(repo, markets):
                conn.rollback()
                return False

            conn.commit()
            return True

    def _safety_check(self, repo: StockRepository, markets: list[Market]) -> bool:
        total, active = repo.count_by_activity(markets)
        ratio = active / total if total > 0 else 0
        if ratio < _SAFETY_THRESHOLD:
            logger.error(
                f"[Pipeline] Safety check FAILED: {active}/{total} active ({ratio:.1%}). "
                f"Did you forget to run the initial pipeline? Aborting compute."
            )
            return False
        logger.info(f"[Pipeline] Safety check OK: {active}/{total} active ({ratio:.1%})")
        return True

    # ── helpers ──

    def _load_prices(self, markets: list[Market]) -> PriceMaps:
        def _load_market(market: Market) -> tuple[Market, dict[int, list[tuple]]]:
            with get_connection() as conn:
                return market, DailyPriceRepository(conn).get_prices_by_market(market, limit_per_stock=300)

        price_maps: PriceMaps = {}
        with ThreadPoolExecutor(max_workers=len(markets)) as pool:
            for market, data in pool.map(_load_market, markets):
                price_maps[market] = data
        return price_maps

    def _safe_step(self, name: str, fn: Callable[..., Any], *args: Any) -> StepResult:
        start = time.monotonic()
        try:
            fn(*args)
            duration = int((time.monotonic() - start) * 1000)
            return StepResult(name=name, success=True, duration_ms=duration)
        except Exception as e:
            duration = int((time.monotonic() - start) * 1000)
            logger.error(f"[Pipeline] Step '{name}' failed: {e}", exc_info=True)
            return StepResult(name=name, success=False, duration_ms=duration, error=str(e))

    # ── individual compute steps ──

    def _compute_indicators(self, region: str, price_maps: PriceMaps | None = None) -> None:
        markets = REGION_CONFIG[region]["markets"]
        with get_connection() as conn:
            engine = IndicatorComputeEngine(conn)
            count = engine.run(markets, price_maps)
            logger.info(f"[Pipeline] Computed {count} indicator rows")

    def _compute_fundamentals(self, region: str, price_maps: PriceMaps | None = None) -> None:
        markets = REGION_CONFIG[region]["markets"]
        with get_connection() as conn:
            engine = FundamentalComputeEngine(conn)
            count = engine.run(markets, price_maps)
            logger.info(f"[Pipeline] Computed {count} fundamental rows")

    def _compute_factors(self, region: str, price_maps: PriceMaps | None = None) -> None:
        markets = REGION_CONFIG[region]["markets"]
        with get_connection() as conn:
            engine = FactorComputeEngine(conn)
            count = engine.run(markets, price_maps)
            logger.info(f"[Pipeline] Computed {count} factor exposure rows")

    def _compute_sector_aggregates(self, region: str) -> None:
        markets = REGION_CONFIG[region]["markets"]
        with get_connection() as conn:
            engine = SectorAggregateComputeEngine(conn)
            count = engine.run(markets)
            logger.info(f"[Pipeline] Computed {count} sector aggregate rows")

    def _compute_risk_badges(self, region: str) -> None:
        markets = REGION_CONFIG[region]["markets"]
        with get_connection() as conn:
            from app.services.risk_badge_service import RiskBadgeService
            from app.db.repositories.risk_badge import RiskBadgeRepository
            service = RiskBadgeService(conn)
            for market in markets:
                badges = service.compute_batch(market)
                RiskBadgeRepository(conn).upsert_batch(badges)
            conn.commit()

    def _run_integrity_check(self, region: str) -> None:
        markets = REGION_CONFIG[region]["markets"]
        with get_connection() as conn:
            engine = IntegrityCheckEngine(conn)
            engine.run(markets)

    def _collect_exchange_rates(self) -> None:
        count = self._exchange_rate_collector.collect()
        logger.info(f"[Pipeline] Collected {count} exchange rate rows")
