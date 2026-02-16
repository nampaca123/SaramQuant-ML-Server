import logging
import pandas as pd
from pykrx import stock

from app.collectors.utils.skip_rules import SKIP_INDICES
from app.collectors.utils.throttle import Throttle

logger = logging.getLogger(__name__)

COLUMN_MAP = {"시가": "open", "고가": "high", "저가": "low", "종가": "close", "거래량": "volume"}


class PykrxClient:
    def __init__(self):
        self._throttle = Throttle(min_interval=0.5)

    def fetch_market_ohlcv(self, date_str: str, market: str) -> pd.DataFrame:
        """Fetch all tickers' OHLCV for a single date.
        Returns DataFrame with index=ticker, columns=[open,high,low,close,volume].
        """
        df = stock.get_market_ohlcv(date_str, market=market)
        self._throttle.wait()

        if df is None or df.empty:
            return pd.DataFrame()

        df = df.rename(columns=COLUMN_MAP)
        return df[["open", "high", "low", "close", "volume"]]

    def fetch_sector_map(self, market: str) -> dict[str, str]:
        index_tickers = stock.get_index_ticker_list(market=market)
        sector_map: dict[str, str] = {}

        for idx_ticker in index_tickers:
            if idx_ticker in SKIP_INDICES:
                continue
            idx_name = stock.get_index_ticker_name(idx_ticker)
            try:
                components = stock.get_index_portfolio_deposit_file(idx_ticker)
                self._throttle.wait()
            except Exception as e:
                logger.warning(f"[pykrx] Skip index {idx_ticker} {idx_name}: {e}")
                continue
            for sym in components:
                if sym not in sector_map:
                    sector_map[sym] = idx_name

        logger.info(f"[pykrx] {market} sector map: {len(sector_map)} stocks")
        return sector_map

    def fetch_index_ohlcv(self, start: str, end: str, ticker: str) -> pd.DataFrame:
        """Fetch index OHLCV for a date range.
        Returns DataFrame with index=date, columns=[open,high,low,close,volume].
        """
        df = stock.get_index_ohlcv(start, end, ticker)
        self._throttle.wait()

        if df is None or df.empty:
            return pd.DataFrame()

        df = df.rename(columns=COLUMN_MAP)
        return df[["open", "high", "low", "close", "volume"]]
