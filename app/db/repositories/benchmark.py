"""benchmark_daily_prices 레포지토리 — DuckDB(Iceberg) 조회 + Athena MERGE 쓰기."""
from datetime import date

import pandas as pd

from app.db import lake_writer
from app.db.repositories import lake_rows
from app.schema import Benchmark, BenchmarkPrice

TABLE = "benchmark_daily_prices"
BENCHMARK_COLUMNS = lake_rows.columns_of(TABLE)


class BenchmarkRepository:
    def __init__(self, conn: object | None = None):
        self._conn = conn

    def upsert_batch(self, prices: list[BenchmarkPrice], run_id: str | None = None) -> int:
        if not prices:
            return 0
        rows = pd.DataFrame(
            [
                {"benchmark": p.benchmark.value, "date": p.date, "close": p.close}
                for p in prices
            ]
        ).drop_duplicates(subset=["benchmark", "date"], keep="last")
        rows["created_at"] = lake_rows.now_utc()
        return lake_writer.merge(
            TABLE, rows[BENCHMARK_COLUMNS], lake_rows.resolve_run_id(run_id)
        )

    def get_latest_date(self, benchmark: Benchmark) -> date | None:
        return lake_rows.max_date(TABLE, "benchmark = ?", [benchmark.value])

    def get_prices(
        self,
        benchmark: Benchmark,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int | None = None
    ) -> list[BenchmarkPrice]:
        conditions, params = lake_rows.date_filters(start_date, end_date)
        conditions.insert(0, "benchmark = ?")
        params.insert(0, benchmark.value)
        df = lake_rows.select(
            TABLE,
            "benchmark, date, close",
            " AND ".join(conditions),
            params,
            f" ORDER BY date DESC{lake_rows.limit_clause(limit)}",
        )
        return [
            BenchmarkPrice(
                benchmark=Benchmark(row.benchmark),
                date=lake_rows.to_date(row.date),
                close=lake_rows.to_decimal(row.close),
            )
            for row in df.itertuples(index=False)
        ]

    # ── Delete operations ──

    def delete_all(self) -> int:
        return lake_rows.delete_where(TABLE)

    def delete_by_benchmark(self, benchmark: Benchmark) -> int:
        return lake_rows.delete_where(TABLE, f"benchmark = '{benchmark.value}'")

    def delete_before(self, cutoff: date) -> int:
        return lake_rows.delete_where(TABLE, f"date < DATE '{cutoff.isoformat()}'")
