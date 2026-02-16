from datetime import date

from app.schema import DataCoverage, FinancialStatement, ReportType
from app.quant.fundamentals import (
    eps, bps, per, pbr,
    roe, operating_margin,
    debt_ratio,
)


class FundamentalService:
    @staticmethod
    def compute(
        stock_id: int,
        latest_price: float,
        statements: list[FinancialStatement],
    ) -> tuple | None:
        if not statements:
            return None

        latest = statements[0]

        bs_equity = _to_float(latest.total_equity)
        bs_liabilities = _to_float(latest.total_liabilities)
        shares = latest.shares_outstanding

        ttm = FundamentalService._ttm_income(statements)

        if ttm is not None:
            ttm_revenue, ttm_op_income, ttm_net_income = ttm
            eps_val = eps(ttm_net_income, shares)
            roe_val = roe(ttm_net_income, bs_equity) if bs_equity else None
            op_margin_val = operating_margin(ttm_op_income, ttm_revenue)
        else:
            eps_val = None
            roe_val = None
            op_margin_val = None

        bps_val = bps(bs_equity, shares) if bs_equity is not None else None
        per_val = per(latest_price, eps_val) if eps_val is not None else None
        pbr_val = pbr(latest_price, bps_val) if bps_val is not None else None
        debt_val = debt_ratio(bs_liabilities, bs_equity) if bs_equity and bs_liabilities is not None else None

        if ttm is not None:
            coverage = DataCoverage.LOSS if eps_val is not None and eps_val < 0 else DataCoverage.FULL
        elif bs_equity is not None or bs_liabilities is not None:
            coverage = DataCoverage.PARTIAL
        else:
            coverage = DataCoverage.INSUFFICIENT

        return (
            stock_id,
            date.today(),
            _round(per_val),
            _round(pbr_val),
            _round(eps_val),
            _round(bps_val),
            _round(roe_val),
            _round(debt_val),
            _round(op_margin_val),
            coverage.value,
        )

    @staticmethod
    def _ttm_income(
        statements: list[FinancialStatement],
    ) -> tuple[float, float, float] | None:
        if statements[0].report_type == ReportType.FY:
            s = statements[0]
            r, o, n = _to_float(s.revenue), _to_float(s.operating_income), _to_float(s.net_income)
            if any(v is None for v in (r, o, n)):
                return None
            return r, o, n

        quarters = [s for s in statements if s.report_type != ReportType.FY][:4]
        if len(quarters) < 4:
            return None

        vals = [_to_float(s.revenue) for s in quarters]
        ops = [_to_float(s.operating_income) for s in quarters]
        nets = [_to_float(s.net_income) for s in quarters]
        if any(v is None for v in vals + ops + nets):
            return None

        return sum(vals), sum(ops), sum(nets)


def _to_float(val) -> float | None:
    if val is None:
        return None
    return float(val)


def _round(val: float | None, digits: int = 4) -> float | None:
    if val is None:
        return None
    return round(val, digits)
