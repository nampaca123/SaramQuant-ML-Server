"""Dimension 4: Company Health (debt_ratio + ROE + operating_margin, sector-relative)

Weights: debt_ratio 40%, ROE 30%, operating_margin 30%
Uses sector median comparison with market-wide fallback.
"""

from app.quant.risk_badge.badge_types import DimensionResult
from app.quant.risk_badge.badge_scoring import (
    clamp_score, safe_ratio, sector_or_market_fallback, to_tier,
)

W_DEBT = 0.4
W_ROE = 0.3
W_OP_MARGIN = 0.3


def _debt_score(debt_ratio: float, median: float | None) -> float:
    ratio = safe_ratio(debt_ratio, median)
    if ratio is not None:
        if ratio <= 0.5:
            return 0.0
        if ratio <= 1.0:
            return (ratio - 0.5) / 0.5 * 30
        if ratio <= 1.5:
            return 30 + (ratio - 1.0) / 0.5 * 30
        return 60 + min((ratio - 1.5) / 1.0, 1.0) * 40
    if debt_ratio <= 0.5:
        return 10.0
    if debt_ratio <= 1.0:
        return 30.0
    if debt_ratio <= 2.0:
        return 60.0
    return 85.0


def _roe_score(roe: float, median: float | None) -> float:
    if median is not None and median > 0:
        ratio = roe / median
        if ratio >= 1.5:
            return 0.0
        if ratio >= 1.0:
            return (1.5 - ratio) / 0.5 * 20
        if ratio >= 0.5:
            return 20 + (1.0 - ratio) / 0.5 * 30
        return 50 + min((0.5 - ratio) / 0.5, 1.0) * 50
    if roe >= 0.15:
        return 10.0
    if roe >= 0.05:
        return 30.0
    if roe >= 0:
        return 55.0
    return 80.0


def _op_margin_score(op_margin: float, median: float | None) -> float:
    if median is not None and median > 0:
        ratio = op_margin / median
        if ratio >= 1.5:
            return 0.0
        if ratio >= 1.0:
            return (1.5 - ratio) / 0.5 * 20
        if ratio >= 0.5:
            return 20 + (1.0 - ratio) / 0.5 * 30
        return 50 + min((0.5 - ratio) / 0.5, 1.0) * 50
    if op_margin >= 0.15:
        return 10.0
    if op_margin >= 0.05:
        return 30.0
    if op_margin >= 0:
        return 55.0
    return 80.0


def compute(
    fund_row: dict | None,
    sector_agg: dict | None,
    market_agg: dict | None,
) -> DimensionResult:
    if fund_row is None:
        return DimensionResult(
            name="company_health", score=50.0, tier=to_tier(50.0),
            direction=None, components={}, data_available=False,
        )

    debt = fund_row.get("debt_ratio")
    roe = fund_row.get("roe")
    op_margin = fund_row.get("operating_margin")

    if all(v is None for v in (debt, roe, op_margin)):
        return DimensionResult(
            name="company_health", score=50.0, tier=to_tier(50.0),
            direction=None, components={}, data_available=False,
        )

    agg = sector_or_market_fallback(sector_agg, market_agg)

    scores, weights = [], []
    if debt is not None:
        debt = float(debt)
        med = float(agg["median_debt_ratio"]) if agg and agg.get("median_debt_ratio") is not None else None
        scores.append(_debt_score(debt, med))
        weights.append(W_DEBT)
    if roe is not None:
        roe = float(roe)
        med = float(agg["median_roe"]) if agg and agg.get("median_roe") is not None else None
        scores.append(_roe_score(roe, med))
        weights.append(W_ROE)
    if op_margin is not None:
        op_margin = float(op_margin)
        med = float(agg["median_operating_margin"]) if agg and agg.get("median_operating_margin") is not None else None
        scores.append(_op_margin_score(op_margin, med))
        weights.append(W_OP_MARGIN)

    total_w = sum(weights)
    score = clamp_score(sum(s * w for s, w in zip(scores, weights)) / total_w) if total_w > 0 else 50.0

    return DimensionResult(
        name="company_health",
        score=round(score, 1),
        tier=to_tier(score),
        direction=None,
        components={
            "debt_ratio": round(float(fund_row["debt_ratio"]), 4) if fund_row.get("debt_ratio") is not None else None,
            "roe": round(float(fund_row["roe"]), 4) if fund_row.get("roe") is not None else None,
            "operating_margin": round(float(fund_row["operating_margin"]), 4) if fund_row.get("operating_margin") is not None else None,
        },
        data_available=True,
    )
