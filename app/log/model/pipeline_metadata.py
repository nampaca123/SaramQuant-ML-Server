from dataclasses import dataclass, field
from typing import Optional


@dataclass
class StepResult:
    name: str
    success: bool
    duration_ms: int
    error: Optional[str] = None


@dataclass
class PipelineMetadata:
    command: str
    steps: list[StepResult] = field(default_factory=list)
    total_duration_ms: int = 0
    stocks_processed: int = 0
    coverage: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "command": self.command,
            "steps": [
                {
                    "name": s.name,
                    "status": "success" if s.success else "failed",
                    "duration_ms": s.duration_ms,
                    **({"error": s.error} if s.error else {}),
                }
                for s in self.steps
            ],
            "total_duration_ms": self.total_duration_ms,
            "stocks_processed": self.stocks_processed,
            "coverage": self.coverage,
        }
