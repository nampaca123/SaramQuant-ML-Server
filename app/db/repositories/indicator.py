from psycopg2.extensions import connection
from psycopg2.extras import execute_values
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
        placeholders = ", ".join(["%s"] * len(COLUMNS))
        col_names = ", ".join(COLUMNS)
        query = f"INSERT INTO stock_indicators ({col_names}) VALUES %s"
        with self._conn.cursor() as cur:
            execute_values(cur, query, rows)
            return cur.rowcount
