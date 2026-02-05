from enum import Enum


class Maturity(str, Enum):
    D91 = "91D"
    Y1 = "1Y"
    Y3 = "3Y"
    Y10 = "10Y"
