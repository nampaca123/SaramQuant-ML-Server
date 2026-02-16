import logging
import yfinance as yf
import pandas as pd

logger = logging.getLogger(__name__)


class YfinanceClient:
    def fetch_index_prices(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        """Fetch index daily close prices.
        Returns DataFrame with index=date, columns=[close].
        """
        try:
            df = yf.download(symbol, start=start, end=end, progress=False, auto_adjust=True)
        except Exception as e:
            logger.error(f"[yfinance] Failed to fetch {symbol}: {e}")
            return pd.DataFrame()

        if df is None or df.empty:
            return pd.DataFrame()

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        return df[["Close"]].rename(columns={"Close": "close"})
