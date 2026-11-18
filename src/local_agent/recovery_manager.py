from typing import ClassVar

from local_agent.execution.recorder import ExecutionRecorder
from local_agent.state_manager import StateManager, TaskStatus


class RecoveryError(Exception):
    """Raised when recovery data is inconsistent."""


class RecoveryManager:
    TERMINAL_EVENTS: ClassVar[dict[str, str]] = {
        "SUCCEEDED": "EXECUTION_FINISHED",
        "FAILED": "EXECUTION_FAILED",
    }

    def __init__(
        self,
        state_manager: StateManager,
        execution_recorder: ExecutionRecorder,
    ) -> None:
        self.state_manager = state_manager
        self.execution_recorder = execution_recorder

    def recover(self) -> None:
        tasks = self.state_manager.get_tasks()

        for task in tasks:
            task_id = task["task_id"]

            if TaskStatus(task["status"]) != TaskStatus.RUNNING:
                continue

            executions = (
                self.execution_recorder.get_executions_for_task(task_id)
            )

            if not executions:
                continue

            latest_execution = executions[-1]
            execution_id = latest_execution["execution_id"]
            execution_status = latest_execution["status"]

            events = self.execution_recorder.get_events(execution_id)

            terminal_events = [
                event.event_type
                for event in events
                if event.event_type in (
                    "EXECUTION_FINISHED",
                    "EXECUTION_FAILED",
                )
            ]

            if execution_status == "RUNNING":
                if terminal_events:
                    raise RecoveryError(
                        f"Execution {execution_id} is RUNNING "
                        "but has a terminal event"
                    )
                continue

            expected_event = self.TERMINAL_EVENTS.get(execution_status)

            if expected_event is None:
                raise RecoveryError(
                    f"Unknown execution status: {execution_status}"
                )

            if (
                len(terminal_events) != 1
                or not events
                or events[-1].event_type != expected_event
            ):
                raise RecoveryError(
                    f"Inconsistent history for execution {execution_id}"
                )

            new_status = TaskStatus(execution_status)

            try:
                self.state_manager.transition(task_id, new_status)
            except ValueError as exc:
                raise RecoveryError(
                    f"Could not recover task {task_id}: {exc}"
                ) from exc
