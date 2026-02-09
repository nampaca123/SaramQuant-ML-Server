import argparse
import logging
import sys
from app.schema import Market
from app.db import close_pool
from tests.collectors.run_stock_list import run_stock_list
from tests.collectors.run_daily_price import run_daily_price

logger = logging.getLogger(__name__)


def parse_market(value: str) -> Market | None:
    if not value:
        return None
    try:
        return Market(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"Invalid market: {value}. "
            f"Choose from: {', '.join(m.value for m in Market)}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="SaramQuant Data Collector CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    stocks_parser = subparsers.add_parser("collect-stocks", help="Collect stock list from KIS")
    stocks_parser.add_argument("--market", type=parse_market, default=None)

    prices_parser = subparsers.add_parser(
        "collect-prices", help="Collect daily prices (pykrx for KR, Alpaca for US)"
    )
    prices_parser.add_argument("--market", type=parse_market, default=None)

    subparsers.add_parser("collect-all", help="Collect stocks and prices")

    args = parser.parse_args()
    exit_code = 0

    try:
        if args.command == "collect-stocks":
            results = run_stock_list(market=args.market)
            total = sum(results.values())
            logger.info(f"Stock collection complete: {total} stocks")
            if total == 0:
                exit_code = 1

        elif args.command == "collect-prices":
            results = run_daily_price(market=args.market)
            total = sum(results.values())
            logger.info(f"Price collection complete: {total} records")
            if total == 0:
                logger.warning("No price records collected")

        elif args.command == "collect-all":
            stock_results = run_stock_list(market=None)
            stock_total = sum(stock_results.values())
            if stock_total == 0:
                exit_code = 1
            else:
                price_results = run_daily_price(market=None)
                price_total = sum(price_results.values())
                logger.info(
                    f"Collection complete: {stock_total} stocks, {price_total} price records"
                )

    except Exception as e:
        logger.error(f"Command failed: {e}")
        exit_code = 1
    finally:
        close_pool()

    return exit_code
