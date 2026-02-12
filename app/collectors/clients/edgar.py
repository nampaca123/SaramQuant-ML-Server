import json
import logging
import os
import shutil
import time
import zipfile

import requests

from app.utils import retry_with_backoff

logger = logging.getLogger(__name__)

USER_AGENT = "SaramQuant nampaca123@gmail.com"

BULK_FACTS_URL = "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

MAX_DOWNLOAD_RETRIES = 3


class EdgarClient:
    def __init__(self):
        self._headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"}

    def download_bulk_facts(self, data_dir: str, max_age_days: int = 7) -> str:
        dest = os.path.join(data_dir, "companyfacts")
        zip_path = os.path.join(data_dir, "companyfacts.zip")

        if os.path.isdir(dest) and os.listdir(dest):
            age_days = (time.time() - os.path.getmtime(dest)) / 86400
            if age_days < max_age_days:
                logger.info(f"[EDGAR] Bulk data is {age_days:.1f} days old, skipping")
                return dest
            logger.info(f"[EDGAR] Bulk data is {age_days:.1f} days old, refreshing")
            shutil.rmtree(dest)

        os.makedirs(data_dir, exist_ok=True)
        self._download_zip(zip_path)

        logger.info("[EDGAR] Extracting companyfacts.zip...")
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(data_dir)

        os.remove(zip_path)
        logger.info(f"[EDGAR] Bulk data extracted to {dest}")
        return dest

    def _download_zip(self, zip_path: str) -> None:
        for attempt in range(1, MAX_DOWNLOAD_RETRIES + 1):
            try:
                logger.info(f"[EDGAR] Downloading companyfacts.zip (attempt {attempt}/{MAX_DOWNLOAD_RETRIES})...")
                resp = requests.get(
                    BULK_FACTS_URL, headers=self._headers,
                    stream=True, timeout=(30, 14400),
                )
                resp.raise_for_status()

                total = int(resp.headers.get("Content-Length", 0))
                downloaded = 0

                last_logged_pct = -1
                with open(zip_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=1024 * 1024):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            pct = downloaded * 100 // total
                            if pct >= last_logged_pct + 10:
                                last_logged_pct = pct
                                logger.info(f"[EDGAR] Download: {pct}% ({downloaded // (1024*1024)}MB / {total // (1024*1024)}MB)")

                if total and downloaded < total:
                    raise IOError(f"Incomplete download: {downloaded}/{total} bytes")

                logger.info(f"[EDGAR] Download complete ({downloaded // (1024*1024)}MB)")
                return

            except (requests.RequestException, IOError) as e:
                logger.warning(f"[EDGAR] Download attempt {attempt} failed: {e}")
                if os.path.exists(zip_path):
                    os.remove(zip_path)
                if attempt == MAX_DOWNLOAD_RETRIES:
                    raise
                time.sleep(10 * attempt)

    def parse_company_facts(self, data_dir: str, cik: int) -> dict | None:
        path = os.path.join(data_dir, "companyfacts", f"CIK{cik:010d}.json")
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    @retry_with_backoff(max_retries=3, base_delay=1.0)
    def fetch_company_tickers(self) -> dict[str, int]:
        resp = requests.get(TICKERS_URL, headers=self._headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        mapping: dict[str, int] = {}
        for entry in data.values():
            ticker = entry.get("ticker", "")
            cik = entry.get("cik_str")
            if ticker and cik:
                mapping[ticker.upper()] = int(cik)

        logger.info(f"[EDGAR] Loaded {len(mapping)} ticker-to-CIK mappings")
        return mapping
