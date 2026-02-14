import numpy as np

from app.db import get_connection, DailyPriceRepository, StockRepository
from app.schema import Market
from app.quant.simulation import (
    generate_gbm_paths,
    generate_bootstrap_paths,
    simulation_summary,
)

METHODS = {"gbm", "bootstrap"}
MIN_DATA_POINTS = 60


class SimulationService:
    @staticmethod
    def run(
        symbol: str,
        market: Market,
        days: int = 60,
        num_simulations: int = 10000,
        confidence: float = 0.95,
        lookback: int = 252,
        method: str = "gbm",
    ) -> dict:
        if method not in METHODS:
            raise ValueError(f"method must be one of {METHODS}")

        stock, prices = SimulationService._load_data(symbol, market, lookback)
        stock_id, _, name, _ = stock
        close_prices = np.array([float(p.close) for p in prices])

        if len(close_prices) < MIN_DATA_POINTS:
            raise ValueError(
                f"Insufficient data: {len(close_prices)}/{MIN_DATA_POINTS} days"
            )

        current_price = float(close_prices[0])  # prices are DESC ordered
        returns = SimulationService._compute_log_returns(close_prices)

        if method == "gbm":
            mu, sigma = SimulationService._estimate_gbm_params(returns)
            paths = generate_gbm_paths(
                current_price, mu, sigma, days, num_simulations
            )
        else:
            simple_returns = SimulationService._compute_simple_returns(close_prices)
            paths = generate_bootstrap_paths(
                current_price, simple_returns, days, num_simulations
            )
            mu, sigma = SimulationService._estimate_gbm_params(returns)

        stats = simulation_summary(paths, confidence)

        return {
            "symbol": symbol,
            "name": name,
            "current_price": current_price,
            "simulation_days": days,
            "num_simulations": num_simulations,
            "method": method,
            "confidence": confidence,
            **stats,
            "parameters": {
                "mu_daily": round(float(mu), 8),
                "sigma_daily": round(float(sigma), 8),
                "lookback_days": len(close_prices),
            },
        }

    @staticmethod
    def _load_data(symbol: str, market: Market, lookback: int):
        with get_connection() as conn:
            stock_repo = StockRepository(conn)
            stock = stock_repo.get_by_symbol(symbol, market)
            if stock is None:
                raise ValueError(f"Stock not found: {symbol}")

            stock_id = stock[0]
            price_repo = DailyPriceRepository(conn)
            prices = price_repo.get_prices(stock_id, limit=lookback)

        return stock, prices

    @staticmethod
    def _compute_log_returns(close_prices: np.ndarray) -> np.ndarray:
        ordered = close_prices[::-1]  # ASC order for calculation
        log_returns = np.diff(np.log(ordered))
        return log_returns

    @staticmethod
    def _compute_simple_returns(close_prices: np.ndarray) -> np.ndarray:
        ordered = close_prices[::-1]
        return np.diff(ordered) / ordered[:-1]

    @staticmethod
    def _estimate_gbm_params(log_returns: np.ndarray) -> tuple[float, float]:
        mu = log_returns.mean()
        sigma = log_returns.std(ddof=1)
        return mu, sigma