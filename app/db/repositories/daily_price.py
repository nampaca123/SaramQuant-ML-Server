"""daily_prices 레포지토리 — DuckDB(Iceberg) 조회 + Athena MERGE 쓰기. 공개 시그니처는 Postgres 판과 동일하다."""
from datetime import date

import pandas as pd

from app.db import lake_reader, lake_writer
from app.db.repositories import lake_rows
from app.schema import DailyPrice, Market

PRICE_COLUMNS = lake_rows.columns_of("daily_prices")
ROW_COLUMNS = ["stock_id", "date", "open", "high", "low", "close", "volume"]
_MARKET_GROUPS = ("KR", "US")


def to_market_group(market) -> str:
    """KR_KOSPI/US_NYSE 같은 시장 코드를 daily_prices 파티션 값(KR|US)으로 줄인다."""
    value = str(getattr(market, "value", market))
    group = value.split("_")[0].upper()
    if group not in _MARKET_GROUPS:
        raise ValueError(f"unknown market for daily_prices partition: {value}")
    return group


def attach_market(rows: pd.DataFrame, groups: dict[int, str]) -> pd.DataFrame:
    payload = rows.copy()
    payload["market"] = [groups.get(int(stock_id)) for stock_id in payload["stock_id"]]
    return payload[payload["market"].notna()].reset_index(drop=True)


def _lookup_market_groups(stock_ids: list[int]) -> dict[int, str]:
    df = lake_rows.select(
        "stocks", "id, market", f"id IN ({', '.join('?' * len(stock_ids))})", stock_ids
    )
    return {int(row.id): to_market_group(row.market) for row in df.itertuples(index=False)}


def _write(rows: pd.DataFrame, run_id: str | None) -> int:
    if rows.empty:
        return 0
    unique = rows.drop_duplicates(subset=["stock_id", "date"], keep="last")
    stock_ids = sorted({int(stock_id) for stock_id in unique["stock_id"]})
    payload = attach_market(unique, _lookup_market_groups(stock_ids))
    if payload.empty:
        return 0
    payload["created_at"] = lake_rows.now_utc()
    return lake_writer.merge(
        "daily_prices", payload[PRICE_COLUMNS], lake_rows.resolve_run_id(run_id)
    )


def _to_row(row) -> tuple:
    return (
        lake_rows.to_date(row.date),
        lake_rows.to_decimal(row.open),
        lake_rows.to_decimal(row.high),
        lake_rows.to_decimal(row.low),
        lake_rows.to_decimal(row.close),
        int(row.volume),
    )


class DailyPriceRepository:
    def __init__(self, conn: object | None = None):
        self._conn = conn

    def upsert_batch(
        self, stock_id: int, prices: list[DailyPrice], run_id: str | None = None
    ) -> int:
        if not prices:
            return 0
        rows = pd.DataFrame(
            [
                {
                    "stock_id": int(stock_id), "date": p.date, "open": p.open, "high": p.high,
                    "low": p.low, "close": p.close, "volume": p.volume,
                }
                for p in prices
            ]
        )
        return _write(rows, run_id)

    def bulk_upsert(self, rows: list[tuple], run_id: str | None = None) -> int:
        if not rows:
            return 0
        return _write(pd.DataFrame(list(rows), columns=ROW_COLUMNS), run_id)

    def get_latest_date(self, stock_id: int) -> date | None:
        return lake_rows.max_date("daily_prices", "stock_id = ?", [int(stock_id)])

    def get_latest_date_by_market(self, market: Market) -> date | None:
        sql = (
            f"SELECT max(p.date) AS latest FROM {lake_reader.scan('daily_prices')} p"
            f" JOIN {lake_reader.scan('stocks')} s ON p.stock_id = s.id"
            " WHERE p.market = ? AND s.market = ? AND s.is_active"
        )
        df = lake_reader.query_df(sql, [to_market_group(market), market.value])
        return lake_rows.to_date(lake_rows.scalar(df, "latest"))

    def get_prices(
        self,
        stock_id: int,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int | None = None
    ) -> list[DailyPrice]:
        conditions, params = lake_rows.date_filters(start_date, end_date, "p.date")
        conditions.insert(0, "p.stock_id = ?")
        params.insert(0, int(stock_id))
        sql = (
            "SELECT s.symbol, p.date, p.open, p.high, p.low, p.close, p.volume"
            f" FROM {lake_reader.scan('daily_prices')} p"
            f" JOIN {lake_reader.scan('stocks')} s ON p.stock_id = s.id"
            f" WHERE {' AND '.join(conditions)} ORDER BY p.date DESC"
            f"{lake_rows.limit_clause(limit)}"
        )
        df = lake_reader.query_df(sql, params)
        return [
            DailyPrice(row.symbol, *_to_row(row)) for row in df.itertuples(index=False)
        ]

    def get_prices_by_market(
        self, market: Market, limit_per_stock: int = 300
    ) -> dict[int, list[tuple]]:
        sql = (
            "SELECT p.stock_id, p.date, p.open, p.high, p.low, p.close, p.volume"
            f" FROM {lake_reader.scan('daily_prices')} p"
            f" JOIN {lake_reader.scan('stocks')} s ON p.stock_id = s.id"
            " WHERE p.market = ? AND s.market = ? AND s.is_active"
            " QUALIFY row_number() OVER (PARTITION BY p.stock_id ORDER BY p.date DESC) <= ?"
            " ORDER BY p.stock_id, p.date"
        )
        params = [to_market_group(market), market.value, int(limit_per_stock)]
        result: dict[int, list[tuple]] = {}
        for row in lake_reader.query_df(sql, params).itertuples(index=False):
            result.setdefault(int(row.stock_id), []).append(_to_row(row))
        return result

    def get_close_prices_batch(
        self, stock_ids: list[int], limit: int = 252
    ) -> dict[int, dict]:
        if not stock_ids:
            return {}
        ids = [int(stock_id) for stock_id in stock_ids]
        sql = (
            f"SELECT stock_id, date, close FROM {lake_reader.scan('daily_prices')}"
            f" WHERE stock_id IN ({', '.join('?' * len(ids))})"
            " QUALIFY row_number() OVER (PARTITION BY stock_id ORDER BY date DESC) <= ?"
        )
        result: dict[int, dict] = {}
        for row in lake_reader.query_df(sql, ids + [int(limit)]).itertuples(index=False):
            result.setdefault(int(row.stock_id), {})[lake_rows.to_date(row.date)] = float(
                row.close
            )
        return result

    # ── Delete operations ──

    def delete_all(self) -> int:
        return lake_rows.delete_where("daily_prices")

    def delete_by_stock(self, stock_id: int) -> int:
        return lake_rows.delete_where("daily_prices", f"stock_id = {int(stock_id)}")

    def delete_by_market(self, market: Market) -> int:
        ids = lake_rows.select("stocks", "id", "market = ?", [market.value])
        if ids.empty:
            return 0
        listed = ", ".join(str(int(stock_id)) for stock_id in ids["id"])
        return lake_rows.delete_where("daily_prices", f"stock_id IN ({listed})")

    def delete_before(self, cutoff: date) -> int:
        return lake_rows.delete_where("daily_prices", f"date < DATE '{cutoff.isoformat()}'")
