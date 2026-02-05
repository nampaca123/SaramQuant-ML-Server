import pandas as pd


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = close.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    return (volume * direction).cumsum()


def vma(volume: pd.Series, period: int = 20) -> pd.Series:
    return volume.rolling(window=period).mean()
