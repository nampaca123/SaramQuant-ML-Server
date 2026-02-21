from dataclasses import dataclass
from enum import Enum


class BadgeTier(str, Enum):
    STABLE = "STABLE"
    CAUTION = "CAUTION"
    WARNING = "WARNING"


class Direction(str, Enum):
    OVERHEATED = "OVERHEATED"
    OVERSOLD = "OVERSOLD"
    UPTREND = "UPTREND"
    DOWNTREND = "DOWNTREND"
    NEUTRAL = "NEUTRAL"


TIER_THRESHOLDS = (40, 70)


@dataclass
class DimensionResult:
    name: str
    score: float
    tier: BadgeTier
    direction: Direction | None
    components: dict
    data_available: bool
