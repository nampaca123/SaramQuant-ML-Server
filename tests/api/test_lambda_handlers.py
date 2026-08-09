import json
from datetime import date
from decimal import Decimal

import pytest

from app.api import lambda_handlers as handlers
from app.schema import Market

API_KEY = "secret-key"
AUTH = {"x-api-key": API_KEY}
HOLDINGS = [{"symbol": "005930", "market": "KR_KOSPI", "shares": 10, "avg_price": 70000}]


@pytest.fixture(autouse=True)
def calc_auth_key(monkeypatch):
    monkeypatch.setenv("CALC_AUTH_KEY", API_KEY)
    handlers._reset_price_lookup()


class Spy:
    """서비스 시임 대역 — 호출 인자를 기록하고 미리 정한 값을 돌려주거나 예외를 던진다."""

    def __init__(self, result=None, error=None):
        self.result = {} if result is None else result
        self.error = error
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error is not None:
            raise self.error
        return self.result

    @property
    def kwargs(self):
        return self.calls[-1][1]

    @property
    def args(self):
        return self.calls[-1][0]


def install(monkeypatch, seam: str, spy: Spy) -> Spy:
    monkeypatch.setattr(handlers, seam, lambda: spy)
    return spy


def build_event(method="POST", path="/", body=None, headers=None, query=None, path_params=None):
    return {
        "version": "2.0",
        "rawPath": path,
        "headers": {} if headers is None else headers,
        "queryStringParameters": query,
        "pathParameters": path_params,
        "body": None if body is None else json.dumps(body),
        "isBase64Encoded": False,
        "requestContext": {"http": {"method": method, "path": path}},
    }


def body_of(response):
    return json.loads(response["body"])


# ── auth ──


@pytest.mark.parametrize(
    "handler",
    [
        handlers.handle_analysis,
        handlers.handle_portfolio_simulation,
        handlers.handle_stock_simulation,
        handlers.handle_price_lookup,
    ],
)
def test_internal_handlers_reject_missing_api_key(handler):
    response = handler(build_event(), None)

    assert response["statusCode"] == 401
    assert body_of(response) == {"error": "Unauthorized"}


def test_internal_handler_rejects_wrong_api_key():
    response = handlers.handle_analysis(build_event(headers={"x-api-key": "nope"}), None)

    assert response["statusCode"] == 401


def test_api_key_header_lookup_is_case_insensitive(monkeypatch):
    install(monkeypatch, "_analysis_service", Spy({"ok": True}))

    response = handlers.handle_analysis(
        build_event(headers={"X-Api-Key": API_KEY}, body={"market_group": "KR", "holdings": HOLDINGS}),
        None,
    )

    assert response["statusCode"] == 200


def test_missing_calc_auth_key_env_denies_every_request(monkeypatch):
    monkeypatch.delenv("CALC_AUTH_KEY", raising=False)

    response = handlers.handle_analysis(build_event(headers=AUTH), None)

    assert response["statusCode"] == 401


def test_unauthorized_request_never_reaches_the_service(monkeypatch):
    spy = install(monkeypatch, "_analysis_service", Spy())

    handlers.handle_analysis(build_event(body={"market_group": "KR", "holdings": HOLDINGS}), None)

    assert spy.calls == []


# ── health ──


def test_health_returns_ok_without_auth():
    response = handlers.handle_health(build_event(method="GET", path="/health"), None)

    assert response["statusCode"] == 200
    assert body_of(response) == {"status": "ok"}


def test_response_declares_json_content_type():
    response = handlers.handle_health(build_event(method="GET", path="/health"), None)

    assert response["headers"]["Content-Type"] == "application/json"


def test_response_body_keeps_non_ascii_unescaped(monkeypatch):
    install(monkeypatch, "_analysis_service", Spy({"name": "삼성전자"}))

    response = handlers.handle_analysis(
        build_event(headers=AUTH, body={"market_group": "KR", "holdings": HOLDINGS}), None
    )

    assert "삼성전자" in response["body"]


# ── full-analysis ──


def test_analysis_without_holdings_returns_400():
    response = handlers.handle_analysis(
        build_event(headers=AUTH, body={"market_group": "KR"}), None
    )

    assert response["statusCode"] == 400
    assert "holdings" in body_of(response)["error"]


