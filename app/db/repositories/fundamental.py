from psycopg2.extensions import connection
from psycopg2.extras import execute_values

from app.schema import Market

COLUMNS = [
    "stock_id", "date",
    "per", "pbr", "eps", "bps",
    "roe", "debt_ratio", "operating_margin",
    "data_coverage",
]


class FundamentalRepository:
    def __init__(self, conn: connection):
        self._conn = conn

    def delete_by_markets(self, markets: list[Market]) -> int:
        query = """
            DELETE FROM stock_fundamentals
            WHERE stock_id IN (
                SELECT id FROM stocks WHERE market = ANY(%s::market_type[])
            )
        """
        market_values = [m.value for m in markets]
        with self._conn.cursor() as cur:
            cur.execute(query, (market_values,))
            return cur.rowcount

    def insert_batch(self, rows: list[tuple]) -> int:
        if not rows:
            return 0
        col_names = ", ".join(COLUMNS)
        query = f"INSERT INTO stock_fundamentals ({col_names}) VALUES %s"
        with self._conn.cursor() as cur:
            execute_values(cur, query, rows)
            return len(rows)
