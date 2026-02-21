import json
from datetime import date

from psycopg2.extensions import connection
from psycopg2.extras import execute_values, RealDictCursor

from app.schema import Market


class FactorRepository:
    def __init__(self, conn: connection):
        self._conn = conn

    # ── factor_exposures ──

    def upsert_exposures(self, rows: list[tuple]) -> int:
        """rows: [(stock_id, date, size_z, value_z, momentum_z, volatility_z, quality_z, leverage_z)]"""
        if not rows:
            return 0
        query = """
            INSERT INTO factor_exposures
                (stock_id, date, size_z, value_z, momentum_z, volatility_z, quality_z, leverage_z)
            VALUES %s
            ON CONFLICT (stock_id, date) DO UPDATE SET
                size_z = EXCLUDED.size_z,
                value_z = EXCLUDED.value_z,
                momentum_z = EXCLUDED.momentum_z,
                volatility_z = EXCLUDED.volatility_z,
                quality_z = EXCLUDED.quality_z,
                leverage_z = EXCLUDED.leverage_z
        """
        with self._conn.cursor() as cur:
            execute_values(cur, query, rows, page_size=2000)
            return cur.rowcount

    def get_latest_exposures(self, market: Market) -> list[tuple]:
        """Returns [(stock_id, size_z, value_z, momentum_z, volatility_z, quality_z, leverage_z)]"""
        query = """
            SELECT fe.stock_id, fe.size_z, fe.value_z, fe.momentum_z,
                   fe.volatility_z, fe.quality_z, fe.leverage_z
            FROM factor_exposures fe
            JOIN stocks s ON s.id = fe.stock_id
            WHERE fe.date = (
                SELECT MAX(date) FROM factor_exposures fe2
                JOIN stocks s2 ON s2.id = fe2.stock_id
                WHERE s2.market = %s
            )
            AND s.market = %s AND s.is_active = true
        """
        with self._conn.cursor() as cur:
            cur.execute(query, (market.value, market.value))
            return cur.fetchall()

    # ── factor_returns ──

    def upsert_factor_returns(self, rows: list[tuple]) -> int:
        """rows: [(market, date, factor_name, return_value)]"""
        if not rows:
            return 0
        query = """
            INSERT INTO factor_returns (market, date, factor_name, return_value)
            VALUES %s
            ON CONFLICT (market, date, factor_name) DO UPDATE SET
                return_value = EXCLUDED.return_value
        """
        with self._conn.cursor() as cur:
            execute_values(cur, query, rows, page_size=2000)
            return cur.rowcount

    def get_factor_returns_history(
        self, market: Market, limit: int = 252
    ) -> list[tuple]:
        """Returns [(date, factor_name, return_value)] ordered by date ASC."""
        query = """
            WITH ranked_dates AS (
                SELECT DISTINCT date FROM factor_returns
                WHERE market = %s ORDER BY date DESC LIMIT %s
            )
            SELECT date, factor_name, return_value
            FROM factor_returns
            WHERE market = %s AND date >= (SELECT MIN(date) FROM ranked_dates)
            ORDER BY date ASC
        """
        with self._conn.cursor() as cur:
            cur.execute(query, (market.value, limit, market.value))
            return cur.fetchall()

    def count_factor_return_dates(self, market: Market) -> int:
        query = """
            SELECT COUNT(DISTINCT date) FROM factor_returns WHERE market = %s
        """
        with self._conn.cursor() as cur:
            cur.execute(query, (market.value,))
            return cur.fetchone()[0]

    # ── factor_covariance ──

    def upsert_covariance(
        self, market: Market, dt: date, matrix: list[list[float]]
    ) -> None:
        query = """
            INSERT INTO factor_covariance (market, date, matrix)
            VALUES (%s, %s, %s)
            ON CONFLICT (market, date) DO UPDATE SET matrix = EXCLUDED.matrix
        """
        with self._conn.cursor() as cur:
            cur.execute(query, (market.value, dt, json.dumps(matrix)))

    def get_latest_covariance(self, market: Market) -> tuple[date, list] | None:
        query = """
            SELECT date, matrix FROM factor_covariance
            WHERE market = %s ORDER BY date DESC LIMIT 1
        """
        with self._conn.cursor() as cur:
            cur.execute(query, (market.value,))
            row = cur.fetchone()
            if row is None:
                return None
            return row[0], row[1]

    # ── risk badge helpers ──

    def get_volatility_z_by_stock(self, stock_id: int, market: Market) -> float | None:
        query = """
            SELECT volatility_z FROM factor_exposures
            WHERE stock_id = %s
              AND date = (SELECT MAX(date) FROM factor_exposures fe
                          JOIN stocks s ON s.id = fe.stock_id WHERE s.market = %s)
        """
        with self._conn.cursor() as cur:
            cur.execute(query, (stock_id, market.value))
            row = cur.fetchone()
            return float(row[0]) if row and row[0] is not None else None

    def get_all_exposures_by_market(self, market: Market) -> dict[int, float]:
        """Returns {stock_id: volatility_z} for the latest date."""
        query = """
            SELECT fe.stock_id, fe.volatility_z
            FROM factor_exposures fe
            JOIN stocks s ON s.id = fe.stock_id
            WHERE s.market = %s AND s.is_active = true
              AND fe.date = (SELECT MAX(date) FROM factor_exposures fe2
                             JOIN stocks s2 ON s2.id = fe2.stock_id WHERE s2.market = %s)
        """
        with self._conn.cursor() as cur:
            cur.execute(query, (market.value, market.value))
            return {
                row[0]: float(row[1]) if row[1] is not None else None
                for row in cur.fetchall()
            }

    def get_all_sector_aggregates(self, market: Market) -> dict[str, dict]:
        """Returns {sector: {stock_count, median_per, ...}} for the latest date."""
        query = """
            SELECT * FROM sector_aggregates
            WHERE market = %s
              AND date = (SELECT MAX(date) FROM sector_aggregates WHERE market = %s)
        """
        with self._conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, (market.value, market.value))
            return {row["sector"]: dict(row) for row in cur.fetchall()}

    def get_sector_aggregate_single(self, market: Market, sector: str) -> dict | None:
        query = """
            SELECT * FROM sector_aggregates
            WHERE market = %s AND sector = %s
            ORDER BY date DESC LIMIT 1
        """
        with self._conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, (market.value, sector))
            row = cur.fetchone()
            return dict(row) if row else None

    def get_market_aggregate(self, market: Market) -> dict | None:
        """Market-wide medians computed via SQL aggregation."""
        query = """
            SELECT
                COUNT(*) as stock_count,
                PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY f.per) as median_per,
                PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY f.pbr) as median_pbr,
                PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY f.roe) as median_roe,
                PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY f.operating_margin) as median_operating_margin,
                PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY f.debt_ratio) as median_debt_ratio
            FROM stocks s
            JOIN stock_fundamentals f ON s.id = f.stock_id
            WHERE s.market = %s AND s.is_active = true
              AND f.data_coverage NOT IN ('NO_FS', 'INSUFFICIENT')
        """
        with self._conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, (market.value,))
            row = cur.fetchone()
            if not row or row["stock_count"] == 0:
                return None
            return dict(row)

    # ── sector_aggregates ──

    def get_sector_aggregates(self, markets: list[Market]) -> list[tuple]:
        query = """
            SELECT s.market, s.sector,
                COUNT(*) as stock_count,
                PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY f.per) as median_per,
                PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY f.pbr) as median_pbr,
                PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY f.roe) as median_roe,
                PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY f.operating_margin) as median_op_margin,
                PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY f.debt_ratio) as median_debt_ratio
            FROM stocks s
            JOIN stock_fundamentals f ON s.id = f.stock_id
            WHERE s.is_active = true
              AND s.sector IS NOT NULL AND s.sector != 'N/A'
              AND s.market = ANY(%s::market_type[])
              AND f.data_coverage NOT IN ('NO_FS', 'INSUFFICIENT')
            GROUP BY s.market, s.sector
        """
        market_values = [m.value for m in markets]
        with self._conn.cursor() as cur:
            cur.execute(query, (market_values,))
            return cur.fetchall()

    def upsert_sector_aggregates(self, rows: list[tuple]) -> int:
        if not rows:
            return 0
        query = """
            INSERT INTO sector_aggregates
                (market, sector, date, stock_count,
                 median_per, median_pbr, median_roe,
                 median_operating_margin, median_debt_ratio)
            VALUES %s
            ON CONFLICT (market, sector, date) DO UPDATE SET
                stock_count = EXCLUDED.stock_count,
                median_per = EXCLUDED.median_per,
                median_pbr = EXCLUDED.median_pbr,
                median_roe = EXCLUDED.median_roe,
                median_operating_margin = EXCLUDED.median_operating_margin,
                median_debt_ratio = EXCLUDED.median_debt_ratio
        """
        with self._conn.cursor() as cur:
            execute_values(cur, query, rows)
            return cur.rowcount
