from flask import request, jsonify

from app.api import api_bp, NotFoundError, parse_market
from app.db import get_connection, StockRepository


@api_bp.route("/stocks")
def get_stocks():
    market = parse_market(request.args.get("market"))
    limit = request.args.get("limit", 100, type=int)
    offset = request.args.get("offset", 0, type=int)

    with get_connection() as conn:
        repo = StockRepository(conn)
        stocks = repo.get_list(market=market, limit=limit, offset=offset)

    return jsonify([
        {"id": s[0], "symbol": s[1], "name": s[2], "market": s[3].value}
        for s in stocks
    ])


@api_bp.route("/stocks/<symbol>")
def get_stock(symbol: str):
    market = parse_market(request.args.get("market"))

    with get_connection() as conn:
        repo = StockRepository(conn)
        stock = repo.get_by_symbol(symbol, market)

    if not stock:
        raise NotFoundError(f"Stock {symbol}")

    return jsonify({
        "id": stock[0],
        "symbol": stock[1],
        "name": stock[2],
        "market": stock[3].value
    })
