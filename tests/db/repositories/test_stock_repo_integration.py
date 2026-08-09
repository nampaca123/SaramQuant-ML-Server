import uuid

import pytest

from app.db.athena_runner import run_query
from app.db.lake_reader import invalidate_metadata_cache
from app.db.repositories.stock import StockRepository
from app.schema import Market, StockInfo

pytestmark = pytest.mark.integration

SYMBOLS = ("_T9A", "_T9B")


def _run_id() -> str:
    return f"test-{uuid.uuid4().hex[:8]}"


def _probe_stocks() -> list[StockInfo]:
    return [
        StockInfo(symbol=symbol, name=f"task9 probe {symbol}", market=Market.KR_KOSPI)
        for symbol in SYMBOLS
    ]


@pytest.fixture
def stocks_cleanup():
    yield
    run_query(f"DELETE FROM saramquant.stocks WHERE symbol IN {SYMBOLS}")
    invalidate_metadata_cache("stocks")


def test_upsert_roundtrip_keeps_ids_stable(stocks_cleanup):
    repo = StockRepository()

    written = repo.upsert_batch(_probe_stocks(), run_id=_run_id())
    ids = {
        symbol: stock_id
        for stock_id, symbol, _ in repo.get_active_stocks(Market.KR_KOSPI)
        if symbol in SYMBOLS
    }

    assert written == 2
    assert set(ids) == set(SYMBOLS)
    assert len(set(ids.values())) == 2

    repo.upsert_batch(
        [StockInfo(symbol=SYMBOLS[0], name="task9 renamed", market=Market.KR_KOSPI)],
        run_id=_run_id(),
    )
    renamed = repo.get_by_symbol(SYMBOLS[0], Market.KR_KOSPI)

    assert renamed[0] == ids[SYMBOLS[0]]
    assert renamed[2] == "task9 renamed"
    assert renamed[3] == Market.KR_KOSPI


def test_compute_deactivation_flags_probes_without_price_or_fs(stocks_cleanup):
    repo = StockRepository()
    repo.upsert_batch(_probe_stocks(), run_id=_run_id())

    changes, stats = repo.compute_deactivation("kr")
    flagged = changes[changes["symbol"].isin(SYMBOLS)]

    assert len(flagged) == 2
    assert not flagged["is_active"].any()
    assert stats["active_before"] >= 2
    assert stats["active_after"] == stats["active_before"] - stats["deactivated"] + stats[
        "reactivated"
    ]

    total, active = repo.count_by_activity([Market.KR_KOSPI, Market.KR_KOSDAQ])
    integrity = repo.get_integrity_stats(Market.KR_KOSPI)

    assert total >= active >= 2
    assert len(integrity) == 6
    assert integrity[5] >= 2
