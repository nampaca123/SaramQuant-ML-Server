"""Dimension 5: Valuation (PER 50% + PBR 50%, sector-relative)

Negative PER handling:
  - If operating_margin > 0 (profitable operations but accounting loss): score 50
  - Otherwise: score 70 (structural concern)
"""

from app.quant.risk_badge.dimension import DimensionResult
from app.quant.risk_badge.scoring import (
    clamp_score, safe_ratio, sector_or_market_fallback, to_tier,
)

W_PER = 0.5
W_PBR = 0.5


def _per_score(per: float, median: float | None, op_margin: float | None) -> float:
    if per <= 0:
        return 50.0 if (op_margin is not None and op_margin > 0) else 70.0

    ratio = safe_ratio(per, median)
    if ratio is not None:
        if ratio <= 0.5:
            return 0.0
        if ratio <= 1.0:
            return (ratio - 0.5) / 0.5 * 25
        if ratio <= 1.5:
            return 25 + (ratio - 1.0) / 0.5 * 25
        if ratio <= 2.5:
            return 50 + (ratio - 1.5) / 1.0 * 25
        return 75 + min((ratio - 2.5) / 2.0, 1.0) * 25

    if per <= 10:
        return 10.0
    if per <= 20:
        return 30.0
    if per <= 40:
        return 55.0
    return 80.0


def _pbr_score(pbr: float, median: float | None) -> float:
    ratio = safe_ratio(pbr, median)
    if ratio is not None:
        if ratio <= 0.5:
            return 0.0
        if ratio <= 1.0:
            return (ratio - 0.5) / 0.5 * 25
        if ratio <= 1.5:
            return 25 + (ratio - 1.0) / 0.5 * 25
        if ratio <= 2.5:
            return 50 + (ratio - 1.5) / 1.0 * 25
        return 75 + min((ratio - 2.5) / 2.0, 1.0) * 25

    if pbr <= 1.0:
        return 10.0
    if pbr <= 2.0:
        return 30.0
    if pbr <= 5.0:
        return 55.0
    return 80.0


def compute(
    fund_row: dict | None,
    sector_agg: dict | None,
    market_agg: dict | None,
) -> DimensionResult:
    if fund_row is None:
        return DimensionResult(
            name="valuation", score=50.0, tier=to_tier(50.0),
            direction=None, components={}, data_available=False,
        )

    per = fund_row.get("per")
    pbr = fund_row.get("pbr")

    if per is None and pbr is None:
        return DimensionResult(
            name="valuation", score=50.0, tier=to_tier(50.0),
            direction=None, components={}, data_available=False,
        )

    agg = sector_or_market_fallback(sector_agg, market_agg)
    op_margin = float(fund_row["operating_margin"]) if fund_row.get("operating_margin") is not None else None

    scores, weights = [], []
    if per is not None:
        per_v = float(per)
        med = float(agg["median_per"]) if agg and agg.get("median_per") is not None else None
        scores.append(_per_score(per_v, med, op_margin))
        weights.append(W_PER)
    if pbr is not None:
        pbr_v = float(pbr)
        med = float(agg["median_pbr"]) if agg and agg.get("median_pbr") is not None else None
        scores.append(_pbr_score(pbr_v, med))
        weights.append(W_PBR)

    total_w = sum(weights)
    score = clamp_score(sum(s * w for s, w in zip(scores, weights)) / total_w) if total_w > 0 else 50.0

    return DimensionResult(
        name="valuation",
        score=round(score, 1),
        tier=to_tier(score),
        direction=None,
        components={
            "per": round(float(per), 2) if per is not None else None,
            "pbr": round(float(pbr), 4) if pbr is not None else None,
        },
        data_available=True,
    )
