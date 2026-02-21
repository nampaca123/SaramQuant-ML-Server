from dataclasses import dataclass, field
from typing import Optional
from uuid import UUID


@dataclass
class AuditLogEntry:
    server: str
    action: str
    method: Optional[str] = None
    path: Optional[str] = None
    status_code: Optional[int] = None
    duration_ms: Optional[int] = None
    metadata: Optional[dict] = None
