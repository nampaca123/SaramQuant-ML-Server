from datetime import date, datetime

from app.api.errors import APIError
from app.schema.data_sources import Market


def parse_date(date_str: str | None) -> date | None:
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise APIError(f"Invalid date format: {date_str}. Use YYYY-MM-DD", 400)


def parse_market(market_param: str | None) -> Market | None:
    if not market_param:
        return None
    try:
        return Market(market_param)
    except ValueError:
        valid = ", ".join(m.value for m in Market)
        raise APIError(f"Invalid market: {market_param}. Valid: {valid}", 400)
