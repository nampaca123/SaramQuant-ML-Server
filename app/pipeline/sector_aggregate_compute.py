import logging

from app.schema import Market
from app.services.sector_aggregate_service import SectorAggregateService

logger = logging.getLogger(__name__)


class SectorAggregateComputeEngine:
    def __init__(self, conn: object):
        self._service = SectorAggregateService(conn)
        self._conn = conn

    def run(self, markets: list[Market]) -> int:
        count = self._service.compute(markets)
        self._conn.commit()
        return count
