def debt_ratio(total_liabilities: float, total_equity: float) -> float | None:
    if total_equity is None or total_equity <= 0:
        return None
    return total_liabilities / total_equity
