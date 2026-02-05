import pandas as pd


def parabolic_sar(
    high: pd.Series,
    low: pd.Series,
    af_start: float = 0.02,
    af_step: float = 0.02,
    af_max: float = 0.2
) -> pd.Series:
    length = len(high)
    sar = pd.Series(index=high.index, dtype=float)

    is_uptrend = high.iloc[1] > high.iloc[0] if length > 1 else True
    af = af_start

    if is_uptrend:
        sar.iloc[0] = low.iloc[0]
        ep = high.iloc[0]
    else:
        sar.iloc[0] = high.iloc[0]
        ep = low.iloc[0]

    for i in range(1, length):
        prev_sar = sar.iloc[i - 1]

        if is_uptrend:
            sar.iloc[i] = prev_sar + af * (ep - prev_sar)
            sar.iloc[i] = min(sar.iloc[i], low.iloc[i - 1])
            if i >= 2:
                sar.iloc[i] = min(sar.iloc[i], low.iloc[i - 2])

            if low.iloc[i] < sar.iloc[i]:
                is_uptrend = False
                sar.iloc[i] = ep
                ep = low.iloc[i]
                af = af_start
            else:
                if high.iloc[i] > ep:
                    ep = high.iloc[i]
                    af = min(af + af_step, af_max)
        else:
            sar.iloc[i] = prev_sar + af * (ep - prev_sar)
            sar.iloc[i] = max(sar.iloc[i], high.iloc[i - 1])
            if i >= 2:
                sar.iloc[i] = max(sar.iloc[i], high.iloc[i - 2])

            if high.iloc[i] > sar.iloc[i]:
                is_uptrend = True
                sar.iloc[i] = ep
                ep = high.iloc[i]
                af = af_start
            else:
                if low.iloc[i] < ep:
                    ep = low.iloc[i]
                    af = min(af + af_step, af_max)

    return sar
