"""stocks 레포지토리 — DuckDB(Iceberg) 조회 + Athena MERGE 쓰기. 공개 시그니처는 Postgres 판과 동일하다."""
import os
from datetime import datetime, timezone
from uuid import uuid4

import pandas as pd

from app.db import lake_reader, lake_writer
from app.db.repositories.stock_deactivation import (
    NO_SECTOR,
    STOCK_COLUMNS,
    compute_deactivation,
)
from app.schema import Market, StockInfo


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _resolve_run_id(run_id: str | None) -> str:
    return run_id or os.environ.get("RUN_ID") or uuid4().hex[:12]


def _optional(value):
    return None if pd.isna(value) else value


def _select(where: str = "", params: list | None = None, suffix: str = "") -> pd.DataFrame:
    columns = ", ".join(f"s.{column}" for column in STOCK_COLUMNS)
    sql = f"SELECT {columns} FROM {lake_reader.scan('stocks')} s"
    if where:
        sql += f" WHERE {where}"
    return lake_reader.query_df(sql + suffix, params)


def _write(rows: pd.DataFrame, run_id: str | None) -> int:
    if rows.empty:
        return 0
    payload = rows[STOCK_COLUMNS].copy()
    for column in ("created_at", "updated_at"):
        payload[column] = pd.to_datetime(payload[column], utc=True)
    return lake_writer.merge("stocks", payload, _resolve_run_id(run_id))


def assign_stock_ids(
    incoming: pd.DataFrame, existing: pd.DataFrame, now: datetime
) -> pd.DataFrame:
    """단일 라이터 가정 하에 신규 (symbol, market)에만 max(id)+1부터 순차 채번한다."""
    known = {(row["symbol"], row["market"]): row for row in existing.to_dict("records")}
    next_id = int(existing["id"].max()) + 1 if not existing.empty else 1

    rows = []
    for item in incoming.drop_duplicates(subset=["symbol", "market"], keep="last").to_dict(
        "records"
    ):
        current = known.get((item["symbol"], item["market"]))
        if current is None:
            rows.append(
                {
                    "id": next_id,
                    "symbol": item["symbol"],
                    "name": item["name"],
                    "market": item["market"],
                    "is_active": True,
                    "dart_corp_code": None,
                    "sector": None,
                    "created_at": now,
                    "updated_at": now,
                }
            )
            next_id += 1
        else:
            rows.append({**current, "name": item["name"], "updated_at": now})
    return pd.DataFrame(rows, columns=STOCK_COLUMNS)


