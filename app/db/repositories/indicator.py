"""stock_indicators 레포지토리 — DuckDB(Iceberg) 조회 + 시장 범위 한정 스냅샷 교체 쓰기. 공개 시그니처는 Postgres 판과 동일하다."""
import pandas as pd

from app.db import lake_reader, lake_writer
from app.db.repositories import lake_rows
from app.schema import Market

INDICATOR_COLUMNS = lake_rows.columns_of("stock_indicators")
COLUMNS = [column for column in INDICATOR_COLUMNS if column != "created_at"]
DECIMALS = lake_rows.decimal_columns("stock_indicators") + ("close",)


def markets_of(stock_ids) -> list[str]:
    """행에 담긴 stock_id들이 속한 시장을 찾아 교체 범위를 좁힌다."""
    ids = sorted({int(stock_id) for stock_id in stock_ids})
    if not ids:
        return []
    df = lake_rows.select(
        "stocks", "DISTINCT market", f"id IN ({', '.join('?' * len(ids))})", ids
    )
    return sorted(row.market for row in df.itertuples(index=False))


def replace_scope(stock_ids) -> str:
    markets = markets_of(stock_ids)
    if markets:
        return lake_rows.stock_market_predicate(markets)
    ids = sorted({int(stock_id) for stock_id in stock_ids})
    return f"stock_id IN ({', '.join(str(stock_id) for stock_id in ids)})"


class IndicatorRepository:
    def __init__(self, conn: object | None = None):
        self._conn = conn

    def delete_by_markets(self, markets: list[Market]) -> int:
        return lake_rows.delete_where(
            "stock_indicators",
            lake_rows.stock_market_predicate(markets),
            lake_rows.stock_market_scan_predicate(markets),
        )

    def insert_batch(
        self, rows: list[tuple], run_id: str | None = None,
        markets: list[Market] | None = None,
    ) -> int:
        if not rows:
            return 0
        payload = pd.DataFrame(list(rows), columns=COLUMNS).drop_duplicates(
            subset=["stock_id", "date"], keep="last"
        )
        payload["created_at"] = lake_rows.now_utc()
        scope = (
            lake_rows.stock_market_predicate(markets) if markets
            else replace_scope(payload["stock_id"])
        )
        return lake_writer.snapshot_replace(
            "stock_indicators", payload[INDICATOR_COLUMNS],
            lake_rows.resolve_run_id(run_id), delete_where=scope,
        )

    def get_latest_by_stock(self, stock_id: int) -> dict | None:
        sql = (
            "SELECT si.*, dp.close"
            f" FROM {lake_reader.scan('stock_indicators')} si"
            f" JOIN {lake_reader.scan('daily_prices')} dp"
            " ON dp.stock_id = si.stock_id AND dp.date = si.date"
            " WHERE si.stock_id = ? ORDER BY si.date DESC LIMIT 1"
        )
        df = lake_reader.query_df(sql, [int(stock_id)])
        return None if df.empty else lake_rows.to_dict(df.iloc[0], DECIMALS)

    def get_all_by_market(self, market: Market) -> dict[int, dict]:
        sql = (
            "SELECT si.*, dp.close, s.sector"
            f" FROM {lake_reader.scan('stock_indicators')} si"
            f" JOIN {lake_reader.scan('daily_prices')} dp"
            " ON dp.stock_id = si.stock_id AND dp.date = si.date"
            f" JOIN {lake_reader.scan('stocks')} s ON s.id = si.stock_id"
            " WHERE s.market = ? AND s.is_active"
        )
        df = lake_reader.query_df(sql, [market.value])
        rows = [lake_rows.to_dict(df.iloc[index], DECIMALS) for index in range(len(df))]
        return {int(row["stock_id"]): row for row in rows}
