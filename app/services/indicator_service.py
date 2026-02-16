import warnings

import pandas as pd

from app.quant.indicators import (
    sma, ema, wma,
    rsi, macd, stochastic,
    bollinger_bands, atr, adx,
    obv, vma,
    parabolic_sar,
    daily_returns, beta, alpha, sharpe_ratio,
)

MIN_ROWS = 60


def _safe_last(series: pd.Series):
    val = series.iloc[-1]
    return round(float(val), 4) if not pd.isna(val) else None


def _safe_int_last(series: pd.Series):
    val = series.iloc[-1]
    return int(val) if not pd.isna(val) else None


class IndicatorService:
    @staticmethod
    def build_dataframe(raw_prices: list[tuple]) -> pd.DataFrame | None:
        if len(raw_prices) < MIN_ROWS:
            return None
        df = pd.DataFrame(raw_prices, columns=["date", "open", "high", "low", "close", "volume"])
        df[["open", "high", "low", "close"]] = df[["open", "high", "low", "close"]].astype(float)
        df.set_index("date", inplace=True)
        return df

    @staticmethod
    def compute(
        stock_id: int,
        df: pd.DataFrame,
        benchmark_returns: pd.Series | None,
        risk_free_rate: float,
    ) -> tuple:
        close, high, low, volume = df["close"], df["high"], df["low"], df["volume"]

        macd_line, signal_line, histogram = macd(close)
        k, d = stochastic(high, low, close)
        bb_u, bb_m, bb_l = bollinger_bands(close)
        p_di, m_di, adx_series = adx(high, low, close)

        stock_ret = daily_returns(close)
        beta_val, alpha_val, sharpe_val = IndicatorService._compute_risk(
            stock_ret, benchmark_returns, risk_free_rate
        )

        return (
            stock_id, df.index[-1],
            _safe_last(sma(close, 20)),
            _safe_last(ema(close, 20)),
            _safe_last(wma(close, 20)),
            _safe_last(rsi(close, 14)),
            _safe_last(macd_line),
            _safe_last(signal_line),
            _safe_last(histogram),
            _safe_last(k),
            _safe_last(d),
            _safe_last(bb_u),
            _safe_last(bb_m),
            _safe_last(bb_l),
            _safe_last(atr(high, low, close)),
            _safe_last(adx_series),
            _safe_last(p_di),
            _safe_last(m_di),
            _safe_int_last(obv(close, volume)),
            _safe_int_last(vma(volume, 20)),
            _safe_last(parabolic_sar(high, low)),
            beta_val, alpha_val, sharpe_val,
        )

    @staticmethod
    def _compute_risk(
        stock_ret: pd.Series,
        benchmark_ret: pd.Series | None,
        rf_rate: float,
    ) -> tuple[float | None, float | None, float | None]:
        clean_len = len(stock_ret.dropna())
        beta_val = alpha_val = sharpe_val = None

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)

            if benchmark_ret is not None and clean_len >= MIN_ROWS:
                try:
                    beta_val = round(float(beta(stock_ret, benchmark_ret)), 4)
                    alpha_val = round(float(alpha(stock_ret, benchmark_ret, rf_rate, beta_val)), 4)
                except Exception:
                    pass

            if clean_len >= MIN_ROWS:
                try:
                    sharpe_val = round(float(sharpe_ratio(stock_ret, rf_rate)), 4)
                except Exception:
                    pass

        return beta_val, alpha_val, sharpe_val
