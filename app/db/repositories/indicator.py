from psycopg2.extensions import connection
from psycopg2.extras import RealDictCursor
from app.schema import Market

_COL_TYPES = [
    ("stock_id", "bigint"), ("date", "date"),
    ("sma_20", "numeric"), ("ema_20", "numeric"), ("wma_20", "numeric"),
    ("rsi_14", "numeric"),
    ("macd", "numeric"), ("macd_signal", "numeric"), ("macd_hist", "numeric"),
    ("stoch_k", "numeric"), ("stoch_d", "numeric"),
    ("bb_upper", "numeric"), ("bb_middle", "numeric"), ("bb_lower", "numeric"),
    ("atr_14", "numeric"), ("adx_14", "numeric"), ("plus_di", "numeric"), ("minus_di", "numeric"),
    ("obv", "bigint"), ("vma_20", "bigint"),
    ("sar", "numeric"),
    ("beta", "numeric"), ("alpha", "numeric"), ("sharpe", "numeric"),
]
COLUMNS = [c for c, _ in _COL_TYPES]
_UNNEST = ", ".join(f"%s::{t}[]" for _, t in _COL_TYPES)


_UPDATE_COLS = [c for c in COLUMNS if c not in ("stock_id", "date")]
_UPSERT_CONFLICT = (
    "ON CONFLICT (stock_id, date) DO UPDATE SET "
    + ", ".join(f"{c} = EXCLUDED.{c}" for c in _UPDATE_COLS)
)


class IndicatorRepository:
    def __init__(self, conn: connection):
        self._conn = conn

    def upsert_batch(self, rows: list[tuple]) -> int:
        if not rows:
            return 0
        cols = [list(c) for c in zip(*rows)]
        with self._conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO stock_indicators ({', '.join(COLUMNS)}) "
                f"SELECT * FROM UNNEST({_UNNEST}) "
                f"{_UPSERT_CONFLICT}",
                cols,
            )
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
