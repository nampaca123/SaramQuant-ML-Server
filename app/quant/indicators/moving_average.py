import numpy as np
import pandas as pd


def sma(close: pd.Series, period: int = 20) -> pd.Series:
    return close.rolling(window=period).mean()


def ema(close: pd.Series, period: int = 20) -> pd.Series:
    return close.ewm(span=period, adjust=False).mean()


def wma(close: pd.Series, period: int = 20) -> pd.Series:
    weights = np.arange(1, period + 1, dtype=float)
    weights /= weights.sum()
    values = close.values.astype(float)
    conv = np.convolve(values, weights[::-1], mode="valid")
    result = np.full(len(close), np.nan)
    result[period - 1:] = conv
    return pd.Series(result, index=close.index)
