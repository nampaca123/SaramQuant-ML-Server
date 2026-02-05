import logging
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
import FinanceDataReader as fdr
import pandas as pd
from app.schema import DailyPrice, Market
from app.db import get_connection, StockRepository, DailyPriceRepository
from app.utils import retry_with_backoff

logger = logging.getLogger(__name__)


class DailyPriceCollector:
    def collect_all(self, market: Market | None = None) -> dict[str, int]:
        with get_connection() as conn:
            stock_repo = StockRepository(conn)
            stocks = stock_repo.get_active_stocks(market)

        results = {}
        total = len(stocks)

        for idx, (stock_id, symbol, stock_market) in enumerate(stocks, 1):
            count = self._collect_stock(stock_id, symbol, stock_market)
            results[symbol] = count

            if idx % 100 == 0 or idx == total:
                logger.info(f"[DailyPrice] Progress: {idx}/{total}")

        return results

    def _collect_stock(self, stock_id: int, symbol: str, market: Market) -> int:
        start_date = self._get_start_date(stock_id)
        df = self._fetch_ohlcv(symbol, market, start_date)

        if df is None or df.empty:
            return 0

        prices = self._transform(symbol, df)
        if not prices:
            return 0

        with get_connection() as conn:
            repo = DailyPriceRepository(conn)
            count = repo.upsert_batch(stock_id, prices)
            conn.commit()

        return count

    def _get_start_date(self, stock_id: int) -> str | None:
        with get_connection() as conn:
            repo = DailyPriceRepository(conn)
            latest = repo.get_latest_date(stock_id)

        if latest:
            next_day = latest + timedelta(days=1)
            return next_day.strftime("%Y-%m-%d")
        return None

    @retry_with_backoff(max_retries=3, base_delay=1.0)
    def _fetch_ohlcv(
        self, symbol: str, market: Market, start_date: str | None
    ) -> pd.DataFrame | None:
        fdr_symbol = self._to_fdr_symbol(symbol, market)
        return fdr.DataReader(fdr_symbol, start_date)

    def _to_fdr_symbol(self, symbol: str, market: Market) -> str:
        """
        Convert DB symbol to FDR symbol format.
        Korean stocks: Keep 6-digit code (FDR handles with/without leading zeros)
        US stocks: Use as-is
        """
        if market in (Market.KR_KOSPI, Market.KR_KOSDAQ):
            # KIS mst file stores 9-char symbols, FDR needs 6-digit code
            digits_only = "".join(c for c in symbol if c.isdigit())
            return digits_only[:6]
        return symbol

    def _transform(self, symbol: str, df: pd.DataFrame) -> list[DailyPrice]:
        prices = []

        for idx, row in df.iterrows():
            try:
                price_date = idx.date() if hasattr(idx, "date") else idx
                prices.append(DailyPrice(
                    symbol=symbol,
                    date=price_date,
                    open=Decimal(str(row["Open"])),
                    high=Decimal(str(row["High"])),
                    low=Decimal(str(row["Low"])),
                    close=Decimal(str(row["Close"])),
                    volume=int(row["Volume"]),
                ))
            except (KeyError, InvalidOperation, ValueError) as e:
                logger.warning(f"[Transform] Skip invalid row for {symbol}: {e}")
                continue

        return prices