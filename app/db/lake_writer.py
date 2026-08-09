"""레이크 라이터 — staging parquet 업로드 후 MERGE 또는 스냅샷 교체로 Iceberg 본 테이블에 반영한다."""
import io
import logging

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from app.db import lake_reader
from app.db.athena_runner import run_query
from app.db.aws_session import build_session
from app.db.lake_schemas import (
    TABLES,
    _glue_database,
    _lake_bucket,
    arrow_schema,
    build_staging_ddl,
)

logger = logging.getLogger(__name__)

_s3_client = None


def _get_s3_client():
    global _s3_client
    if _s3_client is None:
        _s3_client = build_session().client("s3")
    return _s3_client


def _column_names(table: str) -> list[str]:
    return [name for name, _ in TABLES[table].columns]


def _cast_column(series: pd.Series, field: pa.Field) -> pa.Array:
    if pa.types.is_date(field.type):
        return pa.array(pd.to_datetime(series).dt.date, type=field.type, from_pandas=True)
    if pa.types.is_timestamp(field.type):
        values = pd.to_datetime(series)
        utc = (
            values.dt.tz_localize("UTC") if values.dt.tz is None else values.dt.tz_convert("UTC")
        )
        return pa.array(utc.dt.floor("us"), type=field.type, from_pandas=True)
    if pa.types.is_decimal(field.type):
        return pa.array(series, from_pandas=True).cast(field.type, safe=False)
    return pa.array(series, type=field.type, from_pandas=True)


def _to_arrow_table(table: str, df: pd.DataFrame) -> pa.Table:
    schema = arrow_schema(table)
    columns = [_cast_column(df[field.name], field) for field in schema]
    return pa.Table.from_arrays(columns, schema=schema)


def write_staging(table: str, df: pd.DataFrame, run_id: str) -> str:
    buffer = io.BytesIO()
    pq.write_table(_to_arrow_table(table, df), buffer, compression="zstd")
    bucket = _lake_bucket()
    key = f"staging/{table}/{run_id}/part-0.parquet"
    _get_s3_client().put_object(Bucket=bucket, Key=key, Body=buffer.getvalue())
    prefix = f"s3://{bucket}/staging/{table}/{run_id}/"
    run_query(f"DROP TABLE IF EXISTS {_glue_database()}.stg_{table}")
    run_query(build_staging_ddl(table, prefix))
    logger.info("Staging written: table=%s rows=%d prefix=%s", table, len(df), prefix)
    return prefix


def build_merge_sql(table: str) -> str:
    spec = TABLES[table]
    if spec.snapshot:
        raise ValueError(f"table {table} is snapshot-managed; use snapshot_replace")
    database = _glue_database()
    columns = _column_names(table)
    on_clause = " AND ".join(f"t.{key} = s.{key}" for key in spec.merge_keys)
    updates = ", ".join(f"{c} = s.{c}" for c in columns if c not in spec.merge_keys)
    return (
        f"MERGE INTO {database}.{table} t USING {database}.stg_{table} s ON ({on_clause})"
        f" WHEN MATCHED THEN UPDATE SET {updates}"
        f" WHEN NOT MATCHED THEN INSERT ({', '.join(columns)})"
        f" VALUES ({', '.join('s.' + c for c in columns)})"
    )


def merge(table: str, df: pd.DataFrame, run_id: str) -> int:
    merge_sql = build_merge_sql(table)
    if df.empty:
        return 0
    write_staging(table, df, run_id)
    run_query(merge_sql)
    lake_reader.invalidate_metadata_cache(table)
    logger.info("Merge applied: table=%s rows=%d run_id=%s", table, len(df), run_id)
    return len(df)


def snapshot_replace(table: str, df: pd.DataFrame, run_id: str) -> int:
    if not TABLES[table].snapshot:
        raise ValueError(f"table {table} is not snapshot-managed; use merge")
    if df.empty:
        return 0
    database = _glue_database()
    columns = ", ".join(_column_names(table))
    write_staging(table, df, run_id)
    run_query(f"DELETE FROM {database}.{table}")
    run_query(
        f"INSERT INTO {database}.{table} ({columns})"
        f" SELECT {columns} FROM {database}.stg_{table}"
    )
    lake_reader.invalidate_metadata_cache(table)
    logger.info("Snapshot replaced: table=%s rows=%d run_id=%s", table, len(df), run_id)
    return len(df)


def optimize_and_vacuum(tables: list[str]) -> None:
    database = _glue_database()
    for table in tables:
        for sql in (
            f"OPTIMIZE {database}.{table} REWRITE DATA USING BIN_PACK",
            f"VACUUM {database}.{table}",
        ):
            try:
                run_query(sql)
            except Exception:
                logger.exception("Table maintenance failed: %s", sql)
        lake_reader.invalidate_metadata_cache(table)
