from dataclasses import dataclass, field
from typing import Optional


@dataclass
class StepResult:
    """input_count/output_count는 런 레코드 counts에 실리는 실제 입력·출력 행 수다."""
    name: str
    success: bool
    duration_ms: int
    error: Optional[str] = None
    input_count: Optional[int] = None
    output_count: Optional[int] = None


@dataclass
class PipelineMetadata:
    """aborted는 오케스트레이터가 중간에 파이프라인을 끊었는지를 뜻한다(error/partial 구분 기준)."""
    command: str
    steps: list[StepResult] = field(default_factory=list)
    total_duration_ms: int = 0
    aborted: bool = False
    run_id: Optional[str] = None
