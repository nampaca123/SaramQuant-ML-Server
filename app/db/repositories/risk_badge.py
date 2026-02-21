import json

from psycopg2.extensions import connection
from psycopg2.extras import execute_values, RealDictCursor


class RiskBadgeRepository:
    def __init__(self, conn: connection):
        self._conn = conn

    def get_by_stock(self, stock_id: int) -> dict | None:
        query = """
            SELECT stock_id, market, date, summary_tier, dimensions, updated_at
            FROM risk_badges WHERE stock_id = %s
        """
        with self._conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, (stock_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    def get_by_stocks(self, stock_ids: list[int]) -> dict[int, dict]:
        if not stock_ids:
            return {}
        query = """
            SELECT stock_id, market, date, summary_tier, dimensions, updated_at
            FROM risk_badges WHERE stock_id = ANY(%s)
        """
        with self._conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, (stock_ids,))
            return {row["stock_id"]: dict(row) for row in cur.fetchall()}

    def upsert_batch(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        values = [
            (r["stock_id"], r["market"], r["date"], r["summary_tier"],
             json.dumps(r["dimensions"]))
            for r in rows
        ]
        query = """
            INSERT INTO risk_badges (stock_id, market, date, summary_tier, dimensions)
            VALUES %s
            ON CONFLICT (stock_id) DO UPDATE SET
                market = EXCLUDED.market,
                date = EXCLUDED.date,
                summary_tier = EXCLUDED.summary_tier,
                dimensions = EXCLUDED.dimensions,
                updated_at = now()
        """
        with self._conn.cursor() as cur:
            execute_values(cur, query, values, page_size=2000)
            return cur.rowcount
