import os
import logging
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from app.schema import Country, Maturity, RiskFreeRate
from app.db import get_connection, RiskFreeRateRepository
from app.collectors.clients import EcosClient, FredClient

logger = logging.getLogger(__name__)


class RiskFreeRateCollector:
    ECOS_SUPPORTED = {Maturity.D91, Maturity.Y3, Maturity.Y10}
    FRED_SUPPORTED = {Maturity.D91, Maturity.Y1, Maturity.Y3, Maturity.Y10}

    def __init__(self):
        self._ecos = EcosClient(os.getenv("ECOS_API_KEY", ""))
        self._fred = FredClient(os.getenv("FRED_API_KEY", ""))

    def collect_kr(self, maturity: Maturity) -> int:
        if maturity not in self.ECOS_SUPPORTED:
            logger.info(f"[RiskFreeRate] KR {maturity.value} not available from ECOS")
            return 0

        start_date = self._get_start_date(Country.KR, maturity)
        end_date = date.today().strftime("%Y%m%d")
        start_date_str = start_date.strftime("%Y%m%d") if start_date else "20000101"

        rows = self._ecos.fetch_rates(maturity, start_date_str, end_date)
        if not rows:
            return 0

        rates = self._transform_ecos(maturity, rows)
        return self._save(rates)

    def collect_us(self, maturity: Maturity) -> int:
        if maturity not in self.FRED_SUPPORTED:
            return 0

        start_date = self._get_start_date(Country.US, maturity)
        end_date = date.today().strftime("%Y-%m-%d")
        start_date_str = start_date.strftime("%Y-%m-%d") if start_date else "2000-01-01"

        rows = self._fred.fetch_rates(maturity, start_date_str, end_date)
        if not rows:
            return 0

        rates = self._transform_fred(maturity, rows)
        return self._save(rates)

    def collect_all(self) -> dict[str, int]:
        results = {}

        for maturity in self.ECOS_SUPPORTED:
            key = f"KR_{maturity.value}"
            results[key] = self.collect_kr(maturity)

        for maturity in self.FRED_SUPPORTED:
            key = f"US_{maturity.value}"
            results[key] = self.collect_us(maturity)

        return results

    def _get_start_date(self, country: Country, maturity: Maturity) -> date | None:
        with get_connection() as conn:
            repo = RiskFreeRateRepository(conn)
            latest = repo.get_latest_date(country, maturity)

        if latest:
            return latest + timedelta(days=1)
        return None

    def _transform_ecos(self, maturity: Maturity, rows: list[dict]) -> list[RiskFreeRate]:
        rates = []
        for row in rows:
            try:
                time_str = row["TIME"]
                rate_date = date(int(time_str[:4]), int(time_str[4:6]), int(time_str[6:8]))
                rate_value = Decimal(row["DATA_VALUE"])
                rates.append(RiskFreeRate(
                    country=Country.KR,
                    maturity=maturity,
                    date=rate_date,
                    rate=rate_value,
                ))
            except (KeyError, InvalidOperation, ValueError) as e:
                logger.warning(f"[ECOS] Skip invalid row: {e}")
                continue
        return rates

    def _transform_fred(self, maturity: Maturity, rows: list[dict]) -> list[RiskFreeRate]:
        rates = []
        for row in rows:
            try:
                if row["value"] == ".":
                    continue
                rate_date = date.fromisoformat(row["date"])
                rate_value = Decimal(row["value"])
                rates.append(RiskFreeRate(
                    country=Country.US,
                    maturity=maturity,
                    date=rate_date,
                    rate=rate_value,
                ))
            except (KeyError, InvalidOperation, ValueError) as e:
                logger.warning(f"[FRED] Skip invalid row: {e}")
                continue
        return rates

    def _save(self, rates: list[RiskFreeRate]) -> int:
        if not rates:
            return 0

        with get_connection() as conn:
            repo = RiskFreeRateRepository(conn)
            count = repo.upsert_batch(rates)
            conn.commit()

        logger.info(f"[RiskFreeRate] Saved {count} rows")
        return count
