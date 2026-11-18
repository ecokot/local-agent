from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from local_agent.state_manager import TaskStatus


@dataclass(frozen=True, slots=True)
class Task:
    task_id: str
    description: str
    status: TaskStatus