"""레포지토리 공용 헬퍼 — DuckDB가 돌려주는 float/Timestamp를 Postgres 시절 Decimal/date 계약으로 되돌린다."""
import os
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pandas as pd

from app.db import lake_reader
from app.db.athena_runner import run_query
from app.db.lake_schemas import TABLES, _glue_database


def columns_of(table: str) -> list[str]:
    return [name for name, _ in TABLES[table].columns]


def resolve_run_id(run_id: str | None) -> str:
    return run_id or os.environ.get("RUN_ID") or uuid4().hex[:12]


def now_utc() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC")


def to_date(value) -> date | None:
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).date()


def to_decimal(value) -> Decimal | None:
    if value is None or pd.isna(value):
        return None
    return Decimal(str(value))


def select(table: str, columns: str, where: str = "", params: list | None = None,
           suffix: str = "") -> pd.DataFrame:
    sql = f"SELECT {columns} FROM {lake_reader.scan(table)}"
    if where:
        sql += f" WHERE {where}"
    return lake_reader.query_df(sql + suffix, params)


def scalar(df: pd.DataFrame, column: str):
    return None if df.empty else df.iloc[0][column]


def max_date(table: str, where: str = "", params: list | None = None) -> date | None:
    return to_date(scalar(select(table, "max(date) AS latest", where, params), "latest"))


def date_filters(start_date: date | None, end_date: date | None,
                 column: str = "date") -> tuple[list[str], list]:
    conditions, params = [], []
    if start_date:
        conditions.append(f"{column} >= ?")
        params.append(start_date)
    if end_date:
        conditions.append(f"{column} <= ?")
        params.append(end_date)
    return conditions, params


def limit_clause(limit: int | None) -> str:
    return f" LIMIT {int(limit)}" if limit else ""


def delete_where(table: str, predicate: str = "") -> int:
    """predicate는 DuckDB 카운트와 Athena DELETE에 그대로 쓰이므로 리터럴 SQL이어야 한다."""
    clause = f" WHERE {predicate}" if predicate else ""
    deleted = int(lake_reader.query_df(
        f"SELECT count(*) AS n FROM {lake_reader.scan(table)}{clause}"
    ).iloc[0]["n"])
    if deleted:
        run_query(f"DELETE FROM {_glue_database()}.{table}{clause}")
        lake_reader.invalidate_metadata_cache(table)
    return deleted
