import logging
import os
import re
from datetime import date
from decimal import Decimal

from app.schema import FinancialStatement, Market, ReportType
from app.db import get_connection, StockRepository
from app.db.repositories.financial_statement import FinancialStatementRepository
from app.collectors.clients.edgar import EdgarClient

logger = logging.getLogger(__name__)

TARGET_MARKETS = [Market.US_NYSE, Market.US_NASDAQ]

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "edgar")

GAAP_CONCEPTS = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
    ],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss"],
    "total_assets": ["Assets"],
    "total_liabilities": ["Liabilities"],
    "total_equity": [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ],
}

SHARES_CONCEPTS = [
    ("dei", "EntityCommonStockSharesOutstanding"),
    ("us-gaap", "CommonStockSharesOutstanding"),
]

FORM_FY = {"10-K", "10-K/A"}
FORM_Q = {"10-Q", "10-Q/A"}

REPORT_MAP = {"Q1": ReportType.Q1, "Q2": ReportType.Q2, "Q3": ReportType.Q3}

INSTANT_RE = re.compile(r"^CY\d{4}Q[1-4]I$")
SINGLE_Q_RE = re.compile(r"^CY\d{4}Q[1-4]$")

BS_FIELDS = {"total_assets", "total_liabilities", "total_equity"}
IS_FIELDS = {"revenue", "operating_income", "net_income"}

MIN_RECENT_YEAR = date.today().year - 3


class UsFinancialStatementCollector:
    def __init__(self):
        self._edgar = EdgarClient()

    def collect_all(self) -> dict[str, int]:
        data_dir = os.path.abspath(DATA_DIR)
        self._edgar.download_bulk_facts(data_dir)

        ticker_to_cik = self._edgar.fetch_company_tickers()
        stocks = self._load_us_stocks()
        if not stocks:
            logger.warning("[UsFS] No active US stocks in DB")
            return {"success": 0, "failed": 0}

        all_statements: list[FinancialStatement] = []
        matched = failed = 0

        for i, (stock_id, symbol) in enumerate(stocks, 1):
            cik = ticker_to_cik.get(symbol.upper())
            if not cik:
                continue
            try:
                facts = self._edgar.parse_company_facts(data_dir, cik)
                if not facts:
                    continue
                stmts = self._extract_from_facts(stock_id, facts)
                all_statements.extend(stmts)
                matched += 1
            except Exception as e:
                logger.warning(f"[UsFS] Skip {symbol} ({stock_id}): {e}")
                failed += 1

            if i % 1000 == 0:
                logger.info(f"[UsFS] Parsed {i}/{len(stocks)} stocks, {len(all_statements)} stmts")

        saved = self._save(all_statements)
        logger.info(f"[UsFS] Done: {saved} saved from {matched} stocks, {failed} failed")
        return {"success": saved, "failed": failed}

    def _extract_from_facts(
        self, stock_id: int, facts: dict
    ) -> list[FinancialStatement]:
        us_gaap = facts.get("facts", {}).get("us-gaap", {})
        dei = facts.get("facts", {}).get("dei", {})
        all_ns = {"us-gaap": us_gaap, "dei": dei}

        fy_data: dict[int, dict] = {}
        q_data: dict[tuple[int, str], dict] = {}

        for field, concepts in GAAP_CONCEPTS.items():
            concept, entries = self._pick_concept(us_gaap, concepts)
            if not entries:
                continue
            for entry in entries:
                self._place_entry(entry, field, fy_data, q_data)

        shares_map = self._extract_shares(all_ns)

        results: list[FinancialStatement] = []

        for fy, accounts in sorted(fy_data.items(), reverse=True)[:3]:
            shares = shares_map.get(("FY", fy))
            results.append(self._build_statement(
                stock_id, fy, ReportType.FY, accounts, shares
            ))

        for (fy, fp), accounts in sorted(q_data.items(), reverse=True)[:8]:
            rt = REPORT_MAP.get(fp)
            if not rt:
                continue
            shares = shares_map.get((fp, fy))
            results.append(self._build_statement(stock_id, fy, rt, accounts, shares))

        return results

    @staticmethod
    def _pick_concept(
        namespace: dict, candidates: list[str]
    ) -> tuple[str | None, list[dict]]:
        for concept in candidates:
            node = namespace.get(concept)
            if not node:
                continue
            entries = node.get("units", {}).get("USD", [])
            if any(e.get("fy", 0) >= MIN_RECENT_YEAR for e in entries):
                return concept, entries
        return None, []

    @staticmethod
    def _place_entry(
        entry: dict,
        field: str,
        fy_data: dict[int, dict],
        q_data: dict[tuple[int, str], dict],
    ) -> None:
        form = entry.get("form", "")
        fy = entry.get("fy")
        fp = entry.get("fp")
        val = entry.get("val")
        frame = entry.get("frame", "")
        if not fy or val is None:
            return

        if form in FORM_FY and fp == "FY":
            if field in BS_FIELDS and frame and not INSTANT_RE.match(frame):
                return
            fy_data.setdefault(fy, {})[field] = val

        elif form in FORM_Q and fp in REPORT_MAP:
            if field in IS_FIELDS:
                if not SINGLE_Q_RE.match(frame):
                    return
            elif field in BS_FIELDS:
                if frame and not INSTANT_RE.match(frame):
                    return
            q_data.setdefault((fy, fp), {})[field] = val

    @staticmethod
    def _extract_shares(all_ns: dict) -> dict[tuple[str, int], int]:
        result: dict[tuple[str, int], int] = {}
        for ns_name, concept_name in SHARES_CONCEPTS:
            ns = all_ns.get(ns_name, {})
            node = ns.get(concept_name)
            if not node:
                continue
            for unit_entries in node.get("units", {}).values():
                for entry in unit_entries:
                    fy = entry.get("fy")
                    fp = entry.get("fp")
                    val = entry.get("val")
                    if fy and fp and val:
                        result.setdefault((fp, fy), int(val))
            if result:
                break
        return result

    @staticmethod
    def _build_statement(
        stock_id: int,
        fiscal_year: int,
        report_type: ReportType,
        accounts: dict,
        shares: int | None,
    ) -> FinancialStatement:
        def _dec(key: str) -> Decimal | None:
            v = accounts.get(key)
            return Decimal(str(v)) if v is not None else None

        return FinancialStatement(
            stock_id=stock_id,
            fiscal_year=fiscal_year,
            report_type=report_type,
            revenue=_dec("revenue"),
            operating_income=_dec("operating_income"),
            net_income=_dec("net_income"),
            total_assets=_dec("total_assets"),
            total_liabilities=_dec("total_liabilities"),
            total_equity=_dec("total_equity"),
            shares_outstanding=shares,
        )

    def _save(self, statements: list[FinancialStatement]) -> int:
        if not statements:
            return 0
        with get_connection() as conn:
            repo = FinancialStatementRepository(conn)
            count = repo.upsert_batch(statements)
            conn.commit()
        return count

    def _load_us_stocks(self) -> list[tuple[int, str]]:
        with get_connection() as conn:
            repo = StockRepository(conn)
            stocks = []
            for market in TARGET_MARKETS:
                for stock_id, symbol, _ in repo.get_active_stocks(market):
                    stocks.append((stock_id, symbol))
            return stocks
