import argparse
import logging
import sys
from app.utils import setup_logging
from app.schema import Market
from app.collectors import StockListCollector
from app.db import close_pool

logger = logging.getLogger(__name__)


def run_stock_list(market: Market | None = None) -> dict[Market, int]:
    collector = StockListCollector()

    if market:
        logger.info(f"Collecting stocks for {market.value}")
        count = collector.collect_market(market)
        return {market: count}

    logger.info("Collecting stocks for all markets")
    return collector.collect_all()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run stock list collector")
    parser.add_argument(
        "--market",
        type=lambda x: Market(x) if x else None,
        default=None,
        help=f"Market to collect. Options: {', '.join(m.value for m in Market)}",
    )
    args = parser.parse_args()

    setup_logging(level=logging.INFO, log_file="logs/app.log")

    try:
        results = run_stock_list(market=args.market)
        total = sum(results.values())
        if total == 0:
            logger.error("No stocks collected")
            sys.exit(1)
        for m, count in results.items():
            logger.info(f"Result: {m.value}: {count} stocks")
    except Exception as e:
        logger.error(f"Failed to collect stocks: {e}")
        sys.exit(1)
    finally:
        close_pool()
