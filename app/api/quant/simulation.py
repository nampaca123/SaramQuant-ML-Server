from flask import Blueprint, request, jsonify
from app.schema import Market
from app.services.simulation_service import SimulationService

simulation_bp = Blueprint("simulation", __name__, url_prefix="/internal")

MARKET_MAP = {
    "KR_KOSPI": Market.KR_KOSPI,
    "KR_KOSDAQ": Market.KR_KOSDAQ,
    "US_NYSE": Market.US_NYSE,
    "US_NASDAQ": Market.US_NASDAQ,
}


@simulation_bp.route("/stocks/<symbol>/simulation", methods=["GET"])
def run_simulation(symbol: str):
    market_str = request.args.get("market", "")
    market = MARKET_MAP.get(market_str)
    if market is None:
        return jsonify({"error": f"Invalid market. Choose from: {list(MARKET_MAP)}"}), 400

    try:
        params = _parse_params(request.args)
        result = SimulationService.run(symbol=symbol, market=market, **params)
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


def _parse_params(args) -> dict:
    return {
        "days": int(args.get("days", 60)),
        "num_simulations": int(args.get("simulations", 10000)),
        "confidence": float(args.get("confidence", 0.95)),
        "lookback": int(args.get("lookback", 252)),
        "method": args.get("method", "gbm"),
    }