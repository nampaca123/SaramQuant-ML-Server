from flask import request, jsonify

from app.api import api_bp
from app.api.errors import APIError
from app.api.utils import parse_date
from app.db.connection import get_connection
from app.services import IndicatorService


VALID_INDICATORS = {"sma", "ema", "wma", "rsi", "macd", "stochastic", "bollinger", "atr", "adx", "obv", "vma", "sar"}


@api_bp.route("/indicators/<symbol>")
def get_indicators(symbol: str):
    indicator = request.args.get("indicator")
    if not indicator:
        raise APIError("indicator parameter is required", 400)
    if indicator not in VALID_INDICATORS:
        raise APIError(f"Invalid indicator. Valid options: {', '.join(sorted(VALID_INDICATORS))}", 400)

    start_date = parse_date(request.args.get("start_date"))
    end_date = parse_date(request.args.get("end_date"))

    params = {}
    for key in ["period", "fast_period", "slow_period", "signal_period", "k_period", "d_period"]:
        val = request.args.get(key, type=int)
        if val:
            params[key] = val

    for key in ["std_dev", "af_start", "af_step", "af_max"]:
        val = request.args.get(key, type=float)
        if val:
            params[key] = val

    with get_connection() as conn:
        service = IndicatorService(conn)
        result = service.calculate(
            symbol=symbol,
            indicator=indicator,
            start_date=start_date,
            end_date=end_date,
            **params
        )

    return jsonify({"symbol": symbol, "indicator": indicator, "data": result})
