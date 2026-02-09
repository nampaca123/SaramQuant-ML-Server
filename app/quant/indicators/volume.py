import numpy as np
import pandas as pd


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff())
    return (volume * direction).cumsum()


def vma(volume: pd.Series, period: int = 20) -> pd.Series:
    return volume.rolling(window=period).mean()
