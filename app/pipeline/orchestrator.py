import logging

from app.db import get_connection
from app.services import PriceCollectionService
from app.services.price_collection_service import REGION_CONFIG
from app.services.fundamental_collection_service import FundamentalCollectionService
from app.collectors import SectorCollector
from app.pipeline.indicator_compute import IndicatorComputeEngine
from app.pipeline.fundamental_compute import FundamentalComputeEngine

logger = logging.getLogger(__name__)


class PipelineOrchestrator:
    def __init__(self):
        self._collector = PriceCollectionService()
        self._fund_collector = FundamentalCollectionService()

    def run_daily_kr(self) -> None:
        logger.info("[Pipeline] Starting KR daily pipeline")
        self._collector.collect_all("kr")
        self._compute("kr")
        self._compute_fundamentals("kr")
        logger.info("[Pipeline] KR daily pipeline complete")

    def run_daily_us(self) -> None:
        logger.info("[Pipeline] Starting US daily pipeline")
        self._collector.collect_all("us")
        self._compute("us")
        self._compute_fundamentals("us")
        logger.info("[Pipeline] US daily pipeline complete")

    def run_daily_all(self) -> None:
        self.run_daily_kr()
        self.run_daily_us()

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

    def run_full(self) -> None:
        logger.info("[Pipeline] Starting full pipeline")
        self.run_daily_kr()
        self.run_daily_us()
        self.run_collect_fs_kr()
        self.run_collect_fs_us()
        self._compute_fundamentals_all()
        logger.info("[Pipeline] Full pipeline complete")

    def _compute(self, region: str) -> None:
        markets = REGION_CONFIG[region]["markets"]
        with get_connection() as conn:
            engine = IndicatorComputeEngine(conn)
            count = engine.run(markets)
            logger.info(f"[Pipeline] Computed {count} indicator rows")

    def _compute_fundamentals(self, region: str) -> None:
        markets = REGION_CONFIG[region]["markets"]
        with get_connection() as conn:
            engine = FundamentalComputeEngine(conn)
            count = engine.run(markets)
            logger.info(f"[Pipeline] Computed {count} fundamental rows")

    def run_sectors(self) -> None:
        logger.info("[Pipeline] Starting sector collection")
        all_markets = REGION_CONFIG["kr"]["markets"] + REGION_CONFIG["us"]["markets"]
        count = SectorCollector().collect(all_markets)
        logger.info(f"[Pipeline] Sector collection complete: {count} updated")

    def _compute_fundamentals_all(self) -> None:
        for region in ("kr", "us"):
            markets = REGION_CONFIG[region]["markets"]
            with get_connection() as conn:
                engine = FundamentalComputeEngine(conn)
                count = engine.run(markets)
                logger.info(f"[Pipeline] Re-computed {count} fundamental rows ({region})")
