"""실제 레이크를 읽는 로컬 핸들러 호출 — 응답이 500으로 새지 않고 계약대로 처리되는지 본다."""
import json

import pytest

from app.api import lambda_handlers as handlers
from app.db.lake_reader import query_df, scan
from tests.api.test_lambda_handlers import AUTH, API_KEY, body_of, build_event

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def calc_auth_key(monkeypatch):
    monkeypatch.setenv("CALC_AUTH_KEY", API_KEY)
    handlers._reset_price_lookup()


@pytest.fixture(scope="module")
def kr_symbols():
    df = query_df(
        f"SELECT symbol, market FROM {scan('stocks')}"
        " WHERE is_active AND market = 'KR_KOSPI' ORDER BY symbol LIMIT 2"
    )
    if df.empty:
        pytest.skip("no active KR_KOSPI stocks in the lake")
    return [(row.symbol, row.market) for row in df.itertuples(index=False)]


def assert_handled(response, allowed_statuses):
    payload = body_of(response)
    assert response["statusCode"] in allowed_statuses, payload
    if response["statusCode"] != 200:
        assert "error" in payload, payload
    return payload


def test_health_against_real_runtime():
    response = handlers.handle_health(build_event(method="GET", path="/health"), None)

    assert response["statusCode"] == 200
    assert body_of(response) == {"status": "ok"}


def test_stock_simulation_on_real_symbol_is_handled(kr_symbols):
    symbol, market = kr_symbols[0]

    response = handlers.handle_stock_simulation(
        build_event(
            method="GET",
            path=f"/internal/stocks/{symbol}/simulation",
            headers=AUTH,
            path_params={"symbol": symbol},
            query={"market": market},
        ),
        None,
    )

    payload = assert_handled(response, {200, 400})
    print(f"\n[stock-simulation {symbol}] {response['statusCode']} {json.dumps(payload)[:220]}")


def test_stock_simulation_on_unknown_symbol_returns_400():
    response = handlers.handle_stock_simulation(
        build_event(
            method="GET",
            headers=AUTH,
            path_params={"symbol": "ZZZZZZ"},
            query={"market": "KR_KOSPI"},
        ),
        None,
    )

    assert response["statusCode"] == 400
    assert "not found" in body_of(response)["error"].lower()


def test_full_analysis_with_two_synthetic_holdings_is_handled(kr_symbols):
    holdings = [
        {
            "symbol": symbol,
            "market": market,
            "shares": 10,
            "avg_price": 50000,
            "currency": "KRW",
            "purchased_at": "2026-08-01",
            "purchase_fx_rate": None,
        }
        for symbol, market in kr_symbols
    ]

    response = handlers.handle_analysis(
        build_event(headers=AUTH, body={"market_group": "KR", "holdings": holdings}), None
    )

    payload = assert_handled(response, {200, 400})
    if response["statusCode"] == 200:
        assert set(payload) == {
            "risk_score",
            "risk_decomposition",
            "diversification",
            "benchmark_comparison",
            "benchmark_chart",
        }
    print(f"\n[full-analysis] {response['statusCode']} {json.dumps(payload)[:400]}")


def test_portfolio_simulation_with_two_synthetic_holdings_is_handled(kr_symbols):
    holdings = [
        {"symbol": symbol, "market": market, "shares": 10, "avg_price": 50000}
        for symbol, market in kr_symbols
    ]

    response = handlers.handle_portfolio_simulation(
        build_event(
            headers=AUTH,
            body={
                "market_group": "KR",
                "holdings": holdings,
                "simulations": 200,
                "days": 5,
            },
        ),
        None,
    )

    payload = assert_handled(response, {200, 400})
    print(f"\n[portfolio-simulation] {response['statusCode']} {json.dumps(payload)[:300]}")


def test_price_lookup_by_symbol_and_market_is_handled(kr_symbols):
    symbol, market = kr_symbols[0]

    response = handlers.handle_price_lookup(
        build_event(
            headers=AUTH,
            body={"symbol": symbol, "market": market, "date": "2026-08-07"},
        ),
        None,
    )

    payload = assert_handled(response, {200, 404})
    print(f"\n[price-lookup {symbol}] {response['statusCode']} {json.dumps(payload)[:300]}")


def test_price_lookup_rejects_unauthenticated_request():
    response = handlers.handle_price_lookup(
        build_event(body={"stock_id": 1, "date": "2026-08-07"}), None
    )

    assert response["statusCode"] == 401
