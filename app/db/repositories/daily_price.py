from datetime import date
from psycopg2.extensions import connection
from app.schema import DailyPrice, Market

_COL_TYPES = [
    ("stock_id", "bigint"), ("date", "date"),
    ("open", "numeric"), ("high", "numeric"), ("low", "numeric"),
    ("close", "numeric"), ("volume", "bigint"),
]
_COLS = [c for c, _ in _COL_TYPES]
_UNNEST = ", ".join(f"%s::{t}[]" for _, t in _COL_TYPES)
_UPSERT_CONFLICT = (
    "ON CONFLICT (stock_id, date) DO UPDATE SET "
    "open = EXCLUDED.open, high = EXCLUDED.high, "
    "low = EXCLUDED.low, close = EXCLUDED.close, volume = EXCLUDED.volume"
)


class DailyPriceRepository:
    def __init__(self, conn: connection):
        self._conn = conn

    def _unnest_upsert(self, cols: list[list]) -> int:
        with self._conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO daily_prices ({', '.join(_COLS)}) "
                f"SELECT * FROM UNNEST({_UNNEST}) "
                f"{_UPSERT_CONFLICT}",
                cols,
            )
            return cur.rowcount

    def upsert_batch(self, stock_id: int, prices: list[DailyPrice]) -> int:
        if not prices:
            return 0
        cols = [
            [stock_id] * len(prices),
            [p.date for p in prices],
            [p.open for p in prices],
            [p.high for p in prices],
            [p.low for p in prices],
            [p.close for p in prices],
            [p.volume for p in prices],
        ]
        return self._unnest_upsert(cols)

    def bulk_upsert(self, rows: list[tuple]) -> int:
        if not rows:
            return 0
        cols = [list(c) for c in zip(*rows)]
        return self._unnest_upsert(cols)

    def get_latest_date(self, stock_id: int) -> date | None:
        query = "SELECT MAX(date) FROM daily_prices WHERE stock_id = %s"
        with self._conn.cursor() as cur:
            cur.execute(query, (stock_id,))
            result = cur.fetchone()
            return result[0] if result and result[0] else None

    def get_latest_date_by_market(self, market: Market) -> date | None:
        query = """
            SELECT MAX(dp.date) FROM daily_prices dp
            JOIN stocks s ON dp.stock_id = s.id
            WHERE s.market = %s AND s.is_active = true
        """
        with self._conn.cursor() as cur:
            cur.execute(query, (market.value,))
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

    def get_prices_by_market(
        self, market: Market, limit_per_stock: int = 300
    ) -> dict[int, list[tuple]]:
        query = """
            SELECT s.id AS stock_id,
                   dp.date, dp.open, dp.high, dp.low, dp.close, dp.volume
            FROM stocks s
            CROSS JOIN LATERAL (
                SELECT date, open, high, low, close, volume
                FROM daily_prices dp
                WHERE dp.stock_id = s.id
                ORDER BY dp.date DESC
                LIMIT %s
            ) dp
            WHERE s.market = %s AND s.is_active = true
            ORDER BY s.id, dp.date
        """
        with self._conn.cursor() as cur:
            cur.execute(query, (limit_per_stock, market.value))
            result: dict[int, list[tuple]] = {}
            for row in cur.fetchall():
                stock_id = row[0]
                if stock_id not in result:
                    result[stock_id] = []
                result[stock_id].append(row[1:])
            return result

    def get_close_prices_batch(
        self, stock_ids: list[int], limit: int = 252
    ) -> dict[int, dict]:
        if not stock_ids:
            return {}
        query = """
            SELECT stock_id, date, close FROM (
                SELECT stock_id, date, close,
                       ROW_NUMBER() OVER (PARTITION BY stock_id ORDER BY date DESC) AS rn
                FROM daily_prices WHERE stock_id = ANY(%s)
            ) t WHERE rn <= %s
        """
        with self._conn.cursor() as cur:
            cur.execute(query, (stock_ids, limit))
            result: dict[int, dict] = {}
            for stock_id, dt, close in cur.fetchall():
                if stock_id not in result:
                    result[stock_id] = {}
                result[stock_id][dt] = float(close)
            return result

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
