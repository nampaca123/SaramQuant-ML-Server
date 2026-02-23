import logging
import numpy as np
from decimal import Decimal

from app.db import get_connection, PortfolioRepository, StockRepository, DailyPriceRepository
from app.schema import Market
from app.quant.portfolio.hypothetical_returns import build_hypothetical_returns
from app.quant.portfolio.portfolio_risk_score import compute_risk_score
from app.quant.portfolio.diversification import compute_diversification_metrics
from app.quant.portfolio.risk_contribution import compute_mcar
from app.quant.portfolio.portfolio_metrics import compute_factor_risk

logger = logging.getLogger(__name__)

MARKET_GROUP_TO_MARKETS = {
    "KR": [Market.KR_KOSPI, Market.KR_KOSDAQ],
    "US": [Market.US_NYSE, Market.US_NASDAQ],
}


class PortfolioAnalysisService:
    @staticmethod
    def risk_score(portfolio_id: int) -> dict:
        holdings, market_group = PortfolioAnalysisService._load_context(portfolio_id)
        if not holdings:
            return {"error": "No holdings"}

        stock_ids, weights = PortfolioAnalysisService._compute_weights(holdings)
        hyp = build_hypothetical_returns(stock_ids, weights)

        if hyp["coverage"] == "INSUFFICIENT":
            return {"score": None, "tier": "UNKNOWN", "reason": "Insufficient data",
                    "effective_lookback": hyp["effective_lookback"]}

        result = compute_risk_score(hyp["returns"], market_group, hyp["effective_lookback"])
        return result

    @staticmethod
    def diversification(portfolio_id: int) -> dict:
        holdings, market_group = PortfolioAnalysisService._load_context(portfolio_id)
        if not holdings:
            return {"error": "No holdings"}

        stock_ids, weights = PortfolioAnalysisService._compute_weights(holdings)

        with get_connection() as conn:
            stock_repo = StockRepository(conn)
            sectors = []
            for sid in stock_ids:
                info = stock_repo.find_by_id(sid)
                sectors.append(info["sector"] if info else None)

        return compute_diversification_metrics(weights, sectors=sectors)

    @staticmethod
    def risk_decomposition(portfolio_id: int) -> dict:
        holdings, market_group = PortfolioAnalysisService._load_context(portfolio_id)
        if not holdings:
            return {"error": "No holdings"}

        stock_ids, weights = PortfolioAnalysisService._compute_weights(holdings)

        hyp = build_hypothetical_returns(stock_ids, weights)
        if hyp["coverage"] == "INSUFFICIENT":
            return {"error": "Insufficient data for risk decomposition"}

        returns_matrix_data = PortfolioAnalysisService._get_returns_matrix(stock_ids)
        if returns_matrix_data is None:
            return {"error": "Cannot build returns matrix"}

        cov_matrix = np.atleast_2d(np.cov(returns_matrix_data.T))
        mcar_result = compute_mcar(weights, cov_matrix)

        with get_connection() as conn:
            stock_repo = StockRepository(conn)
            stock_info = {}
            for sid in stock_ids:
                info = stock_repo.find_by_id(sid)
                if info:
                    stock_info[sid] = info

        stock_contributions = []
        for i, sid in enumerate(stock_ids):
            info = stock_info.get(sid, {})
            stock_contributions.append({
                "stock_id": sid,
                "symbol": info.get("symbol", "?"),
                "weight": round(float(weights[i]), 6),
                "mcar": mcar_result["mcar"][i],
                "contribution_pct": mcar_result["contribution_pct"][i],
            })

        markets = MARKET_GROUP_TO_MARKETS.get(market_group, [])
        factor_result = None
        if markets:
            for m in markets:
                fr = compute_factor_risk(stock_ids, weights, m)
                if fr:
                    factor_result = fr
                    break

        result = {
            "portfolio_vol": mcar_result["portfolio_vol"],
            "stock_contributions": stock_contributions,
        }
        if factor_result:
            result["factor_analysis"] = factor_result

        return result

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

    @staticmethod
    def _get_returns_matrix(stock_ids: list[int], lookback: int = 252):
        with get_connection() as conn:
            price_repo = DailyPriceRepository(conn)
            all_series = {}
            for sid in stock_ids:
                prices = price_repo.get_prices(sid, limit=lookback)
                all_series[sid] = {p.date: float(p.close) for p in prices}

        all_dates = [set(s.keys()) for s in all_series.values()]
        if not all_dates:
            return None
        common_dates = sorted(set.intersection(*all_dates))
        if len(common_dates) < 30:
            return None

        price_matrix = np.array([
            [all_series[sid][d] for d in common_dates]
            for sid in stock_ids
        ]).T
        return np.diff(price_matrix, axis=0) / price_matrix[:-1]
