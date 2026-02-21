import logging
from typing import Callable, Any

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

logger = logging.getLogger(__name__)

PriceMaps = dict[Market, dict[int, list[tuple]]]
_SAFETY_THRESHOLD = 0.10


class PipelineOrchestrator:
    def __init__(self):
        self._collector = PriceCollectionService()
        self._fund_collector = FundamentalCollectionService()

    # ── public entry points ──

    def run_daily_kr(self) -> None:
        logger.info("[Pipeline] Starting KR daily pipeline")
        self._collector.collect_all("kr")
        self._run_compute_pipeline("kr")
        logger.info("[Pipeline] KR daily pipeline complete")

    def run_daily_us(self) -> None:
        logger.info("[Pipeline] Starting US daily pipeline")
        self._collector.collect_all("us")
        self._run_compute_pipeline("us")
        logger.info("[Pipeline] US daily pipeline complete")

    def run_initial_kr(self) -> None:
        logger.info("[Pipeline] Starting KR initial pipeline")
        self._collector.collect_all("kr")
        self._fund_collector.collect_all("kr")
        self._run_compute_pipeline("kr")
        logger.info("[Pipeline] KR initial pipeline complete")

    def run_initial_us(self) -> None:
        logger.info("[Pipeline] Starting US initial pipeline")
        self._collector.collect_all("us")
        self._fund_collector.collect_all("us")
        self._run_compute_pipeline("us")
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

    def _run_compute_pipeline(self, region: str) -> None:
        markets = REGION_CONFIG[region]["markets"]

        if not self._progressive_deactivate(markets):
            return

        price_maps = self._load_prices(markets)

        fund_ok = self._safe_step("fundamentals", self._compute_fundamentals, region, price_maps)
        factor_ok = self._safe_step("factors", self._compute_factors, region, price_maps) if fund_ok else False
        if factor_ok:
            self._safe_step("indicators", self._compute_indicators, region, price_maps)
        if fund_ok:
            self._safe_step("sector_agg", self._compute_sector_aggregates, region)
            self._safe_step("risk_badges", self._compute_risk_badges, region)
        self._run_integrity_check(region)

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
        price_maps: PriceMaps = {}
        with get_connection() as conn:
            repo = DailyPriceRepository(conn)
            for market in markets:
                price_maps[market] = repo.get_prices_by_market(market, limit_per_stock=300)
        return price_maps

    def _safe_step(self, name: str, fn: Callable[..., Any], *args: Any) -> bool:
        try:
            fn(*args)
            return True
        except Exception as e:
            logger.error(f"[Pipeline] Step '{name}' failed: {e}", exc_info=True)
            return False

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
