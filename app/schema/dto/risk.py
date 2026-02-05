from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.schema.enums import Country, Maturity


@dataclass
class RiskFreeRate:
    country: Country
    maturity: Maturity
    date: date
    rate: Decimal
