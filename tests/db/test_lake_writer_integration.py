import uuid
from datetime import date, datetime

import pandas as pd
import pytest

from app.db.athena_runner import run_query
from app.db.lake_reader import invalidate_metadata_cache, query_df, scan
from app.db.lake_writer import merge, snapshot_replace

pytestmark = pytest.mark.integration

TEST_IDS = (999901, 999902)


def _run_id() -> str:
    return f"test-{uuid.uuid4().hex[:8]}"


def _stocks_rows(name_suffix: str) -> pd.DataFrame:
    now = datetime(2026, 8, 9, 0, 0, 0)
    return pd.DataFrame(
        [
            {
                "id": TEST_IDS[index],
                "symbol": f"_TEST{index + 1}",
                "name": f"lake writer probe {index + 1} {name_suffix}",
                "market": "KR_KOSPI",
                "is_active": True,
                "dart_corp_code": None,
                "sector": "TEST",
                "created_at": now,
                "updated_at": now,
            }
            for index in range(2)
        ]
    )


@pytest.fixture
def stocks_cleanup():
    yield
    run_query(f"DELETE FROM saramquant.stocks WHERE id IN {TEST_IDS}")
    invalidate_metadata_cache("stocks")


@pytest.fixture
def risk_badges_cleanup():
    yield
    run_query("DELETE FROM saramquant.risk_badges")
    invalidate_metadata_cache("risk_badges")


def _probe_stocks() -> pd.DataFrame:
    return query_df(
        f"SELECT id, symbol, name, market FROM {scan('stocks')}"
        f" WHERE id IN {TEST_IDS} ORDER BY id"
    )


def test_merge_inserts_then_updates_same_keys(stocks_cleanup):
    inserted = merge("stocks", _stocks_rows("v1"), _run_id())
    first = _probe_stocks()

    assert inserted == 2
    assert len(first) == 2
    assert list(first["symbol"]) == ["_TEST1", "_TEST2"]
    assert list(first["market"]) == ["KR_KOSPI", "KR_KOSPI"]
    assert first["name"].iloc[0].endswith("v1")

    updated = merge("stocks", _stocks_rows("v2"), _run_id())
    second = _probe_stocks()

    assert updated == 2
    assert len(second) == 2
    assert list(second["name"]) == [
        "lake writer probe 1 v2",
        "lake writer probe 2 v2",
    ]


def test_snapshot_replace_roundtrip_on_risk_badges(risk_badges_cleanup):
    rows = pd.DataFrame(
        [
            {
                "stock_id": TEST_IDS[0],
                "market": "KR_KOSPI",
                "date": date(2026, 8, 9),
                "summary_tier": "LOW",
                "dimensions": '{"valuation": "LOW", "leverage": "MID"}',
                "updated_at": datetime(2026, 8, 9, 0, 0, 0),
            },
            {
                "stock_id": TEST_IDS[1],
                "market": "KR_KOSPI",
                "date": date(2026, 8, 9),
                "summary_tier": "HIGH",
                "dimensions": '{"valuation": "HIGH"}',
                "updated_at": datetime(2026, 8, 9, 0, 0, 0),
            },
        ]
    )

    written = snapshot_replace("risk_badges", rows, _run_id())
    df = query_df(
        f"SELECT stock_id, summary_tier, dimensions, date FROM {scan('risk_badges')}"
        " ORDER BY stock_id"
    )

    assert written == 2
    assert len(df) == 2
    assert list(df["stock_id"]) == list(TEST_IDS)
    assert list(df["summary_tier"]) == ["LOW", "HIGH"]
    assert df["dimensions"].iloc[0] == '{"valuation": "LOW", "leverage": "MID"}'
    assert str(df["date"].iloc[0])[:10] == "2026-08-09"
