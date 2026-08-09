"""레이크하우스 테이블 스펙 단일 소스 — 설계 문서 §2.3 컬럼 정의를 그대로 옮긴다."""
import os
import re
from dataclasses import dataclass, field

import pyarrow as pa


@dataclass(frozen=True)
class TableSpec:
    columns: list[tuple[str, str]]
    partition: list[str] = field(default_factory=list)
    sort: list[str] = field(default_factory=list)
    merge_keys: list[str] = field(default_factory=list)
    snapshot: bool = False


TABLES: dict[str, TableSpec] = {
    "stocks": TableSpec(
        columns=[
            ("id", "bigint"),
            ("symbol", "string"),
            ("name", "string"),
            ("market", "string"),
            ("is_active", "boolean"),
            ("dart_corp_code", "string"),
            ("sector", "string"),
            ("created_at", "timestamp"),
            ("updated_at", "timestamp"),
        ],
        sort=["market", "symbol"],
        merge_keys=["symbol", "market"],
    ),
    "daily_prices": TableSpec(
        columns=[
            ("market", "string"),
            ("stock_id", "bigint"),
            ("date", "date"),
            ("open", "decimal(15,2)"),
            ("high", "decimal(15,2)"),
            ("low", "decimal(15,2)"),
            ("close", "decimal(15,2)"),
            ("volume", "bigint"),
            ("created_at", "timestamp"),
        ],
        partition=["market", "month(date)"],
        sort=["stock_id", "date"],
        merge_keys=["stock_id", "date"],
    ),
    "benchmark_daily_prices": TableSpec(
        columns=[
            ("benchmark", "string"),
            ("date", "date"),
            ("close", "decimal(15,2)"),
            ("created_at", "timestamp"),
        ],
        sort=["benchmark", "date"],
        merge_keys=["benchmark", "date"],
    ),
    "risk_free_rates": TableSpec(
        columns=[
            ("country", "string"),
            ("maturity", "string"),
            ("date", "date"),
            ("rate", "decimal(6,4)"),
            ("created_at", "timestamp"),
        ],
        sort=["country", "maturity", "date"],
        merge_keys=["country", "maturity", "date"],
    ),
    "exchange_rates": TableSpec(
        columns=[
            ("pair", "string"),
            ("date", "date"),
            ("rate", "decimal(12,4)"),
        ],
        sort=["pair", "date"],
        merge_keys=["pair", "date"],
    ),
    "financial_statements": TableSpec(
        columns=[
            ("market", "string"),
            ("stock_id", "bigint"),
            ("fiscal_year", "int"),
            ("report_type", "string"),
            ("revenue", "decimal(20,2)"),
            ("operating_income", "decimal(20,2)"),
            ("net_income", "decimal(20,2)"),
            ("total_assets", "decimal(20,2)"),
            ("total_liabilities", "decimal(20,2)"),
            ("total_equity", "decimal(20,2)"),
            ("shares_outstanding", "bigint"),
            ("created_at", "timestamp"),
        ],
        partition=["market"],
        sort=["stock_id", "fiscal_year"],
        merge_keys=["stock_id", "fiscal_year", "report_type"],
    ),
    "stock_fundamentals": TableSpec(
        columns=[
            ("stock_id", "bigint"),
            ("date", "date"),
            ("per", "decimal(12,4)"),
            ("pbr", "decimal(12,4)"),
            ("eps", "decimal(15,4)"),
            ("bps", "decimal(15,4)"),
            ("roe", "decimal(10,4)"),
            ("debt_ratio", "decimal(10,4)"),
            ("operating_margin", "decimal(10,4)"),
            ("data_coverage", "string"),
            ("created_at", "timestamp"),
        ],
        partition=["month(date)"],
        sort=["stock_id"],
        merge_keys=["stock_id", "date"],
    ),
    "stock_indicators": TableSpec(
        columns=[
            ("stock_id", "bigint"),
            ("date", "date"),
            ("sma_20", "decimal(15,4)"),
            ("ema_20", "decimal(15,4)"),
            ("wma_20", "decimal(15,4)"),
            ("rsi_14", "decimal(8,4)"),
            ("macd", "decimal(15,4)"),
            ("macd_signal", "decimal(15,4)"),
            ("macd_hist", "decimal(15,4)"),
            ("stoch_k", "decimal(8,4)"),
            ("stoch_d", "decimal(8,4)"),
            ("bb_upper", "decimal(15,4)"),
            ("bb_middle", "decimal(15,4)"),
            ("bb_lower", "decimal(15,4)"),
            ("atr_14", "decimal(15,4)"),
            ("adx_14", "decimal(8,4)"),
            ("plus_di", "decimal(8,4)"),
            ("minus_di", "decimal(8,4)"),
            ("obv", "bigint"),
            ("vma_20", "bigint"),
            ("sar", "decimal(15,4)"),
            ("beta", "decimal(8,4)"),
            ("alpha", "decimal(8,4)"),
            ("sharpe", "decimal(8,4)"),
            ("created_at", "timestamp"),
        ],
        sort=["stock_id"],
        merge_keys=["stock_id", "date"],
        snapshot=True,
    ),
    "factor_exposures": TableSpec(
        columns=[
            ("stock_id", "bigint"),
            ("date", "date"),
            ("size_z", "decimal(8,4)"),
            ("value_z", "decimal(8,4)"),
            ("momentum_z", "decimal(8,4)"),
            ("volatility_z", "decimal(8,4)"),
            ("quality_z", "decimal(8,4)"),
            ("leverage_z", "decimal(8,4)"),
        ],
        partition=["month(date)"],
        sort=["stock_id"],
        merge_keys=["stock_id", "date"],
    ),
    "factor_returns": TableSpec(
        columns=[
            ("market", "string"),
            ("date", "date"),
            ("factor_name", "string"),
            ("return_value", "decimal(12,8)"),
        ],
        partition=["month(date)"],
        sort=["market", "factor_name"],
        merge_keys=["market", "date", "factor_name"],
    ),
    "factor_covariance": TableSpec(
        columns=[
            ("market", "string"),
            ("date", "date"),
            ("matrix", "string"),
        ],
        sort=["market", "date"],
        merge_keys=["market", "date"],
    ),
    "sector_aggregates": TableSpec(
        columns=[
            ("market", "string"),
            ("sector", "string"),
            ("date", "date"),
            ("stock_count", "int"),
            ("median_per", "decimal(12,4)"),
            ("median_pbr", "decimal(12,4)"),
            ("median_roe", "decimal(12,6)"),
            ("median_operating_margin", "decimal(12,6)"),
            ("median_debt_ratio", "decimal(12,6)"),
        ],
        sort=["market", "sector", "date"],
        merge_keys=["market", "sector", "date"],
    ),
    "risk_badges": TableSpec(
        columns=[
            ("stock_id", "bigint"),
            ("market", "string"),
            ("date", "date"),
            ("summary_tier", "string"),
            ("dimensions", "string"),
            ("updated_at", "timestamp"),
        ],
        sort=["stock_id"],
        merge_keys=["stock_id"],
        snapshot=True,
    ),
}

