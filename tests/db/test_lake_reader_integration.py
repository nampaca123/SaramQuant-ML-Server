import pytest

import app.db.lake_reader as lake_reader
from app.db.lake_reader import invalidate_metadata_cache, query_df, resolve_metadata_location, scan
from app.db.lake_schemas import TABLES

pytestmark = pytest.mark.integration


class CountingGlueClient:
    def __init__(self, inner):
        self.inner = inner
        self.calls = 0

    def get_table(self, **kwargs):
        self.calls += 1
        return self.inner.get_table(**kwargs)


@pytest.fixture(autouse=True)
def clean_cache():
    invalidate_metadata_cache()
    yield
    invalidate_metadata_cache()


def test_count_query_on_stocks_returns_dataframe():
    df = query_df(f"SELECT count(*) AS n FROM {scan('stocks')}")

    assert list(df.columns) == ["n"]
    assert len(df) == 1
    assert int(df["n"].iloc[0]) >= 0


def test_metadata_cache_avoids_second_glue_call(monkeypatch):
    counting = CountingGlueClient(lake_reader._get_glue_client())
    monkeypatch.setattr(lake_reader, "_glue_client", counting)

    first = resolve_metadata_location("stocks")
    second = resolve_metadata_location("stocks")

    assert first == second
    assert counting.calls == 1


def test_scan_resolves_metadata_for_all_tables():
    for name in TABLES:
        fragment = scan(name)
        assert fragment.startswith("iceberg_scan('s3://"), name
        assert fragment.endswith("')"), name
