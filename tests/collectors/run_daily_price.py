import argparse
import logging
import sys
from app.utils import setup_logging
from app.schema.data_sources import Market
from app.collectors import DailyPriceCollector
from app.db import close_pool

logger = logging.getLogger(__name__)


def run_daily_price(market: Market | None = None) -> dict[str, int]:
    collector = DailyPriceCollector()

    if market:
        logger.info(f"Collecting daily prices for {market.value}")
    else:
        logger.info("Collecting daily prices for all markets")

    return collector.collect_all(market=market)


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
