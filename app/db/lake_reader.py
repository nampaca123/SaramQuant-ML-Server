"""DuckDB 레이크하우스 리더 — Glue metadata_location을 통한 Iceberg 읽기 전용 경로."""
import os
import time

import duckdb
import pandas as pd

from app.db.aws_session import build_session

_REGION = "ap-northeast-2"
_METADATA_TTL_S = 300.0

_connection: duckdb.DuckDBPyConnection | None = None
_glue_client = None
_metadata_cache: dict[str, tuple[str, float]] = {}


def _get_glue_client():
    global _glue_client
    if _glue_client is None:
        _glue_client = build_session().client("glue")
    return _glue_client


def _mint_s3_secret(connection: duckdb.DuckDBPyConnection) -> None:
    credentials = build_session().get_credentials()
    if credentials is None:
        raise RuntimeError("No AWS credentials resolved for DuckDB S3 access")
    frozen = credentials.get_frozen_credentials()
    region = os.getenv("AWS_REGION_NAME", _REGION)
    token_clause = f", SESSION_TOKEN '{frozen.token}'" if frozen.token else ""
    connection.execute(
        f"CREATE OR REPLACE SECRET s3sec (TYPE s3, KEY_ID '{frozen.access_key}',"
        f" SECRET '{frozen.secret_key}'{token_clause}, REGION '{region}')"
    )


def get_connection() -> duckdb.DuckDBPyConnection:
    global _connection
    if _connection is None:
        connection = duckdb.connect()
        connection.execute("INSTALL httpfs")
        connection.execute("LOAD httpfs")
        connection.execute("INSTALL iceberg")
        connection.execute("LOAD iceberg")
        connection.execute("SET unsafe_enable_version_guessing=false")
        _connection = connection
    _mint_s3_secret(_connection)
    return _connection


def resolve_metadata_location(table: str) -> str:
    cached = _metadata_cache.get(table)
    if cached is not None and time.monotonic() - cached[1] < _METADATA_TTL_S:
        return cached[0]
    database = os.getenv("GLUE_DATABASE", "saramquant")
    parameters = _get_glue_client().get_table(DatabaseName=database, Name=table)["Table"][
        "Parameters"
    ]
    if "metadata_location" not in parameters:
        raise KeyError(f"metadata_location missing for Glue table {database}.{table}")
    location = parameters["metadata_location"]
    _metadata_cache[table] = (location, time.monotonic())
    return location


def invalidate_metadata_cache(table: str | None = None) -> None:
    if table is None:
        _metadata_cache.clear()
    else:
        _metadata_cache.pop(table, None)


def scan(table: str) -> str:
    return f"iceberg_scan('{resolve_metadata_location(table)}')"


def query_df(sql: str, params: list | None = None) -> pd.DataFrame:
    return get_connection().execute(sql, params).df()
