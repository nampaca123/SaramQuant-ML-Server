"""financial_statements 레포지토리 — DuckDB(Iceberg) 조회 + Athena MERGE 쓰기. 공개 시그니처는 Postgres 판과 동일하다."""
import pandas as pd

from app.db import lake_reader, lake_writer
from app.db.repositories import lake_rows
from app.db.repositories.daily_price import to_market_group
from app.schema import FinancialStatement, Market, ReportType

STATEMENT_COLUMNS = lake_rows.columns_of("financial_statements")
MERGE_KEYS = ["stock_id", "fiscal_year", "report_type"]
TTM_ORDER = (
    "CASE report_type WHEN 'FY' THEN 4 WHEN 'Q3' THEN 3"
    " WHEN 'Q2' THEN 2 WHEN 'Q1' THEN 1 END DESC"
)
TTM_LIMIT = 10

_VALUE_COLUMNS = (
    "revenue", "operating_income", "net_income",
    "total_assets", "total_liabilities", "total_equity",
)
_READ_COLUMNS = ["stock_id", "fiscal_year", "report_type", *_VALUE_COLUMNS, "shares_outstanding"]


def _lookup_market_groups(stock_ids: list[int]) -> dict[int, str]:
    df = lake_rows.select(
        "stocks", "id, market", f"id IN ({', '.join('?' * len(stock_ids))})", stock_ids
    )
    return {int(row.id): to_market_group(row.market) for row in df.itertuples(index=False)}


def _to_dto(row) -> FinancialStatement:
    return FinancialStatement(
        stock_id=int(row.stock_id),
        fiscal_year=int(row.fiscal_year),
        report_type=ReportType(row.report_type),
        revenue=lake_rows.to_decimal(row.revenue),
        operating_income=lake_rows.to_decimal(row.operating_income),
        net_income=lake_rows.to_decimal(row.net_income),
        total_assets=lake_rows.to_decimal(row.total_assets),
        total_liabilities=lake_rows.to_decimal(row.total_liabilities),
        total_equity=lake_rows.to_decimal(row.total_equity),
        shares_outstanding=lake_rows.to_int(row.shares_outstanding),
    )


class FinancialStatementRepository:
    def __init__(self, conn: object | None = None):
        self._conn = conn

    def upsert_batch(
        self, statements: list[FinancialStatement], run_id: str | None = None
    ) -> int:
        if not statements:
            return 0
        rows = pd.DataFrame(
            [
                {
                    "stock_id": int(s.stock_id), "fiscal_year": int(s.fiscal_year),
                    "report_type": s.report_type.value, "revenue": s.revenue,
                    "operating_income": s.operating_income, "net_income": s.net_income,
                    "total_assets": s.total_assets, "total_liabilities": s.total_liabilities,
                    "total_equity": s.total_equity, "shares_outstanding": s.shares_outstanding,
                }
                for s in statements
            ]
        )
        unique = rows.drop_duplicates(subset=MERGE_KEYS, keep="last")
        groups = _lookup_market_groups(sorted({int(sid) for sid in unique["stock_id"]}))
        unique = unique.assign(
            market=[groups.get(int(sid)) for sid in unique["stock_id"]]
        )
        payload = unique[unique["market"].notna()].reset_index(drop=True)
        if payload.empty:
            return 0
        payload["created_at"] = lake_rows.now_utc()
        return lake_writer.merge(
            "financial_statements", payload[STATEMENT_COLUMNS], lake_rows.resolve_run_id(run_id)
        )

    def get_ttm_by_stock(self, stock_id: int) -> list[FinancialStatement]:
        sql = (
            f"SELECT {', '.join(_READ_COLUMNS)}"
            f" FROM {lake_reader.scan('financial_statements')}"
            " WHERE stock_id = ?"
            f" ORDER BY fiscal_year DESC, {TTM_ORDER} LIMIT {TTM_LIMIT}"
        )
        df = lake_reader.query_df(sql, [int(stock_id)])
        return [_to_dto(row) for row in df.itertuples(index=False)]

    def get_ttm_by_market(self, market: Market) -> dict[int, list[FinancialStatement]]:
        sql = (
            f"SELECT {', '.join('f.' + column for column in _READ_COLUMNS)}"
            f" FROM {lake_reader.scan('financial_statements')} f"
            f" JOIN {lake_reader.scan('stocks')} s ON s.id = f.stock_id"
            " WHERE s.market = ? AND s.is_active"
            " QUALIFY f.fiscal_year >= max(f.fiscal_year) OVER (PARTITION BY f.stock_id) - 1"
            f" ORDER BY f.stock_id, f.fiscal_year DESC, {TTM_ORDER}"
        )
        result: dict[int, list[FinancialStatement]] = {}
        for row in lake_reader.query_df(sql, [market.value]).itertuples(index=False):
            bucket = result.setdefault(int(row.stock_id), [])
            if len(bucket) < TTM_LIMIT:
                bucket.append(_to_dto(row))
        return result
