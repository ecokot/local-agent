from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4


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
            event_type: str,
            data: dict[str, Any] | None = None,
    ) -> "ExecutionEvent":
        if not execution_id.strip():
            raise ValueError("Execution ID cannot be empty")
        if not event_type.strip():
            raise ValueError("Event type cannot be empty")

        return cls(
            execution_id=execution_id,
            event_type=event_type,
            data=data.copy() if data is not None else {},
            event_id=str(uuid4()),
            timestamp=datetime.now(UTC).isoformat(),
        )