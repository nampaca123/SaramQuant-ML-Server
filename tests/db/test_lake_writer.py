import io
from datetime import date, datetime
from decimal import Decimal

import pandas as pd
import pyarrow.parquet as pq
import pytest

import app.db.lake_writer as lake_writer
from app.db.lake_schemas import TABLES, arrow_schema


class StubS3Client:
    def __init__(self):
        self.puts = []

    def put_object(self, Bucket, Key, Body):
        self.puts.append((Bucket, Key, Body))
        return {}


@pytest.fixture
def aws(monkeypatch):
    executed = []
    invalidated = []
    s3 = StubS3Client()
    monkeypatch.setattr(lake_writer, "run_query", lambda sql: executed.append(sql) or "qid")
    monkeypatch.setattr(lake_writer, "_s3_client", s3)
    monkeypatch.setattr(
        lake_writer.lake_reader, "invalidate_metadata_cache", invalidated.append
    )
    return {"executed": executed, "invalidated": invalidated, "s3": s3}


def _stocks_df():
    return pd.DataFrame(
        [
            {
                "id": 1,
                "symbol": "005930",
                "name": "삼성전자",
                "market": "KR_KOSPI",
                "is_active": True,
                "dart_corp_code": "00126380",
                "sector": "IT",
                "created_at": datetime(2026, 8, 9, 1, 2, 3),
                "updated_at": datetime(2026, 8, 9, 1, 2, 3),
            }
        ]
    )


def test_merge_sql_targets_table_and_staging_alias():
    sql = lake_writer.build_merge_sql("daily_prices")

    assert sql.startswith(
        "MERGE INTO saramquant.daily_prices t USING saramquant.stg_daily_prices s ON ("
    )


def test_merge_sql_joins_on_all_merge_keys():
    sql = lake_writer.build_merge_sql("daily_prices")

    assert "ON (t.stock_id = s.stock_id AND t.date = s.date)" in sql


def test_merge_sql_updates_every_non_key_column():
    sql = lake_writer.build_merge_sql("daily_prices")
    update_clause = sql.split("WHEN MATCHED THEN UPDATE SET ")[1].split(" WHEN NOT MATCHED")[0]

    assert update_clause == (
        "market = s.market, open = s.open, high = s.high, low = s.low,"
        " close = s.close, volume = s.volume, created_at = s.created_at"
    )


def test_merge_sql_inserts_every_column():
    sql = lake_writer.build_merge_sql("daily_prices")
    columns = [name for name, _ in TABLES["daily_prices"].columns]

    assert f"WHEN NOT MATCHED THEN INSERT ({', '.join(columns)})" in sql
    assert f"VALUES ({', '.join('s.' + c for c in columns)})" in sql


def test_merge_sql_uses_configured_glue_database(monkeypatch):
    monkeypatch.setenv("GLUE_DATABASE", "db-test")

    assert "MERGE INTO db-test.stocks t USING db-test.stg_stocks s" in (
        lake_writer.build_merge_sql("stocks")
    )


def test_merge_sql_rejects_snapshot_table():
    with pytest.raises(ValueError):
        lake_writer.build_merge_sql("risk_badges")


def test_merge_with_empty_dataframe_makes_no_aws_calls(aws):
    written = lake_writer.merge("stocks", pd.DataFrame(), "run-empty")

    assert written == 0
    assert aws["executed"] == []
    assert aws["s3"].puts == []
    assert aws["invalidated"] == []


def test_merge_on_snapshot_table_raises_before_any_aws_call(aws):
    with pytest.raises(ValueError):
        lake_writer.merge("risk_badges", _stocks_df(), "run-1")

    assert aws["executed"] == []
    assert aws["s3"].puts == []


def test_merge_writes_staging_then_runs_merge_then_invalidates(aws):
    written = lake_writer.merge("stocks", _stocks_df(), "run-1")

    assert written == 1
    assert len(aws["s3"].puts) == 1
    assert aws["executed"][0] == "DROP TABLE IF EXISTS saramquant.stg_stocks"
    assert aws["executed"][1].startswith("CREATE EXTERNAL TABLE IF NOT EXISTS saramquant.stg_stocks")
    assert aws["executed"][2] == lake_writer.build_merge_sql("stocks")
    assert aws["invalidated"] == ["stocks"]


def test_write_staging_uploads_parquet_to_run_prefix(aws):
    prefix = lake_writer.write_staging("stocks", _stocks_df(), "run-1")

    bucket, key, body = aws["s3"].puts[0]
    assert bucket == "saramquant-bucket"
    assert key == "staging/stocks/run-1/part-0.parquet"
    assert prefix == "s3://saramquant-bucket/staging/stocks/run-1/"
    assert body[:4] == b"PAR1"
    assert f"LOCATION '{prefix}'" in aws["executed"][1]


