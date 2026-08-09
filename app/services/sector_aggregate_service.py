import logging
from datetime import date

from app.db import FactorRepository
from app.schema import Market

logger = logging.getLogger(__name__)


class SectorAggregateService:
    def __init__(self, conn: object):
        self._repo = FactorRepository(conn)

    def compute(self, markets: list[Market]) -> int:
        rows = self._repo.get_sector_aggregates(markets)
        if not rows:
            logger.info("[SectorAggregate] No data to aggregate")
            return 0

        today = date.today()
        insert_data = [
            (row[0], row[1], today, row[2], row[3], row[4], row[5], row[6], row[7])
            for row in rows
        ]

        count = self._repo.upsert_sector_aggregates(insert_data)
        logger.info(f"[SectorAggregate] Upserted {count} sector aggregates")
        return count
