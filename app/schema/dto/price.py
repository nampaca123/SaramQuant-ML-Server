from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.schema.enums import Benchmark


@dataclass
class OHLCV:
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


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
