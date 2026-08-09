import os

import pytest

# 실데이터 레이크를 전체/광역 DELETE하는 통합 테스트 전용 가드 — 환경변수로만 열린다
skip_destructive = pytest.mark.skipif(
    os.environ.get("LAKE_DESTRUCTIVE_TESTS") != "1",
    reason="destructive against production lake; set LAKE_DESTRUCTIVE_TESTS=1",
)
