from psycopg2.extensions import connection
from psycopg2.extras import execute_values, RealDictCursor
from app.schema import Market


COLUMNS = [
    "stock_id", "date",
    "sma_20", "ema_20", "wma_20",
    "rsi_14",
    "macd", "macd_signal", "macd_hist",
    "stoch_k", "stoch_d",
    "bb_upper", "bb_middle", "bb_lower",
    "atr_14", "adx_14", "plus_di", "minus_di",
    "obv", "vma_20",
    "sar",
    "beta", "alpha", "sharpe",
]


class IndicatorRepository:
    def __init__(self, conn: connection):
        self._conn = conn

    def delete_by_markets(self, markets: list[Market]) -> int:
        query = """
            DELETE FROM stock_indicators
            WHERE stock_id IN (
                SELECT id FROM stocks WHERE market = ANY(%s::market_type[])
            )
        """
        market_values = [m.value for m in markets]
        with self._conn.cursor() as cur:
            cur.execute(query, (market_values,))
            return cur.rowcount

    def insert_batch(self, rows: list[tuple]) -> int:
        if not rows:
            return 0
        col_names = ", ".join(COLUMNS)
        query = f"INSERT INTO stock_indicators ({col_names}) VALUES %s"
        with self._conn.cursor() as cur:
            execute_values(cur, query, rows)
            return cur.rowcount

    def get_latest_by_stock(self, stock_id: int) -> dict | None:
        query = """
            SELECT si.*, dp.close
            FROM stock_indicators si
            JOIN daily_prices dp ON dp.stock_id = si.stock_id AND dp.date = si.date
            WHERE si.stock_id = %s
            ORDER BY si.date DESC LIMIT 1
        """
        with self._conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, (stock_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    def get_all_by_market(self, market: Market) -> dict[int, dict]:
        query = """
            SELECT si.*, dp.close, s.sector
            FROM stock_indicators si
            JOIN daily_prices dp ON dp.stock_id = si.stock_id AND dp.date = si.date
            JOIN stocks s ON s.id = si.stock_id
            WHERE s.market = %s AND s.is_active = true
        """
        with self._conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, (market.value,))
            return {row["stock_id"]: dict(row) for row in cur.fetchall()}
