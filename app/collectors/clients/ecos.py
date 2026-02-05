import logging
import requests
from app.schema import Maturity
from app.utils import retry_with_backoff

logger = logging.getLogger(__name__)


class EcosClient:
    BASE_URL = "https://ecos.bok.or.kr/api/StatisticSearch"
    PAGE_SIZE = 100
    STAT_CODE = "817Y002"

    ITEM_CODES = {
        Maturity.D91: "010502000",
        Maturity.Y3: "010200000",
        Maturity.Y10: "010210000",
    }

    def __init__(self, api_key: str):
        self._api_key = api_key

    @retry_with_backoff(max_retries=3, base_delay=1.0)
    def _fetch_page(
        self,
        item_code: str,
        start_date: str,
        end_date: str,
        start_idx: int,
        end_idx: int
    ) -> dict:
        url = (
            f"{self.BASE_URL}/{self._api_key}/json/kr/"
            f"{start_idx}/{end_idx}/{self.STAT_CODE}/D/{start_date}/{end_date}/{item_code}"
        )
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def fetch_rates(
        self,
        maturity: Maturity,
        start_date: str,
        end_date: str
    ) -> list[dict]:
        if maturity not in self.ITEM_CODES:
            return []

        item_code = self.ITEM_CODES[maturity]
        first = self._fetch_page(item_code, start_date, end_date, 1, 1)

        if not first or "StatisticSearch" not in first:
            logger.warning(f"[ECOS] No data for {maturity.value}")
            return []

        total = int(first["StatisticSearch"]["list_total_count"])
        rows = []

        for start in range(1, total + 1, self.PAGE_SIZE):
            end = min(start + self.PAGE_SIZE - 1, total)
            page = self._fetch_page(item_code, start_date, end_date, start, end)
            if page and "StatisticSearch" in page:
                rows.extend(page["StatisticSearch"]["row"])

        return rows
