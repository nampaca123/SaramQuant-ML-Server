"""stock_indicators 레포지토리 — DuckDB(Iceberg) 조회 + 스냅샷 전체 교체 쓰기. 공개 시그니처는 Postgres 판과 동일하다."""
import pandas as pd

from app.db import lake_reader, lake_writer
from app.db.repositories import lake_rows
from app.schema import Market

INDICATOR_COLUMNS = lake_rows.columns_of("stock_indicators")
COLUMNS = [column for column in INDICATOR_COLUMNS if column != "created_at"]
DECIMALS = lake_rows.decimal_columns("stock_indicators") + ("close",)


class IndicatorRepository:
    def __init__(self, conn: object | None = None):
        self._conn = conn

    def delete_by_markets(self, markets: list[Market]) -> int:
        """스냅샷 테이블이라 insert_batch의 전체 교체가 삭제를 대신한다."""
        return 0

    def insert_batch(self, rows: list[tuple], run_id: str | None = None) -> int:
        if not rows:
            return 0
        payload = pd.DataFrame(list(rows), columns=COLUMNS).drop_duplicates(
            subset=["stock_id", "date"], keep="last"
        )
        payload["created_at"] = lake_rows.now_utc()
        return lake_writer.snapshot_replace(
            "stock_indicators", payload[INDICATOR_COLUMNS], lake_rows.resolve_run_id(run_id)
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
