"""Dimension 3: Trend Strength (ADX with directional weighting)

Uptrend weight: 0.6 (strong uptrend is less alarming)
Downtrend weight: 1.0 (strong downtrend is more alarming)
"""

from app.quant.risk_badge.badge_types import DimensionResult, Direction
from app.quant.risk_badge.badge_scoring import clamp_score, to_tier

UPTREND_WEIGHT = 0.6
DOWNTREND_WEIGHT = 1.0


def _base_adx_score(adx: float) -> float:
    if adx <= 20:
        return adx / 20 * 20
    if adx <= 40:
        return 20 + (adx - 20) / 20 * 30
    if adx <= 60:
        return 50 + (adx - 40) / 20 * 25
    return 75 + min((adx - 60) / 20, 1.0) * 25


def _direction_from_di(plus_di: float | None, minus_di: float | None) -> Direction:
    if plus_di is None or minus_di is None:
        return Direction.NEUTRAL
    if plus_di > minus_di:
        return Direction.UPTREND
    if minus_di > plus_di:
        return Direction.DOWNTREND
    return Direction.NEUTRAL


def compute(row: dict) -> DimensionResult:
    adx = row.get("adx_14")
    if adx is None:
        return DimensionResult(
            name="trend", score=50.0, tier=to_tier(50.0),
            direction=Direction.NEUTRAL, components={}, data_available=False,
        )

    adx = float(adx)
    plus_di = float(row["plus_di"]) if row.get("plus_di") is not None else None
    minus_di = float(row["minus_di"]) if row.get("minus_di") is not None else None
    direction = _direction_from_di(plus_di, minus_di)

    base = _base_adx_score(adx)
    weight = DOWNTREND_WEIGHT if direction == Direction.DOWNTREND else UPTREND_WEIGHT
    score = clamp_score(base * weight)

    return DimensionResult(
        name="trend",
        score=round(score, 1),
        tier=to_tier(score),
        direction=direction,
        components={
            "adx": round(adx, 2),
            "plus_di": round(plus_di, 2) if plus_di is not None else None,
            "minus_di": round(minus_di, 2) if minus_di is not None else None,
        },
        data_available=True,
    )
