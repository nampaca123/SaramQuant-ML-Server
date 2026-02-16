import logging

from app.schema import Market, Benchmark, Maturity
from app.collectors import (
    StockListCollector,
    SectorCollector,
    KrDailyPriceCollector,
    UsDailyPriceCollector,
    BenchmarkCollector,
    RiskFreeRateCollector,
)

logger = logging.getLogger(__name__)

REGION_CONFIG = {
    "kr": {
        "markets": [Market.KR_KOSPI, Market.KR_KOSDAQ],
        "benchmarks": [Benchmark.KR_KOSPI, Benchmark.KR_KOSDAQ],
        "maturities": [Maturity.D91, Maturity.Y3, Maturity.Y10],
    },
    "us": {
        "markets": [Market.US_NYSE, Market.US_NASDAQ],
        "benchmarks": [Benchmark.US_SP500, Benchmark.US_NASDAQ],
        "maturities": [Maturity.D91, Maturity.Y1, Maturity.Y3, Maturity.Y10],
    },
}


class PriceCollectionService:
    def collect_all(self, region: str) -> dict[str, int]:
        cfg = REGION_CONFIG[region]
        results: dict[str, int] = {}

        results["stocks"] = self._collect_stocks(cfg["markets"])
        results["sectors"] = self._collect_sectors(cfg["markets"])
        results["prices"] = self._collect_prices(region)
        results["benchmarks"] = self._collect_benchmarks(cfg["benchmarks"])
        results["risk_free_rates"] = self._collect_risk_free_rates(region, cfg["maturities"])

        logger.info(f"[PriceCollection] {region.upper()} complete: {results}")
        return results

    def _collect_stocks(self, markets: list[Market]) -> int:
        collector = StockListCollector()
        total = 0
        for market in markets:
            count = collector.collect_market(market)
            logger.info(f"[PriceCollection] Stocks {market.value}: {count}")
            total += count
        return total

    def _collect_sectors(self, markets: list[Market]) -> int:
        try:
            count = SectorCollector().collect(markets)
            logger.info(f"[PriceCollection] Sectors updated: {count}")
            return count
        except Exception as e:
            logger.warning(f"[PriceCollection] Sector collection failed (non-blocking): {e}")
            return 0

    def _collect_prices(self, region: str) -> int:
        if region == "kr":
            results = KrDailyPriceCollector().collect_all()
        else:
            results = UsDailyPriceCollector().collect_all()
        total = sum(results.values())
        logger.info(f"[PriceCollection] {region.upper()} daily prices: {total} records")
        return total

    def _collect_benchmarks(self, benchmarks: list[Benchmark]) -> int:
        collector = BenchmarkCollector()
        total = 0
        for bench in benchmarks:
            count = collector.collect(bench)
            logger.info(f"[PriceCollection] Benchmark {bench.value}: {count} records")
            total += count
        return total

    def _collect_risk_free_rates(self, region: str, maturities: list[Maturity]) -> int:
        collector = RiskFreeRateCollector()
        total = 0
        for maturity in maturities:
            count = collector.collect_kr(maturity) if region == "kr" else collector.collect_us(maturity)
            logger.info(f"[PriceCollection] {region.upper()} risk-free rate {maturity.value}: {count} records")
            total += count
        return total
