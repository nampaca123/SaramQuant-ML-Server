from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum


class Market(str, Enum):
    KR_KOSPI = "KR_KOSPI"
    KR_KOSDAQ = "KR_KOSDAQ"
    US_NYSE = "US_NYSE"
    US_NASDAQ = "US_NASDAQ"


class DataSource(str, Enum):
    FDR = "FDR"
    KIS = "KIS"


@dataclass
class OHLCV:
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


@dataclass
class StockInfo:
    symbol: str
    name: str
    market: Market


@dataclass
class DailyPrice:
    symbol: str
    date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
