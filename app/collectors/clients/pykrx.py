import logging
import time

import pandas as pd
from pykrx import stock

from app.collectors.utils.skip_rules import SKIP_INDICES
from app.collectors.utils.throttle import Throttle

logger = logging.getLogger(__name__)

COLUMN_MAP = {"시가": "open", "고가": "high", "저가": "low", "종가": "close", "거래량": "volume"}

_RETRIES = 3
_RETRY_WAIT = 3.0


class PykrxClient:
    def __init__(self):
        self._throttle = Throttle(min_interval=0.5)

    def _call(self, fn, *args, **kwargs):
        for attempt in range(_RETRIES):
            self._throttle.wait()
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                if attempt == _RETRIES - 1:
                    raise
                wait = _RETRY_WAIT * (attempt + 1)
                logger.warning(f"[pykrx] Retry {attempt + 1}/{_RETRIES} in {wait}s: {e}")
                time.sleep(wait)

    def fetch_market_ohlcv(self, date_str: str, market: str) -> pd.DataFrame:
        df = self._call(stock.get_market_ohlcv, date_str, market=market)
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.rename(columns=COLUMN_MAP)
        return df[["open", "high", "low", "close", "volume"]]

    def fetch_sector_map(self, market: str) -> dict[str, str]:
        index_tickers = self._call(stock.get_index_ticker_list, market=market)

        sector_map: dict[str, str] = {}
        for idx_ticker in index_tickers:
            if idx_ticker in SKIP_INDICES:
                continue
            idx_name = self._call(stock.get_index_ticker_name, idx_ticker)
            try:
                components = self._call(stock.get_index_portfolio_deposit_file, idx_ticker)
            except Exception as e:
                logger.warning(f"[pykrx] Skip index {idx_ticker} {idx_name}: {e}")
                continue
            for sym in components:
                if sym not in sector_map:
                    sector_map[sym] = idx_name

        logger.info(f"[pykrx] {market} sector map: {len(sector_map)} stocks")
        return sector_map

    def fetch_index_ohlcv(self, start: str, end: str, ticker: str) -> pd.DataFrame:
        df = self._call(stock.get_index_ohlcv, start, end, ticker)
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.rename(columns=COLUMN_MAP)
        return df[["open", "high", "low", "close", "volume"]]
