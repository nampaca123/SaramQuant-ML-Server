from flask import request, jsonify

from app.api import api_bp
from app.api.utils import parse_date
from app.db.connection import get_connection
from app.services import PriceService


MAX_LIMIT = 1000
DEFAULT_LIMIT = 365


@api_bp.route("/prices/daily/<symbol>")
def get_daily_prices(symbol: str):
    start_date = parse_date(request.args.get("start_date"))
    end_date = parse_date(request.args.get("end_date"))
    limit = request.args.get("limit", DEFAULT_LIMIT, type=int)
    limit = min(limit, MAX_LIMIT)

    with get_connection() as conn:
        service = PriceService(conn)
        df = service.get_dataframe(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            limit=limit
        )

    prices = [
        {
            "date": str(idx),
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"],
            "volume": int(row["volume"])
        }
        for idx, row in df.iterrows()
    ]

    return jsonify({"symbol": symbol, "prices": prices})
