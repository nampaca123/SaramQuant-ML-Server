import io
import logging
import zipfile
import requests
from requests.exceptions import RequestException
from app.schema import Market, StockInfo
from app.db import get_connection, StockRepository
from app.utils import retry_with_backoff

logger = logging.getLogger(__name__)

MST_URLS = {
    Market.KR_KOSPI: "https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip",
    Market.KR_KOSDAQ: "https://new.real.download.dws.co.kr/common/master/kosdaq_code.mst.zip",
    Market.US_NYSE: "https://new.real.download.dws.co.kr/common/master/nysmst.cod.zip",
    Market.US_NASDAQ: "https://new.real.download.dws.co.kr/common/master/nasmst.cod.zip",
}

KR_INVALID_KEYWORDS = ["스팩", "리츠", "ETN", "ETF", "선물", "인버스", "레버리지"]


class StockListCollector:
    def collect_all(self) -> dict[Market, int]:
        results = {}
        for market in Market:
            count = self.collect_market(market)
            results[market] = count
        return results

    def collect_market(self, market: Market) -> int:
        count = self._collect_market(market)
        logger.info(f"[StockList] {market.value}: {count} stocks")
        return count

    def _collect_market(self, market: Market) -> int:
        raw_data = self._download(market)
        if raw_data is None:
            return 0

        stocks = self._parse(raw_data, market)
        if not stocks:
            return 0

        with get_connection() as conn:
            repo = StockRepository(conn)
            repo.upsert_batch(stocks)
            repo.deactivate_unlisted(market, {s.symbol for s in stocks})
            conn.commit()

        return len(stocks)

    @retry_with_backoff(max_retries=3, base_delay=2.0)
    def _download(self, market: Market) -> bytes | None:
        url = MST_URLS[market]
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()

        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            filename = zf.namelist()[0]
            return zf.read(filename)

    def _parse(self, data: bytes, market: Market) -> list[StockInfo]:
        if market in (Market.KR_KOSPI, Market.KR_KOSDAQ):
            return self._parse_kr(data, market)
        return self._parse_us(data, market)

    def _parse_kr(self, data: bytes, market: Market) -> list[StockInfo]:
        stocks = []

        for line in data.split(b"\n"):
            if len(line) < 61:
                continue

            symbol = line[0:9].decode("cp949", errors="ignore").strip()
            name = line[21:61].decode("cp949", errors="ignore").strip()

            if not symbol or not name:
                continue
            if self._is_invalid_kr_stock(symbol, name):
                continue

            stocks.append(StockInfo(symbol=symbol, name=name, market=market))

        return stocks

    def _parse_us(self, data: bytes, market: Market) -> list[StockInfo]:
        stocks = []
        lines = data.decode("cp949", errors="ignore").splitlines()

        for line in lines:
            parts = line.split("\t")
            if len(parts) < 8:
                continue

            symbol = parts[4].strip()
            name = parts[7].strip()

            if not symbol or not name:
                continue
            if self._is_invalid_us_stock(symbol):
                continue

            stocks.append(StockInfo(symbol=symbol, name=name, market=market))

        return stocks

    def _is_invalid_kr_stock(self, symbol: str, name: str) -> bool:
        if not symbol.isdigit():
            return True
        return any(kw in name for kw in KR_INVALID_KEYWORDS)

    def _is_invalid_us_stock(self, symbol: str) -> bool:
        return len(symbol) > 5 or not symbol.isalpha()