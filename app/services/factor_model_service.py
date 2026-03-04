import logging
from datetime import date

import numpy as np
import pandas as pd
from psycopg2.extensions import connection

from app.db import (
    DailyPriceRepository,
    FactorRepository,
    FundamentalRepository,
    StockRepository,
)
from app.schema import Market
from app.schema.enums.market import market_to_country
from app.utils import load_risk_free_rates
from app.quant.factor_model.exposure import (
    STYLE_FACTORS,
    compute_exposures,
    build_design_matrix,
)
from app.quant.factor_model.regression import constrained_wls
from app.quant.factor_model.covariance import ewm_factor_covariance
from app.quant.factor_model.beta import factor_beta, build_exposure_vector

logger = logging.getLogger(__name__)

_MIN_COVARIANCE_DAYS = 90
_PRICE_LIMIT = 300


class FactorModelService:
    """
    Daily factor model pipeline per market.

    run()        — daily pipeline: compute + store exposures, factor returns, covariance
    get_betas()  — load stored data, return {stock_id: factor_beta} for indicator computation
    """

    def __init__(self, conn: connection):
        self._conn = conn
        self._stock_repo = StockRepository(conn)
        self._price_repo = DailyPriceRepository(conn)
        self._factor_repo = FactorRepository(conn)
        self._fund_repo = FundamentalRepository(conn)

    def run(self, market: Market, price_map: dict[int, list[tuple]] | None = None) -> dict:
        today = date.today()
        rf_rates = load_risk_free_rates(self._conn, [market])
        rf_daily = rf_rates.get(market_to_country(market), 3.0) / 100 / 252

        # 1. eligible stocks (sector-filtered)
        stocks = self._load_eligible_stocks(market)
        if len(stocks) < 30:
            logger.warning(f"[FactorModel] {market.value}: only {len(stocks)} eligible stocks, skipping")
            return {"market": market.value, "status": "skipped", "reason": "insufficient_stocks"}

        stock_ids = np.array([s[0] for s in stocks])
        sectors = pd.Series({s[0]: s[2] for s in stocks})

        # 2. price features
        if price_map is None:
            price_map = self._price_repo.get_prices_by_market(market, limit_per_stock=_PRICE_LIMIT)
        close_series, returns_today, returns_252, returns_21, ewm_vol = (
            self._compute_price_features(stock_ids, price_map)
        )

        # 3. fundamentals
        shares, pbr, roe, opm, debt = self._load_fundamentals(stock_ids)

        # 4. exposures
        style_exp, industry_dummies = compute_exposures(
            stock_ids, close_series, shares, pbr, roe, opm, debt,
            returns_252, returns_21, ewm_vol, sectors,
        )

        # 5. cross-sectional regression
        X_df = build_design_matrix(style_exp, industry_dummies)
        valid_ids = X_df.index.values
        excess_returns = returns_today.reindex(valid_ids) - rf_daily
        valid_mask = excess_returns.notna()
        valid_ids = valid_ids[valid_mask.values]
        if len(valid_ids) < 30:
            logger.warning(f"[FactorModel] {market.value}: only {len(valid_ids)} valid rows after filter")
            return {"market": market.value, "status": "skipped", "reason": "insufficient_valid"}

        X_sub = X_df.loc[valid_ids]
        y = excess_returns.loc[valid_ids].values

        # re-drop zero-variance columns after excess-return filtering
        zero_cols = X_sub.columns[(X_sub == 0).all()]
        if len(zero_cols) > 0:
            X_sub = X_sub.drop(columns=zero_cols)

        X = X_sub.values
        factor_names = list(X_sub.columns)
        style_set = set(STYLE_FACTORS)
        n_styles = sum(1 for c in factor_names if c in style_set)
        industry_cols = [c for c in factor_names if c != "market" and c not in style_set]

        mcap = shares.reindex(valid_ids).fillna(0) * close_series.reindex(valid_ids).fillna(0)
        w = np.sqrt(mcap.values.astype(float).clip(min=0))
        w = np.where(np.isnan(w) | (w == 0), 1.0, w)

        industry_mcap = np.array([
            mcap.reindex(valid_ids)[sectors.reindex(valid_ids) == col].sum()
            for col in industry_cols
        ], dtype=float)
        total_mcap = industry_mcap.sum()
        industry_mcap_weights = (
            industry_mcap / total_mcap if total_mcap > 0
            else np.ones(len(industry_cols)) / max(len(industry_cols), 1)
        )

        factor_ret, _ = constrained_wls(y, X, w, industry_mcap_weights, n_styles)

        # 6. save exposures
        exp_rows = []
        for sid in stock_ids:
            row_data = style_exp.loc[sid] if sid in style_exp.index else pd.Series(dtype=float)
            if row_data.isna().all():
                continue
            exp_rows.append((
                int(sid), today,
                self._to_db(row_data.get("size")),
                self._to_db(row_data.get("value")),
                self._to_db(row_data.get("momentum")),
                self._to_db(row_data.get("volatility")),
                self._to_db(row_data.get("quality")),
                self._to_db(row_data.get("leverage")),
            ))
        self._factor_repo.upsert_exposures(exp_rows)

        # 7. save factor returns
        fr_rows = [
            (market.value, today, factor_names[i], float(factor_ret[i]))
            for i in range(len(factor_names))
        ]
        self._factor_repo.upsert_factor_returns(fr_rows)

        # 8. covariance (if enough history)
        n_dates = self._factor_repo.count_factor_return_dates(market)
        if n_dates >= _MIN_COVARIANCE_DAYS:
            self._update_covariance(market, today)

        logger.info(
            f"[FactorModel] {market.value}: exposures={len(exp_rows)}, "
            f"factor_returns={len(fr_rows)}, history_days={n_dates}"
        )
        return {
            "market": market.value, "status": "ok",
            "exposures": len(exp_rows), "factor_returns": len(fr_rows),
            "history_days": n_dates,
        }

    def get_betas(self, market: Market) -> dict[int, float]:
        """
        Load stored covariance + exposures → compute factor betas.
        Returns empty dict if < 90 days history (ols_beta used as fallback).
        """
        cov_data = self._factor_repo.get_latest_covariance(market)
        if cov_data is None:
            return {}

        _, matrix_json = cov_data
        cov_matrix = np.array(matrix_json)

        exposures = self._factor_repo.get_latest_exposures(market)
        if not exposures:
            return {}

        history = self._factor_repo.get_factor_returns_history(market, limit=1)
        if not history:
            return {}
        factor_names = sorted(set(r[1] for r in history))

        if cov_matrix.shape[0] != len(factor_names):
            return {}

        sectors = self._stock_repo.get_sectors_by_market(market)

        name_idx = {n: i for i, n in enumerate(factor_names)}
        X_m = np.zeros(len(factor_names))
        if "market" in name_idx:
            X_m[name_idx["market"]] = 1.0

        betas: dict[int, float] = {}
        for row in exposures:
            sid = row[0]
            style_vals = np.array([float(v) if v is not None else np.nan for v in row[1:]])
            if np.isnan(style_vals).any():
                continue
            X_i = build_exposure_vector(style_vals, sectors.get(sid), factor_names)
            betas[sid] = factor_beta(X_i, X_m, cov_matrix)

        return betas

    def _update_covariance(self, market: Market, today: date) -> None:
        history = self._factor_repo.get_factor_returns_history(market, limit=252)
        if not history:
            return

        dates_sorted = sorted(set(r[0] for r in history))
        factor_names = sorted(set(r[1] for r in history))

        date_idx = {d: i for i, d in enumerate(dates_sorted)}
        name_idx = {n: i for i, n in enumerate(factor_names)}
        fr_matrix = np.zeros((len(dates_sorted), len(factor_names)))
        for dt, fn, val in history:
            fr_matrix[date_idx[dt], name_idx[fn]] = float(val)

        cov_matrix = ewm_factor_covariance(fr_matrix, halflife=90)
        self._factor_repo.upsert_covariance(market, today, cov_matrix.tolist())

    def _load_eligible_stocks(self, market: Market) -> list[tuple]:
        return self._stock_repo.get_eligible_for_factors(market)

    def _compute_price_features(
        self, stock_ids: np.ndarray, price_map: dict
    ) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series, pd.Series]:
        length_groups: dict[int, tuple[list[int], list[list[float]]]] = {}
        for sid in stock_ids:
            prices = price_map.get(int(sid))
            if not prices or len(prices) < 2:
                continue
            closes = [float(p[4]) for p in prices]
            n = len(closes)
            if n not in length_groups:
                length_groups[n] = ([], [])
            length_groups[n][0].append(sid)
            length_groups[n][1].append(closes)

        close_dict, ret_today, ret_252, ret_21, vol_dict = {}, {}, {}, {}, {}

        for length, (sids, close_lists) in length_groups.items():
            arr = np.array(close_lists)
            sid_arr = np.array(sids)

            close_dict.update(zip(sid_arr, arr[:, -1]))
            ret_today.update(zip(sid_arr, arr[:, -1] / arr[:, -2] - 1))

            if length >= 252:
                ret_252.update(zip(sid_arr, arr[:, -1] / arr[:, -252] - 1))
            if length >= 21:
                ret_21.update(zip(sid_arr, arr[:, -1] / arr[:, -21] - 1))
            if length >= 43:
                rets_df = pd.DataFrame(arr.T).pct_change().iloc[1:]
                ewm_std = rets_df.ewm(halflife=42).std().iloc[-1].values
                vol_dict.update(zip(sid_arr, ewm_std))

        return (
            pd.Series(close_dict), pd.Series(ret_today),
            pd.Series(ret_252), pd.Series(ret_21), pd.Series(vol_dict),
        )

    def _load_fundamentals(self, stock_ids: np.ndarray):
        rows = self._fund_repo.get_with_shares(stock_ids.tolist())

        shares, pbr, roe, opm, debt = {}, {}, {}, {}, {}
        for r in rows:
            sid = r[0]
            pbr[sid] = float(r[1]) if r[1] is not None else np.nan
            roe[sid] = float(r[2]) if r[2] is not None else np.nan
            opm[sid] = float(r[3]) if r[3] is not None else np.nan
            debt[sid] = float(r[4]) if r[4] is not None else np.nan
            shares[sid] = float(r[5]) if r[5] is not None else np.nan

        return pd.Series(shares), pd.Series(pbr), pd.Series(roe), pd.Series(opm), pd.Series(debt)

    @staticmethod
    def _to_db(val) -> float | None:
        if val is None or (isinstance(val, float) and np.isnan(val)):
            return None
        return round(float(val), 4)
