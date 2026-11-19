from typing import ClassVar

from local_agent.approval_manager import ApprovalManager
from local_agent.execution.models import ExecutionEventType
from local_agent.execution.recorder import ExecutionRecorder
from local_agent.models import ExecutionStatus
from local_agent.state_manager import StateManager, TaskStatus


class RecoveryError(Exception):
    """Raised when recovery data is inconsistent."""


class RecoveryManager:
    TERMINAL_EVENTS: ClassVar[dict[ExecutionStatus, str]] = {
        ExecutionStatus.SUCCEEDED: ExecutionEventType.EXECUTION_FINISHED,
        ExecutionStatus.FAILED: ExecutionEventType.EXECUTION_FAILED,
    }

    def __init__(
        self,
        state_manager: StateManager,
        execution_recorder: ExecutionRecorder,
        approval_manager: ApprovalManager,
    ) -> None:
        self.state_manager = state_manager
        self.execution_recorder = execution_recorder
        self.approval_manager = approval_manager

    def _validate_execution(self, execution, is_latest: bool) -> None:
        execution_id = execution.execution_id
        status = execution.status

        events = self.execution_recorder.get_events(execution_id)

        allowed_events = {
            ExecutionEventType.EXECUTION_STARTED,
            ExecutionEventType.EXECUTION_FINISHED,
            ExecutionEventType.EXECUTION_FAILED,
        }

        if any(event.event_type not in allowed_events for event in events):
            raise RecoveryError(
                f"Unknown event type in execution {execution_id}"
            )

        if not events:
            raise RecoveryError(
                f"Execution {execution_id} has no events"
            )

        start_events = [
            event
            for event in events
            if event.event_type == ExecutionEventType.EXECUTION_STARTED
        ]

        terminal_events = [
            event.event_type
            for event in events
            if event.event_type in (
                ExecutionEventType.EXECUTION_FINISHED,
                ExecutionEventType.EXECUTION_FAILED,
            )
        ]

        if (
            len(start_events) != 1
            or events[0].event_type != ExecutionEventType.EXECUTION_STARTED
        ):
            raise RecoveryError(
                f"Invalid start event history for execution {execution_id}"
            )

        if status == ExecutionStatus.RUNNING:
            if terminal_events or not is_latest:
                raise RecoveryError(
                    f"Inconsistent RUNNING execution {execution_id}"
                )
            return

        expected_event = self.TERMINAL_EVENTS.get(status)

        if expected_event is None:
            raise RecoveryError(
                f"Unknown execution status: {status}"
            )

        if (
            len(terminal_events) != 1
            or events[-1].event_type != expected_event
        ):
            raise RecoveryError(
                f"Inconsistent history for execution {execution_id}"
            )

        if not is_latest and status != ExecutionStatus.FAILED:
            raise RecoveryError(
                f"Execution {execution_id} is not FAILED "
                "but a subsequent execution exists"
            )

    def recover(self) -> None:
        tasks = self.state_manager.get_tasks()

        for task in tasks:
            task_id = task.task_id

            if task.status == TaskStatus.WAITING_FOR_APPROVAL:
                pending_approvals = (
                    self.approval_manager.get_pending_approvals(task_id)
                )

                if not pending_approvals:
                    raise RecoveryError(
                        f"Task {task_id} is WAITING_FOR_APPROVAL "
                        "but has no pending approvals"
                    )

                continue

            if task.status != TaskStatus.RUNNING:
                continue

            pending_approvals = (
                self.approval_manager.get_pending_approvals(task_id)
            )

            if pending_approvals:
                raise RecoveryError(
                    f"Task {task_id} is RUNNING but has pending approvals"
                )

            executions = (
                self.execution_recorder.get_executions_for_task(task_id)
            )

            # Execution может отсутствовать, пока Orchestrator
            # ещё не интегрирован с ExecutionRecorder.
            if not executions:
                continue

            for index, execution in enumerate(executions):
                self._validate_execution(
                    execution,
                    is_latest=index == len(executions) - 1,
                )

            latest_execution = executions[-1]

            # Неудачная попытка не означает провал всей задачи.
            if latest_execution.status != ExecutionStatus.SUCCEEDED:
                continue

            try:
                self.state_manager.transition(
                    task_id,
                    TaskStatus.SUCCEEDED,
                )
            except ValueError as exc:
                raise RecoveryError(
                    f"Could not recover task {task_id}: {exc}"
                ) from exc
