from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4


class ExecutionEventType(StrEnum):
    EXECUTION_STARTED = "EXECUTION_STARTED"
    EXECUTION_FINISHED = "EXECUTION_FINISHED"
    EXECUTION_FAILED = "EXECUTION_FAILED"


@dataclass(frozen=True, slots=True)
class ExecutionEvent:
    execution_id: str
    event_type: str
    data: dict[str, Any]
    event_id: str
    timestamp: str

    @classmethod
    def create(
            cls,
            execution_id: str,
            event_type: str | ExecutionEventType,
            data: dict[str, Any] | None = None,
    ) -> ExecutionEvent:
        if not execution_id.strip():
            raise ValueError("Execution ID cannot be empty")

        event_type = str(event_type)

        if not event_type.strip():
            raise ValueError("Event type cannot be empty")

        return cls(
            execution_id=execution_id,
            event_type=event_type,
            data=deepcopy(data) if data is not None else {},
            event_id=str(uuid4()),
            timestamp=datetime.now(UTC).isoformat(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "execution_id": self.execution_id,
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "data": self.data,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ExecutionEvent:
        return cls(
            event_id=value["event_id"],
            execution_id=value["execution_id"],
            event_type=value["event_type"],
            timestamp=value["timestamp"],
            data=value["data"],
        )