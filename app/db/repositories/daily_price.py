from datetime import date
from psycopg2.extensions import connection
from psycopg2.extras import execute_values
from app.schema import DailyPrice, Market


class DailyPriceRepository:
    def __init__(self, conn: connection):
        self._conn = conn

    def upsert_batch(self, stock_id: int, prices: list[DailyPrice]) -> int:
        if not prices:
            return 0
        query = """
            INSERT INTO daily_prices (stock_id, date, open, high, low, close, volume)
            VALUES %s
            ON CONFLICT (stock_id, date) DO UPDATE SET
                open = EXCLUDED.open,
                high = EXCLUDED.high,
                low = EXCLUDED.low,
                close = EXCLUDED.close,
                volume = EXCLUDED.volume
        """
        data = [
            (stock_id, p.date, p.open, p.high, p.low, p.close, p.volume)
            for p in prices
        ]
        with self._conn.cursor() as cur:
            execute_values(cur, query, data)
            return cur.rowcount

    def get_latest_date(self, stock_id: int) -> date | None:
        query = "SELECT MAX(date) FROM daily_prices WHERE stock_id = %s"
        with self._conn.cursor() as cur:
            cur.execute(query, (stock_id,))
            result = cur.fetchone()
            return result[0] if result and result[0] else None

    def get_prices(
        self,
        stock_id: int,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int | None = None
    ) -> list[DailyPrice]:
        conditions = ["dp.stock_id = %s"]
        params: list = [stock_id]

        if start_date:
            conditions.append("dp.date >= %s")
            params.append(start_date)
        if end_date:
            conditions.append("dp.date <= %s")
            params.append(end_date)

        where_clause = " AND ".join(conditions)
        limit_clause = f"LIMIT {limit}" if limit else ""

        query = f"""
            SELECT s.symbol, dp.date, dp.open, dp.high, dp.low, dp.close, dp.volume
            FROM daily_prices dp
            JOIN stocks s ON dp.stock_id = s.id
            WHERE {where_clause}
            ORDER BY dp.date DESC
            {limit_clause}
        """

        with self._conn.cursor() as cur:
            cur.execute(query, tuple(params))
            return [
                DailyPrice(
                    symbol=row[0], date=row[1],
                    open=row[2], high=row[3], low=row[4],
                    close=row[5], volume=row[6]
                )
                for row in cur.fetchall()
            ]

    # ── Delete operations ──

    def delete_all(self) -> int:
        with self._conn.cursor() as cur:
            cur.execute("DELETE FROM daily_prices")
            return cur.rowcount

    def delete_by_stock(self, stock_id: int) -> int:
        with self._conn.cursor() as cur:
            cur.execute("DELETE FROM daily_prices WHERE stock_id = %s", (stock_id,))
            return cur.rowcount

    def delete_by_market(self, market: Market) -> int:
        query = """
            DELETE FROM daily_prices
            WHERE stock_id IN (SELECT id FROM stocks WHERE market = %s)
        """
        with self._conn.cursor() as cur:
            cur.execute(query, (market.value,))
            return cur.rowcount

    def delete_before(self, cutoff: date) -> int:
        with self._conn.cursor() as cur:
            cur.execute("DELETE FROM daily_prices WHERE date < %s", (cutoff,))
            return cur.rowcount
