import logging

from psycopg2.extensions import connection

from app.db import DailyPriceRepository
from app.db.repositories.financial_statement import FinancialStatementRepository
from app.db.repositories.fundamental import FundamentalRepository
from app.schema import Market
from app.services.fundamental_service import FundamentalService

logger = logging.getLogger(__name__)


class FundamentalComputeEngine:
    def __init__(self, conn: connection):
        self._conn = conn
        self._fs_repo = FinancialStatementRepository(conn)
        self._fund_repo = FundamentalRepository(conn)
        self._price_repo = DailyPriceRepository(conn)

    def run(
        self,
        markets: list[Market],
        price_maps: dict[Market, dict[int, list[tuple]]] | None = None,
    ) -> int:
        FundamentalService.reset_stats()
        all_rows: list[tuple] = []

        for market in markets:
            pm = price_maps.get(market) if price_maps else None
            rows = self._process_market(market, pm)
            all_rows.extend(rows)

        stats = FundamentalService.get_stats()
        logger.info(
            f"[FundCompute] Quality: {stats['shares_sanitized']} shares_sanitized, "
            f"{stats['clamped']} clamped, {stats['negative_equity']} negative_equity"
        )

        deleted = self._fund_repo.delete_by_markets(markets)
        logger.info(f"[FundCompute] Deleted {deleted} old fundamental rows")

        inserted = self._fund_repo.insert_batch(all_rows)
        self._conn.commit()
        logger.info(f"[FundCompute] Inserted {inserted} fundamental rows")
        return inserted

    def _process_market(
        self, market: Market, price_map: dict[int, list[tuple]] | None = None
    ) -> list[tuple]:
        fs_map = self._fs_repo.get_ttm_by_market(market)

        if price_map is None:
            price_map = self._price_repo.get_prices_by_market(market, limit_per_stock=1)

        rows: list[tuple] = []
        fs_stock_ids = set(fs_map.keys()) if fs_map else set()

        for i, (stock_id, statements) in enumerate((fs_map or {}).items(), 1):
            prices = price_map.get(stock_id)
            if not prices:
                continue

            latest_close = float(prices[-1][4])
            result = FundamentalService.compute(stock_id, latest_close, statements)
            if result:
                rows.append(result)

            if i % 500 == 0:
                logger.info(f"[FundCompute] {market.value}: {i}/{len(fs_map)} stocks")

        no_fs_count = 0
        for stock_id in price_map:
            if stock_id not in fs_stock_ids:
                rows.append(FundamentalService.no_fs_row(stock_id))
                no_fs_count += 1

        logger.info(
            f"[FundCompute] {market.value}: {len(rows)} rows "
            f"({len(rows) - no_fs_count} computed, {no_fs_count} NO_FS)"
        )
        return rows
