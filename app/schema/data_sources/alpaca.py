"""
Alpaca Market Data API response types.

StockHistoricalDataClient.get_stock_bars() returns BarSet,
mapping symbols to lists of Bar objects.
"""
from typing import TypedDict


class AlpacaBar(TypedDict):
    t: str      # timestamp (ISO 8601)
    o: float    # open
    h: float    # high
    l: float    # low
    c: float    # close
    v: int      # volume
    n: int      # trade count
    vw: float   # volume-weighted average price
