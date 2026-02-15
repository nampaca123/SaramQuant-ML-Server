import logging
import time
import pandas as pd
from pykrx import stock

logger = logging.getLogger(__name__)

COLUMN_MAP = {"시가": "open", "고가": "high", "저가": "low", "종가": "close", "거래량": "volume"}

SKIP_INDICES = frozenset({
    "1001", "1002", "1003", "1004", "1027", "1028", "1034", "1035",
    "1150", "1151", "1152", "1153", "1154", "1155", "1156", "1157",
    "1158", "1159", "1160", "1167", "1168", "1182", "1224", "1227",
    "1232", "1244", "1894",
    "2001", "2002", "2003", "2004", "2024",
    "2181", "2182", "2183", "2184", "2189",
    "2203", "2212", "2213", "2214", "2215", "2216", "2217", "2218",
})


class PykrxClient:
    REQUEST_DELAY = 0.5

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

    def fetch_sector_map(self, market: str) -> dict[str, str]:
        index_tickers = stock.get_index_ticker_list(market=market)
        sector_map: dict[str, str] = {}

        for idx_ticker in index_tickers:
            if idx_ticker in SKIP_INDICES:
                continue
            idx_name = stock.get_index_ticker_name(idx_ticker)
            try:
                components = stock.get_index_portfolio_deposit_file(idx_ticker)
                self._throttle()
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
        self._throttle()

        if df is None or df.empty:
            return pd.DataFrame()

        df = df.rename(columns=COLUMN_MAP)
        return df[["open", "high", "low", "close", "volume"]]