def test_analysis_with_empty_holdings_returns_400():
    response = handlers.handle_analysis(
        build_event(headers=AUTH, body={"market_group": "KR", "holdings": []}), None
    )

    assert response["statusCode"] == 400


def test_analysis_with_invalid_market_group_returns_400():
    response = handlers.handle_analysis(
        build_event(headers=AUTH, body={"market_group": "JP", "holdings": HOLDINGS}), None
    )

    assert response["statusCode"] == 400


def test_analysis_forwards_market_group_and_holdings_to_service(monkeypatch):
    spy = install(monkeypatch, "_analysis_service", Spy({"risk_score": {"score": 42}}))

    response = handlers.handle_analysis(
        build_event(headers=AUTH, body={"market_group": "US", "holdings": HOLDINGS}), None
    )

    assert response["statusCode"] == 200
    assert spy.args == ("US", HOLDINGS)
    assert body_of(response) == {"risk_score": {"score": 42}}


def test_analysis_maps_service_value_error_to_400(monkeypatch):
    install(monkeypatch, "_analysis_service", Spy(error=ValueError("bad input")))

    response = handlers.handle_analysis(
        build_event(headers=AUTH, body={"market_group": "KR", "holdings": HOLDINGS}), None
    )

    assert response["statusCode"] == 400
    assert body_of(response)["error"] == "bad input"


def test_analysis_maps_unexpected_error_to_500(monkeypatch):
    install(monkeypatch, "_analysis_service", Spy(error=RuntimeError("glue exploded")))

    response = handlers.handle_analysis(
        build_event(headers=AUTH, body={"market_group": "KR", "holdings": HOLDINGS}), None
    )

    assert response["statusCode"] == 500
    assert "error" in body_of(response)


def test_malformed_json_body_returns_400():
    event = build_event(headers=AUTH)
    event["body"] = "{not json"

    response = handlers.handle_analysis(event, None)

    assert response["statusCode"] == 400


def test_non_object_json_body_returns_400():
    event = build_event(headers=AUTH)
    event["body"] = "[1, 2, 3]"

    response = handlers.handle_analysis(event, None)

    assert response["statusCode"] == 400


# ── portfolio simulation ──


def test_portfolio_simulation_without_holdings_returns_400():
    response = handlers.handle_portfolio_simulation(
        build_event(headers=AUTH, body={"market_group": "KR"}), None
    )

    assert response["statusCode"] == 400


def test_portfolio_simulation_forwards_optional_params(monkeypatch):
    spy = install(
        monkeypatch, "_portfolio_simulation_service", Spy({"target": {"type": "portfolio"}})
    )

    response = handlers.handle_portfolio_simulation(
        build_event(
            headers=AUTH,
            body={
                "market_group": "KR",
                "holdings": HOLDINGS,
                "days": 30,
                "simulations": 100,
                "confidence": 0.9,
                "lookback": 120,
                "method": "gbm",
            },
        ),
        None,
    )

    assert response["statusCode"] == 200
    assert spy.kwargs == {
        "market_group": "KR",
        "holdings": HOLDINGS,
        "days": 30,
        "num_simulations": 100,
        "confidence": 0.9,
        "lookback": 120,
        "method": "gbm",
    }


def test_portfolio_simulation_uses_defaults_when_params_absent(monkeypatch):
    from app.quant.simulation.defaults import (
        DEFAULT_CONFIDENCE,
        DEFAULT_DAYS,
        DEFAULT_LOOKBACK,
        DEFAULT_NUM_SIMULATIONS,
    )

    spy = install(monkeypatch, "_portfolio_simulation_service", Spy())

    handlers.handle_portfolio_simulation(
        build_event(headers=AUTH, body={"market_group": "KR", "holdings": HOLDINGS}), None
    )

    assert spy.kwargs["days"] == DEFAULT_DAYS
    assert spy.kwargs["num_simulations"] == DEFAULT_NUM_SIMULATIONS
    assert spy.kwargs["confidence"] == DEFAULT_CONFIDENCE
    assert spy.kwargs["lookback"] == DEFAULT_LOOKBACK
    assert spy.kwargs["method"] == "bootstrap"


