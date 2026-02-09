from datetime import date
from decimal import Decimal

import pandas as pd
from psycopg2.extensions import connection

from app.db import StockRepository, DailyPriceRepository
from app.utils.system.errors import NotFoundError


class PriceService:
    def __init__(self, conn: connection):
        self._stock_repo = StockRepository(conn)
        self._price_repo = DailyPriceRepository(conn)

    def get_dataframe(
        self,
        symbol: str,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int | None = None
    ) -> pd.DataFrame:
        stock = self._stock_repo.get_by_symbol(symbol)
        if not stock:
            raise NotFoundError(f"Stock {symbol}")

        stock_id = stock[0]
        prices = self._price_repo.get_prices(
            stock_id=stock_id,
            start_date=start_date,
            end_date=end_date,
            limit=limit
        )

        if not prices:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        data = [
            {
                "date": p.date,
                "open": float(p.open) if isinstance(p.open, Decimal) else p.open,
                "high": float(p.high) if isinstance(p.high, Decimal) else p.high,
                "low": float(p.low) if isinstance(p.low, Decimal) else p.low,
                "close": float(p.close) if isinstance(p.close, Decimal) else p.close,
                "volume": p.volume
            }
            for p in prices
        ]

        df = pd.DataFrame(data)
        df.set_index("date", inplace=True)
        df.sort_index(inplace=True)
        return df
