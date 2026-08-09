"""stock_fundamentals 레포지토리 — DuckDB(Iceberg) 조회 + Athena MERGE 쓰기. 공개 시그니처는 Postgres 판과 동일하다."""
import pandas as pd

from app.db import lake_reader, lake_writer
from app.db.repositories import lake_rows
from app.db.repositories.financial_statement import TTM_ORDER
from app.schema import Market

FUNDAMENTAL_COLUMNS = lake_rows.columns_of("stock_fundamentals")
COLUMNS = [column for column in FUNDAMENTAL_COLUMNS if column != "created_at"]
DECIMALS = lake_rows.decimal_columns("stock_fundamentals")
EXCLUDED_COVERAGE = "('NO_FS', 'INSUFFICIENT')"

_LATEST_SHARES = (
    "SELECT stock_id, shares_outstanding FROM {scan}"
    " QUALIFY row_number() OVER (PARTITION BY stock_id"
    " ORDER BY fiscal_year DESC, {order}) = 1"
)


class FundamentalRepository:
    def __init__(self, conn: object | None = None):
        self._conn = conn

    def upsert_batch(self, rows: list[tuple], run_id: str | None = None) -> int:
        if not rows:
            return 0
        payload = pd.DataFrame(list(rows), columns=COLUMNS).drop_duplicates(
            subset=["stock_id", "date"], keep="last"
        )
        payload["created_at"] = lake_rows.now_utc()
        return lake_writer.merge(
            "stock_fundamentals", payload[FUNDAMENTAL_COLUMNS], lake_rows.resolve_run_id(run_id)
        )

    def get_with_shares(self, stock_ids: list[int]) -> list[tuple]:
        """Returns [(stock_id, pbr, roe, operating_margin, debt_ratio, shares_outstanding)]."""
        if not stock_ids:
            return []
        ids = [int(stock_id) for stock_id in stock_ids]
        shares = _LATEST_SHARES.format(
            scan=lake_reader.scan("financial_statements"), order=TTM_ORDER
        )
        sql = (
            "SELECT f.stock_id, f.pbr, f.roe, f.operating_margin, f.debt_ratio,"
            " fs.shares_outstanding"
            f" FROM {lake_reader.scan('stock_fundamentals')} f"
            f" JOIN {lake_reader.scan('stocks')} s ON s.id = f.stock_id"
            f" LEFT JOIN ({shares}) fs ON fs.stock_id = f.stock_id"
            f" WHERE f.stock_id IN ({', '.join('?' * len(ids))})"
            f" AND f.data_coverage NOT IN {EXCLUDED_COVERAGE}"
        )
        return [
            (
                int(row.stock_id),
                lake_rows.to_decimal(row.pbr),
                lake_rows.to_decimal(row.roe),
                lake_rows.to_decimal(row.operating_margin),
                lake_rows.to_decimal(row.debt_ratio),
                lake_rows.to_int(row.shares_outstanding),
            )
            for row in lake_reader.query_df(sql, ids).itertuples(index=False)
        ]

    def get_latest_by_stock(self, stock_id: int) -> dict | None:
        sql = (
            "SELECT sf.*, s.sector, s.market"
            f" FROM {lake_reader.scan('stock_fundamentals')} sf"
            f" JOIN {lake_reader.scan('stocks')} s ON s.id = sf.stock_id"
            " WHERE sf.stock_id = ? ORDER BY sf.date DESC LIMIT 1"
        )
        df = lake_reader.query_df(sql, [int(stock_id)])
        return None if df.empty else lake_rows.to_dict(df.iloc[0], DECIMALS)

    def get_all_by_market(self, market: Market) -> dict[int, dict]:
        sql = (
            "SELECT sf.*, s.sector, s.market, s.symbol"
            f" FROM {lake_reader.scan('stock_fundamentals')} sf"
            f" JOIN {lake_reader.scan('stocks')} s ON s.id = sf.stock_id"
            " WHERE s.market = ? AND s.is_active"
        )
        df = lake_reader.query_df(sql, [market.value])
        rows = [lake_rows.to_dict(df.iloc[index], DECIMALS) for index in range(len(df))]
        return {int(row["stock_id"]): row for row in rows}
