import logging
from flask import request, jsonify
from app.api.portfolio import portfolio_bp
from app.quant.simulation.defaults import (
    DEFAULT_DAYS, DEFAULT_NUM_SIMULATIONS, DEFAULT_CONFIDENCE,
    DEFAULT_LOOKBACK,
)

logger = logging.getLogger(__name__)


@portfolio_bp.route("/<int:portfolio_id>/simulation", methods=["POST"])
def portfolio_simulation(portfolio_id: int):
    from app.services.portfolio_simulation_service import PortfolioSimulationService

    try:
        params = {
            "days": int(request.args.get("days", DEFAULT_DAYS)),
            "num_simulations": int(request.args.get("simulations", DEFAULT_NUM_SIMULATIONS)),
            "confidence": float(request.args.get("confidence", DEFAULT_CONFIDENCE)),
            "lookback": int(request.args.get("lookback", DEFAULT_LOOKBACK)),
            "method": request.args.get("method", "bootstrap"),
        }
        result = PortfolioSimulationService.run(portfolio_id=portfolio_id, **params)
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.exception("portfolio simulation failed for %s", portfolio_id)
        return jsonify({"error": str(e)}), 200
