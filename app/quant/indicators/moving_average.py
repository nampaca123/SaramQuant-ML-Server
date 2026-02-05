import pandas as pd


def sma(close: pd.Series, period: int = 20) -> pd.Series:
    return close.rolling(window=period).mean()


def ema(close: pd.Series, period: int = 20) -> pd.Series:
    return close.ewm(span=period, adjust=False).mean()


def wma(close: pd.Series, period: int = 20) -> pd.Series:
    weights = pd.Series(range(1, period + 1))
    return close.rolling(window=period).apply(
        lambda x: (x * weights).sum() / weights.sum(),
        raw=True
    )
