from decimal import Decimal

from psycopg2.extensions import connection
from psycopg2.extras import execute_values

from app.schema import FinancialStatement, Market, ReportType


class FinancialStatementRepository:
    def __init__(self, conn: connection):
        self._conn = conn

    def upsert_batch(self, statements: list[FinancialStatement]) -> int:
        if not statements:
            return 0
        query = """
            INSERT INTO financial_statements
                (stock_id, fiscal_year, report_type, revenue, operating_income,
                 net_income, total_assets, total_liabilities, total_equity,
                 shares_outstanding)
            VALUES %s
            ON CONFLICT (stock_id, fiscal_year, report_type) DO UPDATE SET
                revenue = EXCLUDED.revenue,
                operating_income = EXCLUDED.operating_income,
                net_income = EXCLUDED.net_income,
                total_assets = EXCLUDED.total_assets,
                total_liabilities = EXCLUDED.total_liabilities,
                total_equity = EXCLUDED.total_equity,
                shares_outstanding = EXCLUDED.shares_outstanding
        """
        data = [
            (
                s.stock_id, s.fiscal_year, s.report_type.value,
                s.revenue, s.operating_income, s.net_income,
                s.total_assets, s.total_liabilities, s.total_equity,
                s.shares_outstanding,
            )
            for s in statements
        ]
        with self._conn.cursor() as cur:
            execute_values(cur, query, data)
            return len(data)

    def get_ttm_by_stock(self, stock_id: int) -> list[FinancialStatement]:
        query = """
            SELECT stock_id, fiscal_year, report_type,
                   revenue, operating_income, net_income,
                   total_assets, total_liabilities, total_equity,
                   shares_outstanding
            FROM financial_statements
            WHERE stock_id = %s
            ORDER BY fiscal_year DESC,
                     CASE report_type
                         WHEN 'FY' THEN 4 WHEN 'Q3' THEN 3
                         WHEN 'Q2' THEN 2 WHEN 'Q1' THEN 1
                     END DESC
            LIMIT 10
        """
        with self._conn.cursor() as cur:
            cur.execute(query, (stock_id,))
            return [self._row_to_dto(row) for row in cur.fetchall()]

    def get_ttm_by_market(
        self, market: Market
    ) -> dict[int, list[FinancialStatement]]:
        query = """
            SELECT stock_id, fiscal_year, report_type,
                   revenue, operating_income, net_income,
                   total_assets, total_liabilities, total_equity,
                   shares_outstanding
            FROM (
                SELECT fs.*,
                       MAX(fs.fiscal_year) OVER (PARTITION BY fs.stock_id) AS max_fy
                FROM financial_statements fs
                JOIN stocks s ON s.id = fs.stock_id
                WHERE s.market = %s AND s.is_active = true
            ) sub
            WHERE fiscal_year >= max_fy - 1
            ORDER BY stock_id,
                     fiscal_year DESC,
                     CASE report_type
                         WHEN 'FY' THEN 4 WHEN 'Q3' THEN 3
                         WHEN 'Q2' THEN 2 WHEN 'Q1' THEN 1
                     END DESC
        """
        result: dict[int, list[FinancialStatement]] = {}
        with self._conn.cursor() as cur:
            cur.execute(query, (market.value,))
            for row in cur.fetchall():
                dto = self._row_to_dto(row)
                result.setdefault(dto.stock_id, []).append(dto)

        for stock_id in result:
            result[stock_id] = result[stock_id][:10]

        return result

    @staticmethod
    def _row_to_dto(row: tuple) -> FinancialStatement:
        return FinancialStatement(
            stock_id=row[0],
            fiscal_year=row[1],
            report_type=ReportType(row[2]),
            revenue=row[3],
            operating_income=row[4],
            net_income=row[5],
            total_assets=row[6],
            total_liabilities=row[7],
            total_equity=row[8],
            shares_outstanding=row[9],
        )
