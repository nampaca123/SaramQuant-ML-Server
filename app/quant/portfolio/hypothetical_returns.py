import numpy as np

MIN_DATA_POINTS = 60


def build_from_prices(
    all_series: dict[int, dict],
    stock_ids: list[int],
    weights: np.ndarray,
    lookback: int = 252,
) -> dict:
    date_sets = [set(all_series.get(sid, {}).keys()) for sid in stock_ids]
    if not date_sets or any(not ds for ds in date_sets):
        return {"returns": np.array([]), "effective_lookback": 0, "coverage": "INSUFFICIENT"}

    common_dates = sorted(set.intersection(*date_sets))
    if len(common_dates) < MIN_DATA_POINTS:
        return {"returns": np.array([]), "effective_lookback": len(common_dates), "coverage": "INSUFFICIENT"}

    price_matrix = np.array([
        [all_series[sid][d] for d in common_dates]
        for sid in stock_ids
    ]).T

    stock_returns = np.diff(price_matrix, axis=0) / price_matrix[:-1]
    portfolio_returns = stock_returns @ weights

    return {
        "returns": portfolio_returns,
        "effective_lookback": len(common_dates) - 1,
        "coverage": "FULL" if len(common_dates) >= lookback else "PARTIAL",
    }
