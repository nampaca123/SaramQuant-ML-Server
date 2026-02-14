import numpy as np


def generate_gbm_paths(
    current_price: float,
    mu: float,
    sigma: float,
    days: int,
    num_simulations: int,
    antithetic: bool = True,
) -> np.ndarray:

    rng = np.random.default_rng()
    dt = 1.0

    if antithetic:
        half = num_simulations // 2
        z_half = rng.standard_normal((half, days))
        z = np.concatenate([z_half, -z_half], axis=0)
    else:
        z = rng.standard_normal((num_simulations, days))

    drift = (mu - 0.5 * sigma ** 2) * dt
    diffusion = sigma * np.sqrt(dt) * z

    log_returns = drift + diffusion
    daily_factors = np.exp(log_returns)

    price_paths = np.empty((z.shape[0], days + 1))
    price_paths[:, 0] = current_price
    price_paths[:, 1:] = current_price * np.cumprod(daily_factors, axis=1)

    return price_paths


def generate_bootstrap_paths(
    current_price: float,
    historical_returns: np.ndarray,
    days: int,
    num_simulations: int,
) -> np.ndarray:

    rng = np.random.default_rng()

    sampled = rng.choice(historical_returns, size=(num_simulations, days), replace=True)
    daily_factors = 1.0 + sampled

    price_paths = np.empty((num_simulations, days + 1))
    price_paths[:, 0] = current_price
    price_paths[:, 1:] = current_price * np.cumprod(daily_factors, axis=1)

    return price_paths