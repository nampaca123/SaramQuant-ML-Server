import logging

from app.schema import Market
from app.services.factor_model_service import FactorModelService

logger = logging.getLogger(__name__)


class FactorComputeEngine:
    def __init__(self, conn: object):
        self._service = FactorModelService(conn)
        self._conn = conn

    def run(
        self,
        markets: list[Market],
        price_maps: dict[Market, dict[int, list[tuple]]] | None = None,
    ) -> int:
        total = 0
        for market in markets:
            pm = price_maps.get(market) if price_maps else None
            result = self._service.run(market, pm)
            if result.get("status") == "ok":
                total += result.get("exposures", 0)
        self._conn.commit()
        return total
