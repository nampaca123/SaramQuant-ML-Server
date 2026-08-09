"""progressive deactivate 계산 — 가격/섹터/재무 존재 여부로 is_active 변경분과 안전 통계를 만든다."""
from datetime import datetime, timezone

import pandas as pd

from app.db import lake_reader
from app.db.lake_schemas import TABLES
from app.schema import Market

STOCK_COLUMNS = [name for name, _ in TABLES["stocks"].columns]
CANDIDATE_COLUMNS = STOCK_COLUMNS + ["has_price", "has_fs", "relisted"]

MARKET_GROUPS = {
    "kr": [Market.KR_KOSPI.value, Market.KR_KOSDAQ.value],
    "us": [Market.US_NYSE.value, Market.US_NASDAQ.value],
}
NO_SECTOR = "N/A"


def resolve_markets(market_group: str) -> list[str]:
    group = MARKET_GROUPS.get(market_group.lower())
    if group is not None:
        return list(group)
    if market_group in {market.value for market in Market}:
        return [market_group]
    raise ValueError(f"unknown market group: {market_group}")


def decide_activation(candidates: pd.DataFrame, now: datetime) -> pd.DataFrame:
    """활성 유지 조건은 가격·섹터·재무 3종 존재이며, 재상장(relisted) 종목만 비활성에서 복귀한다."""
    if candidates.empty:
        return pd.DataFrame(columns=STOCK_COLUMNS)

    active = candidates["is_active"].astype(bool)
    has_sector = candidates["sector"].notna() & (candidates["sector"] != NO_SECTOR)
    eligible = candidates["has_price"].astype(bool) & has_sector & candidates["has_fs"].astype(bool)
    new_active = (active | candidates["relisted"].astype(bool)) & eligible

    changed = new_active != active
    changes = candidates.loc[changed, STOCK_COLUMNS].copy()
    changes["is_active"] = new_active[changed]
    changes["updated_at"] = now
    return changes.reset_index(drop=True)


def summarize_activation(candidates: pd.DataFrame, changes: pd.DataFrame) -> dict[str, int]:
    active_before = int(candidates["is_active"].astype(bool).sum()) if not candidates.empty else 0
    if changes.empty:
        reactivated, deactivated = 0, 0
    else:
        flags = changes["is_active"].astype(bool)
        reactivated, deactivated = int(flags.sum()), int((~flags).sum())
    return {
        "total": len(candidates),
        "active_before": active_before,
        "active_after": active_before + reactivated - deactivated,
        "deactivated": deactivated,
        "reactivated": reactivated,
    }


def _relisted_flags(
    candidates: pd.DataFrame, active_symbols: dict | None
) -> pd.Series | bool:
    if active_symbols is None:
        return candidates["has_price"].astype(bool)
    listed = {
        getattr(market, "value", market): set(symbols)
        for market, symbols in active_symbols.items()
    }
    return pd.Series(
        [
            symbol in listed.get(market, set())
            for symbol, market in zip(candidates["symbol"], candidates["market"])
        ],
        index=candidates.index,
        dtype=bool,
    )


def load_candidates(market_group: str, active_symbols: dict | None = None) -> pd.DataFrame:
    markets = resolve_markets(market_group)
    columns = ", ".join(f"s.{column}" for column in STOCK_COLUMNS)
    sql = (
        f"SELECT {columns},"
        f" EXISTS (SELECT 1 FROM {lake_reader.scan('daily_prices')} p WHERE p.stock_id = s.id)"
        " AS has_price,"
        f" EXISTS (SELECT 1 FROM {lake_reader.scan('financial_statements')} f"
        " WHERE f.stock_id = s.id) AS has_fs"
        f" FROM {lake_reader.scan('stocks')} s"
        f" WHERE s.market IN ({', '.join('?' * len(markets))})"
    )
    candidates = lake_reader.query_df(sql, markets)
    if candidates.empty:
        return pd.DataFrame(columns=CANDIDATE_COLUMNS)
    candidates["relisted"] = _relisted_flags(candidates, active_symbols)
    return candidates


def compute_deactivation(
    market_group: str, active_symbols: dict | None = None
) -> tuple[pd.DataFrame, dict[str, int]]:
    candidates = load_candidates(market_group, active_symbols)
    changes = decide_activation(candidates, datetime.now(timezone.utc))
    return changes, summarize_activation(candidates, changes)
