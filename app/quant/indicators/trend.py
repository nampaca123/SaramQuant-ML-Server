import numpy as np
import pandas as pd


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


def parabolic_sar(
    high: pd.Series,
    low: pd.Series,
    af_start: float = 0.02,
    af_step: float = 0.02,
    af_max: float = 0.2,
) -> pd.Series:
    h = high.values.astype(float)
    l = low.values.astype(float)
    n = len(h)
    sar = np.empty(n)

    is_uptrend = h[1] > h[0] if n > 1 else True
    af = af_start

    if is_uptrend:
        sar[0] = l[0]
        ep = h[0]
    else:
        sar[0] = h[0]
        ep = l[0]

    for i in range(1, n):
        prev_sar = sar[i - 1]

        if is_uptrend:
            s = prev_sar + af * (ep - prev_sar)
            s = min(s, l[i - 1])
            if i >= 2:
                s = min(s, l[i - 2])

            if l[i] < s:
                is_uptrend = False
                s = ep
                ep = l[i]
                af = af_start
            elif h[i] > ep:
                ep = h[i]
                af = min(af + af_step, af_max)
        else:
            s = prev_sar + af * (ep - prev_sar)
            s = max(s, h[i - 1])
            if i >= 2:
                s = max(s, h[i - 2])

            if h[i] > s:
                is_uptrend = True
                s = ep
                ep = h[i]
                af = af_start
            elif l[i] < ep:
                ep = l[i]
                af = min(af + af_step, af_max)

        sar[i] = s

    return pd.Series(sar, index=high.index)
