import logging
from decimal import Decimal

import numpy as np
import pandas as pd
from psycopg2.extensions import connection

from app.db import (
    DailyPriceRepository,
    BenchmarkRepository,
    RiskFreeRateRepository,
)
from app.db.repositories.indicator import IndicatorRepository
from app.schema import Market, Benchmark, Country, Maturity
from app.schema.enums.market import market_to_benchmark, market_to_country
from app.quant.indicators import (
    sma, ema, wma,
    rsi, macd, stochastic,
    bollinger_bands, atr, adx,
    obv, vma,
    parabolic_sar,
    daily_returns, beta, alpha, sharpe_ratio,
)

logger = logging.getLogger(__name__)

MIN_ROWS = 60


class ComputeEngine:
    def __init__(self, conn: connection):
        self._conn = conn
        self._price_repo = DailyPriceRepository(conn)
        self._benchmark_repo = BenchmarkRepository(conn)
        self._rfr_repo = RiskFreeRateRepository(conn)
        self._indicator_repo = IndicatorRepository(conn)

    def run(self, markets: list[Market]) -> int:
        benchmark_returns = self._load_benchmark_returns(markets)
        rf_rates = self._load_risk_free_rates(markets)

        all_rows: list[tuple] = []
        for market in markets:
            rows = self._compute_market(market, benchmark_returns, rf_rates)
            all_rows.extend(rows)

        deleted = self._indicator_repo.delete_by_markets(markets)
        logger.info(f"[Compute] Deleted {deleted} old indicator rows")

        inserted = self._indicator_repo.insert_batch(all_rows)
        self._conn.commit()
        logger.info(f"[Compute] Inserted {inserted} indicator rows")
        return inserted

    def _compute_market(
        self,
        market: Market,
        benchmark_returns: dict[Benchmark, pd.Series],
        rf_rates: dict[Country, float],
    ) -> list[tuple]:
        price_map = self._price_repo.get_prices_by_market(market, limit_per_stock=300)
        if not price_map:
            logger.warning(f"[Compute] No price data for {market.value}")
            return []

        bench = market_to_benchmark(market)
        country = market_to_country(market)
        bench_ret = benchmark_returns.get(bench)
        rf_rate = rf_rates.get(country, 3.0)

        rows: list[tuple] = []
        processed = 0
        for stock_id, raw_prices in price_map.items():
            row = self._compute_stock(stock_id, raw_prices, bench_ret, rf_rate)
            if row:
                rows.append(row)
            processed += 1
            if processed % 500 == 0:
                logger.info(f"[Compute] {market.value}: {processed}/{len(price_map)} stocks")

        logger.info(f"[Compute] {market.value}: {len(rows)}/{len(price_map)} stocks computed")
        return rows

    def _compute_stock(
        self,
        stock_id: int,
        raw_prices: list[tuple],
        bench_ret: pd.Series | None,
        rf_rate: float,
    ) -> tuple | None:
        if len(raw_prices) < MIN_ROWS:
            return None

        df = pd.DataFrame(raw_prices, columns=["date", "open", "high", "low", "close", "volume"])
        for col in ("open", "high", "low", "close"):
            df[col] = df[col].apply(lambda v: float(v) if isinstance(v, Decimal) else v)
        df.set_index("date", inplace=True)

        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]
        latest_date = df.index[-1]

        def safe_last(series: pd.Series):
            val = series.iloc[-1]
            return round(float(val), 4) if not pd.isna(val) else None

        sma_val = safe_last(sma(close, 20))
        ema_val = safe_last(ema(close, 20))
        wma_val = safe_last(wma(close, 20))

        rsi_val = safe_last(rsi(close, 14))

        macd_line, signal_line, histogram = macd(close)
        macd_val = safe_last(macd_line)
        macd_sig_val = safe_last(signal_line)
        macd_hist_val = safe_last(histogram)

        k, d = stochastic(high, low, close)
        stoch_k_val = safe_last(k)
        stoch_d_val = safe_last(d)

        bb_u, bb_m, bb_l = bollinger_bands(close)
        bb_upper_val = safe_last(bb_u)
        bb_middle_val = safe_last(bb_m)
        bb_lower_val = safe_last(bb_l)

        atr_val = safe_last(atr(high, low, close))

        p_di, m_di, adx_val_series = adx(high, low, close)
        adx_val = safe_last(adx_val_series)
        plus_di_val = safe_last(p_di)
        minus_di_val = safe_last(m_di)

        obv_series = obv(close, volume)
        obv_val = int(obv_series.iloc[-1]) if not pd.isna(obv_series.iloc[-1]) else None

        vma_series = vma(volume, 20)
        vma_val = int(vma_series.iloc[-1]) if not pd.isna(vma_series.iloc[-1]) else None

        sar_val = safe_last(parabolic_sar(high, low))

        stock_ret = daily_returns(close)
        beta_val = None
        alpha_val = None
        sharpe_val = None

        if bench_ret is not None and len(stock_ret.dropna()) >= MIN_ROWS:
            try:
                beta_val = round(beta(stock_ret, bench_ret), 4)
                alpha_val = round(alpha(stock_ret, bench_ret, rf_rate, beta_val), 4)
            except Exception:
                pass

        if len(stock_ret.dropna()) >= MIN_ROWS:
            try:
                sharpe_val = round(sharpe_ratio(stock_ret, rf_rate), 4)
            except Exception:
                pass

        return (
            stock_id, latest_date,
            sma_val, ema_val, wma_val,
            rsi_val,
            macd_val, macd_sig_val, macd_hist_val,
            stoch_k_val, stoch_d_val,
            bb_upper_val, bb_middle_val, bb_lower_val,
            atr_val, adx_val, plus_di_val, minus_di_val,
            obv_val, vma_val,
            sar_val,
            beta_val, alpha_val, sharpe_val,
        )

    def _load_benchmark_returns(
        self, markets: list[Market]
    ) -> dict[Benchmark, pd.Series]:
        benchmarks = {market_to_benchmark(m) for m in markets}
        result: dict[Benchmark, pd.Series] = {}

        for bench in benchmarks:
            prices = self._benchmark_repo.get_prices(bench, limit=300)
            if not prices:
                logger.warning(f"[Compute] No benchmark data for {bench.value}")
                continue

            data = [
                {"date": p.date, "close": float(p.close) if isinstance(p.close, Decimal) else p.close}
                for p in prices
            ]
            df = pd.DataFrame(data).set_index("date").sort_index()
            result[bench] = daily_returns(df["close"])

        return result

    def _load_risk_free_rates(self, markets: list[Market]) -> dict[Country, float]:
        countries = {market_to_country(m) for m in markets}
        result: dict[Country, float] = {}

        for country in countries:
            rate = self._rfr_repo.get_latest_rate(country, Maturity.D91)
            if rate is not None:
                result[country] = float(rate)
            else:
                default = 3.0 if country == Country.KR else 4.0
                logger.warning(
                    f"[Compute] No risk-free rate for {country.value}, using default {default}%"
                )
                result[country] = default

        return result
