"""Dimension 1: Price Overheating (RSI 60% + Bollinger %B 40%)"""

from app.quant.risk_badge.dimension import BadgeTier, DimensionResult, Direction
from app.quant.risk_badge.scoring import clamp_score, to_tier

RSI_WEIGHT = 0.6
BB_WEIGHT = 0.4


def _rsi_score(rsi: float) -> float:
    if rsi <= 30:
        return 100.0 - (rsi / 30) * 30
    if rsi <= 50:
        return 30 - ((rsi - 30) / 20) * 30
    if rsi <= 70:
        return ((rsi - 50) / 20) * 30
    return 30 + ((rsi - 70) / 30) * 70


def _bb_pct_b(close: float, bb_upper: float, bb_lower: float) -> float | None:
    width = bb_upper - bb_lower
    if width <= 0:
        return None
    return (close - bb_lower) / width


def _bb_score(pct_b: float) -> float:
    if pct_b <= 0:
        return 100.0 - pct_b * -50
    if pct_b <= 0.2:
        return 70.0 - (pct_b / 0.2) * 40
    if pct_b <= 0.8:
        return 30 - ((pct_b - 0.2) / 0.6) * 30
    if pct_b <= 1.0:
        return ((pct_b - 0.8) / 0.2) * 30
    return 30 + (pct_b - 1.0) * 50


def _direction_from_rsi(rsi: float) -> Direction:
    if rsi >= 70:
        return Direction.OVERHEATED
    if rsi <= 30:
        return Direction.OVERSOLD
    return Direction.NEUTRAL


def compute(row: dict) -> DimensionResult:
    rsi = row.get("rsi_14")
    if rsi is None:
        return DimensionResult(
            name="price_heat", score=50.0, tier=BadgeTier.CAUTION,
            direction=Direction.NEUTRAL, components={}, data_available=False,
        )

    rsi = float(rsi)
    r_score = _rsi_score(rsi)

    close = row.get("close")
    bb_upper = row.get("bb_upper")
    bb_lower = row.get("bb_lower")
    pct_b = _bb_pct_b(float(close), float(bb_upper), float(bb_lower)) \
        if close and bb_upper and bb_lower else None

    if pct_b is not None:
        b_score = _bb_score(pct_b)
        score = clamp_score(r_score * RSI_WEIGHT + b_score * BB_WEIGHT)
        components = {"rsi": rsi, "bb_pct_b": round(pct_b, 4)}
    else:
        score = clamp_score(r_score)
        components = {"rsi": rsi, "bb_pct_b": None}

    return DimensionResult(
        name="price_heat",
        score=round(score, 1),
        tier=to_tier(score),
        direction=_direction_from_rsi(rsi),
        components=components,
        data_available=True,
    )
