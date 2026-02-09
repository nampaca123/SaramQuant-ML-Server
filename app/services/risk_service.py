import logging
from dataclasses import dataclass
from decimal import Decimal

import pandas as pd
from psycopg2.extensions import connection

from app.db import StockRepository, BenchmarkRepository, RiskFreeRateRepository
from app.services.price_service import PriceService
from app.schema import (
    Benchmark, Country, Maturity,
    market_to_benchmark, market_to_country,
)
from app.utils.system.errors import NotFoundError, InsufficientDataError
from app.quant.indicators import daily_returns, beta, alpha, sharpe_ratio

logger = logging.getLogger(__name__)


@dataclass
class _PreparedReturns:
    stock_returns: pd.Series
    market_returns: pd.Series | None
    rf_rate: float
    benchmark: Benchmark
    period_days: int


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

        needs_benchmark = metric in ("all", "beta", "alpha")
        needs_rf = metric in ("all", "alpha", "sharpe")

        prep = self._prepare(
            symbol, benchmark, country, period_days,
            needs_benchmark=needs_benchmark,
            needs_rf=needs_rf,
        )

        if metric == "all":
            return self._build_all(symbol, prep)
        elif metric == "beta":
            return self._build_beta(symbol, prep)
        elif metric == "alpha":
            return self._build_alpha(symbol, prep)
        elif metric == "sharpe":
            return self._build_sharpe(symbol, prep)
        else:
            raise NotFoundError(f"Metric {metric}")

    def _prepare(
        self,
        symbol: str,
        benchmark: Benchmark,
        country: Country,
        period_days: int,
        needs_benchmark: bool = True,
        needs_rf: bool = True,
    ) -> _PreparedReturns:
        stock_df = self._price_service.get_dataframe(symbol, limit=period_days + 10)
        if len(stock_df) < self.MINIMUM_DAYS:
            raise InsufficientDataError(required=self.MINIMUM_DAYS, actual=len(stock_df))

        stock_returns = daily_returns(stock_df["close"])
        market_returns = None

        if needs_benchmark:
            benchmark_df = self._get_benchmark_dataframe(benchmark, period_days + 10)
            if len(benchmark_df) < self.MINIMUM_DAYS:
                raise InsufficientDataError(required=self.MINIMUM_DAYS, actual=len(benchmark_df))
            market_returns = daily_returns(benchmark_df["close"])

            aligned = pd.concat([stock_returns, market_returns], axis=1).dropna()
            if len(aligned) < self.MINIMUM_DAYS:
                raise InsufficientDataError(required=self.MINIMUM_DAYS, actual=len(aligned))

        rf_rate = self._get_risk_free_rate(country, Maturity.D91) if needs_rf else 0.0

        return _PreparedReturns(
            stock_returns=stock_returns,
            market_returns=market_returns,
            rf_rate=rf_rate,
            benchmark=benchmark,
            period_days=period_days,
        )

    def _build_all(self, symbol: str, p: _PreparedReturns) -> dict:
        beta_val = beta(p.stock_returns, p.market_returns)
        alpha_val = alpha(p.stock_returns, p.market_returns, p.rf_rate, beta_val)
        sharpe_val = sharpe_ratio(p.stock_returns, p.rf_rate)
        return {
            "symbol": symbol,
            "benchmark": p.benchmark.value,
            "period_days": p.period_days,
            "risk_free_rate": p.rf_rate,
            "beta": round(beta_val, 4),
            "alpha": round(alpha_val, 4),
            "sharpe_ratio": round(sharpe_val, 4),
        }

    def _build_beta(self, symbol: str, p: _PreparedReturns) -> dict:
        beta_val = beta(p.stock_returns, p.market_returns)
        return {
            "symbol": symbol,
            "benchmark": p.benchmark.value,
            "period_days": p.period_days,
            "beta": round(beta_val, 4),
        }

    def _build_alpha(self, symbol: str, p: _PreparedReturns) -> dict:
        beta_val = beta(p.stock_returns, p.market_returns)
        alpha_val = alpha(p.stock_returns, p.market_returns, p.rf_rate, beta_val)
        return {
            "symbol": symbol,
            "benchmark": p.benchmark.value,
            "period_days": p.period_days,
            "risk_free_rate": p.rf_rate,
            "beta": round(beta_val, 4),
            "alpha": round(alpha_val, 4),
        }

    def _build_sharpe(self, symbol: str, p: _PreparedReturns) -> dict:
        sharpe_val = sharpe_ratio(p.stock_returns, p.rf_rate)
        return {
            "symbol": symbol,
            "period_days": p.period_days,
            "risk_free_rate": p.rf_rate,
            "sharpe_ratio": round(sharpe_val, 4),
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
