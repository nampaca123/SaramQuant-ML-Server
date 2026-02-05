from enum import Enum


class Benchmark(str, Enum):
    KR_KOSPI = "KR_KOSPI"
    KR_KOSDAQ = "KR_KOSDAQ"
    US_SP500 = "US_SP500"
    US_NASDAQ = "US_NASDAQ"
