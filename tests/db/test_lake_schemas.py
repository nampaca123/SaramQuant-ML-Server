import pyarrow as pa
import pytest

from app.db.lake_schemas import TABLES, TableSpec, arrow_schema, build_create_ddl, build_staging_ddl

EXPECTED_TABLES = {
    "stocks",
    "daily_prices",
    "benchmark_daily_prices",
    "risk_free_rates",
    "exchange_rates",
    "financial_statements",
    "stock_fundamentals",
    "stock_indicators",
    "factor_exposures",
    "factor_returns",
    "factor_covariance",
    "sector_aggregates",
    "risk_badges",
}


def test_all_13_tables_defined():
    assert set(TABLES) == EXPECTED_TABLES
    assert len(TABLES) == 13
    assert all(isinstance(spec, TableSpec) for spec in TABLES.values())


def test_daily_prices_ddl_contains_partition_and_zstd():
    ddl = build_create_ddl("daily_prices")
    assert ddl.startswith("CREATE TABLE IF NOT EXISTS saramquant.daily_prices (")
    assert "PARTITIONED BY (market, month(date))" in ddl
    assert "LOCATION 's3://saramquant-bucket/warehouse/daily_prices/'" in ddl
    assert "'table_type'='ICEBERG'" in ddl
    assert "'format'='parquet'" in ddl
    assert "'write_compression'='zstd'" in ddl
    assert "open decimal(15,2)" in ddl
    assert "market string" in ddl


def test_unpartitioned_ddl_omits_partitioned_by():
    ddl = build_create_ddl("stocks")
    assert "PARTITIONED BY" not in ddl
    assert "id bigint" in ddl
    assert "LOCATION 's3://saramquant-bucket/warehouse/stocks/'" in ddl


def test_staging_ddl_is_plain_parquet_external_table():
    prefix = "s3://saramquant-bucket/staging/daily_prices/run-1/"
    ddl = build_staging_ddl("daily_prices", prefix)
    assert ddl.startswith("CREATE EXTERNAL TABLE IF NOT EXISTS saramquant.stg_daily_prices (")
    assert ddl.endswith(f"STORED AS PARQUET LOCATION '{prefix}'")
    assert "PARTITIONED BY" not in ddl
    assert "ICEBERG" not in ddl
    # 파티션 변환 컬럼(date)도 일반 컬럼으로 그대로 존재해야 한다
    assert "date date" in ddl
    assert "market string" in ddl


def test_arrow_schema_decimal_mapping():
    schema = arrow_schema("daily_prices")
    assert schema.field("open").type == pa.decimal128(15, 2)
    assert schema.field("stock_id").type == pa.int64()
    assert schema.field("date").type == pa.date32()
    assert schema.field("market").type == pa.string()
    assert schema.field("created_at").type == pa.timestamp("us", tz="UTC")
    assert schema.names == [name for name, _ in TABLES["daily_prices"].columns]


def test_arrow_schema_int_and_boolean_mapping():
    assert arrow_schema("financial_statements").field("fiscal_year").type == pa.int32()
    assert arrow_schema("stocks").field("is_active").type == pa.bool_()
    assert arrow_schema("sector_aggregates").field("stock_count").type == pa.int32()


def test_merge_keys():
    assert TABLES["financial_statements"].merge_keys == ["stock_id", "fiscal_year", "report_type"]
    assert TABLES["stocks"].merge_keys == ["symbol", "market"]
    assert TABLES["daily_prices"].merge_keys == ["stock_id", "date"]
    assert TABLES["risk_free_rates"].merge_keys == ["country", "maturity", "date"]
    assert TABLES["factor_returns"].merge_keys == ["market", "date", "factor_name"]
    assert TABLES["risk_badges"].merge_keys == ["stock_id"]


def test_snapshot_tables():
    snapshots = {name for name, spec in TABLES.items() if spec.snapshot}
    assert snapshots == {"stock_indicators", "risk_badges"}


def test_partitions_match_spec():
    partitioned = {name: spec.partition for name, spec in TABLES.items() if spec.partition}
    assert partitioned == {
        "daily_prices": ["market", "month(date)"],
        "financial_statements": ["market"],
        "stock_fundamentals": ["month(date)"],
        "factor_exposures": ["month(date)"],
        "factor_returns": ["month(date)"],
    }


def test_fact_tables_drop_id_and_stocks_keeps_it():
    assert "id" in dict(TABLES["stocks"].columns)
    for name in (
        "daily_prices",
        "benchmark_daily_prices",
        "risk_free_rates",
        "financial_statements",
        "exchange_rates",
    ):
        assert "id" not in dict(TABLES[name].columns), name


def test_market_column_added_to_two_tables_only():
    assert TABLES["daily_prices"].columns[0] == ("market", "string")
    assert TABLES["financial_statements"].columns[0] == ("market", "string")


def test_merge_and_sort_keys_reference_real_columns():
    for name, spec in TABLES.items():
        cols = dict(spec.columns)
        for key in spec.merge_keys + spec.sort:
            assert key in cols, f"{name}.{key}"


@pytest.mark.parametrize("name", sorted(EXPECTED_TABLES))
def test_every_table_builds_ddl_and_schema(name):
    ddl = build_create_ddl(name)
    assert ddl.startswith(f"CREATE TABLE IF NOT EXISTS saramquant.{name} (")
    assert f"warehouse/{name}/" in ddl
    assert len(arrow_schema(name)) == len(TABLES[name].columns)


def test_unknown_table_raises():
    with pytest.raises(KeyError):
        build_create_ddl("predictions")
