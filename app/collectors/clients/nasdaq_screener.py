import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from app.utils import retry_with_backoff

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.nasdaq.com/api/screener/stocks"
_EXCHANGES = ("nasdaq", "nyse")
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; SaramQuant/1.0)"}
_TIMEOUT = 30


class NasdaqScreenerClient:
    def fetch_all_sectors(self) -> dict[str, str]:
        sector_map: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {
                pool.submit(self._fetch_exchange, ex): ex
                for ex in _EXCHANGES
            }
            for future in as_completed(futures):
                exchange = futures[future]
                try:
                    result = future.result()
                    if result:
                        sector_map.update(result)
                        logger.info(f"[NasdaqScreener] {exchange}: {len(result)} sectors")
                except Exception as e:
                    logger.warning(f"[NasdaqScreener] {exchange} failed: {e}")
        return sector_map

    @retry_with_backoff(max_retries=3, base_delay=2.0)
    def _fetch_exchange(self, exchange: str) -> dict[str, str]:
        resp = requests.get(
            _BASE_URL,
            params={
                "tableonly": "true",
                "limit": 25000,
                "offset": 0,
                "exchange": exchange,
                "download": "true",
            },
            headers=_HEADERS,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()

        rows = resp.json().get("data", {}).get("rows", [])
        return {
            row["symbol"]: row["sector"]
            for row in rows
            if row.get("sector", "").strip()
        }
