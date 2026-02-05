from flask import request, jsonify

from app.api import api_bp
from app.api.errors import APIError
from app.db.connection import get_connection
from app.services import RiskService
from app.schema.data_sources import Benchmark


VALID_METRICS = {"all", "beta", "alpha", "sharpe"}
VALID_BENCHMARKS = {b.value for b in Benchmark}


@api_bp.route("/risk/<symbol>")
def get_risk_metrics(symbol: str):
    metric = request.args.get("metric", "all")
    if metric not in VALID_METRICS:
        raise APIError(f"Invalid metric. Valid options: {', '.join(sorted(VALID_METRICS))}", 400)

    benchmark_str = request.args.get("benchmark")
    benchmark = None
    if benchmark_str:
        if benchmark_str not in VALID_BENCHMARKS:
            raise APIError(f"Invalid benchmark. Valid options: {', '.join(sorted(VALID_BENCHMARKS))}", 400)
        benchmark = Benchmark(benchmark_str)

    period = request.args.get("period", 252, type=int)
    if period < 30 or period > 1000:
        raise APIError("period must be between 30 and 1000", 400)

    with get_connection() as conn:
        service = RiskService(conn)
        result = service.calculate(
            symbol=symbol,
            metric=metric,
            benchmark=benchmark,
            period_days=period
        )

    return jsonify(result)
