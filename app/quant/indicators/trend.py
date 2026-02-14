import numpy as np
import pandas as pd


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
