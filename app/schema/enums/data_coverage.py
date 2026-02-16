from enum import Enum


class DataCoverage(str, Enum):
    FULL = "FULL"
    LOSS = "LOSS"
    PARTIAL = "PARTIAL"
    INSUFFICIENT = "INSUFFICIENT"
    NO_FS = "NO_FS"
