import logging

from app.schema import Market, Benchmark, Maturity
from app.db import get_connection
from app.collectors import (
    StockListCollector,
    KrDailyPriceCollector,
    UsDailyPriceCollector,
    BenchmarkCollector,
    RiskFreeRateCollector,
)
from app.pipeline.compute import ComputeEngine

logger = logging.getLogger(__name__)

KR_MARKETS = [Market.KR_KOSPI, Market.KR_KOSDAQ]
US_MARKETS = [Market.US_NYSE, Market.US_NASDAQ]

KR_BENCHMARKS = [Benchmark.KR_KOSPI, Benchmark.KR_KOSDAQ]
US_BENCHMARKS = [Benchmark.US_SP500, Benchmark.US_NASDAQ]

KR_MATURITIES = [Maturity.D91, Maturity.Y3, Maturity.Y10]
US_MATURITIES = [Maturity.D91, Maturity.Y1, Maturity.Y3, Maturity.Y10]


class PipelineOrchestrator:
    def run_kr(self) -> None:
        logger.info("[Pipeline] Starting KR pipeline")
        self._collect_stocks(KR_MARKETS)
        self._collect_kr_prices()
        self._collect_benchmarks(KR_BENCHMARKS)
        self._collect_risk_free_rates_kr()
        self._compute(KR_MARKETS)
        logger.info("[Pipeline] KR pipeline complete")

    def run_us(self) -> None:
        logger.info("[Pipeline] Starting US pipeline")
        self._collect_stocks(US_MARKETS)
        self._collect_us_prices()
        self._collect_benchmarks(US_BENCHMARKS)
        self._collect_risk_free_rates_us()
        self._compute(US_MARKETS)
        logger.info("[Pipeline] US pipeline complete")

    def run_all(self) -> None:
        self.run_kr()
        self.run_us()

    def _collect_stocks(self, markets: list[Market]) -> None:
        collector = StockListCollector()
        for market in markets:
            count = collector.collect_market(market)
            logger.info(f"[Pipeline] Stocks {market.value}: {count}")

    def _collect_kr_prices(self) -> None:
        results = KrDailyPriceCollector().collect_all()
        total = sum(results.values())
        logger.info(f"[Pipeline] KR daily prices: {total} records")

    def _collect_us_prices(self) -> None:
        results = UsDailyPriceCollector().collect_all()
        total = sum(results.values())
        logger.info(f"[Pipeline] US daily prices: {total} records")

    def _collect_benchmarks(self, benchmarks: list[Benchmark]) -> None:
        collector = BenchmarkCollector()
        for bench in benchmarks:
            count = collector.collect(bench)
            logger.info(f"[Pipeline] Benchmark {bench.value}: {count} records")

    def _collect_risk_free_rates_kr(self) -> None:
        collector = RiskFreeRateCollector()
        for maturity in KR_MATURITIES:
            count = collector.collect_kr(maturity)
            logger.info(f"[Pipeline] KR risk-free rate {maturity.value}: {count} records")

    def _collect_risk_free_rates_us(self) -> None:
        collector = RiskFreeRateCollector()
        for maturity in US_MATURITIES:
            count = collector.collect_us(maturity)
            logger.info(f"[Pipeline] US risk-free rate {maturity.value}: {count} records")

    def _compute(self, markets: list[Market]) -> None:
        with get_connection() as conn:
            engine = ComputeEngine(conn)
            count = engine.run(markets)
            logger.info(f"[Pipeline] Computed {count} indicator rows")
