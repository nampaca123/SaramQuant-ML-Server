"""exchange_rates 레포지토리 — DuckDB(Iceberg) 조회 + Athena MERGE 쓰기. 단건 upsert도 1행 merge로 처리한다."""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pandas as pd

from app.db import lake_writer
from app.db.repositories import lake_rows

TABLE = "exchange_rates"
FX_COLUMNS = lake_rows.columns_of(TABLE)


@dataclass
class ExchangeRateRow:
    pair: str
    date: date
    rate: Decimal


class ExchangeRateRepository:
    def __init__(self, conn: object | None = None):
        self._conn = conn

    def upsert_batch(self, rows: list[ExchangeRateRow], run_id: str | None = None) -> int:
        if not rows:
            return 0
        payload = pd.DataFrame(
            [{"pair": r.pair, "date": r.date, "rate": r.rate} for r in rows]
        ).drop_duplicates(subset=["pair", "date"], keep="last")
        return lake_writer.merge(TABLE, payload[FX_COLUMNS], lake_rows.resolve_run_id(run_id))

    def upsert_one(self, row: ExchangeRateRow, run_id: str | None = None) -> None:
        self.upsert_batch([row], run_id)

    def get_latest_date(self, pair: str) -> date | None:
        return lake_rows.max_date(TABLE, "pair = ?", [pair])

    def get_rate_on_or_before(self, pair: str, target_date: date) -> Decimal | None:
        df = lake_rows.select(
            TABLE, "rate", "pair = ? AND date <= ?", [pair, target_date],
            " ORDER BY date DESC LIMIT 1",
        )
        return lake_rows.to_decimal(lake_rows.scalar(df, "rate"))

    def get_latest_rate(self, pair: str) -> Decimal | None:
        df = lake_rows.select(TABLE, "rate", "pair = ?", [pair], " ORDER BY date DESC LIMIT 1")
        return lake_rows.to_decimal(lake_rows.scalar(df, "rate"))
