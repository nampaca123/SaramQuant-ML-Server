import logging
import numpy as np

from app.db import get_connection, PortfolioRepository, StockRepository, DailyPriceRepository, BenchmarkRepository
from app.schema import Market, Benchmark
from app.quant.portfolio.hypothetical_returns import build_from_prices
from app.quant.portfolio.portfolio_risk_score import compute_risk_score
from app.quant.portfolio.diversification import compute_diversification_metrics
from app.quant.portfolio.risk_contribution import compute_mcar
from app.quant.portfolio.portfolio_metrics import compute_factor_risk

logger = logging.getLogger(__name__)

MARKET_GROUP_TO_MARKETS = {
    "KR": [Market.KR_KOSPI, Market.KR_KOSDAQ],
    "US": [Market.US_NYSE, Market.US_NASDAQ],
}

_NO_HOLDINGS = {"error": "No holdings"}


class PortfolioAnalysisService:

    @staticmethod
    def full_analysis(portfolio_id: int) -> dict:
        holdings, market_group = PortfolioAnalysisService._load_context(portfolio_id)
        if not holdings:
            return {k: _NO_HOLDINGS for k in (
                "risk_score", "risk_decomposition", "diversification",
                "benchmark_comparison", "benchmark_chart",
            )}

        stock_ids, weights = PortfolioAnalysisService._compute_weights(holdings)
        benchmark_key = Benchmark.KR_KOSPI if market_group == "KR" else Benchmark.US_SP500
        benchmark_name = "KOSPI" if market_group == "KR" else "S&P 500"

        with get_connection() as conn:
            all_series = DailyPriceRepository(conn).get_close_prices_batch(stock_ids, limit=252)
            stock_info = StockRepository(conn).find_by_ids(stock_ids)
            bench_prices = BenchmarkRepository(conn).get_prices(benchmark_key, limit=253)

        hyp = build_from_prices(all_series, stock_ids, weights)

        return {
            "risk_score": PortfolioAnalysisService._calc_risk_score(
                hyp, market_group, bench_prices),
            "risk_decomposition": PortfolioAnalysisService._calc_risk_decomposition(
                hyp, all_series, stock_ids, weights, stock_info, market_group),
            "diversification": PortfolioAnalysisService._calc_diversification(
                stock_ids, weights, stock_info),
            "benchmark_comparison": PortfolioAnalysisService._calc_benchmark_comparison(
                hyp, bench_prices, benchmark_name),
            "benchmark_chart": PortfolioAnalysisService._calc_benchmark_chart(
                all_series, stock_ids, weights, bench_prices, benchmark_name),
        }

    # ── sub-analyses (all operate on pre-loaded data) ──

    @staticmethod
    def _calc_risk_score(hyp, market_group, bench_prices):
        if hyp["coverage"] == "INSUFFICIENT":
            return {"score": None, "tier": "UNKNOWN", "reason": "Insufficient data",
                    "effective_lookback": hyp["effective_lookback"]}

        lookback = hyp["effective_lookback"]
        bench_vol = _benchmark_vol_from_prices(bench_prices, min(lookback, 252))
        return compute_risk_score(hyp["returns"], market_group, lookback, benchmark_vol=bench_vol)

    @staticmethod
    def _calc_risk_decomposition(hyp, all_series, stock_ids, weights, stock_info, market_group):
        if hyp["coverage"] == "INSUFFICIENT":
            return {"error": "Insufficient data for risk decomposition"}

        returns_matrix = _build_returns_matrix(all_series, stock_ids)
        if returns_matrix is None:
            return {"error": "Cannot build returns matrix"}

        cov_matrix = np.atleast_2d(np.cov(returns_matrix.T))
        mcar_result = compute_mcar(weights, cov_matrix)

        stock_contributions = [
            {
                "stock_id": sid,
                "symbol": stock_info.get(sid, {}).get("symbol", "?"),
                "weight": round(float(weights[i]), 6),
                "mcar": mcar_result["mcar"][i],
                "contribution_pct": mcar_result["contribution_pct"][i],
            }
            for i, sid in enumerate(stock_ids)
        ]

        result: dict = {
            "portfolio_vol": mcar_result["portfolio_vol"],
            "stock_contributions": stock_contributions,
        }

        for m in MARKET_GROUP_TO_MARKETS.get(market_group, []):
            fr = compute_factor_risk(stock_ids, weights, m)
            if fr:
                result["factor_analysis"] = fr
                break

        return result

    @staticmethod
    def _calc_diversification(stock_ids, weights, stock_info):
        sectors = [stock_info.get(sid, {}).get("sector") for sid in stock_ids]
        return compute_diversification_metrics(weights, sectors=sectors)

    @staticmethod
    def _calc_benchmark_comparison(hyp, bench_prices, benchmark_name):
        if hyp["coverage"] == "INSUFFICIENT":
            return {"error": "Insufficient data"}

        portfolio_returns = hyp["returns"]

        if len(bench_prices) < 20:
            return {"error": "Benchmark data unavailable"}

        closes = np.array([float(p.close) for p in bench_prices])[::-1]
        bench_returns = np.diff(closes) / closes[:-1]
        n = min(len(portfolio_returns), len(bench_returns))

        port_cum = float(np.prod(1 + portfolio_returns[-n:]) - 1)
        bench_cum = float(np.prod(1 + bench_returns[-n:]) - 1)

        return {
            "portfolio_return": round(port_cum * 100, 2),
            "benchmark_return": round(bench_cum * 100, 2),
            "excess_return": round((port_cum - bench_cum) * 100, 2),
            "benchmark_name": benchmark_name,
            "lookback_days": n,
        }

    @staticmethod
    def _calc_benchmark_chart(all_series, stock_ids, weights, bench_prices_raw, benchmark_name):
        date_sets = [set(all_series.get(sid, {}).keys()) for sid in stock_ids]
        if not date_sets or any(not ds for ds in date_sets):
            return {"error": "Insufficient data"}

        common_dates = sorted(set.intersection(*date_sets))
        if len(common_dates) < 30:
            return {"error": "Insufficient data"}

        price_matrix = np.array([
            [all_series[sid][d] for d in common_dates]
            for sid in stock_ids
        ]).T

        stock_returns = np.diff(price_matrix, axis=0) / price_matrix[:-1]
        portfolio_returns = stock_returns @ weights
        port_cum = np.cumprod(1 + portfolio_returns)

        bench_prices = {p.date: float(p.close) for p in bench_prices_raw}
        bench_dates = sorted(set(bench_prices.keys()) & set(common_dates[1:]))
        if len(bench_dates) < 20:
            return {"error": "Insufficient benchmark overlap"}

        date_strs = [str(d) for d in common_dates[1:]]
        n = len(date_strs)
        bench_closes = [bench_prices.get(d) for d in common_dates[1:]]

        bench_returns = []
        prev = bench_prices.get(common_dates[0])
        for c in bench_closes:
            if c is not None and prev is not None and prev > 0:
                bench_returns.append((c - prev) / prev)
                prev = c
            else:
                bench_returns.append(0.0)
                if c is not None:
                    prev = c

        bench_cum = np.cumprod(1 + np.array(bench_returns))

        return {
            "benchmark_name": benchmark_name,
            "portfolio_series": [
                {"date": date_strs[i], "value": round(float(port_cum[i]) * 100, 2)}
                for i in range(n)
            ],
            "benchmark_series": [
                {"date": date_strs[i], "value": round(float(bench_cum[i]) * 100, 2)}
                for i in range(n)
            ],
        }

    # ── shared helpers ──

    @staticmethod
    def _load_context(portfolio_id: int):
        with get_connection() as conn:
            repo = PortfolioRepository(conn)
            holdings = repo.get_holdings(portfolio_id)
            market_group = repo.get_portfolio_market_group(portfolio_id) or "UNKNOWN"
        return holdings, market_group

    @staticmethod
    def _compute_weights(holdings) -> tuple[list[int], np.ndarray]:
        stock_ids = [h.stock_id for h in holdings]
        values = np.array([float(h.shares * h.avg_price) for h in holdings])
        total = values.sum()
        weights = values / total if total > 0 else np.ones(len(values)) / len(values)
        return stock_ids, weights


# ── module-level helpers ──

def _benchmark_vol_from_prices(bench_prices, lookback: int) -> float | None:
    if len(bench_prices) < 20:
        return None
    prices_to_use = bench_prices[:lookback + 1]
    closes = np.array([float(p.close) for p in prices_to_use])[::-1]
    returns = np.diff(closes) / closes[:-1]
    return float(np.std(returns, ddof=1) * np.sqrt(252))


def _build_returns_matrix(all_series: dict, stock_ids: list[int]):
    date_sets = [set(all_series.get(sid, {}).keys()) for sid in stock_ids]
    if not date_sets or any(not ds for ds in date_sets):
        return None
    common_dates = sorted(set.intersection(*date_sets))
    if len(common_dates) < 30:
        return None
    price_matrix = np.array([
        [all_series[sid][d] for d in common_dates]
        for sid in stock_ids
    ]).T
    return np.diff(price_matrix, axis=0) / price_matrix[:-1]
