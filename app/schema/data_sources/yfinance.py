"""
yfinance response types.

yfinance.download(symbol, start, end) returns DataFrame.
Index: Date (datetime), Columns: Open, High, Low, Close, Volume
"""
from typing import TypedDict


class YfinanceDailyPrice(TypedDict):
    Open: float
    High: float
    Low: float
    Close: float
    Volume: int
