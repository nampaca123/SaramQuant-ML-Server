import logging
from datetime import timedelta
from decimal import Decimal, InvalidOperation
import FinanceDataReader as fdr
import pandas as pd
from app.schema import Benchmark, BenchmarkPrice
from app.db import get_connection, BenchmarkRepository
from app.utils import retry_with_backoff

logger = logging.getLogger(__name__)


class BenchmarkCollector:
    BENCHMARK_SYMBOLS = {
        Benchmark.KR_KOSPI: "KS11",
        Benchmark.KR_KOSDAQ: "KQ11",
        Benchmark.US_SP500: "^GSPC",
        Benchmark.US_NASDAQ: "^IXIC",
    }

    def collect(self, benchmark: Benchmark) -> int:
        start_date = self._get_start_date(benchmark)
        df = self._fetch_prices(benchmark, start_date)

        if df is None or df.empty:
            return 0

        prices = self._transform(benchmark, df)
        if not prices:
            return 0

        with get_connection() as conn:
            repo = BenchmarkRepository(conn)
            count = repo.upsert_batch(prices)
            conn.commit()

        logger.info(f"[Benchmark] {benchmark.value}: {count} rows")
        return count

    def collect_all(self) -> dict[str, int]:
        results = {}
        for benchmark in Benchmark:
            results[benchmark.value] = self.collect(benchmark)
        return results

    def _get_start_date(self, benchmark: Benchmark) -> str | None:
        with get_connection() as conn:
            repo = BenchmarkRepository(conn)
            latest = repo.get_latest_date(benchmark)

        if latest:
            next_day = latest + timedelta(days=1)
            return next_day.strftime("%Y-%m-%d")
        return None

    @retry_with_backoff(max_retries=3, base_delay=1.0)
    def _fetch_prices(
        self, benchmark: Benchmark, start_date: str | None
    ) -> pd.DataFrame | None:
        symbol = self.BENCHMARK_SYMBOLS[benchmark]
        return fdr.DataReader(symbol, start_date)

    def _transform(self, benchmark: Benchmark, df: pd.DataFrame) -> list[BenchmarkPrice]:
        prices = []

        for idx, row in df.iterrows():
            try:
                price_date = idx.date() if hasattr(idx, "date") else idx
                prices.append(BenchmarkPrice(
                    benchmark=benchmark,
                    date=price_date,
                    close=Decimal(str(row["Close"])),
                ))
            except (KeyError, InvalidOperation, ValueError) as e:
                logger.warning(f"[Benchmark] Skip invalid row: {e}")
                continue

        return prices
