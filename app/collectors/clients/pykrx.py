import logging
import os
import threading
import time
from datetime import date

import pandas as pd
import requests
from pykrx import stock
from pykrx.website.comm import webio

from app.collectors.utils.skip_rules import SKIP_INDICES
from app.collectors.utils.throttle import Throttle

logger = logging.getLogger(__name__)

COLUMN_MAP = {"시가": "open", "고가": "high", "저가": "low", "종가": "close", "거래량": "volume"}

_RETRIES = 3
_RETRY_WAIT = 3.0

# ── KRX Login ──

_session = requests.Session()
_logged_in = False
_login_lock = threading.Lock()

_LOGIN_PAGE = "https://data.krx.co.kr/contents/MDC/COMS/client/MDCCOMS001.cmd"
_LOGIN_JSP = "https://data.krx.co.kr/contents/MDC/COMS/client/view/login.jsp?site=mdc"
_LOGIN_URL = "https://data.krx.co.kr/contents/MDC/COMS/client/MDCCOMS001D1.cmd"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _do_login() -> bool:
    krx_id = os.environ.get("KRX_ID")
    krx_pw = os.environ.get("KRX_PASSWORD")
    if not krx_id or not krx_pw:
        logger.warning("[pykrx] KRX_ID / KRX_PASSWORD not set — skipping login")
        return False

    _session.cookies.clear()
    hdrs = {"User-Agent": _UA}

    _session.get(_LOGIN_PAGE, headers=hdrs, timeout=15)
    _session.get(_LOGIN_JSP, headers={**hdrs, "Referer": _LOGIN_PAGE}, timeout=15)

    payload = {
        "mbrNm": "", "telNo": "", "di": "", "certType": "",
        "mbrId": krx_id, "pw": krx_pw,
    }
    resp = _session.post(_LOGIN_URL, data=payload, headers={**hdrs, "Referer": _LOGIN_PAGE}, timeout=15)
    data = resp.json()
    code = data.get("_error_code", "")

    if code == "CD011":
        payload["skipDup"] = "Y"
        resp = _session.post(_LOGIN_URL, data=payload, headers={**hdrs, "Referer": _LOGIN_PAGE}, timeout=15)
        data = resp.json()
        code = data.get("_error_code", "")

    if code != "CD001":
        logger.error(f"[pykrx] KRX login failed: {data}")
        return False

    logger.info("[pykrx] KRX login OK")
    return True


def _is_auth_failure(resp: requests.Response) -> bool:
    return resp.status_code != 200


def _refresh_and_retry(method: str, url: str, headers: dict, params: dict) -> requests.Response:
    """Acquire lock, re-check with a fresh request, re-login only if still failing."""
    with _login_lock:
        fn = _session.post if method == "post" else _session.get
        kwargs = {"data": params} if method == "post" else {"params": params}
        resp = fn(url, headers=headers, **kwargs)
        if _is_auth_failure(resp):
            _do_login()
            resp = fn(url, headers=headers, **kwargs)
    return resp


def _setup_webio_hooks() -> None:
    def _post_read(self, **params):
        resp = _session.post(self.url, headers=self.headers, data=params)
        if _is_auth_failure(resp):
            resp = _refresh_and_retry("post", self.url, self.headers, params)
        return resp

    def _get_read(self, **params):
        resp = _session.get(self.url, headers=self.headers, params=params)
        if _is_auth_failure(resp):
            resp = _refresh_and_retry("get", self.url, self.headers, params)
        return resp

    webio.Post.read = _post_read
    webio.Get.read = _get_read


def _ensure_login() -> None:
    global _logged_in
    if _logged_in:
        return
    if _do_login():
        _setup_webio_hooks()
        _logged_in = True


# ── PykrxClient ──

class PykrxClient:
    def __init__(self):
        self._throttle = Throttle(min_interval=0.5)
        _ensure_login()

    def _call(self, fn, *args, **kwargs):
        for attempt in range(_RETRIES):
            self._throttle.wait()
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                if attempt == _RETRIES - 1:
                    raise
                wait = _RETRY_WAIT * (attempt + 1)
                logger.warning(f"[pykrx] Retry {attempt + 1}/{_RETRIES} in {wait}s: {e}")
                time.sleep(wait)

    def get_trading_days(self, start: str, end: str) -> list[date] | None:
        try:
            df = self._call(stock.get_index_ohlcv, start, end, "1001")
        except Exception:
            logger.error(f"[pykrx] Failed to fetch trading days {start}~{end}")
            return None
        if df is None or df.empty:
            return []
        return [ts.date() for ts in df.index]

    def fetch_market_ohlcv(self, date_str: str, market: str) -> pd.DataFrame:
        df = self._call(stock.get_market_ohlcv, date_str, market=market)
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.rename(columns=COLUMN_MAP)
        return df[["open", "high", "low", "close", "volume"]]

    def fetch_sector_map(self, market: str) -> dict[str, str]:
        index_tickers = self._call(stock.get_index_ticker_list, market=market)

        sector_map: dict[str, str] = {}
        for idx_ticker in index_tickers:
            if idx_ticker in SKIP_INDICES:
                continue
            idx_name = self._call(stock.get_index_ticker_name, idx_ticker)
            try:
                components = self._call(stock.get_index_portfolio_deposit_file, idx_ticker)
            except Exception as e:
                logger.warning(f"[pykrx] Skip index {idx_ticker} {idx_name}: {e}")
                continue
            for sym in components:
                if sym not in sector_map:
                    sector_map[sym] = idx_name

        logger.info(f"[pykrx] {market} sector map: {len(sector_map)} stocks")
        return sector_map

    def fetch_index_ohlcv(self, start: str, end: str, ticker: str) -> pd.DataFrame:
        df = self._call(stock.get_index_ohlcv, start, end, ticker)
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.rename(columns=COLUMN_MAP)
        return df[["open", "high", "low", "close", "volume"]]
