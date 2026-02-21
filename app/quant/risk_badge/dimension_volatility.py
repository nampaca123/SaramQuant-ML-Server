"""Dimension 2: Volatility (Beta 50% + volatility_z 50%)"""

from app.quant.risk_badge.badge_types import DimensionResult, Direction
from app.quant.risk_badge.badge_scoring import clamp_beta, clamp_score, to_tier


def _beta_score(beta: float) -> float:
    abs_b = abs(beta)
    if abs_b <= 0.8:
        return abs_b / 0.8 * 20
    if abs_b <= 1.2:
        return 20 + (abs_b - 0.8) / 0.4 * 20
    if abs_b <= 2.0:
        return 40 + (abs_b - 1.2) / 0.8 * 30
    return 70 + min((abs_b - 2.0) / 3.0, 1.0) * 30


def _volatility_z_score(vol_z: float) -> float:
    abs_z = abs(vol_z)
    if abs_z <= 1.0:
        return abs_z * 30
    if abs_z <= 2.0:
        return 30 + (abs_z - 1.0) * 30
    return 60 + min((abs_z - 2.0) / 2.0, 1.0) * 40


def compute(row: dict, vol_z: float | None = None) -> DimensionResult:
    raw_beta = row.get("beta")
    beta = clamp_beta(float(raw_beta) if raw_beta is not None else None)

    has_beta = beta is not None
    has_vol_z = vol_z is not None

    if not has_beta and not has_vol_z:
        return DimensionResult(
            name="volatility", score=50.0, tier=to_tier(50.0),
            direction=None, components={}, data_available=False,
        )

    if has_beta and has_vol_z:
        score = clamp_score(_beta_score(beta) * 0.5 + _volatility_z_score(vol_z) * 0.5)
    elif has_beta:
        score = clamp_score(_beta_score(beta))
    else:
        score = clamp_score(_volatility_z_score(vol_z))

    return DimensionResult(
        name="volatility",
        score=round(score, 1),
        tier=to_tier(score),
        direction=None,
        components={
            "beta": round(beta, 4) if beta is not None else None,
            "volatility_z": round(vol_z, 4) if vol_z is not None else None,
        },
        data_available=True,
    )
