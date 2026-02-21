import math

from app.quant.risk_badge.badge_types import TIER_THRESHOLDS, BadgeTier

BETA_CLAMP = (-5.0, 5.0)


def clamp_beta(beta: float | None) -> float | None:
    if beta is None or math.isnan(beta) or math.isinf(beta):
        return None
    return max(BETA_CLAMP[0], min(BETA_CLAMP[1], beta))


def sector_or_market_fallback(
    sector_agg: dict | None, market_agg: dict | None, min_count: int = 5
) -> dict | None:
    if sector_agg is None or sector_agg.get("stock_count", 0) < min_count:
        return market_agg
    return sector_agg


def safe_ratio(value: float | None, median: float | None) -> float | None:
    if value is None or median is None or median <= 0:
        return None
    return value / median


def to_tier(score: float) -> BadgeTier:
    if score < TIER_THRESHOLDS[0]:
        return BadgeTier.STABLE
    if score < TIER_THRESHOLDS[1]:
        return BadgeTier.CAUTION
    return BadgeTier.WARNING


def clamp_score(score: float) -> float:
    return max(0.0, min(100.0, score))
