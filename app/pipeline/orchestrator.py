import logging

from app.db import get_connection
from app.services import PriceCollectionService
from app.services.price_collection_service import REGION_CONFIG
from app.pipeline.compute import ComputeEngine

logger = logging.getLogger(__name__)


class PipelineOrchestrator:
    def __init__(self):
        self._collector = PriceCollectionService()

    def run_kr(self) -> None:
        logger.info("[Pipeline] Starting KR pipeline")
        self._collector.collect_all("kr")
        self._compute("kr")
        logger.info("[Pipeline] KR pipeline complete")

    def run_us(self) -> None:
        logger.info("[Pipeline] Starting US pipeline")
        self._collector.collect_all("us")
        self._compute("us")
        logger.info("[Pipeline] US pipeline complete")

    def run_all(self) -> None:
        self.run_kr()
        self.run_us()

    def _compute(self, region: str) -> None:
        markets = REGION_CONFIG[region]["markets"]
        with get_connection() as conn:
            engine = ComputeEngine(conn)
            count = engine.run(markets)
            logger.info(f"[Pipeline] Computed {count} indicator rows")
