import logging
from datetime import date

from app.schema import DataCoverage, FinancialStatement, ReportType
from app.quant.fundamentals import (
    eps, bps, per, pbr,
    roe, operating_margin,
    debt_ratio,
)

logger = logging.getLogger(__name__)

_Q_ORDER = [ReportType.Q1, ReportType.Q2, ReportType.Q3]

_MIN_SHARES = 100
_MAX_SHARES = 50_000_000_000

_BOUNDS: dict[str, tuple[float, float]] = {
    "per": (-1000, 1000),
    "pbr": (0, 200),
    "roe": (-10, 10),
    "debt_ratio": (0, 100),
    "operating_margin": (-100, 100),
}

_stats = {"shares_sanitized": 0, "clamped": 0, "negative_equity": 0}


def _sanitize_shares(shares: int | None, stock_id: int) -> int | None:
    if shares is None:
        return None
    if not (_MIN_SHARES <= shares <= _MAX_SHARES):
        logger.warning(f"[Fundamental] stock_id={stock_id}: shares={shares} out of range, set to None")
        _stats["shares_sanitized"] += 1
        return None
    return shares


def _clamp(val: float | None, key: str, stock_id: int) -> float | None:
    if val is None:
        return None
    lo, hi = _BOUNDS[key]
    if not (lo <= val <= hi):
        logger.warning(f"[Fundamental] stock_id={stock_id}: {key}={val:.4f} out of bounds ({lo}, {hi})")
        _stats["clamped"] += 1
        return None
    return round(val, 4)


class FundamentalService:
    @staticmethod
    def reset_stats():
        for k in _stats:
            _stats[k] = 0

    @staticmethod
    def get_stats() -> dict[str, int]:
        return dict(_stats)

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
        shares = _sanitize_shares(latest.shares_outstanding, stock_id)

        negative_equity = bs_equity is not None and bs_equity < 0
        if negative_equity:
            logger.warning(f"[Fundamental] stock_id={stock_id}: negative equity={bs_equity}")
            _stats["negative_equity"] += 1

        ttm = FundamentalService._ttm_income(statements)

        if ttm is not None:
            ttm_revenue, ttm_op_income, ttm_net_income = ttm
            eps_val = eps(ttm_net_income, shares) if ttm_net_income is not None else None
            roe_val = roe(ttm_net_income, bs_equity) if ttm_net_income is not None and bs_equity else None
            op_margin_val = (
                operating_margin(ttm_op_income, ttm_revenue)
                if ttm_op_income is not None and ttm_revenue is not None
                else None
            )
            all_present = all(v is not None for v in ttm)
            if eps_val is not None and eps_val < 0:
                coverage = DataCoverage.LOSS
            elif all_present:
                coverage = DataCoverage.FULL
            else:
                coverage = DataCoverage.PARTIAL
        else:
            eps_val = roe_val = op_margin_val = None
            if bs_equity is not None or bs_liabilities is not None:
                coverage = DataCoverage.PARTIAL
            else:
                coverage = DataCoverage.INSUFFICIENT

        bps_val = bps(bs_equity, shares) if bs_equity is not None else None
        per_val = per(latest_price, eps_val) if eps_val is not None else None
        pbr_val = pbr(latest_price, bps_val) if bps_val is not None else None
        debt_val = debt_ratio(bs_liabilities, bs_equity) if bs_equity and bs_liabilities is not None else None

        return (
            stock_id,
            date.today(),
            _clamp(per_val, "per", stock_id),
            _clamp(pbr_val, "pbr", stock_id),
            _round(eps_val),
            _round(bps_val),
            _clamp(roe_val, "roe", stock_id),
            _clamp(debt_val, "debt_ratio", stock_id),
            _clamp(op_margin_val, "operating_margin", stock_id),
            coverage.value,
        )

    @staticmethod
    def no_fs_row(stock_id: int) -> tuple:
        return (
            stock_id, date.today(),
            None, None, None, None, None, None, None,
            DataCoverage.NO_FS.value,
        )

    @staticmethod
    def _ttm_income(
        statements: list[FinancialStatement],
    ) -> tuple[float | None, float | None, float | None] | None:
        if not statements:
            return None

        if statements[0].report_type == ReportType.FY:
            s = statements[0]
            return _to_float(s.revenue), _to_float(s.operating_income), _to_float(s.net_income)

        fy_stmt: FinancialStatement | None = None
        current_qs: dict[ReportType, FinancialStatement] = {}
        prior_qs: dict[ReportType, FinancialStatement] = {}

        for s in statements:
            if s.report_type == ReportType.FY and fy_stmt is None:
                fy_stmt = s
            elif s.report_type != ReportType.FY:
                if fy_stmt is None:
                    current_qs.setdefault(s.report_type, s)
                elif s.fiscal_year == fy_stmt.fiscal_year:
                    prior_qs.setdefault(s.report_type, s)

        if fy_stmt is None:
            return None

        n = 0
        for qt in _Q_ORDER:
            if qt in current_qs and qt in prior_qs:
                n += 1
            else:
                break
        if n == 0:
            return None

        def _field_ttm(getter):
            fy_val = getter(fy_stmt)
            if fy_val is None:
                return None
            cur = [getter(current_qs[_Q_ORDER[i]]) for i in range(n)]
            pri = [getter(prior_qs[_Q_ORDER[i]]) for i in range(n)]
            if any(v is None for v in cur) or any(v is None for v in pri):
                return None
            return fy_val + sum(cur) - sum(pri)

        result = (
            _field_ttm(lambda s: _to_float(s.revenue)),
            _field_ttm(lambda s: _to_float(s.operating_income)),
            _field_ttm(lambda s: _to_float(s.net_income)),
        )
        if all(v is None for v in result):
            return None
        return result


def _to_float(val) -> float | None:
    if val is None:
        return None
    return float(val)


def _round(val: float | None, digits: int = 4) -> float | None:
    if val is None:
        return None
    return round(val, digits)
