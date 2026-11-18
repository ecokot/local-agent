from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class Approval:
    approval_id: str
    task_id: str
    action_type: str
    parameters: dict[str, Any]
    parameters_hash: str
    status: ApprovalStatus
    created_at: datetime
    decided_at: datetime | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "parameters", deepcopy(self.parameters))