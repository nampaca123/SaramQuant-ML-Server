import pandas as pd


def bollinger_bands(
    close: pd.Series,
    period: int = 20,
    std_dev: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    middle = close.rolling(window=period).mean()
    std = close.rolling(window=period).std()

    upper = middle + (std * std_dev)
    lower = middle - (std * std_dev)

    return upper, middle, lower


def atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14
) -> pd.Series:
    prev_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()

    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return true_range.ewm(alpha=1/period, min_periods=period, adjust=False).mean()


def adx(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14
) -> tuple[pd.Series, pd.Series, pd.Series]:
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    prev_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    plus_dm = high - prev_high
    minus_dm = prev_low - low
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    atr_val = true_range.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    atr_val = atr_val.replace(0, float('nan'))

    plus_di = 100 * (plus_dm.ewm(alpha=1/period, min_periods=period, adjust=False).mean() / atr_val)
    minus_di = 100 * (minus_dm.ewm(alpha=1/period, min_periods=period, adjust=False).mean() / atr_val)

    di_sum = plus_di + minus_di
    di_sum = di_sum.replace(0, float('nan'))
    dx = 100 * (plus_di - minus_di).abs() / di_sum
    adx_val = dx.ewm(alpha=1/period, min_periods=period, adjust=False).mean()

    return plus_di, minus_di, adx_val
