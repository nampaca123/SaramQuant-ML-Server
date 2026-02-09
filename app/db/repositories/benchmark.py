from datetime import date
from psycopg2.extensions import connection
from psycopg2.extras import execute_values
from app.schema import Benchmark, BenchmarkPrice


class BenchmarkRepository:
    def __init__(self, conn: connection):
        self._conn = conn

    def upsert_batch(self, prices: list[BenchmarkPrice]) -> int:
        if not prices:
            return 0
        query = """
            INSERT INTO benchmark_daily_prices (benchmark, date, close)
            VALUES %s
            ON CONFLICT (benchmark, date) DO UPDATE SET
                close = EXCLUDED.close
        """
        data = [(p.benchmark.value, p.date, p.close) for p in prices]
        with self._conn.cursor() as cur:
            execute_values(cur, query, data)
            return cur.rowcount

    def get_latest_date(self, benchmark: Benchmark) -> date | None:
        query = "SELECT MAX(date) FROM benchmark_daily_prices WHERE benchmark = %s"
        with self._conn.cursor() as cur:
            cur.execute(query, (benchmark.value,))
            result = cur.fetchone()
            return result[0] if result and result[0] else None

    def get_prices(
        self,
        benchmark: Benchmark,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int | None = None
    ) -> list[BenchmarkPrice]:
        conditions = ["benchmark = %s"]
        params: list = [benchmark.value]

        if start_date:
            conditions.append("date >= %s")
            params.append(start_date)
        if end_date:
            conditions.append("date <= %s")
            params.append(end_date)

        where_clause = " AND ".join(conditions)
        limit_clause = f"LIMIT {limit}" if limit else ""

        query = f"""
            SELECT benchmark, date, close
            FROM benchmark_daily_prices
            WHERE {where_clause}
            ORDER BY date DESC
            {limit_clause}
        """

        with self._conn.cursor() as cur:
            cur.execute(query, tuple(params))
            return [
                BenchmarkPrice(
                    benchmark=Benchmark(row[0]),
                    date=row[1],
                    close=row[2]
                )
                for row in cur.fetchall()
            ]

    # ── Delete operations ──

    def delete_all(self) -> int:
        with self._conn.cursor() as cur:
            cur.execute("DELETE FROM benchmark_daily_prices")
            return cur.rowcount

    def delete_by_benchmark(self, benchmark: Benchmark) -> int:
        with self._conn.cursor() as cur:
            cur.execute(
                "DELETE FROM benchmark_daily_prices WHERE benchmark = %s",
                (benchmark.value,),
            )
            return cur.rowcount

    def delete_before(self, cutoff: date) -> int:
        with self._conn.cursor() as cur:
            cur.execute("DELETE FROM benchmark_daily_prices WHERE date < %s", (cutoff,))
            return cur.rowcount
