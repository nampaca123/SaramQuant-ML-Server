import logging
from dataclasses import dataclass

from app.db import StockRepository
from app.schema import Market

logger = logging.getLogger(__name__)


@dataclass
class MarketIntegrityReport:
    market: str
    active_total: int
    has_sector: int
    sector_null: int
    sector_na: int
    no_fs: int
    no_price: int


class IntegrityCheckService:
    def __init__(self, conn: object):
        self._repo = StockRepository(conn)

    def check(self, markets: list[Market]) -> list[MarketIntegrityReport]:
        reports = []
        for market in markets:
            row = self._repo.get_integrity_stats(market)
            report = MarketIntegrityReport(
                market=market.value,
                active_total=row[0], has_sector=row[1],
                sector_null=row[2], sector_na=row[3],
                no_fs=row[4], no_price=row[5],
            )
            self._log_report(report)
            reports.append(report)
        return reports

    @staticmethod
    def _log_report(r: MarketIntegrityReport) -> None:
        logger.info(
            f"[IntegrityCheck] {r.market}: "
            f"active={r.active_total}, quant_eligible={r.has_sector}, "
            f"sector_null={r.sector_null}, sector_na={r.sector_na}, "
            f"no_fs={r.no_fs}, no_price={r.no_price}"
        )
        if r.active_total > 0:
            excluded_pct = (r.sector_null + r.sector_na) / r.active_total * 100
            if excluded_pct > 20:
                logger.warning(
                    f"[IntegrityCheck] {r.market}: {excluded_pct:.1f}% excluded (threshold: 20%)"
                )
