import logging
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from app.schema import DailyPrice, Market
from app.db import get_connection, StockRepository, DailyPriceRepository
from app.collectors.clients import PykrxClient

logger = logging.getLogger(__name__)

MARKET_TO_PYKRX = {
    Market.KR_KOSPI: "KOSPI",
    Market.KR_KOSDAQ: "KOSDAQ",
}


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

        last_date = self._get_market_last_date(stock_map)
        dates = self._generate_dates(last_date)
        if not dates:
            logger.info(f"[KrDailyPrice] {market.value} already up to date")
            return {}

        logger.info(f"[KrDailyPrice] {market.value}: collecting {len(dates)} days")

        results: dict[str, int] = {}
        for i, d in enumerate(dates, 1):
            date_str = d.strftime("%Y%m%d")
            df = self._client.fetch_market_ohlcv(date_str, pykrx_market)

            if df.empty:
                continue

            count = self._upsert_day(df, d, stock_map)
            for sym, c in count.items():
                results[sym] = results.get(sym, 0) + c

            if i % 10 == 0 or i == len(dates):
                logger.info(f"[KrDailyPrice] {market.value}: {i}/{len(dates)} days done")

        return results

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

    def _get_market_last_date(self, stock_map: dict[str, int]) -> date | None:
        """Find the latest date across all stocks in this market."""
        latest = None
        with get_connection() as conn:
            repo = DailyPriceRepository(conn)
            for stock_id in stock_map.values():
                d = repo.get_latest_date(stock_id)
                if d and (latest is None or d > latest):
                    latest = d
        return latest

    def _generate_dates(self, last_date: date | None) -> list[date]:
        start = (last_date + timedelta(days=1)) if last_date else (date.today() - timedelta(days=365))
        end = date.today()
        if start > end:
            return []
        return [start + timedelta(days=i) for i in range((end - start).days + 1)]

    def _upsert_day(
        self, df, price_date: date, stock_map: dict[str, int]
    ) -> dict[str, int]:
        counts: dict[str, int] = {}

        with get_connection() as conn:
            repo = DailyPriceRepository(conn)

            for ticker, row in df.iterrows():
                if int(row["volume"]) == 0:
                    continue

                stock_id = stock_map.get(str(ticker))
                if stock_id is None:
                    continue

                try:
                    price = DailyPrice(
                        symbol=str(ticker),
                        date=price_date,
                        open=Decimal(str(int(row["open"]))),
                        high=Decimal(str(int(row["high"]))),
                        low=Decimal(str(int(row["low"]))),
                        close=Decimal(str(int(row["close"]))),
                        volume=int(row["volume"]),
                    )
                except (InvalidOperation, ValueError) as e:
                    logger.warning(f"[KrDailyPrice] Skip {ticker}: {e}")
                    continue

                count = repo.upsert_batch(stock_id, [price])
                counts[str(ticker)] = count

            conn.commit()

        return counts
