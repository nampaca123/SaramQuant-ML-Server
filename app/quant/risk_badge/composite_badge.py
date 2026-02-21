"""Composite badge logic: critical/signal worst-of with mitigation.

Critical dimensions (always propagate WARNING):
  - company_health, valuation

Signal dimensions (single WARNING mitigated to CAUTION unless compound):
  - price_heat, volatility, trend

Rules:
  1. Any critical dimension WARNING -> summary WARNING
  2. Multiple signal WARNINGs -> summary WARNING
  3. Single signal WARNING + another CAUTION or worse -> summary WARNING
  4. Single signal WARNING alone -> summary CAUTION (mitigation)
  5. Uptrend-only WARNING -> CAUTION (strong uptrend is not alarming alone)
  6. Otherwise -> worst tier among valid dimensions
"""

from app.quant.risk_badge.badge_types import BadgeTier, DimensionResult, Direction

CRITICAL_DIMS = {"company_health", "valuation"}
SIGNAL_DIMS = {"price_heat", "volatility", "trend"}


def compute_composite(dimensions: list[DimensionResult]) -> BadgeTier:
    valid = [d for d in dimensions if d.data_available]
    if not valid:
        return BadgeTier.CAUTION

    critical_warnings = [d for d in valid if d.name in CRITICAL_DIMS and d.tier == BadgeTier.WARNING]
    if critical_warnings:
        return BadgeTier.WARNING

    signal_warnings = [d for d in valid if d.name in SIGNAL_DIMS and d.tier == BadgeTier.WARNING]
    if len(signal_warnings) >= 2:
        return BadgeTier.WARNING

    if len(signal_warnings) == 1:
        sw = signal_warnings[0]
        if sw.name == "trend" and sw.direction == Direction.UPTREND:
            return BadgeTier.CAUTION

        caution_or_worse = [d for d in valid if d.tier != BadgeTier.STABLE and d not in signal_warnings]
        if caution_or_worse:
            return BadgeTier.WARNING
        return BadgeTier.CAUTION

    worst = max(valid, key=lambda d: list(BadgeTier).index(d.tier))
    return worst.tier
