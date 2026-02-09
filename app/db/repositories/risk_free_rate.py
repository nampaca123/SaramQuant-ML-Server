from datetime import date
from decimal import Decimal
from psycopg2.extensions import connection
from psycopg2.extras import execute_values
from app.schema import Country, Maturity, RiskFreeRate


class RiskFreeRateRepository:
    def __init__(self, conn: connection):
        self._conn = conn

    def upsert_batch(self, rates: list[RiskFreeRate]) -> int:
        if not rates:
            return 0
        query = """
            INSERT INTO risk_free_rates (country, maturity, date, rate)
            VALUES %s
            ON CONFLICT (country, maturity, date) DO UPDATE SET
                rate = EXCLUDED.rate
        """
        data = [(r.country.value, r.maturity.value, r.date, r.rate) for r in rates]
        with self._conn.cursor() as cur:
            execute_values(cur, query, data)
            return cur.rowcount

    def get_latest_date(self, country: Country, maturity: Maturity) -> date | None:
        query = """
            SELECT MAX(date) FROM risk_free_rates
            WHERE country = %s AND maturity = %s
        """
        with self._conn.cursor() as cur:
            cur.execute(query, (country.value, maturity.value))
            result = cur.fetchone()
            return result[0] if result and result[0] else None

    def get_rates(
        self,
        country: Country,
        maturity: Maturity,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int | None = None
    ) -> list[RiskFreeRate]:
        conditions = ["country = %s", "maturity = %s"]
        params: list = [country.value, maturity.value]

        if start_date:
            conditions.append("date >= %s")
            params.append(start_date)
        if end_date:
            conditions.append("date <= %s")
            params.append(end_date)

        where_clause = " AND ".join(conditions)
        limit_clause = f"LIMIT {limit}" if limit else ""

        query = f"""
            SELECT country, maturity, date, rate
            FROM risk_free_rates
            WHERE {where_clause}
            ORDER BY date DESC
            {limit_clause}
        """

        with self._conn.cursor() as cur:
            cur.execute(query, tuple(params))
            return [
                RiskFreeRate(
                    country=Country(row[0]),
                    maturity=Maturity(row[1]),
                    date=row[2],
                    rate=row[3]
                )
                for row in cur.fetchall()
            ]

    def get_latest_rate(self, country: Country, maturity: Maturity) -> Decimal | None:
        query = """
            SELECT rate FROM risk_free_rates
            WHERE country = %s AND maturity = %s
            ORDER BY date DESC
            LIMIT 1
        """
        with self._conn.cursor() as cur:
            cur.execute(query, (country.value, maturity.value))
            result = cur.fetchone()
            return result[0] if result else None

    # ── Delete operations ──

    def delete_all(self) -> int:
        with self._conn.cursor() as cur:
            cur.execute("DELETE FROM risk_free_rates")
            return cur.rowcount

    def delete_before(self, cutoff: date) -> int:
        with self._conn.cursor() as cur:
            cur.execute("DELETE FROM risk_free_rates WHERE date < %s", (cutoff,))
            return cur.rowcount
