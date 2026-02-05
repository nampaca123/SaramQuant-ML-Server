import logging
import requests
from app.schema import Maturity
from app.utils import retry_with_backoff

logger = logging.getLogger(__name__)


class FredClient:
    BASE_URL = "https://api.stlouisfed.org/fred/series/observations"

    SERIES_IDS = {
        Maturity.D91: "DTB3",
        Maturity.Y1: "DGS1",
        Maturity.Y3: "DGS3",
        Maturity.Y10: "DGS10",
    }

    def __init__(self, api_key: str):
        self._api_key = api_key

    @retry_with_backoff(max_retries=3, base_delay=1.0)
    def fetch_rates(
        self,
        maturity: Maturity,
        start_date: str,
        end_date: str
    ) -> list[dict]:
        if maturity not in self.SERIES_IDS:
            return []

        params = {
            "series_id": self.SERIES_IDS[maturity],
            "api_key": self._api_key,
            "file_type": "json",
            "observation_start": start_date,
            "observation_end": end_date,
        }

        resp = requests.get(self.BASE_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        if "observations" not in data:
            logger.warning(f"[FRED] No data for {maturity.value}")
            return []

        return data["observations"]
