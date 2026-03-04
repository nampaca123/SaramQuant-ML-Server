from psycopg2.extensions import connection
from psycopg2.extras import RealDictCursor

from app.schema import Market

_COL_TYPES = [
    ("stock_id", "bigint"), ("date", "date"),
    ("per", "numeric"), ("pbr", "numeric"), ("eps", "numeric"), ("bps", "numeric"),
    ("roe", "numeric"), ("debt_ratio", "numeric"), ("operating_margin", "numeric"),
    ("data_coverage", "data_coverage_type"),
]
COLUMNS = [c for c, _ in _COL_TYPES]
_UNNEST = ", ".join(f"%s::{t}[]" for _, t in _COL_TYPES)


_UPDATE_COLS = [c for c in COLUMNS if c not in ("stock_id", "date")]
_UPSERT_CONFLICT = (
    "ON CONFLICT (stock_id, date) DO UPDATE SET "
    + ", ".join(f"{c} = EXCLUDED.{c}" for c in _UPDATE_COLS)
)


class FundamentalRepository:
    def __init__(self, conn: connection):
        self._conn = conn

    def upsert_batch(self, rows: list[tuple]) -> int:
        if not rows:
            return 0
        cols = [list(c) for c in zip(*rows)]
        with self._conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO stock_fundamentals ({', '.join(COLUMNS)}) "
                f"SELECT * FROM UNNEST({_UNNEST}) "
                f"{_UPSERT_CONFLICT}",
                cols,
            )
            return cur.rowcount

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
