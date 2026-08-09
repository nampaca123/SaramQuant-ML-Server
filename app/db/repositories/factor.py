"""factor_* / sector_aggregates 레포지토리 — DuckDB(Iceberg) 조회 + Athena MERGE 쓰기. 공개 시그니처는 Postgres 판과 동일하다."""
import json
from datetime import date

import pandas as pd

from app.db import lake_reader, lake_writer
from app.db.repositories import lake_rows
from app.schema import Market

EXPOSURE_COLUMNS = lake_rows.columns_of("factor_exposures")
RETURN_COLUMNS = lake_rows.columns_of("factor_returns")
COVARIANCE_COLUMNS = lake_rows.columns_of("factor_covariance")
SECTOR_COLUMNS = lake_rows.columns_of("sector_aggregates")
SECTOR_DECIMALS = lake_rows.decimal_columns("sector_aggregates")
NO_SECTOR = "N/A"
EXCLUDED_COVERAGE = "('NO_FS', 'INSUFFICIENT')"

_MEDIAN_COLUMNS = (
    "median(f.per) AS median_per, median(f.pbr) AS median_pbr,"
    " median(f.roe) AS median_roe,"
    " median(f.operating_margin) AS {op_alias},"
    " median(f.debt_ratio) AS median_debt_ratio"
)
_AGGREGATE_DECIMALS = (
    "median_per", "median_pbr", "median_roe", "median_operating_margin", "median_debt_ratio",
)


def _latest_exposure_date() -> str:
    return (
        f"SELECT max(fe2.date) FROM {lake_reader.scan('factor_exposures')} fe2"
        f" JOIN {lake_reader.scan('stocks')} s2 ON s2.id = fe2.stock_id"
        " WHERE s2.market = ?"
    )


def _fundamental_source() -> str:
    return (
        f" FROM {lake_reader.scan('stocks')} s"
        f" JOIN {lake_reader.scan('stock_fundamentals')} f ON s.id = f.stock_id"
    )


def _merge(table: str, rows: list[tuple], columns: list[str], keys: list[str],
           run_id: str | None) -> int:
    payload = pd.DataFrame(list(rows), columns=columns).drop_duplicates(
        subset=keys, keep="last"
    )
    return lake_writer.merge(table, payload[columns], lake_rows.resolve_run_id(run_id))


