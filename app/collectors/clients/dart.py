import io
import logging
import zipfile
import xml.etree.ElementTree as ET

import requests

from app.schema import ReportType
from app.utils import retry_with_backoff
from app.collectors.utils.throttle import Throttle

logger = logging.getLogger(__name__)

REPORT_CODES = {
    ReportType.Q1: "11013",
    ReportType.Q2: "11012",
    ReportType.Q3: "11014",
    ReportType.FY: "11011",
}

MULTI_BATCH_SIZE = 100


class DartClient:
    BASE_URL = "https://opendart.fss.or.kr/api"

    def __init__(self, api_key: str):
        self._api_key = api_key
        self._session = requests.Session()
        self._throttle = Throttle(min_interval=0.06)

    def fetch_corp_codes(self) -> dict[str, str]:
        self._throttle.wait()
        resp = self._session.get(
            f"{self.BASE_URL}/corpCode.xml",
            params={"crtfc_key": self._api_key},
            timeout=60,
        )
        resp.raise_for_status()

        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            xml_name = zf.namelist()[0]
            tree = ET.parse(zf.open(xml_name))

        mapping: dict[str, str] = {}
        for item in tree.getroot().iter("list"):
            stock_code = item.findtext("stock_code", "").strip()
            corp_code = item.findtext("corp_code", "").strip()
            if stock_code and corp_code:
                mapping[stock_code] = corp_code

        logger.info(f"[DART] Loaded {len(mapping)} corp_code mappings")
        return mapping

    @retry_with_backoff(max_retries=3, base_delay=2.0)
    def fetch_multi_financial_statement(
        self,
        corp_codes: list[str],
        bsns_year: str,
        reprt_code: str,
    ) -> list[dict]:
        self._throttle.wait()
        resp = self._session.get(
            f"{self.BASE_URL}/fnlttMultiAcnt.json",
            params={
                "crtfc_key": self._api_key,
                "corp_code": ",".join(corp_codes),
                "bsns_year": bsns_year,
                "reprt_code": reprt_code,
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") != "000":
            return []

        return data.get("list", [])
