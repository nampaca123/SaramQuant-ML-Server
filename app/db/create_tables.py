"""13개 Iceberg 본 테이블 DDL 부트스트랩 — IF NOT EXISTS라 재실행해도 안전하다."""
import logging

from app.db.athena_runner import run_query
from app.db.lake_schemas import TABLES, build_create_ddl

logger = logging.getLogger(__name__)


def create_all_tables() -> None:
    for name in TABLES:
        run_query(build_create_ddl(name))
        logger.info("Iceberg table ensured: %s", name)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    create_all_tables()
