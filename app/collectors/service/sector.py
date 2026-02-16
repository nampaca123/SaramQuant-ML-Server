import logging

import pandas as pd

from app.schema import Market
from app.db import get_connection, StockRepository
from app.collectors.clients import PykrxClient, NasdaqScreenerClient
from app.collectors.utils.market_groups import MARKET_TO_PYKRX, KR_MARKETS, US_MARKETS

logger = logging.getLogger(__name__)


class SectorCollector:
    def __init__(self):
        self._pykrx = PykrxClient()
        self._nasdaq = NasdaqScreenerClient()

    def collect(self, markets: list[Market]) -> int:
        total = 0
        kr = [m for m in markets if m in KR_MARKETS]
        us = [m for m in markets if m in US_MARKETS]
        if kr:
            total += self._collect_kr(kr)
        if us:
            total += self._collect_us(us)
        logger.info(f"[SectorCollector] Total updated: {total}")
        return total

    @staticmethod
    def _preferred_to_common(symbol: str) -> str | None:
        if len(symbol) == 6 and symbol[-1] in ("5", "7", "9"):
            return symbol[:-1] + "0"
        return None

    def _collect_kr(self, markets: list[Market]) -> int:
        sector_map: dict[str, str] = {}
        for market in markets:
            sector_map.update(self._pykrx.fetch_sector_map(MARKET_TO_PYKRX[market]))

        rows = []
        with get_connection() as conn:
            repo = StockRepository(conn)
            for market in markets:
                for _, symbol in repo.get_stocks_without_sector(market):
                    rows.append((symbol, market.value))

        if not rows:
            return 0

        df = pd.DataFrame(rows, columns=["symbol", "market"])
        df["sector"] = df["symbol"].map(sector_map)

        pref_mask = df["sector"].isna()
        df.loc[pref_mask, "sector"] = (
            df.loc[pref_mask, "symbol"]
            .map(self._preferred_to_common)
            .map(sector_map)
        )

        matched = df.dropna(subset=["sector"])

        if matched.empty:
            return 0

        updates = list(matched.itertuples(index=False, name=None))
        with get_connection() as conn:
            repo = StockRepository(conn)
            count = repo.update_sectors(updates)
            conn.commit()
            logger.info(f"[SectorCollector] KR updated: {count}")
            return count

    def _collect_us(self, markets: list[Market]) -> int:
        rows = []
        with get_connection() as conn:
            repo = StockRepository(conn)
            for market in markets:
                for _, symbol in repo.get_stocks_without_sector(market):
                    rows.append((symbol, market.value))

        if not rows:
            return 0

        df = pd.DataFrame(rows, columns=["symbol", "market"])
        logger.info(f"[SectorCollector] US targets: {len(df)} stocks")

        sector_map = self._nasdaq.fetch_all_sectors()
        if not sector_map:
            logger.warning("[SectorCollector] NASDAQ Screener returned empty")
            return 0

        df["sector"] = df["symbol"].map(sector_map)
        matched = df.dropna(subset=["sector"])

        if matched.empty:
            return 0

        updates = list(matched.itertuples(index=False, name=None))
        with get_connection() as conn:
            repo = StockRepository(conn)
            count = repo.update_sectors(updates)
            conn.commit()
            logger.info(f"[SectorCollector] US updated: {count}")
            return count
