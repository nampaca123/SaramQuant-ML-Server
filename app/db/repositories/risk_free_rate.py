"""risk_free_rates 레포지토리 — DuckDB(Iceberg) 조회 + Athena MERGE 쓰기."""
from datetime import date
from decimal import Decimal

import pandas as pd

from app.db import lake_writer
from app.db.repositories import lake_rows
from app.schema import Country, Maturity, RiskFreeRate

TABLE = "risk_free_rates"
RATE_COLUMNS = lake_rows.columns_of(TABLE)


class RiskFreeRateRepository:
    def __init__(self, conn: object | None = None):
        self._conn = conn

    def upsert_batch(self, rates: list[RiskFreeRate], run_id: str | None = None) -> int:
        if not rates:
            return 0
        rows = pd.DataFrame(
            [
                {
                    "country": r.country.value, "maturity": r.maturity.value,
                    "date": r.date, "rate": r.rate,
                }
                for r in rates
            ]
        ).drop_duplicates(subset=["country", "maturity", "date"], keep="last")
        rows["created_at"] = lake_rows.now_utc()
        return lake_writer.merge(TABLE, rows[RATE_COLUMNS], lake_rows.resolve_run_id(run_id))

    def get_latest_date(self, country: Country, maturity: Maturity) -> date | None:
        return lake_rows.max_date(
            TABLE, "country = ? AND maturity = ?", [country.value, maturity.value]
        )

    def get_rates(
        self,
        country: Country,
        maturity: Maturity,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int | None = None
    ) -> list[RiskFreeRate]:
        conditions, params = lake_rows.date_filters(start_date, end_date)
        conditions[:0] = ["country = ?", "maturity = ?"]
        params[:0] = [country.value, maturity.value]
        df = lake_rows.select(
            TABLE,
            "country, maturity, date, rate",
            " AND ".join(conditions),
            params,
            f" ORDER BY date DESC{lake_rows.limit_clause(limit)}",
        )
        return [
            RiskFreeRate(
                country=Country(row.country),
                maturity=Maturity(row.maturity),
                date=lake_rows.to_date(row.date),
                rate=lake_rows.to_decimal(row.rate),
            )
            for row in df.itertuples(index=False)
        ]

    def get_latest_rate(self, country: Country, maturity: Maturity) -> Decimal | None:
        df = lake_rows.select(
            TABLE,
            "rate",
            "country = ? AND maturity = ?",
            [country.value, maturity.value],
            " ORDER BY date DESC LIMIT 1",
        )
        return lake_rows.to_decimal(lake_rows.scalar(df, "rate"))

    # ── Delete operations ──

    def delete_all(self) -> int:
        return lake_rows.delete_where(TABLE)

    def delete_before(self, cutoff: date) -> int:
        return lake_rows.delete_where(TABLE, f"date < DATE '{cutoff.isoformat()}'")
