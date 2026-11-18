from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ExecutionStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class Execution:
    execution_id: str
    task_id: str
    status: ExecutionStatus
    started_at: str
    finished_at: str | None
