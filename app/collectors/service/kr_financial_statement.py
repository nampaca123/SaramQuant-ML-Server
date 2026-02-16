import os
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from decimal import Decimal, InvalidOperation

from pykrx import stock as pykrx_stock

from app.schema import FinancialStatement, Market, ReportType
from app.db import get_connection, StockRepository
from app.db.repositories.financial_statement import FinancialStatementRepository
from app.collectors.clients import DartClient
from app.collectors.clients.dart import REPORT_CODES, MULTI_BATCH_SIZE

logger = logging.getLogger(__name__)

TARGET_MARKETS = [Market.KR_KOSPI, Market.KR_KOSDAQ]

ACCOUNT_MAP = {
    "매출액": "revenue",
    "영업이익": "operating_income",
    "당기순이익": "net_income",
    "당기순이익(손실)": "net_income",
    "자산총계": "total_assets",
    "부채총계": "total_liabilities",
    "자본총계": "total_equity",
}

MAX_WORKERS = 10


class KrFinancialStatementCollector:
    def __init__(self):
        self._dart = DartClient(os.getenv("DART_API_KEY", ""))

    def sync_corp_codes(self) -> int:
        mapping = self._dart.fetch_corp_codes()
        updated = 0

        with get_connection() as conn:
            repo = StockRepository(conn)
            for market in TARGET_MARKETS:
                stocks = repo.get_active_stocks(market)
                for stock_id, symbol, _ in stocks:
                    corp_code = mapping.get(symbol)
                    if corp_code:
                        self._update_corp_code(conn, stock_id, corp_code)
                        updated += 1
            conn.commit()

        logger.info(f"[KrFS] Synced {updated} corp_codes")
        return updated

    def collect_all(
        self,
        fiscal_years: list[int] | None = None,
        report_types: list[ReportType] | None = None,
    ) -> dict[str, int]:
        if fiscal_years is None:
            current = date.today().year - 1
            fiscal_years = [current - 1, current]
        if report_types is None:
            report_types = [ReportType.FY, ReportType.Q1, ReportType.Q2, ReportType.Q3]

        stocks = self._load_stocks_with_corp_code()
        if not stocks:
            logger.info("[KrFS] No corp_codes cached, running sync_corp_codes first")
            self.sync_corp_codes()
            stocks = self._load_stocks_with_corp_code()

        if not stocks:
            logger.warning("[KrFS] Still no stocks with dart_corp_code after sync")
            return {"success": 0, "failed": 0}

        shares_map = self._fetch_shares_via_pykrx()

        stock_map = {cc: (sid, sym) for sid, sym, cc in stocks}
        corp_codes = list(stock_map.keys())
        batches = [
            corp_codes[i:i + MULTI_BATCH_SIZE]
            for i in range(0, len(corp_codes), MULTI_BATCH_SIZE)
        ]

        tasks = [
            (batch, fy, rt)
            for batch in batches
            for fy in fiscal_years
            for rt in report_types
        ]

        logger.info(
            f"[KrFS] {len(stocks)} stocks, {len(batches)} batches, "
            f"{len(tasks)} API calls (years={fiscal_years}, "
            f"types={[r.value for r in report_types]})"
        )

        all_statements: list[FinancialStatement] = []
        done = failed = 0

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {
                pool.submit(self._fetch_batch, batch, fy, rt): (batch, fy, rt)
                for batch, fy, rt in tasks
            }
            for future in as_completed(futures):
                batch, fy, rt = futures[future]
                try:
                    rows = future.result()
                    stmts = self._parse_batch(rows, stock_map, shares_map, fy, rt)
                    all_statements.extend(stmts)
                    done += 1
                except Exception as e:
                    logger.warning(f"[KrFS] Batch failed {fy}/{rt.value}: {e}")
                    failed += 1

                if done % 20 == 0:
                    logger.info(f"[KrFS] API calls: {done}/{len(tasks)} done, {len(all_statements)} stmts")

        saved = self._save(all_statements)
        logger.info(f"[KrFS] Done: {saved} saved from {len(all_statements)} stmts, {failed} batch failures")
        return {"success": saved, "failed": failed}

    def _fetch_batch(
        self, corp_codes: list[str], fiscal_year: int, report_type: ReportType
    ) -> list[dict]:
        reprt_code = REPORT_CODES[report_type]
        return self._dart.fetch_multi_financial_statement(
            corp_codes, str(fiscal_year), reprt_code
        )

    def _parse_batch(
        self,
        rows: list[dict],
        stock_map: dict[str, tuple[int, str]],
        shares_map: dict[str, int],
        fiscal_year: int,
        report_type: ReportType,
    ) -> list[FinancialStatement]:
        if not rows:
            return []

        by_corp: dict[str, list[dict]] = {}
        for row in rows:
            by_corp.setdefault(row.get("corp_code", ""), []).append(row)

        results: list[FinancialStatement] = []
        for corp_code, corp_rows in by_corp.items():
            if corp_code not in stock_map:
                continue
            stock_id, symbol = stock_map[corp_code]
            accounts = self._extract_accounts(corp_rows, fs_div="CFS")
            if not accounts:
                accounts = self._extract_accounts(corp_rows, fs_div="OFS")
            if not accounts:
                continue

            results.append(FinancialStatement(
                stock_id=stock_id,
                fiscal_year=fiscal_year,
                report_type=report_type,
                revenue=accounts.get("revenue"),
                operating_income=accounts.get("operating_income"),
                net_income=accounts.get("net_income"),
                total_assets=accounts.get("total_assets"),
                total_liabilities=accounts.get("total_liabilities"),
                total_equity=accounts.get("total_equity"),
                shares_outstanding=shares_map.get(symbol),
            ))
        return results

    @staticmethod
    def _extract_accounts(rows: list[dict], fs_div: str = "CFS") -> dict[str, Decimal]:
        result: dict[str, Decimal] = {}
        for row in rows:
            if row.get("fs_div") != fs_div:
                continue
            account_nm = row.get("account_nm", "")
            field = ACCOUNT_MAP.get(account_nm)
            if not field or field in result:
                continue
            raw = row.get("thstrm_amount", "").replace(",", "")
            try:
                result[field] = Decimal(raw)
            except (InvalidOperation, ValueError):
                continue
        return result

    @staticmethod
    def _fetch_shares_via_pykrx() -> dict[str, int]:
        today_str = date.today().strftime("%Y%m%d")
        result: dict[str, int] = {}
        try:
            df = pykrx_stock.get_market_cap_by_ticker(today_str, market="ALL")
            if df is not None and not df.empty:
                for ticker, row in df.iterrows():
                    val = int(row["상장주식수"])
                    if val > 0:
                        result[str(ticker)] = val
            logger.info(f"[KrFS] pykrx shares loaded: {len(result)} tickers")
        except Exception as e:
            logger.warning(f"[KrFS] pykrx shares failed: {e}")
        return result

    def _save(self, statements: list[FinancialStatement]) -> int:
        if not statements:
            return 0
        with get_connection() as conn:
            repo = FinancialStatementRepository(conn)
            count = repo.upsert_batch(statements)
            conn.commit()
        logger.info(f"[KrFS] Saved {count} statements")
        return count

    def _load_stocks_with_corp_code(self) -> list[tuple[int, str, str]]:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, symbol, dart_corp_code FROM stocks
                    WHERE is_active = true
                      AND market IN ('KR_KOSPI', 'KR_KOSDAQ')
                      AND dart_corp_code IS NOT NULL
                """)
                return [(row[0], row[1], row[2]) for row in cur.fetchall()]

    @staticmethod
    def _update_corp_code(conn, stock_id: int, corp_code: str) -> None:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE stocks SET dart_corp_code = %s WHERE id = %s",
                (corp_code, stock_id),
            )
