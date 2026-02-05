from dataclasses import dataclass

from app.schema.enums import Market


@dataclass
class StockInfo:
    symbol: str
    name: str
    market: Market
