import logging
import numpy as np

from app.db import get_connection, DailyPriceRepository, PortfolioRepository, StockRepository
from app.quant.simulation import (
    generate_portfolio_bootstrap_paths,
    generate_correlated_gbm_paths,
    simulation_summary,
)
from app.quant.simulation.defaults import (
    DEFAULT_DAYS, DEFAULT_NUM_SIMULATIONS, DEFAULT_CONFIDENCE,
    DEFAULT_LOOKBACK,
)

logger = logging.getLogger(__name__)

METHODS = {"bootstrap", "gbm"}
MIN_DATA_POINTS = 60


class PortfolioSimulationService:
    @staticmethod
    def run(
        portfolio_id: int,
        days: int = DEFAULT_DAYS,
        num_simulations: int = DEFAULT_NUM_SIMULATIONS,
        confidence: float = DEFAULT_CONFIDENCE,
        lookback: int = DEFAULT_LOOKBACK,
        method: str = "bootstrap",
    ) -> dict:
        if method not in METHODS:
            raise ValueError(f"method must be one of {METHODS}")

        holdings, stock_info = PortfolioSimulationService._load_holdings(portfolio_id)
        if not holdings:
            raise ValueError("Portfolio has no holdings")

        stock_ids = [h.stock_id for h in holdings]
        shares_arr = np.array([float(h.shares) for h in holdings])

        returns_matrix, current_prices, common_dates, excluded = (
            PortfolioSimulationService._build_returns_matrix(stock_ids, lookback)
        )

        effective_lookback = len(common_dates)
        if effective_lookback < MIN_DATA_POINTS:
            raise ValueError(
                f"Insufficient common trading days: {effective_lookback}/{MIN_DATA_POINTS}"
            )

        active_mask = np.array([sid not in excluded for sid in stock_ids])
        active_shares = shares_arr[active_mask]
        active_current = current_prices

        if method == "bootstrap":
            paths = generate_portfolio_bootstrap_paths(
                active_current, returns_matrix, active_shares, days, num_simulations,
            )
        else:
            log_returns = np.log(1.0 + returns_matrix)
            mu = log_returns.mean(axis=0)
            sigma = log_returns.std(axis=0, ddof=1)
            corr = np.corrcoef(returns_matrix.T)
            paths = generate_correlated_gbm_paths(
                active_current, mu, sigma, corr, active_shares, days, num_simulations,
            )

        stats = simulation_summary(paths, confidence)
        market_group = PortfolioSimulationService._get_market_group(portfolio_id)
        current_value = float((active_current * active_shares).sum())

        active_stock_ids = [sid for sid in stock_ids if sid not in excluded]
        excluded_info = [
            {"stock_id": sid, "symbol": stock_info.get(sid, {}).get("symbol", "?")}
            for sid in excluded
        ]

        return {
            "target": {
                "type": "portfolio",
                "portfolio_id": portfolio_id,
                "market_group": market_group,
                "holdings_count": len(active_stock_ids),
                "current_value": round(current_value, 2),
            },
            "simulation_days": days,
            "num_simulations": num_simulations,
            "method": method,
            "confidence": confidence,
            "results": {
                "expected_return": stats["expected_return"],
                "var": stats["var"],
                "cvar": stats["cvar"],
                "final_value_percentiles": stats["final_price_percentiles"],
                "path_percentiles": stats["path_percentiles"],
            },
            "parameters": {
                "lookback_days": lookback,
                "effective_lookback_days": effective_lookback,
                "min_data_points": MIN_DATA_POINTS,
            },
            "data_coverage": "PARTIAL" if excluded else "FULL",
            "excluded_stocks": excluded_info,
        }

    @staticmethod
    def _load_holdings(portfolio_id: int):
        with get_connection() as conn:
            repo = PortfolioRepository(conn)
            holdings = repo.get_holdings(portfolio_id)
            stock_repo = StockRepository(conn)
            stock_info = {}
            for h in holdings:
                info = stock_repo.find_by_id(h.stock_id)
                if info:
                    stock_info[h.stock_id] = info
        return holdings, stock_info

    @staticmethod
    def _build_returns_matrix(stock_ids: list[int], lookback: int):
        with get_connection() as conn:
            price_repo = DailyPriceRepository(conn)

            all_series = {}
            for sid in stock_ids:
                prices = price_repo.get_prices(sid, limit=lookback)
                date_close = {p.date: float(p.close) for p in prices}
                all_series[sid] = date_close

        all_dates = [set(s.keys()) for s in all_series.values()]
        if not all_dates:
            return np.empty((0, 0)), np.array([]), [], []

        common_dates = sorted(set.intersection(*all_dates))

        excluded = set()
        if len(common_dates) < MIN_DATA_POINTS:
            for sid in stock_ids:
                other_dates = [set(all_series[s].keys()) for s in stock_ids if s != sid]
                if other_dates:
                    alt_common = sorted(set.intersection(*other_dates))
                    if len(alt_common) >= MIN_DATA_POINTS and len(alt_common) > len(common_dates):
                        excluded.add(sid)

            if excluded:
                remaining_ids = [s for s in stock_ids if s not in excluded]
                remaining_dates = [set(all_series[s].keys()) for s in remaining_ids]
                common_dates = sorted(set.intersection(*remaining_dates)) if remaining_dates else []

        active_ids = [s for s in stock_ids if s not in excluded]
        if len(common_dates) < 2:
            return np.empty((0, 0)), np.array([]), common_dates, list(excluded)

        price_matrix = np.array([
            [all_series[sid][d] for d in common_dates]
            for sid in active_ids
        ]).T

        returns_matrix = np.diff(price_matrix, axis=0) / price_matrix[:-1]
        current_prices = price_matrix[-1]

        return returns_matrix, current_prices, common_dates[1:], list(excluded)

    @staticmethod
    def _get_market_group(portfolio_id: int) -> str:
        with get_connection() as conn:
            repo = PortfolioRepository(conn)
            mg = repo.get_portfolio_market_group(portfolio_id)
        return mg or "UNKNOWN"
