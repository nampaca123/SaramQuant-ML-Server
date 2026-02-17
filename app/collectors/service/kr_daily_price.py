import logging
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from app.schema import Market
from app.db import get_connection, StockRepository, DailyPriceRepository
from app.collectors.clients import PykrxClient
from app.collectors.utils.market_groups import MARKET_TO_PYKRX, INITIAL_LOOKBACK_DAYS

logger = logging.getLogger(__name__)


class KrDailyPriceCollector:
    def __init__(self):
        self._client = PykrxClient()

    def collect_all(self, market: Market | None = None) -> dict[str, int]:
        markets = self._resolve_markets(market)
        results: dict[str, int] = {}

        for mkt in markets:
            mkt_results = self._collect_market(mkt)
            results.update(mkt_results)

        return results

    def _resolve_markets(self, market: Market | None) -> list[Market]:
        if market:
            if market not in MARKET_TO_PYKRX:
                return []
            return [market]
        return list(MARKET_TO_PYKRX.keys())

    def _collect_market(self, market: Market) -> dict[str, int]:
        pykrx_market = MARKET_TO_PYKRX[market]
        stock_map = self._build_stock_map(market)
        if not stock_map:
            logger.warning(f"[KrDailyPrice] No active stocks for {market.value}")
            return {}

        last_date = self._get_market_last_date(market)
        dates = self._generate_dates(last_date)
        if not dates:
            logger.info(f"[KrDailyPrice] {market.value} already up to date")
            return {}

        logger.info(f"[KrDailyPrice] {market.value}: collecting {len(dates)} days")

        total = 0
        trading_days = 0
        for i, d in enumerate(dates, 1):
            date_str = d.strftime("%Y%m%d")
            df = self._client.fetch_market_ohlcv(date_str, pykrx_market)

            if df.empty:
                continue

            count = self._upsert_day(df, d, stock_map)
            if count > 0:
                trading_days += 1
            total += count

            if i % 10 == 0 or i == len(dates):
                logger.info(f"[KrDailyPrice] {market.value}: {i}/{len(dates)} days done")

        logger.info(
            f"[KrDailyPrice] {market.value}: {trading_days} trading days, {total} records"
        )
        return {market.value: total}

    def _build_stock_map(self, market: Market) -> dict[str, int]:
        """Returns {6-digit ticker: stock_id} for the given market."""
        with get_connection() as conn:
            repo = StockRepository(conn)
            stocks = repo.get_active_stocks(market)

        stock_map = {}
        for stock_id, symbol, _ in stocks:
            ticker = "".join(c for c in symbol if c.isdigit())[:6]
            stock_map[ticker] = stock_id
        return stock_map

    def _get_market_last_date(self, market: Market) -> date | None:
        with get_connection() as conn:
            return DailyPriceRepository(conn).get_latest_date_by_market(market)

    def _generate_dates(self, last_date: date | None) -> list[date]:
        start = (last_date + timedelta(days=1)) if last_date else (date.today() - timedelta(days=INITIAL_LOOKBACK_DAYS - 1))
        end = date.today()
        if start > end:
            return []
        return [start + timedelta(days=i) for i in range((end - start).days + 1)]

    def _upsert_day(
        self, df, price_date: date, stock_map: dict[str, int]
    ) -> int:
        rows: list[tuple] = []
        for ticker, row in df.iterrows():
            if int(row["volume"]) == 0:
                continue
            stock_id = stock_map.get(str(ticker))
            if stock_id is None:
                continue
            try:
                rows.append((
                    stock_id, price_date,
                    Decimal(str(int(row["open"]))),
                    Decimal(str(int(row["high"]))),
                    Decimal(str(int(row["low"]))),
                    Decimal(str(int(row["close"]))),
                    int(row["volume"]),
                ))
            except (InvalidOperation, ValueError) as e:
                logger.warning(f"[KrDailyPrice] Skip {ticker}: {e}")

        if not rows:
            return 0

        with get_connection() as conn:
            count = DailyPriceRepository(conn).bulk_upsert(rows)
            conn.commit()
        return count
