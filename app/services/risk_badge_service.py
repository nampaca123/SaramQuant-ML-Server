import logging
from dataclasses import asdict
from datetime import date
from statistics import median

from psycopg2.extensions import connection

from app.db.repositories.factor import FactorRepository
from app.db.repositories.fundamental import FundamentalRepository
from app.db.repositories.indicator import IndicatorRepository
from app.db.repositories.risk_badge import RiskBadgeRepository
from app.quant.risk_badge import (
    dimension_company_health as company_health,
    composite_badge as composite,
    dimension_price_heat as price_heat,
    dimension_trend as trend,
    dimension_valuation as valuation,
    dimension_volatility as volatility,
)
from app.quant.risk_badge.badge_types import DimensionResult
from app.schema import Market

logger = logging.getLogger(__name__)


class RiskBadgeService:
    def __init__(self, conn: connection):
        self._conn = conn
        self._ind_repo = IndicatorRepository(conn)
        self._fund_repo = FundamentalRepository(conn)
        self._factor_repo = FactorRepository(conn)
        self._badge_repo = RiskBadgeRepository(conn)

    def compute_single(self, stock_id: int, market: Market) -> dict:
        ind = self._ind_repo.get_latest_by_stock(stock_id)
        fund = self._fund_repo.get_latest_by_stock(stock_id)
        vol_z = self._factor_repo.get_volatility_z_by_stock(stock_id, market)

        sector = fund.get("sector") if fund else None
        sec_agg = self._factor_repo.get_sector_aggregate_single(market, sector) if sector else None
        mkt_agg = self._factor_repo.get_market_aggregate(market)

        dims = self._compute_dimensions(ind, fund, vol_z, sec_agg, mkt_agg)
        summary_tier = composite.compute_composite(dims)

        return self._build_badge_row(stock_id, market.value, summary_tier, dims)

    def compute_batch(self, market: Market) -> list[dict]:
        indicators = self._ind_repo.get_all_by_market(market)
        fundamentals = self._fund_repo.get_all_by_market(market)
        exposures = self._factor_repo.get_all_exposures_by_market(market)
        sector_aggs = self._factor_repo.get_all_sector_aggregates(market)

        mkt_agg = _compute_market_aggregate(fundamentals)

        results = []
        for stock_id, ind in indicators.items():
            fund = fundamentals.get(stock_id)
            vol_z = exposures.get(stock_id)
            sector = (fund or ind).get("sector")
            sec_agg = sector_aggs.get(sector) if sector else None

            dims = self._compute_dimensions(ind, fund, vol_z, sec_agg, mkt_agg)
            summary_tier = composite.compute_composite(dims)
            results.append(self._build_badge_row(stock_id, market.value, summary_tier, dims))

        logger.info(f"[RiskBadge] Computed {len(results)} badges for {market.value}")
        return results

    @staticmethod
    def _compute_dimensions(
        ind: dict | None,
        fund: dict | None,
        vol_z: float | None,
        sec_agg: dict | None,
        mkt_agg: dict | None,
    ) -> list[DimensionResult]:
        dims = []
        if ind:
            dims.append(price_heat.compute(ind))
            dims.append(volatility.compute(ind, vol_z))
            dims.append(trend.compute(ind))
        else:
            dims.append(DimensionResult("price_heat", 50, price_heat.to_tier(50), None, {}, False))
            dims.append(DimensionResult("volatility", 50, volatility.to_tier(50), None, {}, False))
            dims.append(DimensionResult("trend", 50, trend.to_tier(50), None, {}, False))

        dims.append(company_health.compute(fund, sec_agg, mkt_agg))
        dims.append(valuation.compute(fund, sec_agg, mkt_agg))
        return dims

    @staticmethod
    def _build_badge_row(
        stock_id: int, market: str, summary_tier, dims: list[DimensionResult],
    ) -> dict:
        return {
            "stock_id": stock_id,
            "market": market,
            "date": date.today().isoformat(),
            "summary_tier": summary_tier.value,
            "dimensions": {
                "dims": [
                    {
                        "name": d.name,
                        "score": d.score,
                        "tier": d.tier.value,
                        "direction": d.direction.value if d.direction else None,
                        "components": d.components,
                        "data_available": d.data_available,
                    }
                    for d in dims
                ],
            },
        }


def _compute_market_aggregate(fundamentals: dict[int, dict]) -> dict | None:
    if not fundamentals:
        return None
    cols = ["per", "pbr", "roe", "operating_margin", "debt_ratio"]
    agg = {"stock_count": len(fundamentals)}
    for col in cols:
        vals = [float(f[col]) for f in fundamentals.values() if f.get(col) is not None]
        agg[f"median_{col}"] = median(vals) if vals else None
    return agg
