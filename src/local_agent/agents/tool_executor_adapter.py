from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from local_agent.execution.models import ToolExecutionStatus
from local_agent.execution.tool_executor import ToolExecutor
from local_agent.models.agent import ExecutionResult
from local_agent.models.plan import Plan


@dataclass
class _TaskProgress:
    plan: Plan
    next_step: int = 0
    step_results: list[dict[str, Any]] = field(default_factory=list)


class ToolExecutorAdapter:
    """Executes an approved plan sequentially through ToolExecutor.

    Progress is kept in memory only. It allows continuation after an action
    approval during the current process, but deliberately does not implement
    recovery after a process restart.
    """

    def __init__(self, tool_executor: ToolExecutor) -> None:
        self._tool_executor = tool_executor
        self._progress: dict[str, _TaskProgress] = {}

    def execute(self, task_id: str, plan: Plan) -> ExecutionResult:
        if not isinstance(task_id, str) or not task_id.strip():
            return ExecutionResult(
                success=False,
                errors=["Task ID must be a non-empty string"],
            )

        progress = self._progress.get(task_id)

        if progress is None:
            progress = _TaskProgress(plan=plan.model_copy(deep=True))
            self._progress[task_id] = progress
        elif progress.plan != plan:
            self._progress.pop(task_id, None)
            return ExecutionResult(
                success=False,
                errors=[
                    (
                        "The plan changed while execution was waiting. "
                        "A new task is required."
                    )
                ],
                step_results=deepcopy(progress.step_results),
            )

        while progress.next_step < len(progress.plan.steps):
            step_index = progress.next_step
            step = progress.plan.steps[step_index]

            try:
                tool_result = self._tool_executor.execute(
                    task_id,
                    step.action,
                    step.parameters,
                )
            except Exception:
                self._progress.pop(task_id, None)
                raise

            if tool_result.status == ToolExecutionStatus.WAITING_FOR_APPROVAL:
                return ExecutionResult(
                    success=False,
                    waiting_for_approval=True,
                    pending_approval_id=tool_result.approval_id,
                    output=(
                        f"Waiting for approval of step {step_index + 1}: "
                        f"{step.description}"
                    ),
                    step_results=deepcopy(progress.step_results),
                )

            if tool_result.status == ToolExecutionStatus.FAILED:
                self._progress.pop(task_id, None)
                return ExecutionResult(
                    success=False,
                    errors=[
                        tool_result.error or (
                            f"Tool failed at step {step_index + 1}: "
                            f"{step.action}"
                        )
                    ],
                    output=f"Execution failed at step {step_index + 1}",
                    step_results=deepcopy(progress.step_results),
                )

            progress.step_results.append(
                {
                    "step_index": step_index,
                    "description": step.description,
                    "action": step.action,
                    "parameters": deepcopy(step.parameters),
                    "execution_id": tool_result.execution_id,
                    "result": deepcopy(tool_result.result),
                }
            )
            progress.next_step += 1

        completed_results = deepcopy(progress.step_results)
        self._progress.pop(task_id, None)

        return ExecutionResult(
            success=True,
            output=f"Successfully executed {len(completed_results)} steps",
            step_results=completed_results,
        )
