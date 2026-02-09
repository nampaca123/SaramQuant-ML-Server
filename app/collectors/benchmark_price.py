import logging
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from app.schema import Benchmark, BenchmarkPrice
from app.db import get_connection, BenchmarkRepository
from app.collectors.clients import PykrxClient, YfinanceClient

logger = logging.getLogger(__name__)

KR_INDEX_TICKERS = {
    Benchmark.KR_KOSPI: "1001",
    Benchmark.KR_KOSDAQ: "2001",
}

US_INDEX_SYMBOLS = {
    Benchmark.US_SP500: "^GSPC",
    Benchmark.US_NASDAQ: "^IXIC",
}


class BenchmarkCollector:
    def __init__(self):
        self._pykrx = PykrxClient()
        self._yfinance = YfinanceClient()

    def collect_all(self) -> dict[str, int]:
        results = {}
        for benchmark in Benchmark:
            results[benchmark.value] = self.collect(benchmark)
        return results

    def collect(self, benchmark: Benchmark) -> int:
        start_date = self._get_start_date(benchmark)

        if benchmark in KR_INDEX_TICKERS:
            prices = self._collect_kr(benchmark, start_date)
        elif benchmark in US_INDEX_SYMBOLS:
            prices = self._collect_us(benchmark, start_date)
        else:
            return 0

        if not prices:
            return 0

        with get_connection() as conn:
            repo = BenchmarkRepository(conn)
            count = repo.upsert_batch(prices)
            conn.commit()

        logger.info(f"[Benchmark] {benchmark.value}: {count} rows")
        return count

    def _get_start_date(self, benchmark: Benchmark) -> date | None:
        with get_connection() as conn:
            repo = BenchmarkRepository(conn)
            return repo.get_latest_date(benchmark)

    def _collect_kr(self, benchmark: Benchmark, latest: date | None) -> list[BenchmarkPrice]:
        ticker = KR_INDEX_TICKERS[benchmark]
        start = (latest + timedelta(days=1)).strftime("%Y%m%d") if latest else "20200101"
        end = date.today().strftime("%Y%m%d")

        df = self._pykrx.fetch_index_ohlcv(start, end, ticker)
        if df.empty:
            return []
        return self._transform(benchmark, df)

    def _collect_us(self, benchmark: Benchmark, latest: date | None) -> list[BenchmarkPrice]:
        symbol = US_INDEX_SYMBOLS[benchmark]
        start = (latest + timedelta(days=1)).strftime("%Y-%m-%d") if latest else "2020-01-01"
        end = date.today().strftime("%Y-%m-%d")

        df = self._yfinance.fetch_index_prices(symbol, start, end)
        if df.empty:
            return []
        return self._transform(benchmark, df)

    def _transform(self, benchmark: Benchmark, df) -> list[BenchmarkPrice]:
        prices = []
        for idx, row in df.iterrows():
            try:
                price_date = idx.date() if hasattr(idx, "date") else idx
                prices.append(BenchmarkPrice(
                    benchmark=benchmark,
                    date=price_date,
                    close=Decimal(str(row["close"])),
                ))
            except (KeyError, InvalidOperation, ValueError) as e:
                logger.warning(f"[Benchmark] Skip invalid row: {e}")
                continue
        return prices
