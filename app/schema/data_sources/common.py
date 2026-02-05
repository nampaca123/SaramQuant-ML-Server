from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum


class Market(str, Enum):
    KR_KOSPI = "KR_KOSPI"
    KR_KOSDAQ = "KR_KOSDAQ"
    US_NYSE = "US_NYSE"
    US_NASDAQ = "US_NASDAQ"


class Benchmark(str, Enum):
    KR_KOSPI = "KR_KOSPI"
    KR_KOSDAQ = "KR_KOSDAQ"
    US_SP500 = "US_SP500"
    US_NASDAQ = "US_NASDAQ"


class Country(str, Enum):
    KR = "KR"
    US = "US"


class Maturity(str, Enum):
    D91 = "91D"
    Y1 = "1Y"
    Y3 = "3Y"
    Y10 = "10Y"


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


@dataclass
class BenchmarkPrice:
    benchmark: Benchmark
    date: date
    close: Decimal


@dataclass
class RiskFreeRate:
    country: Country
    maturity: Maturity
    date: date
    rate: Decimal


def market_to_benchmark(market: Market) -> Benchmark:
    mapping = {
        Market.KR_KOSPI: Benchmark.KR_KOSPI,
        Market.KR_KOSDAQ: Benchmark.KR_KOSDAQ,
        Market.US_NYSE: Benchmark.US_SP500,
        Market.US_NASDAQ: Benchmark.US_NASDAQ,
    }
    return mapping[market]


def market_to_country(market: Market) -> Country:
    if market in (Market.KR_KOSPI, Market.KR_KOSDAQ):
        return Country.KR
    return Country.US
