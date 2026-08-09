"""risk_badges 레포지토리 — DuckDB(Iceberg) 조회 + 스냅샷 전체 교체 쓰기. dimensions는 JSON 문자열로 저장한다."""
import json

import pandas as pd

from app.db import lake_reader, lake_writer
from app.db.repositories import lake_rows

BADGE_COLUMNS = lake_rows.columns_of("risk_badges")


def _to_dict(row) -> dict:
    badge = lake_rows.to_dict(row)
    badge["stock_id"] = int(badge["stock_id"])
    badge["dimensions"] = json.loads(badge["dimensions"]) if badge["dimensions"] else None
    return badge


def _select(where: str, params: list) -> pd.DataFrame:
    return lake_rows.select("risk_badges", ", ".join(BADGE_COLUMNS), where, params)


class RiskBadgeRepository:
    def __init__(self, conn: object | None = None):
        self._conn = conn

    def get_by_stock(self, stock_id: int) -> dict | None:
        df = _select("stock_id = ?", [int(stock_id)])
        return None if df.empty else _to_dict(df.iloc[0])

    def get_by_stocks(self, stock_ids: list[int]) -> dict[int, dict]:
        if not stock_ids:
            return {}
        ids = [int(stock_id) for stock_id in stock_ids]
        df = _select(f"stock_id IN ({', '.join('?' * len(ids))})", ids)
        badges = [_to_dict(df.iloc[index]) for index in range(len(df))]
        return {badge["stock_id"]: badge for badge in badges}

    def upsert_batch(self, rows: list[dict], run_id: str | None = None) -> int:
        if not rows:
            return 0
        payload = pd.DataFrame(
            [
                {
                    "stock_id": int(row["stock_id"]), "market": row["market"],
                    "date": row["date"], "summary_tier": row["summary_tier"],
                    "dimensions": json.dumps(row["dimensions"]),
                }
                for row in rows
            ]
        ).drop_duplicates(subset=["stock_id"], keep="last")
        payload["updated_at"] = lake_rows.now_utc()
        return lake_writer.snapshot_replace(
            "risk_badges", payload[BADGE_COLUMNS], lake_rows.resolve_run_id(run_id)
        )
