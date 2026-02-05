from datetime import date

import pandas as pd
from psycopg2.extensions import connection

from app.services.price_service import PriceService
from app.utils.system.errors import InsufficientDataError, APIError
from app.quant.indicators import (
    sma, ema, wma,
    rsi, macd, stochastic,
    bollinger_bands, atr, adx,
    obv, vma,
    parabolic_sar
)


class IndicatorService:
    MINIMUM_PERIODS = {
        "sma": 20,
        "ema": 20,
        "wma": 20,
        "rsi": 15,
        "macd": 34,
        "stochastic": 17,
        "bollinger": 20,
        "atr": 15,
        "adx": 28,
        "obv": 2,
        "vma": 20,
        "sar": 5,
    }

    def __init__(self, conn: connection):
        self._price_service = PriceService(conn)

    def calculate(
        self,
        symbol: str,
        indicator: str,
        start_date: date | None = None,
        end_date: date | None = None,
        **params
    ) -> dict:
        min_days = self.MINIMUM_PERIODS.get(indicator, 30)
        buffer = 100

        df = self._price_service.get_dataframe(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            limit=min_days + buffer if not start_date else None
        )

        if len(df) < min_days:
            raise InsufficientDataError(required=min_days, actual=len(df))

        return self._compute(indicator, df, **params)

    def _compute(self, indicator: str, df, **params) -> dict:
        close = df["close"]

        if indicator == "sma":
            period = params.get("period", 20)
            result = sma(close, period)
            return self._to_dict(result, f"sma_{period}")

        if indicator == "ema":
            period = params.get("period", 20)
            result = ema(close, period)
            return self._to_dict(result, f"ema_{period}")

        if indicator == "wma":
            period = params.get("period", 20)
            result = wma(close, period)
            return self._to_dict(result, f"wma_{period}")

        if indicator == "rsi":
            period = params.get("period", 14)
            result = rsi(close, period)
            return self._to_dict(result, "rsi")

        if indicator == "macd":
            fast = params.get("fast_period", 12)
            slow = params.get("slow_period", 26)
            signal = params.get("signal_period", 9)
            macd_line, signal_line, histogram = macd(close, fast, slow, signal)
            return {
                "macd": self._series_to_list(macd_line),
                "signal": self._series_to_list(signal_line),
                "histogram": self._series_to_list(histogram),
            }

        if indicator == "stochastic":
            k_period = params.get("k_period", 14)
            d_period = params.get("d_period", 3)
            k, d = stochastic(df["high"], df["low"], close, k_period, d_period)
            return {
                "k": self._series_to_list(k),
                "d": self._series_to_list(d),
            }

        if indicator == "bollinger":
            period = params.get("period", 20)
            std_dev = params.get("std_dev", 2.0)
            upper, middle, lower = bollinger_bands(close, period, std_dev)
            return {
                "upper": self._series_to_list(upper),
                "middle": self._series_to_list(middle),
                "lower": self._series_to_list(lower),
            }

        if indicator == "atr":
            period = params.get("period", 14)
            result = atr(df["high"], df["low"], close, period)
            return self._to_dict(result, "atr")

        if indicator == "obv":
            result = obv(close, df["volume"])
            return self._to_dict(result, "obv")

        if indicator == "vma":
            period = params.get("period", 20)
            result = vma(df["volume"], period)
            return self._to_dict(result, f"vma_{period}")

        if indicator == "adx":
            period = params.get("period", 14)
            plus_di, minus_di, adx_val = adx(df["high"], df["low"], close, period)
            return {
                "plus_di": self._series_to_list(plus_di),
                "minus_di": self._series_to_list(minus_di),
                "adx": self._series_to_list(adx_val),
            }

        if indicator == "sar":
            af_start = params.get("af_start", 0.02)
            af_step = params.get("af_step", 0.02)
            af_max = params.get("af_max", 0.2)
            result = parabolic_sar(df["high"], df["low"], af_start, af_step, af_max)
            return self._to_dict(result, "sar")

        raise APIError(f"Unknown indicator: {indicator}", 400)

    def _to_dict(self, series, name: str) -> dict:
        return {name: self._series_to_list(series)}

    def _series_to_list(self, series) -> list[dict]:
        return [
            {"date": str(idx), "value": None if pd.isna(v) else round(v, 4)}
            for idx, v in series.items()
        ]