_DECIMAL_PATTERN = re.compile(r"^decimal\((\d+),\s*(\d+)\)$")

_ARROW_TYPES = {
    "bigint": pa.int64(),
    "int": pa.int32(),
    "string": pa.string(),
    "date": pa.date32(),
    "timestamp": pa.timestamp("us", tz="UTC"),
    "boolean": pa.bool_(),
}


def _lake_bucket() -> str:
    return os.getenv("LAKE_BUCKET", "saramquant-bucket")


def _glue_database() -> str:
    return os.getenv("GLUE_DATABASE", "saramquant")


def _format_columns(spec: TableSpec) -> str:
    return ", ".join(f"{name} {athena_type}" for name, athena_type in spec.columns)


def build_create_ddl(name: str) -> str:
    spec = TABLES[name]
    partitioned_by = f" PARTITIONED BY ({', '.join(spec.partition)})" if spec.partition else ""
    return (
        f"CREATE TABLE IF NOT EXISTS {_glue_database()}.{name} ({_format_columns(spec)})"
        f"{partitioned_by}"
        f" LOCATION 's3://{_lake_bucket()}/warehouse/{name}/'"
        " TBLPROPERTIES ('table_type'='ICEBERG','format'='parquet','write_compression'='zstd')"
    )


def build_staging_ddl(name: str, s3_prefix: str) -> str:
    spec = TABLES[name]
    return (
        f"CREATE EXTERNAL TABLE IF NOT EXISTS {_glue_database()}.stg_{name}"
        f" ({_format_columns(spec)})"
        f" STORED AS PARQUET LOCATION '{s3_prefix}'"
    )


def _to_arrow_type(athena_type: str) -> pa.DataType:
    decimal = _DECIMAL_PATTERN.match(athena_type)
    if decimal:
        return pa.decimal128(int(decimal.group(1)), int(decimal.group(2)))
    if athena_type not in _ARROW_TYPES:
        raise ValueError(f"unsupported athena type: {athena_type}")
    return _ARROW_TYPES[athena_type]


def arrow_schema(name: str) -> pa.Schema:
    return pa.schema(
        [pa.field(col, _to_arrow_type(athena_type)) for col, athena_type in TABLES[name].columns]
    )
