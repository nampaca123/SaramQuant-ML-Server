import logging
import time
import requests

logger = logging.getLogger(__name__)


class FinnhubClient:
    BASE_URL = "https://finnhub.io/api/v1"
    REQUEST_DELAY = 1.0

    def __init__(self, api_key: str):
        self._api_key = api_key
        self._last_call = 0.0

    def _throttle(self):
        elapsed = time.time() - self._last_call
        if elapsed < self.REQUEST_DELAY:
            time.sleep(self.REQUEST_DELAY - elapsed)
        self._last_call = time.time()

    def fetch_sector(self, symbol: str) -> str | None:
        self._throttle()
        try:
            resp = requests.get(
                f"{self.BASE_URL}/stock/profile2",
                params={"symbol": symbol, "token": self._api_key},
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json().get("finnhubIndustry") or None
        except Exception as e:
            logger.warning(f"[finnhub] {symbol}: {e}")
            return None
