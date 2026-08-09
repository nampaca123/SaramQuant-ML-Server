import pytest

import app.db.lake_reader as lake_reader
from app.db.lake_reader import invalidate_metadata_cache, resolve_metadata_location, scan


class StubGlueClient:
    def __init__(self):
        self.calls = []

    def get_table(self, DatabaseName, Name):
        self.calls.append((DatabaseName, Name))
        return {
            "Table": {
                "Parameters": {
                    "table_type": "ICEBERG",
                    "metadata_location": f"s3://bucket/warehouse/{Name}/metadata/{len(self.calls)}.json",
                }
            }
        }


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def advance(self, seconds):
        self.now += seconds

    def __call__(self):
        return self.now


@pytest.fixture
def glue(monkeypatch):
    client = StubGlueClient()
    monkeypatch.setattr(lake_reader, "_glue_client", client)
    invalidate_metadata_cache()
    yield client
    invalidate_metadata_cache()


@pytest.fixture
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr(lake_reader.time, "monotonic", fake)
    return fake


def test_resolve_metadata_location_returns_glue_parameter(glue):
    location = resolve_metadata_location("stocks")

    assert location == "s3://bucket/warehouse/stocks/metadata/1.json"
    assert glue.calls == [("saramquant", "stocks")]


def test_second_call_within_ttl_hits_cache(glue, clock):
    first = resolve_metadata_location("stocks")
    clock.advance(299)
    second = resolve_metadata_location("stocks")

    assert first == second
    assert len(glue.calls) == 1


def test_call_after_ttl_refetches_from_glue(glue, clock):
    resolve_metadata_location("stocks")
    clock.advance(301)
    second = resolve_metadata_location("stocks")

    assert second == "s3://bucket/warehouse/stocks/metadata/2.json"
    assert len(glue.calls) == 2


def test_cache_is_per_table(glue):
    resolve_metadata_location("stocks")
    resolve_metadata_location("daily_prices")
    resolve_metadata_location("stocks")

    assert glue.calls == [("saramquant", "stocks"), ("saramquant", "daily_prices")]


def test_invalidate_single_table_keeps_other_entries(glue):
    resolve_metadata_location("stocks")
    resolve_metadata_location("daily_prices")

    invalidate_metadata_cache("stocks")
    resolve_metadata_location("stocks")
    resolve_metadata_location("daily_prices")

    assert [name for _, name in glue.calls] == ["stocks", "daily_prices", "stocks"]


def test_invalidate_all_drops_every_entry(glue):
    resolve_metadata_location("stocks")
    resolve_metadata_location("daily_prices")

    invalidate_metadata_cache()
    resolve_metadata_location("stocks")
    resolve_metadata_location("daily_prices")

    assert len(glue.calls) == 4


def test_invalidate_unknown_table_is_noop(glue):
    invalidate_metadata_cache("stocks")

    assert glue.calls == []


def test_resolve_uses_configured_glue_database(glue, monkeypatch):
    monkeypatch.setenv("GLUE_DATABASE", "db-test")

    resolve_metadata_location("stocks")

    assert glue.calls == [("db-test", "stocks")]


def test_missing_metadata_location_raises(glue, monkeypatch):
    monkeypatch.setattr(glue, "get_table", lambda **_: {"Table": {"Parameters": {}}})

    with pytest.raises(KeyError):
        resolve_metadata_location("stocks")


def test_scan_wraps_metadata_location_in_iceberg_scan(glue):
    assert scan("stocks") == "iceberg_scan('s3://bucket/warehouse/stocks/metadata/1.json')"


class RecordingConnection:
    def __init__(self):
        self.statements = []

    def execute(self, sql, *args):
        self.statements.append(sql)


def test_load_extensions_installs_when_no_extension_dir(monkeypatch):
    monkeypatch.delenv("DUCKDB_EXTENSION_DIR", raising=False)
    connection = RecordingConnection()

    lake_reader.load_extensions(connection)

    assert connection.statements == [
        "INSTALL httpfs",
        "INSTALL iceberg",
        "LOAD httpfs",
        "LOAD iceberg",
    ]


def test_load_extensions_uses_baked_directory_without_installing(monkeypatch):
    monkeypatch.setenv("DUCKDB_EXTENSION_DIR", "/opt/duckdb-extensions")
    connection = RecordingConnection()

    lake_reader.load_extensions(connection)

    assert connection.statements == [
        "SET extension_directory='/opt/duckdb-extensions'",
        "SET autoinstall_known_extensions=false",
        "SET autoload_known_extensions=false",
        "LOAD httpfs",
        "LOAD iceberg",
    ]
    assert not any(statement.startswith("INSTALL") for statement in connection.statements)
