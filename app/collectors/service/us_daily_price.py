import logging
import os
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from app.schema import Market
from app.db import get_connection, StockRepository, DailyPriceRepository
from app.collectors.clients import AlpacaClient
from app.collectors.utils.market_groups import US_MARKETS

logger = logging.getLogger(__name__)


class UsDailyPriceCollector:
    def __init__(self):
        api_key = os.environ.get("ALPACA_API_KEY", "")
        secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
        self._client = AlpacaClient(api_key, secret_key)

    def collect_all(self, market: Market | None = None) -> dict[str, int]:
        markets = self._resolve_markets(market)
        if not markets:
            return {}

        stock_map = self._build_stock_map(markets)
        if not stock_map:
            logger.warning("[UsDailyPrice] No active US stocks")
            return {}

        start, end = self._determine_date_range(markets)
        if start > end:
            logger.info("[UsDailyPrice] Already up to date")
            return {}

        symbols = list(stock_map.keys())
        logger.info(
            f"[UsDailyPrice] Fetching {len(symbols)} symbols, "
            f"{start} to {end}"
        )

        bars = self._client.fetch_daily_bars(symbols, start, end)
        return self._upsert_bars(bars, stock_map)

    def _resolve_markets(self, market: Market | None) -> list[Market]:
        if market:
            return [market] if market in US_MARKETS else []
        return list(US_MARKETS)

    def _build_stock_map(self, markets: list[Market]) -> dict[str, int]:
        """Returns {symbol: stock_id} for US stocks."""
        stock_map: dict[str, int] = {}
        with get_connection() as conn:
            repo = StockRepository(conn)
            for mkt in markets:
                for stock_id, symbol, _ in repo.get_active_stocks(mkt):
                    stock_map[symbol] = stock_id
        return stock_map

    def _determine_date_range(self, markets: list[Market]) -> tuple[date, date]:
        latest = None
        with get_connection() as conn:
            repo = DailyPriceRepository(conn)
            for mkt in markets:
                d = repo.get_latest_date_by_market(mkt)
                if d and (latest is None or d > latest):
                    latest = d

        start = (latest + timedelta(days=1)) if latest else (date.today() - timedelta(days=365))
        end = date.today()
        return start, end

    def _upsert_bars(
        self, bars: dict[str, list[dict]], stock_map: dict[str, int]
    ) -> dict[str, int]:
        all_rows: list[tuple] = []
        symbol_count = 0

        for symbol, bar_list in bars.items():
            stock_id = stock_map.get(symbol)
            if stock_id is None or not bar_list:
                continue

            for bar in bar_list:
                try:
                    all_rows.append((
                        stock_id, bar["date"],
                        Decimal(str(bar["open"])),
                        Decimal(str(bar["high"])),
                        Decimal(str(bar["low"])),
                        Decimal(str(bar["close"])),
                        int(bar["volume"]),
                    ))
                except (KeyError, InvalidOperation, ValueError) as e:
                    logger.warning(f"[UsDailyPrice] Skip bar for {symbol}: {e}")
            symbol_count += 1

        if not all_rows:
            return {}

        with get_connection() as conn:
            count = DailyPriceRepository(conn).bulk_upsert(all_rows)
            conn.commit()

        logger.info(f"[UsDailyPrice] Upserted {count} rows for {symbol_count} symbols")
        return {"total": count}