def test_write_staging_casts_dataframe_to_table_schema(aws):
    df = pd.DataFrame(
        [
            {
                "market": "KR_KOSPI",
                "stock_id": 7,
                "date": date(2026, 8, 7),
                "open": 1.5,
                "high": Decimal("2.25"),
                "low": 1.0,
                "close": 2.0,
                "volume": 100,
                "created_at": datetime(2026, 8, 7, 9, 0, 0),
            }
        ]
    )

    lake_writer.write_staging("daily_prices", df, "run-2")

    table = pq.read_table(io.BytesIO(aws["s3"].puts[0][2]))
    assert table.schema.equals(arrow_schema("daily_prices"))
    row = table.to_pylist()[0]
    assert row["date"] == date(2026, 8, 7)
    assert row["open"] == Decimal("1.50")
    assert row["high"] == Decimal("2.25")
    assert row["created_at"].isoformat() == "2026-08-07T09:00:00+00:00"


def test_write_staging_reorders_columns_to_spec_order(aws):
    df = _stocks_df()[["updated_at", "symbol", "market", "id"]].copy()
    for name, _ in TABLES["stocks"].columns:
        if name not in df.columns:
            df[name] = None

    lake_writer.write_staging("stocks", df, "run-3")

    table = pq.read_table(io.BytesIO(aws["s3"].puts[0][2]))
    assert table.schema.names == [name for name, _ in TABLES["stocks"].columns]


def test_snapshot_replace_rejects_non_snapshot_table(aws):
    with pytest.raises(ValueError):
        lake_writer.snapshot_replace("stocks", _stocks_df(), "run-1")

    assert aws["executed"] == []


def test_snapshot_replace_with_empty_dataframe_makes_no_aws_calls(aws):
    assert lake_writer.snapshot_replace("risk_badges", pd.DataFrame(), "run-1") == 0
    assert aws["executed"] == []
    assert aws["s3"].puts == []


def _badge_df():
    return pd.DataFrame(
        [
            {
                "stock_id": 1,
                "market": "KR_KOSPI",
                "date": date(2026, 8, 9),
                "summary_tier": "LOW",
                "dimensions": '{"a": 1}',
                "updated_at": datetime(2026, 8, 9, 0, 0, 0),
            }
        ]
    )


def test_snapshot_replace_deletes_then_inserts_explicit_columns(aws):
    written = lake_writer.snapshot_replace("risk_badges", _badge_df(), "run-1")

    columns = ", ".join(name for name, _ in TABLES["risk_badges"].columns)
    assert written == 1
    assert aws["executed"][2] == "DELETE FROM saramquant.risk_badges"
    assert aws["executed"][3] == (
        f"INSERT INTO saramquant.risk_badges ({columns})"
        f" SELECT {columns} FROM saramquant.stg_risk_badges"
    )
    assert aws["invalidated"] == ["risk_badges"]


def test_snapshot_replace_scopes_the_delete_when_given_a_predicate(aws):
    lake_writer.snapshot_replace(
        "risk_badges", _badge_df(), "run-1",
        delete_where="market IN ('KR_KOSPI', 'KR_KOSDAQ')",
    )

    assert aws["executed"][2] == (
        "DELETE FROM saramquant.risk_badges WHERE market IN ('KR_KOSPI', 'KR_KOSDAQ')"
    )
    assert aws["executed"][3].startswith("INSERT INTO saramquant.risk_badges")


def test_snapshot_replace_keeps_full_delete_when_predicate_is_none(aws):
    lake_writer.snapshot_replace("risk_badges", _badge_df(), "run-1", delete_where=None)

    assert aws["executed"][2] == "DELETE FROM saramquant.risk_badges"


def test_optimize_and_vacuum_runs_both_statements_per_table(aws):
    lake_writer.optimize_and_vacuum(["stocks", "daily_prices"])

    assert aws["executed"] == [
        "OPTIMIZE saramquant.stocks REWRITE DATA USING BIN_PACK",
        "VACUUM saramquant.stocks",
        "OPTIMIZE saramquant.daily_prices REWRITE DATA USING BIN_PACK",
        "VACUUM saramquant.daily_prices",
    ]
    assert aws["invalidated"] == ["stocks", "daily_prices"]


def test_optimize_and_vacuum_swallows_failures(monkeypatch, aws):
    def boom(sql):
        raise RuntimeError("athena down")

    monkeypatch.setattr(lake_writer, "run_query", boom)

    lake_writer.optimize_and_vacuum(["stocks"])

    assert aws["invalidated"] == ["stocks"]
