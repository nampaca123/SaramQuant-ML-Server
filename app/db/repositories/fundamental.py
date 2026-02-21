from psycopg2.extensions import connection
from psycopg2.extras import execute_values, RealDictCursor

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

    def get_with_shares(self, stock_ids: list[int]) -> list[tuple]:
        """Returns [(stock_id, pbr, roe, operating_margin, debt_ratio, shares_outstanding)]."""
        query = """
            SELECT f.stock_id, f.pbr, f.roe, f.operating_margin, f.debt_ratio,
                   fs.shares_outstanding
            FROM stock_fundamentals f
            JOIN stocks s ON s.id = f.stock_id
            LEFT JOIN LATERAL (
                SELECT shares_outstanding FROM financial_statements
                WHERE stock_id = f.stock_id
                ORDER BY fiscal_year DESC,
                    CASE report_type
                        WHEN 'FY' THEN 4 WHEN 'Q3' THEN 3
                        WHEN 'Q2' THEN 2 WHEN 'Q1' THEN 1
                    END DESC
                LIMIT 1
            ) fs ON true
            WHERE f.stock_id = ANY(%s)
              AND f.data_coverage NOT IN ('NO_FS', 'INSUFFICIENT')
        """
        with self._conn.cursor() as cur:
            cur.execute(query, (stock_ids,))
            return cur.fetchall()

    def get_latest_by_stock(self, stock_id: int) -> dict | None:
        query = """
            SELECT sf.*, s.sector, s.market
            FROM stock_fundamentals sf
            JOIN stocks s ON s.id = sf.stock_id
            WHERE sf.stock_id = %s
            ORDER BY sf.date DESC LIMIT 1
        """
        with self._conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, (stock_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    def get_all_by_market(self, market: Market) -> dict[int, dict]:
        query = """
            SELECT sf.*, s.sector, s.market, s.symbol
            FROM stock_fundamentals sf
            JOIN stocks s ON s.id = sf.stock_id
            WHERE s.market = %s AND s.is_active = true
        """
        with self._conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, (market.value,))
            return {row["stock_id"]: dict(row) for row in cur.fetchall()}
