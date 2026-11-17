from enum import StrEnum
from typing import ClassVar
from uuid import uuid4


class TaskStatus(StrEnum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class StateManager:
    ALLOWED_TRANSITIONS: ClassVar[dict[TaskStatus, set[TaskStatus]]] = {
        TaskStatus.CREATED: {
            TaskStatus.RUNNING,
            TaskStatus.CANCELLED,
        },
        TaskStatus.RUNNING: {
            TaskStatus.WAITING_FOR_APPROVAL,
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        },
        TaskStatus.WAITING_FOR_APPROVAL: {
            TaskStatus.RUNNING,
            TaskStatus.CANCELLED
        },
    }

    def __init__(self) -> None:
        self.tasks: dict[str, dict] = {}

    def create_task(self, description: str) -> str:
        if not description.strip():
            raise ValueError("Task description cannot be empty")

        task_id = str(uuid4())

        self.tasks[task_id] = {
            "description": description,
            "status": TaskStatus.CREATED,
        }
        return task_id

    def get_state(self, task_id: str) -> TaskStatus:
        if task_id not in self.tasks:
            raise ValueError(f"Unknown task: {task_id}")

        return self.tasks[task_id]["status"]

    def transition(
            self,
            task_id: str,
            new_status: TaskStatus,
    ) -> None:
        current_status = self.get_state(task_id)

        allowed = self.ALLOWED_TRANSITIONS.get(current_status, set())

        if new_status not in allowed:
            raise ValueError(
                f"Invalid transition: {current_status} -> {new_status}"
            )

        self.tasks[task_id]["status"] = new_status
