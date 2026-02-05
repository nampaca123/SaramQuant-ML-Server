import logging
import sys
from app.utils import setup_logging
from tests.collectors.cli import main

setup_logging(level=logging.INFO, log_file="logs/app.log")

if __name__ == "__main__":
    sys.exit(main())
