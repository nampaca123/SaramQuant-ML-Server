from psycopg2.extensions import connection
from psycopg2.extras import execute_values
from app.schema import Market, StockInfo


class StockRepository:
    def __init__(self, conn: connection):
        self._conn = conn

    def get_by_symbol(
        self, symbol: str, market: Market | None = None
    ) -> tuple[int, str, str, Market] | None:
        if market:
            query = """
                SELECT id, symbol, name, market FROM stocks
                WHERE symbol = %s AND market = %s AND is_active = true
            """
            params: tuple = (symbol, market.value)
        else:
            query = """
                SELECT id, symbol, name, market FROM stocks
                WHERE symbol = %s AND is_active = true
            """
            params = (symbol,)

        with self._conn.cursor() as cur:
            cur.execute(query, params)
            row = cur.fetchone()
            if not row:
                return None
            return (row[0], row[1], row[2], Market(row[3]))

    def get_list(
        self,
        market: Market | None = None,
        limit: int = 100,
        offset: int = 0
    ) -> list[tuple[int, str, str, Market]]:
        if market:
            query = """
                SELECT id, symbol, name, market FROM stocks
                WHERE is_active = true AND market = %s
                ORDER BY symbol
                LIMIT %s OFFSET %s
            """
            params: tuple = (market.value, limit, offset)
        else:
            query = """
                SELECT id, symbol, name, market FROM stocks
                WHERE is_active = true
                ORDER BY symbol
                LIMIT %s OFFSET %s
            """
            params = (limit, offset)

        with self._conn.cursor() as cur:
            cur.execute(query, params)
            return [(row[0], row[1], row[2], Market(row[3])) for row in cur.fetchall()]

    def upsert_batch(self, stocks: list[StockInfo]) -> int:
        if not stocks:
            return 0
        query = """
            INSERT INTO stocks (symbol, name, market)
            VALUES %s
            ON CONFLICT (symbol, market)
            DO UPDATE SET name = EXCLUDED.name, updated_at = now()
        """
        data = [(s.symbol, s.name, s.market.value) for s in stocks]
        with self._conn.cursor() as cur:
            execute_values(cur, query, data)
            return cur.rowcount

    def get_active_stocks(
        self, market: Market | None = None
    ) -> list[tuple[int, str, Market]]:
        if market:
            query = """
                SELECT id, symbol, market FROM stocks
                WHERE is_active = true AND market = %s
            """
            params: tuple = (market.value,)
        else:
            query = "SELECT id, symbol, market FROM stocks WHERE is_active = true"
            params = ()

        with self._conn.cursor() as cur:
            cur.execute(query, params)
            return [(row[0], row[1], Market(row[2])) for row in cur.fetchall()]

    def get_stocks_without_sector(self, market: Market) -> list[tuple[int, str]]:
        query = """
            SELECT id, symbol FROM stocks
            WHERE sector IS NULL AND is_active = true AND market = %s
        """
        with self._conn.cursor() as cur:
            cur.execute(query, (market.value,))
            return [(row[0], row[1]) for row in cur.fetchall()]

    def update_sectors(self, updates: list[tuple[str, str, str]]) -> int:
        if not updates:
            return 0
        query = """
            UPDATE stocks AS s
            SET sector = v.sector, updated_at = now()
            FROM (VALUES %s) AS v(symbol, market, sector)
            WHERE s.symbol = v.symbol AND s.market = v.market::market_type
        """
        with self._conn.cursor() as cur:
            execute_values(cur, query, updates)
            return cur.rowcount

    def deactivate_unlisted(self, market: Market, active_symbols: set[str]) -> int:
        if not active_symbols:
            return 0
        query = """
            UPDATE stocks SET is_active = false, updated_at = now()
            WHERE market = %s AND symbol != ALL(%s) AND is_active = true
        """
        with self._conn.cursor() as cur:
            cur.execute(query, (market.value, list(active_symbols)))
            return cur.rowcount
