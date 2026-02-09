import logging
import time
import pandas as pd
from pykrx import stock

logger = logging.getLogger(__name__)

COLUMN_MAP = {"시가": "open", "고가": "high", "저가": "low", "종가": "close", "거래량": "volume"}


class PykrxClient:
    REQUEST_DELAY = 1.0

    def _throttle(self) -> None:
        time.sleep(self.REQUEST_DELAY)

    def fetch_market_ohlcv(self, date_str: str, market: str) -> pd.DataFrame:
        """Fetch all tickers' OHLCV for a single date.
        Returns DataFrame with index=ticker, columns=[open,high,low,close,volume].
        """
        df = stock.get_market_ohlcv(date_str, market=market)
        self._throttle()

        if df is None or df.empty:
            return pd.DataFrame()

        df = df.rename(columns=COLUMN_MAP)
        return df[["open", "high", "low", "close", "volume"]]

    def fetch_index_ohlcv(self, start: str, end: str, ticker: str) -> pd.DataFrame:
        """Fetch index OHLCV for a date range.
        Returns DataFrame with index=date, columns=[open,high,low,close,volume].
        """
        df = stock.get_index_ohlcv(start, end, ticker)
        self._throttle()

        if df is None or df.empty:
            return pd.DataFrame()

        df = df.rename(columns=COLUMN_MAP)
        return df[["open", "high", "low", "close", "volume"]]
