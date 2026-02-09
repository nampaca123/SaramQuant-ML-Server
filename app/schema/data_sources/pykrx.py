"""
pykrx response types.

stock.get_market_ohlcv(date, market="KOSPI"):
    Index: ticker (str), Columns: 시가, 고가, 저가, 종가, 거래량, 거래대금, 등락률

stock.get_index_ohlcv(start, end, index_ticker):
    Index: date, Columns: 시가, 고가, 저가, 종가, 거래량
"""
from typing import TypedDict


class PykrxMarketOhlcv(TypedDict):
    시가: int
    고가: int
    저가: int
    종가: int
    거래량: int


class PykrxIndexOhlcv(TypedDict):
    시가: float
    고가: float
    저가: float
    종가: float
    거래량: int
