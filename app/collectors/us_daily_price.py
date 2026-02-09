import logging
import os
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from app.schema import DailyPrice, Market
from app.db import get_connection, StockRepository, DailyPriceRepository
from app.collectors.clients import AlpacaClient

logger = logging.getLogger(__name__)

US_MARKETS = (Market.US_NYSE, Market.US_NASDAQ)


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

        start, end = self._determine_date_range(stock_map)
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

    def _determine_date_range(self, stock_map: dict[str, int]) -> tuple[date, date]:
        latest = None
        with get_connection() as conn:
            repo = DailyPriceRepository(conn)
            for stock_id in stock_map.values():
                d = repo.get_latest_date(stock_id)
                if d and (latest is None or d > latest):
                    latest = d

        start = (latest + timedelta(days=1)) if latest else (date.today() - timedelta(days=365))
        end = date.today()
        return start, end

    def _upsert_bars(
        self, bars: dict[str, list[dict]], stock_map: dict[str, int]
    ) -> dict[str, int]:
        results: dict[str, int] = {}

        with get_connection() as conn:
            repo = DailyPriceRepository(conn)

            for symbol, bar_list in bars.items():
                stock_id = stock_map.get(symbol)
                if stock_id is None or not bar_list:
                    continue

                prices = self._transform(symbol, bar_list)
                if not prices:
                    continue

                count = repo.upsert_batch(stock_id, prices)
                results[symbol] = count

            conn.commit()

        collected = sum(results.values())
        logger.info(f"[UsDailyPrice] Upserted {collected} rows for {len(results)} symbols")
        return results

    def _transform(self, symbol: str, bar_list: list[dict]) -> list[DailyPrice]:
        prices = []
        for bar in bar_list:
            try:
                prices.append(DailyPrice(
                    symbol=symbol,
                    date=bar["date"],
                    open=Decimal(str(bar["open"])),
                    high=Decimal(str(bar["high"])),
                    low=Decimal(str(bar["low"])),
                    close=Decimal(str(bar["close"])),
                    volume=int(bar["volume"]),
                ))
            except (KeyError, InvalidOperation, ValueError) as e:
                logger.warning(f"[UsDailyPrice] Skip bar for {symbol}: {e}")
                continue
        return prices
