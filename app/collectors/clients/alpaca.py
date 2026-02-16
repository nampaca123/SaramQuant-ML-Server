import logging
from datetime import date, datetime
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from app.collectors.utils.throttle import Throttle

logger = logging.getLogger(__name__)


class AlpacaClient:
    BATCH_SIZE = 50
    CALLS_PER_MIN = 200

    def __init__(self, api_key: str, secret_key: str):
        self._client = StockHistoricalDataClient(api_key, secret_key)
        self._throttle = Throttle(min_interval=60.0 / self.CALLS_PER_MIN)

    def fetch_daily_bars(
        self, symbols: list[str], start: date, end: date
    ) -> dict[str, list[dict]]:
        """Fetch daily OHLCV bars for multiple symbols.
        Batches internally by BATCH_SIZE. No limit param (SDK auto-paginates).
        Returns {symbol: [{date, open, high, low, close, volume}, ...]}.
        """
        all_bars: dict[str, list[dict]] = {}

        for i in range(0, len(symbols), self.BATCH_SIZE):
            batch = symbols[i : i + self.BATCH_SIZE]
            batch_bars = self._fetch_batch(batch, start, end)
            all_bars.update(batch_bars)

            batch_num = i // self.BATCH_SIZE + 1
            total_batches = (len(symbols) + self.BATCH_SIZE - 1) // self.BATCH_SIZE
            logger.info(f"[Alpaca] Batch {batch_num}/{total_batches} done ({len(batch)} symbols)")

        return all_bars

    def _fetch_batch(
        self, symbols: list[str], start: date, end: date
    ) -> dict[str, list[dict]]:
        self._throttle.wait()

        request = StockBarsRequest(
            symbol_or_symbols=symbols,
            timeframe=TimeFrame.Day,
            start=datetime.combine(start, datetime.min.time()),
            end=datetime.combine(end, datetime.min.time()),
        )

        try:
            bar_set = self._client.get_stock_bars(request)
        except Exception as e:
            logger.error(f"[Alpaca] Batch request failed: {e}")
            return {}

        result: dict[str, list[dict]] = {}
        for sym in symbols:
            bars = bar_set.data.get(sym, [])
            result[sym] = [
                {
                    "date": bar.timestamp.date(),
                    "open": float(bar.open),
                    "high": float(bar.high),
                    "low": float(bar.low),
                    "close": float(bar.close),
                    "volume": int(bar.volume),
                }
                for bar in bars
            ]

        return result
