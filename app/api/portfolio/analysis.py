import logging
from flask import request, jsonify
from app.api.portfolio import portfolio_bp
from app.services.portfolio_analysis_service import PortfolioAnalysisService

logger = logging.getLogger(__name__)


def _get_portfolio_id():
    body = request.get_json(silent=True) or {}
    pid = body.get("portfolio_id")
    return int(pid) if pid else None


@portfolio_bp.route("/risk-score", methods=["POST"])
def risk_score():
    pid = _get_portfolio_id()
    if not pid:
        return jsonify({"error": "portfolio_id required"}), 400
    try:
        return jsonify(PortfolioAnalysisService.risk_score(pid))
    except Exception as e:
        logger.exception("risk_score failed for portfolio %s", pid)
        return jsonify({"error": str(e)}), 200


@portfolio_bp.route("/risk", methods=["POST"])
def risk_decomposition():
    pid = _get_portfolio_id()
    if not pid:
        return jsonify({"error": "portfolio_id required"}), 400
    try:
        return jsonify(PortfolioAnalysisService.risk_decomposition(pid))
    except Exception as e:
        logger.exception("risk_decomposition failed for portfolio %s", pid)
        return jsonify({"error": str(e)}), 200


@portfolio_bp.route("/diversification", methods=["POST"])
def diversification():
    pid = _get_portfolio_id()
    if not pid:
        return jsonify({"error": "portfolio_id required"}), 400
    try:
        return jsonify(PortfolioAnalysisService.diversification(pid))
    except Exception as e:
        logger.exception("diversification failed for portfolio %s", pid)
        return jsonify({"error": str(e)}), 200


@portfolio_bp.route("/benchmark-comparison", methods=["POST"])
def benchmark_comparison():
    pid = _get_portfolio_id()
    if not pid:
        return jsonify({"error": "portfolio_id required"}), 400
    try:
        return jsonify(PortfolioAnalysisService.benchmark_comparison(pid))
    except Exception as e:
        logger.exception("benchmark_comparison failed for portfolio %s", pid)
        return jsonify({"error": str(e)}), 200
