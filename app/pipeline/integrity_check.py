from app.schema import Market
from app.services.integrity_check_service import IntegrityCheckService


class IntegrityCheckEngine:
    def __init__(self, conn: object):
        self._service = IntegrityCheckService(conn)

    def run(self, markets: list[Market]) -> None:
        self._service.check(markets)
