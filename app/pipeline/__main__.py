import logging
import sys

from app.utils import setup_logging
from app.db import close_pool
from app.pipeline.orchestrator import PipelineOrchestrator

COMMANDS = {"kr", "us", "all", "kr-fs", "us-fs", "full"}

logger = logging.getLogger(__name__)


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(f"Usage: python -m app.pipeline <{'|'.join(sorted(COMMANDS))}>")
        return 1

    setup_logging(level=logging.INFO, log_file="logs/pipeline.log")
    command = sys.argv[1]
    pipeline = PipelineOrchestrator()

    try:
        match command:
            case "kr":
                pipeline.run_daily_kr()
            case "us":
                pipeline.run_daily_us()
            case "all":
                pipeline.run_daily_all()
            case "kr-fs":
                pipeline.run_collect_fs_kr()
            case "us-fs":
                pipeline.run_collect_fs_us()
            case "full":
                pipeline.run_full()
    except Exception as e:
        logger.error(f"[Pipeline] Failed: {e}", exc_info=True)
        return 1
    finally:
        close_pool()

    return 0


if __name__ == "__main__":
    sys.exit(main())