class StockRepository:
    def __init__(self, conn: object | None = None):
        self._conn = conn

    def find_by_id(self, stock_id: int) -> dict | None:
        df = _select("s.id = ?", [int(stock_id)])
        if df.empty:
            return None
        return self._to_info(df.iloc[0])

    def find_by_ids(self, stock_ids: list[int]) -> dict[int, dict]:
        if not stock_ids:
            return {}
        ids = [int(stock_id) for stock_id in stock_ids]
        df = _select(f"s.id IN ({', '.join('?' * len(ids))})", ids)
        infos = [self._to_info(df.iloc[index]) for index in range(len(df))]
        return {info["id"]: info for info in infos}

    @staticmethod
    def _to_info(row) -> dict:
        return {
            "id": int(row["id"]),
            "symbol": row["symbol"],
            "name": row["name"],
            "market": row["market"],
            "sector": _optional(row["sector"]),
        }

    def get_by_symbol(
        self, symbol: str, market: Market | None = None
    ) -> tuple[int, str, str, Market] | None:
        where, params = "s.symbol = ? AND s.is_active", [symbol]
        if market:
            where += " AND s.market = ?"
            params.append(market.value)
        df = _select(where, params)
        if df.empty:
            return None
        row = df.iloc[0]
        return (int(row["id"]), row["symbol"], row["name"], Market(row["market"]))

    def get_list(
        self, market: Market | None = None, limit: int = 100, offset: int = 0
    ) -> list[tuple[int, str, str, Market]]:
        where, params = "s.is_active", []
        if market:
            where += " AND s.market = ?"
            params.append(market.value)
        suffix = f" ORDER BY s.symbol LIMIT {int(limit)} OFFSET {int(offset)}"
        df = _select(where, params, suffix)
        return [
            (int(row.id), row.symbol, row.name, Market(row.market))
            for row in df.itertuples(index=False)
        ]

    def upsert_batch(self, stocks: list[StockInfo], run_id: str | None = None) -> int:
        if not stocks:
            return 0
        incoming = pd.DataFrame(
            [{"symbol": s.symbol, "name": s.name, "market": s.market.value} for s in stocks]
        )
        return _write(assign_stock_ids(incoming, _select(), _now()), run_id)

    def get_active_stocks(self, market: Market | None = None) -> list[tuple[int, str, Market]]:
        where, params = "s.is_active", []
        if market:
            where += " AND s.market = ?"
            params.append(market.value)
        df = _select(where, params)
        return [
            (int(row.id), row.symbol, Market(row.market))
            for row in df.itertuples(index=False)
        ]

    def get_stocks_without_sector(self, market: Market) -> list[tuple[int, str]]:
        df = _select(
            "s.sector IS NULL AND s.is_active AND s.market = ?", [market.value]
        )
        return [(int(row.id), row.symbol) for row in df.itertuples(index=False)]

    def _update_column(
        self, updates: list[tuple[str, str, str]], column: str, run_id: str | None
    ) -> int:
        if not updates:
            return 0
        wanted = {(symbol, market): value for symbol, market, value in updates}
        current = _select()
        if current.empty:
            return 0
        mask = pd.Series(
            [key in wanted for key in zip(current["symbol"], current["market"])],
            index=current.index,
            dtype=bool,
        )
        matched = current[mask].copy()
        if matched.empty:
            return 0
        matched[column] = [
            wanted[key] for key in zip(matched["symbol"], matched["market"])
        ]
        matched["updated_at"] = _now()
        return _write(matched, run_id)

    def update_sectors(
        self, updates: list[tuple[str, str, str]], run_id: str | None = None
    ) -> int:
        return self._update_column(updates, "sector", run_id)

    def update_dart_corp_codes(
        self, updates: list[tuple[str, str, str]], run_id: str | None = None
    ) -> int:
        """updates: [(symbol, market, dart_corp_code)]"""
        return self._update_column(updates, "dart_corp_code", run_id)

    def get_stocks_with_corp_code(self, markets: list[Market]) -> list[tuple[int, str, str]]:
        """Returns [(id, symbol, dart_corp_code)] for active stocks that have a DART corp code."""
        values = [market.value for market in markets]
        df = _select(
            "s.is_active AND s.dart_corp_code IS NOT NULL"
            f" AND s.market IN ({', '.join('?' * len(values))})",
            values,
        )
        return [
            (int(row.id), row.symbol, row.dart_corp_code) for row in df.itertuples(index=False)
        ]

    def _set_active(self, rows: pd.DataFrame, is_active: bool, run_id: str | None) -> int:
        if rows.empty:
            return 0
        updated = rows.copy()
        updated["is_active"] = is_active
        updated["updated_at"] = _now()
        return _write(updated, run_id)

    def deactivate_no_price_stocks(self, market: Market, run_id: str | None = None) -> int:
        rows = _select(
            "s.market = ? AND s.is_active AND NOT EXISTS"
            f" (SELECT 1 FROM {lake_reader.scan('daily_prices')} p WHERE p.stock_id = s.id)",
            [market.value],
        )
        return self._set_active(rows, False, run_id)

    def deactivate_no_sector_stocks(self, market: Market, run_id: str | None = None) -> int:
        rows = _select(
            "s.market = ? AND s.is_active AND (s.sector IS NULL OR s.sector = ?)",
            [market.value, NO_SECTOR],
        )
        return self._set_active(rows, False, run_id)

    def deactivate_no_fs_stocks(self, market: Market, run_id: str | None = None) -> int:
        rows = _select(
            "s.market = ? AND s.is_active AND NOT EXISTS"
            f" (SELECT 1 FROM {lake_reader.scan('financial_statements')} f"
            " WHERE f.stock_id = s.id)",
            [market.value],
        )
        return self._set_active(rows, False, run_id)

    def deactivate_unlisted(
        self, market: Market, active_symbols: set[str], run_id: str | None = None
    ) -> int:
        if not active_symbols:
            return 0
        rows = _select("s.market = ? AND s.is_active", [market.value])
        if rows.empty:
            return 0
        return self._set_active(rows[~rows["symbol"].isin(active_symbols)], False, run_id)

    def reactivate_listed_stocks(
        self, market: Market, active_symbols: set[str], run_id: str | None = None
    ) -> int:
        if not active_symbols:
            return 0
        rows = _select("s.market = ? AND NOT s.is_active", [market.value])
        if rows.empty:
            return 0
        return self._set_active(rows[rows["symbol"].isin(active_symbols)], True, run_id)

    def get_integrity_stats(self, market: Market) -> tuple:
        sql = (
            "SELECT COUNT(*) FILTER (WHERE s.is_active) AS active_total,"
            " COUNT(*) FILTER (WHERE s.is_active AND s.sector IS NOT NULL"
            " AND s.sector <> 'N/A') AS has_sector,"
            " COUNT(*) FILTER (WHERE s.is_active AND s.sector IS NULL) AS sector_null,"
            " COUNT(*) FILTER (WHERE s.is_active AND s.sector = 'N/A') AS sector_na,"
            " COUNT(*) FILTER (WHERE s.is_active AND s.id IN"
            f" (SELECT stock_id FROM {lake_reader.scan('stock_fundamentals')}"
            " WHERE data_coverage IN ('NO_FS', 'INSUFFICIENT'))) AS no_fs,"
            " COUNT(*) FILTER (WHERE s.is_active AND NOT EXISTS"
            f" (SELECT 1 FROM {lake_reader.scan('daily_prices')} p"
            " WHERE p.stock_id = s.id)) AS no_price"
            f" FROM {lake_reader.scan('stocks')} s WHERE s.market = ?"
        )
        row = lake_reader.query_df(sql, [market.value]).iloc[0]
        return tuple(int(value) for value in row)

    def get_eligible_for_factors(self, market: Market) -> list[tuple]:
        """Returns [(id, symbol, sector)] for quant-eligible stocks."""
        df = _select(
            "s.market = ? AND s.is_active AND s.sector IS NOT NULL AND s.sector <> ?",
            [market.value, NO_SECTOR],
        )
        return [(int(row.id), row.symbol, row.sector) for row in df.itertuples(index=False)]

    def get_sectors_by_market(self, market: Market) -> dict[int, str]:
        df = _select("s.market = ? AND s.is_active", [market.value])
        return {int(row.id): _optional(row.sector) for row in df.itertuples(index=False)}

    def count_by_activity(self, markets: list[Market]) -> tuple[int, int]:
        """Returns (total, active) counts for given markets."""
        values = [market.value for market in markets]
        sql = (
            "SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE s.is_active) AS active"
            f" FROM {lake_reader.scan('stocks')} s"
            f" WHERE s.market IN ({', '.join('?' * len(values))})"
        )
        row = lake_reader.query_df(sql, values).iloc[0]
        return int(row["total"]), int(row["active"])

    def compute_deactivation(
        self, market_group: str, active_symbols: dict | None = None
    ) -> tuple[pd.DataFrame, dict[str, int]]:
        return compute_deactivation(market_group, active_symbols)
