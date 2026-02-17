import numpy as np
import pandas as pd

from .normalize import winsorize, z_score

STYLE_FACTORS = ["size", "value", "momentum", "volatility", "quality", "leverage"]


def compute_exposures(
    stock_ids: np.ndarray,
    close_prices: pd.Series,
    shares_outstanding: pd.Series,
    pbr: pd.Series,
    roe: pd.Series,
    operating_margin: pd.Series,
    debt_ratio: pd.Series,
    returns_252: pd.Series,
    returns_21: pd.Series,
    ewm_vol: pd.Series,
    sectors: pd.Series,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Compute standardized factor exposures for all stocks.

    Returns:
        (style_exposures, industry_dummies) — both indexed by stock_id.
        style_exposures: DataFrame[stock_id, 6 style z-scores]
        industry_dummies: DataFrame[stock_id, one-hot industry columns]
    """
    exposures = pd.DataFrame(index=stock_ids)

    # ensure float64 dtype
    close_prices = close_prices.astype(float)
    shares_outstanding = shares_outstanding.astype(float)
    pbr = pbr.astype(float)

    # raw values
    mcap = shares_outstanding * close_prices
    exposures["size"] = np.log(mcap.where(mcap > 0, np.nan))
    exposures["value"] = (1.0 / pbr).replace([np.inf, -np.inf], np.nan)
    exposures["momentum"] = returns_252 - returns_21
    exposures["volatility"] = ewm_vol

    # Quality = equal-weighted average of ROE z-score + operating_margin z-score
    roe_z = z_score(winsorize(roe.reindex(stock_ids)))
    opm_z = z_score(winsorize(operating_margin.reindex(stock_ids)))
    exposures["quality"] = (roe_z + opm_z) / 2

    exposures["leverage"] = debt_ratio

    # standardize: winsorize → z_score (weighted by sqrt market cap)
    sqrt_mcap = np.sqrt(mcap.reindex(stock_ids).clip(lower=0)).replace(0, np.nan)
    for col in STYLE_FACTORS:
        raw = exposures[col].copy()
        valid = raw.dropna()
        if len(valid) < 2:
            exposures[col] = 0.0
            continue
        w = winsorize(valid)
        exposures.loc[valid.index, col] = z_score(w, weights=sqrt_mcap.reindex(valid.index))

    # industry dummies from sector
    valid_sectors = sectors.reindex(stock_ids).dropna()
    valid_sectors = valid_sectors[valid_sectors != "N/A"]
    industry_dummies = pd.get_dummies(valid_sectors, dtype=float)
    industry_dummies = industry_dummies.reindex(stock_ids, fill_value=0.0)

    return exposures[STYLE_FACTORS], industry_dummies


def build_design_matrix(
    style_exposures: pd.DataFrame,
    industry_dummies: pd.DataFrame,
) -> pd.DataFrame:
    """
    Build full design matrix X = [market(1) | styles(S) | industries(I)].
    Only includes rows where all style factors are non-NaN.
    Drops zero-variance industry columns to prevent singular KKT matrix.
    """
    market_col = pd.DataFrame(
        {"market": 1.0}, index=style_exposures.index
    )
    X = pd.concat([market_col, style_exposures, industry_dummies], axis=1)
    X = X.dropna()
    zero_cols = X.columns[(X == 0).all()]
    if len(zero_cols) > 0:
        X = X.drop(columns=zero_cols)
    return X
