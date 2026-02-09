import argparse
import logging
import sys
from app.utils import setup_logging
from app.schema import Market
from app.collectors import KrDailyPriceCollector, UsDailyPriceCollector
from app.db import close_pool

logger = logging.getLogger(__name__)

KR_MARKETS = {Market.KR_KOSPI, Market.KR_KOSDAQ}
US_MARKETS = {Market.US_NYSE, Market.US_NASDAQ}


def run_daily_price(market: Market | None = None) -> dict[str, int]:
    results: dict[str, int] = {}

    if market is None or market in KR_MARKETS:
        logger.info("Collecting KR daily prices via pykrx")
        kr = KrDailyPriceCollector()
        results.update(kr.collect_all(market=market))

    if market is None or market in US_MARKETS:
        logger.info("Collecting US daily prices via Alpaca")
        us = UsDailyPriceCollector()
        results.update(us.collect_all(market=market))

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run daily price collector")
    parser.add_argument(
        "--market",
        type=lambda x: Market(x) if x else None,
        default=None,
        help=f"Market to collect. Options: {', '.join(m.value for m in Market)}",
    )
    args = parser.parse_args()

    setup_logging(level=logging.INFO, log_file="logs/app.log")

    try:
        results = run_daily_price(market=args.market)
        total = sum(results.values())
        logger.info(f"Result: {total} price records for {len(results)} stocks")
        if total == 0:
            logger.warning("No price records collected")
    except Exception as e:
        logger.error(f"Failed to collect prices: {e}")
        sys.exit(1)
    finally:
        close_pool()
