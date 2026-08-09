import logging
from datetime import datetime, timedelta, timezone

from app.collectors.service.kr_financial_statement import KrFinancialStatementCollector
from app.log.run_record import read_run_summary

logger = logging.getLogger(__name__)

USA_SUMMARY_KEY = "run-summary/usa_fstatements.json"
MAX_SUMMARY_AGE = timedelta(hours=72)


class FreshnessGateError(RuntimeError):
    """US 재무는 별도 서비스가 적재하므로, 신선하지 않으면 이 단계만 건너뛴다."""


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
        summary = read_run_summary(USA_SUMMARY_KEY)
        if not summary:
            self._reject("no run summary found")
        if summary.get("status") != "ok":
            self._reject(f"last run status is '{summary.get('status')}'")

        age = self._age(summary.get("written_at_utc"))
        if age is None:
            self._reject(f"unreadable written_at_utc '{summary.get('written_at_utc')}'")
        if age > MAX_SUMMARY_AGE:
            self._reject(f"last run is {age.total_seconds() / 3600:.1f}h old")

        logger.info(
            "[FundCollection] US freshness gate passed: run_id=%s age=%.1fh",
            summary.get("run_id"), age.total_seconds() / 3600,
        )
        return {}

    @staticmethod
    def _age(written_at: str | None) -> timedelta | None:
        if not written_at:
            return None
        try:
            moment = datetime.fromisoformat(written_at)
        except (TypeError, ValueError):
            return None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - moment

    @staticmethod
    def _reject(reason: str) -> None:
        message = f"US financial statements are not fresh ({USA_SUMMARY_KEY}): {reason}"
        logger.warning(f"[FundCollection] {message}")
        raise FreshnessGateError(message)
