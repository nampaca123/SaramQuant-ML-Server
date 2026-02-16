import logging
import os
import time

import requests

from app.collectors.service.kr_financial_statement import KrFinancialStatementCollector

logger = logging.getLogger(__name__)

POLL_INTERVAL = 30
POLL_TIMEOUT = 1800


class FundamentalCollectionService:
    def collect_all(self, region: str) -> dict[str, int]:
        if region == "kr":
            return self._collect_kr()
        elif region == "us":
            return self._collect_us()
        return {}

    def _collect_kr(self) -> dict[str, int]:
        collector = KrFinancialStatementCollector()
        results = collector.collect_all()
        logger.info(f"[FundCollection] KR complete: {results}")
        return results

    def _collect_us(self) -> dict[str, int]:
        base_url = os.getenv("USA_FS_COLLECTOR_URL")
        api_key = os.getenv("USA_FS_COLLECTOR_AUTH_KEY")
        if not base_url or not api_key:
            raise RuntimeError("USA_FS_COLLECTOR_URL and USA_FS_COLLECTOR_AUTH_KEY must be set")

        headers = {"x-api-key": api_key}
        url = f"{base_url.rstrip('/')}/usa-financial-statements"

        resp = requests.post(f"{url}/collect", headers=headers, timeout=30)
        resp.raise_for_status()
        job_id = resp.json()["jobId"]
        logger.info(f"[FundCollection] US remote job started: {job_id}")

        deadline = time.time() + POLL_TIMEOUT
        while time.time() < deadline:
            time.sleep(POLL_INTERVAL)
            status_resp = requests.get(f"{url}/status/{job_id}", headers=headers, timeout=30)
            status_resp.raise_for_status()
            data = status_resp.json()
            status = data.get("status")

            if status == "completed":
                result = data.get("result", {})
                logger.info(f"[FundCollection] US (remote) complete: {result}")
                return result
            elif status == "failed":
                raise RuntimeError(f"Remote job failed: {data.get('error', 'unknown')}")

            progress = data.get("progress", {})
            phase = progress.get("phase", "processing")
            parsed = progress.get("parsed")
            total = progress.get("total")
            if parsed and total:
                logger.info(f"[FundCollection] US remote: {phase} ({parsed}/{total})")
            else:
                logger.info(f"[FundCollection] US remote: {phase}")

        raise TimeoutError(f"Remote job {job_id} timed out after {POLL_TIMEOUT}s")