class FactorRepository:
    def __init__(self, conn: object | None = None):
        self._conn = conn

    # ── factor_exposures ──

    def upsert_exposures(self, rows: list[tuple], run_id: str | None = None) -> int:
        """rows: [(stock_id, date, size_z, value_z, momentum_z, volatility_z, quality_z, leverage_z)]"""
        if not rows:
            return 0
        return _merge(
            "factor_exposures", rows, EXPOSURE_COLUMNS, ["stock_id", "date"], run_id
        )

    def get_latest_exposures(self, market: Market) -> list[tuple]:
        """Returns [(stock_id, size_z, value_z, momentum_z, volatility_z, quality_z, leverage_z)]"""
        styles = [column for column in EXPOSURE_COLUMNS if column.endswith("_z")]
        sql = (
            f"SELECT fe.stock_id, {', '.join('fe.' + column for column in styles)}"
            f" FROM {lake_reader.scan('factor_exposures')} fe"
            f" JOIN {lake_reader.scan('stocks')} s ON s.id = fe.stock_id"
            f" WHERE fe.date = ({_latest_exposure_date()})"
            " AND s.market = ? AND s.is_active"
        )
        df = lake_reader.query_df(sql, [market.value, market.value])
        return [
            (int(row[0]), *(lake_rows.to_decimal(value) for value in row[1:]))
            for row in df.itertuples(index=False)
        ]

    # ── factor_returns ──

    def upsert_factor_returns(self, rows: list[tuple], run_id: str | None = None) -> int:
        """rows: [(market, date, factor_name, return_value)]"""
        if not rows:
            return 0
        return _merge(
            "factor_returns", rows, RETURN_COLUMNS, ["market", "date", "factor_name"], run_id
        )

    def get_factor_returns_history(self, market: Market, limit: int = 252) -> list[tuple]:
        """Returns [(date, factor_name, return_value)] ordered by date ASC."""
        scan = lake_reader.scan("factor_returns")
        sql = (
            f"WITH ranked_dates AS (SELECT DISTINCT date FROM {scan}"
            f" WHERE market = ? ORDER BY date DESC LIMIT {int(limit)})"
            f" SELECT date, factor_name, return_value FROM {scan}"
            " WHERE market = ? AND date >= (SELECT min(date) FROM ranked_dates)"
            " ORDER BY date ASC"
        )
        df = lake_reader.query_df(sql, [market.value, market.value])
        return [
            (lake_rows.to_date(row.date), row.factor_name, lake_rows.to_decimal(row.return_value))
            for row in df.itertuples(index=False)
        ]

    def count_factor_return_dates(self, market: Market) -> int:
        df = lake_rows.select(
            "factor_returns", "count(DISTINCT date) AS n", "market = ?", [market.value]
        )
        return int(lake_rows.scalar(df, "n") or 0)

    # ── factor_covariance ──

    def upsert_covariance(
        self, market: Market, dt: date, matrix: list[list[float]], run_id: str | None = None
    ) -> None:
        _merge(
            "factor_covariance",
            [(market.value, dt, json.dumps(matrix))],
            COVARIANCE_COLUMNS,
            ["market", "date"],
            run_id,
        )

    def get_latest_covariance(self, market: Market) -> tuple[date, list] | None:
        df = lake_rows.select(
            "factor_covariance", "date, matrix", "market = ?", [market.value],
            " ORDER BY date DESC LIMIT 1",
        )
        if df.empty:
            return None
        row = df.iloc[0]
        return lake_rows.to_date(row["date"]), json.loads(row["matrix"])

    # ── risk badge helpers ──

    def get_volatility_z_by_stock(self, stock_id: int, market: Market) -> float | None:
        sql = (
            f"SELECT volatility_z FROM {lake_reader.scan('factor_exposures')}"
            f" WHERE stock_id = ? AND date = ({_latest_exposure_date()})"
        )
        df = lake_reader.query_df(sql, [int(stock_id), market.value])
        return lake_rows.to_float(lake_rows.scalar(df, "volatility_z"))

    def get_all_exposures_by_market(self, market: Market) -> dict[int, float]:
        """Returns {stock_id: volatility_z} for the latest date."""
        sql = (
            "SELECT fe.stock_id, fe.volatility_z"
            f" FROM {lake_reader.scan('factor_exposures')} fe"
            f" JOIN {lake_reader.scan('stocks')} s ON s.id = fe.stock_id"
            " WHERE s.market = ? AND s.is_active"
            f" AND fe.date = ({_latest_exposure_date()})"
        )
        df = lake_reader.query_df(sql, [market.value, market.value])
        return {
            int(row.stock_id): lake_rows.to_float(row.volatility_z)
            for row in df.itertuples(index=False)
        }

    def get_all_sector_aggregates(self, market: Market) -> dict[str, dict]:
        """Returns {sector: {stock_count, median_per, ...}} for the latest date."""
        scan = lake_reader.scan("sector_aggregates")
        sql = (
            f"SELECT * FROM {scan} WHERE market = ?"
            f" AND date = (SELECT max(date) FROM {scan} WHERE market = ?)"
        )
        df = lake_reader.query_df(sql, [market.value, market.value])
        rows = [lake_rows.to_dict(df.iloc[index], SECTOR_DECIMALS) for index in range(len(df))]
        return {row["sector"]: row for row in rows}

    def get_sector_aggregate_single(self, market: Market, sector: str) -> dict | None:
        df = lake_rows.select(
            "sector_aggregates", "*", "market = ? AND sector = ?", [market.value, sector],
            " ORDER BY date DESC LIMIT 1",
        )
        return None if df.empty else lake_rows.to_dict(df.iloc[0], SECTOR_DECIMALS)

    def get_market_aggregate(self, market: Market) -> dict | None:
        """Market-wide medians computed via SQL aggregation."""
        medians = _MEDIAN_COLUMNS.format(op_alias="median_operating_margin")
        sql = (
            f"SELECT count(*) AS stock_count, {medians}{_fundamental_source()}"
            " WHERE s.market = ? AND s.is_active"
            f" AND f.data_coverage NOT IN {EXCLUDED_COVERAGE}"
        )
        df = lake_reader.query_df(sql, [market.value])
        if df.empty or int(df.iloc[0]["stock_count"]) == 0:
            return None
        return lake_rows.to_dict(df.iloc[0], _AGGREGATE_DECIMALS)

    # ── sector_aggregates ──

    def get_sector_aggregates(self, markets: list[Market]) -> list[tuple]:
        values = [market.value for market in markets]
        medians = _MEDIAN_COLUMNS.format(op_alias="median_op_margin")
        sql = (
            f"SELECT s.market, s.sector, count(*) AS stock_count, {medians}"
            f"{_fundamental_source()}"
            " WHERE s.is_active AND s.sector IS NOT NULL AND s.sector <> ?"
            f" AND s.market IN ({', '.join('?' * len(values))})"
            f" AND f.data_coverage NOT IN {EXCLUDED_COVERAGE}"
            " GROUP BY s.market, s.sector"
        )
        df = lake_reader.query_df(sql, [NO_SECTOR, *values])
        return [
            (row[0], row[1], int(row[2]), *(lake_rows.to_decimal(value) for value in row[3:]))
            for row in df.itertuples(index=False)
        ]

    def upsert_sector_aggregates(self, rows: list[tuple], run_id: str | None = None) -> int:
        if not rows:
            return 0
        return _merge(
            "sector_aggregates", rows, SECTOR_COLUMNS, ["market", "sector", "date"], run_id
        )
