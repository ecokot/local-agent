import json
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from local_agent.execution.models import ToolExecutionStatus
from local_agent.execution.tool_executor import ToolExecutor
from local_agent.models.agent import ExecutionResult
from local_agent.models.plan import Plan, PlanStep


@dataclass
class _CompletedStep:
    signature: tuple[str, str]
    result: dict[str, Any]


@dataclass
class _TaskProgress:
    plan: Plan
    next_step: int = 0
    step_results: list[dict[str, Any]] = field(default_factory=list)
    completed_steps: list[_CompletedStep] = field(default_factory=list)
    reused_step_indexes: set[int] = field(default_factory=set)


class ToolExecutorAdapter:
    """Executes approved plans and reuses completed identical steps per task.

    Progress and completed-step reuse are in-memory only. They survive plan
    revisions in the current process, but are not restored after a restart.
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
            if progress.next_step < len(progress.plan.steps):
                return ExecutionResult(
                    success=False,
                    errors=[
                        "The plan changed while execution was waiting. "
                        "A new plan cannot replace a suspended execution."
                    ],
                    step_results=deepcopy(progress.step_results),
                )
            progress.plan = plan.model_copy(deep=True)
            progress.next_step = 0
            progress.step_results = []
            progress.reused_step_indexes.clear()

        while progress.next_step < len(progress.plan.steps):
            step_index = progress.next_step
            step = progress.plan.steps[step_index]
            signature = self._step_signature(step)
            completed = self._find_completed_step(
                progress,
                signature,
                step_index,
            )

            if completed is not None:
                progress.step_results.append(
                    self._result_for_step(step, step_index, completed)
                )
                progress.next_step += 1
                continue

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

            result = {
                "step_index": step_index,
                "description": step.description,
                "action": step.action,
                "parameters": deepcopy(step.parameters),
                "execution_id": tool_result.execution_id,
                "result": deepcopy(tool_result.result),
            }
            progress.completed_steps.append(
                _CompletedStep(signature=signature, result=deepcopy(result))
            )
            progress.reused_step_indexes.add(
                len(progress.completed_steps) - 1
            )
            progress.step_results.append(result)
            progress.next_step += 1

        completed_results = deepcopy(progress.step_results)
        return ExecutionResult(
            success=True,
            output=f"Successfully executed {len(completed_results)} steps",
            step_results=completed_results,
        )

    @staticmethod
    def _step_signature(step: PlanStep) -> tuple[str, str]:
        return (
            step.action,
            json.dumps(
                step.parameters,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )

    @staticmethod
    def _find_completed_step(
        progress: _TaskProgress,
        signature: tuple[str, str],
        step_index: int,
    ) -> dict[str, Any] | None:
        for completed_index, completed in enumerate(progress.completed_steps):
            if (
                completed.signature == signature
                and completed_index not in progress.reused_step_indexes
            ):
                progress.reused_step_indexes.add(completed_index)
                reused = deepcopy(completed.result)
                reused["step_index"] = step_index
                return reused
        return None

    @staticmethod
    def _result_for_step(
        step: PlanStep,
        step_index: int,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        reused = deepcopy(result)
        reused["step_index"] = step_index
        reused["description"] = step.description
        reused["action"] = step.action
        reused["parameters"] = deepcopy(step.parameters)
        return reused
