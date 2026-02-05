import logging
from decimal import Decimal

import pandas as pd
from psycopg2.extensions import connection

from app.db.repository import (
    StockRepository,
    BenchmarkRepository,
    RiskFreeRateRepository,
)
from app.services.price_service import PriceService
from app.schema import (
    Benchmark, Country, Maturity,
    market_to_benchmark, market_to_country,
)
from app.api.errors import NotFoundError, InsufficientDataError
from app.quant.indicators import daily_returns, beta, alpha, sharpe_ratio

logger = logging.getLogger(__name__)


class RiskService:
    MINIMUM_DAYS = 60

    def __init__(self, conn: connection):
        self._price_service = PriceService(conn)
        self._stock_repo = StockRepository(conn)
        self._benchmark_repo = BenchmarkRepository(conn)
        self._rfr_repo = RiskFreeRateRepository(conn)

    def calculate(
        self,
        symbol: str,
        metric: str,
        benchmark: Benchmark | None = None,
        period_days: int = 252
    ) -> dict:
        stock = self._stock_repo.get_by_symbol(symbol)
        if not stock:
            raise NotFoundError(f"Stock {symbol}")

        market = stock[3]
        if benchmark is None:
            benchmark = market_to_benchmark(market)
        country = market_to_country(market)

        if metric == "all":
            return self.calculate_all(symbol, benchmark, period_days)
        elif metric == "beta":
            return self._calculate_beta(symbol, benchmark, period_days)
        elif metric == "alpha":
            return self._calculate_alpha(symbol, benchmark, country, period_days)
        elif metric == "sharpe":
            return self._calculate_sharpe(symbol, country, period_days)
        else:
            raise NotFoundError(f"Metric {metric}")

    def calculate_all(
        self,
        symbol: str,
        benchmark: Benchmark | None = None,
        period_days: int = 252
    ) -> dict:
        stock = self._stock_repo.get_by_symbol(symbol)
        if not stock:
            raise NotFoundError(f"Stock {symbol}")

        market = stock[3]
        if benchmark is None:
            benchmark = market_to_benchmark(market)
        country = market_to_country(market)

        stock_df = self._price_service.get_dataframe(symbol, limit=period_days + 10)
        if len(stock_df) < self.MINIMUM_DAYS:
            raise InsufficientDataError(required=self.MINIMUM_DAYS, actual=len(stock_df))

        benchmark_df = self._get_benchmark_dataframe(benchmark, period_days + 10)
        if len(benchmark_df) < self.MINIMUM_DAYS:
            raise InsufficientDataError(required=self.MINIMUM_DAYS, actual=len(benchmark_df))

        rf_rate = self._get_risk_free_rate(country, Maturity.D91)

        stock_returns = daily_returns(stock_df["close"])
        market_returns = daily_returns(benchmark_df["close"])

        aligned = pd.concat([stock_returns, market_returns], axis=1).dropna()
        if len(aligned) < self.MINIMUM_DAYS:
            raise InsufficientDataError(required=self.MINIMUM_DAYS, actual=len(aligned))

        beta_value = beta(stock_returns, market_returns)
        alpha_value = alpha(stock_returns, market_returns, rf_rate, beta_value)
        sharpe_value = sharpe_ratio(stock_returns, rf_rate)

        return {
            "symbol": symbol,
            "benchmark": benchmark.value,
            "period_days": period_days,
            "risk_free_rate": rf_rate,
            "beta": round(beta_value, 4),
            "alpha": round(alpha_value, 4),
            "sharpe_ratio": round(sharpe_value, 4),
        }

    def _calculate_beta(
        self,
        symbol: str,
        benchmark: Benchmark,
        period_days: int
    ) -> dict:
        stock_df = self._price_service.get_dataframe(symbol, limit=period_days + 10)
        if len(stock_df) < self.MINIMUM_DAYS:
            raise InsufficientDataError(required=self.MINIMUM_DAYS, actual=len(stock_df))

        benchmark_df = self._get_benchmark_dataframe(benchmark, period_days + 10)
        if len(benchmark_df) < self.MINIMUM_DAYS:
            raise InsufficientDataError(required=self.MINIMUM_DAYS, actual=len(benchmark_df))

        stock_returns = daily_returns(stock_df["close"])
        market_returns = daily_returns(benchmark_df["close"])

        aligned = pd.concat([stock_returns, market_returns], axis=1).dropna()
        if len(aligned) < self.MINIMUM_DAYS:
            raise InsufficientDataError(required=self.MINIMUM_DAYS, actual=len(aligned))

        beta_value = beta(stock_returns, market_returns)

        return {
            "symbol": symbol,
            "benchmark": benchmark.value,
            "period_days": period_days,
            "beta": round(beta_value, 4),
        }

    def _calculate_alpha(
        self,
        symbol: str,
        benchmark: Benchmark,
        country: Country,
        period_days: int
    ) -> dict:
        stock_df = self._price_service.get_dataframe(symbol, limit=period_days + 10)
        if len(stock_df) < self.MINIMUM_DAYS:
            raise InsufficientDataError(required=self.MINIMUM_DAYS, actual=len(stock_df))

        benchmark_df = self._get_benchmark_dataframe(benchmark, period_days + 10)
        if len(benchmark_df) < self.MINIMUM_DAYS:
            raise InsufficientDataError(required=self.MINIMUM_DAYS, actual=len(benchmark_df))

        rf_rate = self._get_risk_free_rate(country, Maturity.D91)

        stock_returns = daily_returns(stock_df["close"])
        market_returns = daily_returns(benchmark_df["close"])

        aligned = pd.concat([stock_returns, market_returns], axis=1).dropna()
        if len(aligned) < self.MINIMUM_DAYS:
            raise InsufficientDataError(required=self.MINIMUM_DAYS, actual=len(aligned))

        beta_value = beta(stock_returns, market_returns)
        alpha_value = alpha(stock_returns, market_returns, rf_rate, beta_value)

        return {
            "symbol": symbol,
            "benchmark": benchmark.value,
            "period_days": period_days,
            "risk_free_rate": rf_rate,
            "beta": round(beta_value, 4),
            "alpha": round(alpha_value, 4),
        }

    def _calculate_sharpe(
        self,
        symbol: str,
        country: Country,
        period_days: int
    ) -> dict:
        stock_df = self._price_service.get_dataframe(symbol, limit=period_days + 10)
        if len(stock_df) < self.MINIMUM_DAYS:
            raise InsufficientDataError(required=self.MINIMUM_DAYS, actual=len(stock_df))

        rf_rate = self._get_risk_free_rate(country, Maturity.D91)
        stock_returns = daily_returns(stock_df["close"])
        sharpe_value = sharpe_ratio(stock_returns, rf_rate)

        return {
            "symbol": symbol,
            "period_days": period_days,
            "risk_free_rate": rf_rate,
            "sharpe_ratio": round(sharpe_value, 4),
        }

    def _get_benchmark_dataframe(self, benchmark: Benchmark, limit: int) -> pd.DataFrame:
        prices = self._benchmark_repo.get_prices(benchmark, limit=limit)
        if not prices:
            return pd.DataFrame(columns=["close"])

        data = [
            {
                "date": p.date,
                "close": float(p.close) if isinstance(p.close, Decimal) else p.close,
            }
            for p in prices
        ]

        df = pd.DataFrame(data)
        df.set_index("date", inplace=True)
        df.sort_index(inplace=True)
        return df

    def _get_risk_free_rate(self, country: Country, maturity: Maturity) -> float:
        rate = self._rfr_repo.get_latest_rate(country, maturity)
        if rate is None:
            default = 3.0 if country == Country.KR else 4.0
            logger.warning(
                f"[RiskService] No risk-free rate for {country.value}/{maturity.value}, "
                f"using default {default}%"
            )
            return default
        return float(rate)
