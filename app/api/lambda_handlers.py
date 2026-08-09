"""API Gateway v2 → Lambda 핸들러 5종. 인증·본문 파싱·응답 직렬화·요청 로그를 한곳에서 처리한다."""
import base64
import hmac
import json
import logging
import os
import time
from datetime import date

from app.log.service.audit_log_service import log_api
from app.quant.simulation.defaults import (
    DEFAULT_CONFIDENCE,
    DEFAULT_DAYS,
    DEFAULT_LOOKBACK,
    DEFAULT_NUM_SIMULATIONS,
)
from app.schema import Market

logger = logging.getLogger(__name__)

MARKET_GROUPS = ("KR", "US")
MARKETS = {market.value: market for market in Market}

_price_lookup = None


# ── 서비스 시임: 콜드스타트를 줄이려 호출 시점에 import한다 ──


def _analysis_service():
    from app.services.portfolio_analysis_service import PortfolioAnalysisService

    return PortfolioAnalysisService.full_analysis


def _portfolio_simulation_service():
    from app.services.portfolio_simulation_service import PortfolioSimulationService

    return PortfolioSimulationService.run


def _stock_simulation_service():
    from app.services.simulation_service import SimulationService

    return SimulationService.run


def _price_lookup_service():
    global _price_lookup
    if _price_lookup is None:
        from app.services.historical_price_lookup import HistoricalPriceLookup

        _price_lookup = HistoricalPriceLookup()
    return _price_lookup


def _reset_price_lookup() -> None:
    global _price_lookup
    _price_lookup = None


def _stock_repository():
    from app.db.repositories.stock import StockRepository

    return StockRepository()


# ── 공통 헬퍼 ──


def _respond(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, ensure_ascii=False, default=str),
    }


def _authorize(headers: dict | None) -> bool:
    expected = os.getenv("CALC_AUTH_KEY", "")
    if not expected:
        return False
    provided = ""
    for name, value in (headers or {}).items():
        if name.lower() == "x-api-key":
            provided = value or ""
            break
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def _json_body(event: dict) -> dict:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("Request body must be a JSON object")
    return parsed


def _query(event: dict) -> dict:
    return event.get("queryStringParameters") or {}


def _run(event: dict, method: str, path: str, action, require_auth: bool = True) -> dict:
    started = time.monotonic()
    status = 500
    try:
        if require_auth and not _authorize(event.get("headers")):
            status, body = 401, {"error": "Unauthorized"}
        else:
            status, body = action(event)
        return _respond(status, body)
    except (ValueError, json.JSONDecodeError) as error:
        status = 400
        return _respond(status, {"error": str(error)})
    except Exception as error:  # noqa: BLE001
        logger.exception("Unhandled error on %s %s", method, path)
        status = 500
        return _respond(status, {"error": str(error)})
    finally:
        log_api(method, path, status, int((time.monotonic() - started) * 1000))


def _holdings_payload(event: dict) -> tuple[str, list[dict]]:
    body = _json_body(event)
    market_group = body.get("market_group")
    if market_group not in MARKET_GROUPS:
        raise ValueError(f"market_group must be one of {list(MARKET_GROUPS)}")
    holdings = body.get("holdings")
    if not holdings:
        raise ValueError("holdings is required and must be non-empty")
    return market_group, holdings


def _simulation_params(raw: dict, default_method: str) -> dict:
    return {
        "days": int(raw.get("days", DEFAULT_DAYS)),
        "num_simulations": int(raw.get("simulations", DEFAULT_NUM_SIMULATIONS)),
        "confidence": float(raw.get("confidence", DEFAULT_CONFIDENCE)),
        "lookback": int(raw.get("lookback", DEFAULT_LOOKBACK)),
        "method": raw.get("method", default_method),
    }


# ── 핸들러 ──


def handle_health(event, context=None):
    return _run(event, "GET", "/health", lambda _: (200, {"status": "ok"}), require_auth=False)


def handle_analysis(event, context=None):
    return _run(event, "POST", "/internal/portfolios/full-analysis", _run_analysis)


def _run_analysis(event: dict) -> tuple[int, dict]:
    market_group, holdings = _holdings_payload(event)
    return 200, _analysis_service()(market_group, holdings)


def handle_portfolio_simulation(event, context=None):
    return _run(event, "POST", "/internal/portfolios/simulation", _run_portfolio_simulation)


def _run_portfolio_simulation(event: dict) -> tuple[int, dict]:
    market_group, holdings = _holdings_payload(event)
    raw = {**_query(event), **_json_body(event)}
    result = _portfolio_simulation_service()(
        market_group=market_group, holdings=holdings, **_simulation_params(raw, "bootstrap")
    )
    return 200, result


def handle_stock_simulation(event, context=None):
    return _run(event, "GET", "/internal/stocks/{symbol}/simulation", _run_stock_simulation)


def _run_stock_simulation(event: dict) -> tuple[int, dict]:
    symbol = _path_symbol(event)
    if not symbol:
        raise ValueError("symbol path parameter is required")
    query = _query(event)
    market = MARKETS.get(query.get("market", ""))
    if market is None:
        raise ValueError(f"Invalid market. Choose from: {list(MARKETS)}")
    result = _stock_simulation_service()(
        symbol=symbol, market=market, **_simulation_params(query, "gbm")
    )
    return 200, result


def _path_symbol(event: dict) -> str:
    symbol = (event.get("pathParameters") or {}).get("symbol")
    if symbol:
        return symbol
    segments = (event.get("rawPath") or "").strip("/").split("/")
    if len(segments) >= 4 and segments[-1] == "simulation" and segments[-3] == "stocks":
        return segments[-2]
    return ""


def handle_price_lookup(event, context=None):
    return _run(event, "POST", "/internal/portfolios/price-lookup", _run_price_lookup)


def _run_price_lookup(event: dict) -> tuple[int, dict]:
    body = _json_body(event)
    date_str = body.get("date")
    if not date_str:
        raise ValueError("date is required")
    try:
        target_date = date.fromisoformat(date_str)
    except ValueError:
        raise ValueError("Invalid date format. Use YYYY-MM-DD") from None

    stock_id = _resolve_stock_id(body)
    if stock_id is None:
        return 404, {"error": "Stock not found"}

    result = _price_lookup_service().lookup(stock_id, target_date)
    if result is None:
        return 200, {"found": False, "message": "Price not available"}

    response = {
        "found": True,
        "close": float(result["close"]),
        "date": result["date"].isoformat(),
        "source": result["source"],
    }
    for key in ("open", "high", "low"):
        if key in result:
            response[key] = float(result[key])
    if "fx_rate" in result:
        response["fx_rate"] = result["fx_rate"]
    return 200, response


def _resolve_stock_id(body: dict) -> int | None:
    if body.get("stock_id"):
        return int(body["stock_id"])
    symbol, market_str = body.get("symbol"), body.get("market")
    if not symbol or not market_str:
        raise ValueError("stock_id, or symbol and market, are required")
    market = MARKETS.get(market_str)
    if market is None:
        raise ValueError(f"Invalid market. Choose from: {list(MARKETS)}")
    found = _stock_repository().get_by_symbol(symbol, market)
    return None if found is None else int(found[0])
