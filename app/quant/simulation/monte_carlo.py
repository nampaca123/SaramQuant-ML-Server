import numpy as np


def final_prices(paths: np.ndarray) -> np.ndarray:
    return paths[:, -1]


def expected_return(paths: np.ndarray) -> float:
    initial = paths[:, 0].mean()
    final = final_prices(paths).mean()
    return float((final - initial) / initial)


def value_at_risk(paths: np.ndarray, confidence: float = 0.95) -> float:
    returns = final_prices(paths) / paths[:, 0] - 1.0
    return float(np.percentile(returns, (1 - confidence) * 100))


def conditional_var(paths: np.ndarray, confidence: float = 0.95) -> float:
    returns = final_prices(paths) / paths[:, 0] - 1.0
    var_threshold = np.percentile(returns, (1 - confidence) * 100)
    tail_returns = returns[returns <= var_threshold]
    if len(tail_returns) == 0:
        return float(var_threshold)
    return float(tail_returns.mean())


def price_percentiles(
    paths: np.ndarray,
    levels: tuple = (10, 25, 50, 75, 90),
) -> dict[int, float]:
    finals = final_prices(paths)
    values = np.percentile(finals, levels)
    return {level: round(float(val), 2) for level, val in zip(levels, values)}


def summary(paths: np.ndarray, confidence: float = 0.95) -> dict:
    return {
        "expected_return": round(expected_return(paths), 6),
        "var": round(value_at_risk(paths, confidence), 6),
        "cvar": round(conditional_var(paths, confidence), 6),
        "final_price_percentiles": price_percentiles(paths),
    }