def test_portfolio_simulation_accepts_params_from_query_string(monkeypatch):
    spy = install(monkeypatch, "_portfolio_simulation_service", Spy())

    handlers.handle_portfolio_simulation(
        build_event(
            headers=AUTH,
            body={"market_group": "KR", "holdings": HOLDINGS},
            query={"days": "5", "method": "gbm"},
        ),
        None,
    )

    assert spy.kwargs["days"] == 5
    assert spy.kwargs["method"] == "gbm"


# ── stock simulation ──


def test_stock_simulation_without_market_returns_400():
    response = handlers.handle_stock_simulation(
        build_event(method="GET", headers=AUTH, path_params={"symbol": "005930"}), None
    )

    assert response["statusCode"] == 400


def test_stock_simulation_with_unknown_market_returns_400():
    response = handlers.handle_stock_simulation(
        build_event(
            method="GET",
            headers=AUTH,
            path_params={"symbol": "005930"},
            query={"market": "JP_TSE"},
        ),
        None,
    )

    assert response["statusCode"] == 400


def test_stock_simulation_without_symbol_returns_400():
    response = handlers.handle_stock_simulation(
        build_event(method="GET", headers=AUTH, query={"market": "KR_KOSPI"}), None
    )

    assert response["statusCode"] == 400


def test_stock_simulation_forwards_symbol_and_market(monkeypatch):
    spy = install(monkeypatch, "_stock_simulation_service", Spy({"ok": 1}))

    response = handlers.handle_stock_simulation(
        build_event(
            method="GET",
            headers=AUTH,
            path_params={"symbol": "005930"},
            query={"market": "KR_KOSPI", "days": "10"},
        ),
        None,
    )

    assert response["statusCode"] == 200
    assert spy.kwargs["symbol"] == "005930"
    assert spy.kwargs["market"] == Market.KR_KOSPI
    assert spy.kwargs["days"] == 10
    assert spy.kwargs["method"] == "gbm"


def test_stock_simulation_falls_back_to_raw_path_symbol(monkeypatch):
    spy = install(monkeypatch, "_stock_simulation_service", Spy({"ok": 1}))

    handlers.handle_stock_simulation(
        build_event(
            method="GET",
            path="/internal/stocks/AAPL/simulation",
            headers=AUTH,
            query={"market": "US_NASDAQ"},
        ),
        None,
    )

    assert spy.kwargs["symbol"] == "AAPL"


def test_stock_simulation_maps_insufficient_data_to_400(monkeypatch):
    install(
        monkeypatch,
        "_stock_simulation_service",
        Spy(error=ValueError("Insufficient data: 2/60 days")),
    )

    response = handlers.handle_stock_simulation(
        build_event(
            method="GET",
            headers=AUTH,
            path_params={"symbol": "005930"},
            query={"market": "KR_KOSPI"},
        ),
        None,
    )

    assert response["statusCode"] == 400
    assert "Insufficient data" in body_of(response)["error"]


# ── price lookup ──


class StubLookup:
    def __init__(self, result=None):
        self.result = result
        self.calls = []

    def lookup(self, stock_id, target_date):
        self.calls.append((stock_id, target_date))
        return self.result


class StubStockRepository:
    def __init__(self, found=None):
        self.found = found
        self.calls = []

    def get_by_symbol(self, symbol, market=None):
        self.calls.append((symbol, market))
        return self.found


def test_price_lookup_without_date_returns_400():
    response = handlers.handle_price_lookup(build_event(headers=AUTH, body={"stock_id": 1}), None)

    assert response["statusCode"] == 400
    assert "date" in body_of(response)["error"]


def test_price_lookup_without_identifier_returns_400():
    response = handlers.handle_price_lookup(
        build_event(headers=AUTH, body={"date": "2026-08-07"}), None
    )

    assert response["statusCode"] == 400


def test_price_lookup_with_invalid_date_returns_400():
    response = handlers.handle_price_lookup(
        build_event(headers=AUTH, body={"stock_id": 1, "date": "07/08/2026"}), None
    )

    assert response["statusCode"] == 400


