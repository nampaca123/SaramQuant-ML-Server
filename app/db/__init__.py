"""레이크하우스 전환으로 Postgres 풀은 사라졌다. 기존 `with get_connection()` 호출부를 위해 무동작 스텁만 남긴다."""
from contextlib import contextmanager
from typing import Generator


class NullConnection:
    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass


@contextmanager
def get_connection() -> Generator[NullConnection, None, None]:
    yield NullConnection()


from .repositories import (  # noqa: E402
    BenchmarkRepository,
    DailyPriceRepository,
    ExchangeRateRepository,
    FactorRepository,
    FinancialStatementRepository,
    FundamentalRepository,
    IndicatorRepository,
    RiskBadgeRepository,
    RiskFreeRateRepository,
    StockRepository,
)

__all__ = [
    "get_connection",
    "NullConnection",
    "BenchmarkRepository",
    "DailyPriceRepository",
    "ExchangeRateRepository",
    "FactorRepository",
    "FinancialStatementRepository",
    "FundamentalRepository",
    "IndicatorRepository",
    "RiskBadgeRepository",
    "RiskFreeRateRepository",
    "StockRepository",
]
