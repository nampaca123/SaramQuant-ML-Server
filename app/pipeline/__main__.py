import logging
import sys

from app.utils import setup_logging
from app.pipeline.orchestrator import PipelineOrchestrator

COMMANDS = {"kr", "us", "kr-fs", "us-fs", "kr-initial", "us-initial"}

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
            case "kr-fs":
                pipeline.run_collect_fs_kr()
            case "us-fs":
                pipeline.run_collect_fs_us()
            case "kr-initial":
                pipeline.run_initial_kr()
            case "us-initial":
                pipeline.run_initial_us()
    except Exception as e:
        logger.error(f"[Pipeline] Failed: {e}", exc_info=True)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