def test_price_lookup_returns_not_found_payload(monkeypatch):
    install(monkeypatch, "_price_lookup_service", StubLookup(None))

    response = handlers.handle_price_lookup(
        build_event(headers=AUTH, body={"stock_id": 7, "date": "2026-08-07"}), None
    )

    assert response["statusCode"] == 200
    assert body_of(response)["found"] is False


def test_price_lookup_serialises_ohlc_and_fx_rate(monkeypatch):
    stub = install(
        monkeypatch,
        "_price_lookup_service",
        StubLookup(
            {
                "open": Decimal("101"),
                "high": Decimal("105"),
                "low": Decimal("99"),
                "close": Decimal("100.5"),
                "date": date(2026, 8, 7),
                "source": "DB",
                "fx_rate": 1380.5,
            }
        ),
    )

    response = handlers.handle_price_lookup(
        build_event(headers=AUTH, body={"stock_id": 7, "date": "2026-08-07"}), None
    )

    assert stub.calls == [(7, date(2026, 8, 7))]
    assert body_of(response) == {
        "found": True,
        "close": 100.5,
        "date": "2026-08-07",
        "source": "DB",
        "open": 101.0,
        "high": 105.0,
        "low": 99.0,
        "fx_rate": 1380.5,
    }


def test_price_lookup_omits_absent_ohlc_fields(monkeypatch):
    install(
        monkeypatch,
        "_price_lookup_service",
        StubLookup({"close": Decimal("42"), "date": date(2026, 8, 7), "source": "AUTO"}),
    )

    response = handlers.handle_price_lookup(
        build_event(headers=AUTH, body={"stock_id": 7, "date": "2026-08-07"}), None
    )

    assert body_of(response) == {
        "found": True,
        "close": 42.0,
        "date": "2026-08-07",
        "source": "AUTO",
    }


def test_price_lookup_resolves_symbol_and_market_to_stock_id(monkeypatch):
    stub = install(monkeypatch, "_price_lookup_service", StubLookup(None))
    repo = install(
        monkeypatch,
        "_stock_repository",
        StubStockRepository((42, "005930", "삼성전자", Market.KR_KOSPI)),
    )

    response = handlers.handle_price_lookup(
        build_event(
            headers=AUTH, body={"symbol": "005930", "market": "KR_KOSPI", "date": "2026-08-07"}
        ),
        None,
    )

    assert response["statusCode"] == 200
    assert stub.calls == [(42, date(2026, 8, 7))]
    assert repo.calls == [("005930", Market.KR_KOSPI)]


def test_price_lookup_with_unknown_symbol_returns_404(monkeypatch):
    install(monkeypatch, "_stock_repository", StubStockRepository(None))

    response = handlers.handle_price_lookup(
        build_event(
            headers=AUTH, body={"symbol": "ZZZZ", "market": "KR_KOSPI", "date": "2026-08-07"}
        ),
        None,
    )

    assert response["statusCode"] == 404


def test_price_lookup_with_invalid_market_returns_400():
    response = handlers.handle_price_lookup(
        build_event(headers=AUTH, body={"symbol": "005930", "market": "JP", "date": "2026-08-07"}),
        None,
    )

    assert response["statusCode"] == 400


# ── request logging ──


def test_every_request_emits_one_log_line(monkeypatch):
    calls = []
    monkeypatch.setattr(handlers, "log_api", lambda *args: calls.append(args))

    handlers.handle_health(build_event(method="GET", path="/health"), None)
    handlers.handle_analysis(build_event(), None)

    assert [call[:3] for call in calls] == [
        ("GET", "/health", 200),
        ("POST", "/internal/portfolios/full-analysis", 401),
    ]
    assert all(isinstance(call[3], int) for call in calls)


def test_log_line_is_emitted_even_when_handler_crashes(monkeypatch):
    calls = []
    monkeypatch.setattr(handlers, "log_api", lambda *args: calls.append(args))
    install(monkeypatch, "_analysis_service", Spy(error=RuntimeError("x")))

    handlers.handle_analysis(
        build_event(headers=AUTH, body={"market_group": "KR", "holdings": HOLDINGS}), None
    )

    assert [call[2] for call in calls] == [500]
