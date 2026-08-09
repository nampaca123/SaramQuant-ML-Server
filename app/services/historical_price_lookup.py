import os
import logging
from datetime import date, timedelta
from decimal import Decimal

from app.db import get_connection, DailyPriceRepository, StockRepository
from app.db.repositories.exchange_rate import ExchangeRateRepository
from app.collectors.clients import AlpacaClient, PykrxClient, YfinanceClient, EcosClient
from app.schema import Market

logger = logging.getLogger(__name__)

MAX_LOOKBACK_DAYS = 5
ECOS_FX_LOOKBACK_DAYS = 7


class HistoricalPriceLookup:
    def __init__(self):
        self._pykrx = PykrxClient()
        self._alpaca = AlpacaClient(
            os.getenv("ALPACA_API_KEY", ""),
            os.getenv("ALPACA_SECRET_KEY", ""),
        )
        self._yfinance = YfinanceClient()
        self._ecos = EcosClient(os.getenv("ECOS_API_KEY", ""))

    def lookup(self, stock_id: int, target_date: date) -> dict | None:
        with get_connection() as conn:
            stock_repo = StockRepository(conn)
            stock = stock_repo.find_by_id(stock_id)
            if not stock:
                return None

        symbol = stock["symbol"]
        market = Market(stock["market"])
        is_kr = market in (Market.KR_KOSPI, Market.KR_KOSDAQ)

        ohlc = self._try_db_ohlc(stock_id, target_date)
        if ohlc is not None:
            result = {**ohlc, "date": target_date, "source": "DB"}
        else:
            result = None
            for offset in range(MAX_LOOKBACK_DAYS):
                d = target_date - timedelta(days=offset)
                price = self._try_external(symbol, d, is_kr)
                if price is not None:
                    result = {"close": price, "date": d, "source": "AUTO"}
                    break

        if result is None:
            return None

        if not is_kr:
            result["fx_rate"] = self._resolve_fx_rate(target_date)

        return result

    def _resolve_fx_rate(self, target_date: date) -> float | None:
        with get_connection() as conn:
            fx_repo = ExchangeRateRepository(conn)
            rate = fx_repo.get_rate_on_or_before("USDKRW", target_date)
            if rate is not None:
                return float(rate)

        start = target_date - timedelta(days=ECOS_FX_LOOKBACK_DAYS)
        start_str = start.strftime("%Y%m%d")
        end_str = target_date.strftime("%Y%m%d")
        try:
            rows = self._ecos.fetch_exchange_rates(start_str, end_str)
        except Exception as e:
            logger.warning(f"[FX] ECOS fetch failed for {target_date}: {e}")
            return None

        if not rows:
            return None

        best = None
        for r in rows:
            d = date(int(r["TIME"][:4]), int(r["TIME"][4:6]), int(r["TIME"][6:8]))
            val = Decimal(r["DATA_VALUE"].replace(",", ""))
            if d <= target_date and (best is None or d > best[0]):
                best = (d, val)

        if best is None:
            return None

        # 서빙 경로는 읽기 전용 — 조회한 환율은 응답으로만 돌려주고 레이크에 쓰지 않는다.
        return float(best[1])

    def _try_db_ohlc(self, stock_id: int, target_date: date) -> dict | None:
        with get_connection() as conn:
            repo = DailyPriceRepository(conn)
            for offset in range(MAX_LOOKBACK_DAYS):
                d = target_date - timedelta(days=offset)
                prices = repo.get_prices(stock_id, start_date=d, end_date=d, limit=1)
                if prices:
                    p = prices[0]
                    return {
                        "open": Decimal(str(p.open)),
                        "high": Decimal(str(p.high)),
                        "low": Decimal(str(p.low)),
                        "close": Decimal(str(p.close)),
                    }
        return None

    def _try_external(self, symbol: str, d: date, is_kr: bool) -> Decimal | None:
        if is_kr:
            return self._try_pykrx(symbol, d)
        price = self._try_alpaca(symbol, d)
        if price is not None:
            return price
        return self._try_yfinance(symbol, d)

    def _try_pykrx(self, symbol: str, d: date) -> Decimal | None:
        try:
            date_str = d.strftime("%Y%m%d")
            df = self._pykrx.fetch_market_ohlcv(date_str, "ALL")
            if df.empty or symbol not in df.index:
                return None
            close = df.loc[symbol, "close"]
            if close == 0:
                return None
            return Decimal(str(int(close)))
        except Exception as e:
            logger.warning(f"[PriceLookup] pykrx failed for {symbol} on {d}: {e}")
            return None

    def _try_alpaca(self, symbol: str, d: date) -> Decimal | None:
        try:
            bars = self._alpaca.fetch_daily_bars([symbol], d, d)
            bar_list = bars.get(symbol, [])
            if not bar_list:
                return None
            return Decimal(str(bar_list[0]["close"]))
        except Exception as e:
            logger.warning(f"[PriceLookup] Alpaca failed for {symbol} on {d}: {e}")
            return None

    def _try_yfinance(self, symbol: str, d: date) -> Decimal | None:
        try:
            start_str = d.strftime("%Y-%m-%d")
            end_str = (d + timedelta(days=1)).strftime("%Y-%m-%d")
            df = self._yfinance.fetch_index_prices(symbol, start_str, end_str)
            if df.empty:
                return None
            return Decimal(str(round(df.iloc[0]["close"], 4)))
        except Exception as e:
            logger.warning(f"[PriceLookup] yfinance failed for {symbol} on {d}: {e}")
            return None
