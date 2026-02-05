from enum import Enum

from .benchmark import Benchmark
from .country import Country


class Market(str, Enum):
    KR_KOSPI = "KR_KOSPI"
    KR_KOSDAQ = "KR_KOSDAQ"
    US_NYSE = "US_NYSE"
    US_NASDAQ = "US_NASDAQ"


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
