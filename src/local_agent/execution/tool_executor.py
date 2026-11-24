from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from local_agent.approval_manager import ApprovalManager
from local_agent.execution.models import (
    ToolExecutionResult,
    ToolExecutionStatus,
)
from local_agent.execution.recorder import (
    ExecutionRecorder,
    ExecutionRecorderError,
)
from local_agent.execution.tools.create_file import CreateFileTool
from local_agent.execution.tools.get_diff import GetDiffTool
from local_agent.execution.tools.list_directory import ListDirectoryTool
from local_agent.execution.tools.read_file import ReadFileTool
from local_agent.execution.tools.run_command import RunCommandTool
from local_agent.execution.tools.run_tests import RunTestsTool
from local_agent.execution.tools.write_file import WriteFileTool
from local_agent.models import ApprovalStatus


@dataclass(frozen=True, slots=True)
class _ToolDefinition:
    action_type: str
    requires_approval: bool
    execute: Callable[..., Any]
    required_parameters: frozenset[str]
    optional_parameters: frozenset[str]


class ToolExecutor:
    """Единая точка запуска инструментов."""

    def __init__(
            self,
            *,
            approval_manager: ApprovalManager,
            list_directory: ListDirectoryTool,
            read_file: ReadFileTool,
            create_file: CreateFileTool,
            write_file: WriteFileTool,
            get_diff: GetDiffTool,
            run_command: RunCommandTool,
            run_tests: RunTestsTool,
            execution_recorder: ExecutionRecorder,
    ) -> None:
        self._approval_manager = approval_manager
        self._execution_recorder = execution_recorder
        self._tools = {
            "list_directory": _ToolDefinition(
                action_type="LIST_DIRECTORY",
                requires_approval=False,
                execute=list_directory.execute,
                required_parameters=frozenset(),
                optional_parameters=frozenset({
                    "path",
                    "max_depth",
                    "max_entries",
                }),
            ),
            "read_file": _ToolDefinition(
                action_type="READ_FILE",
                requires_approval=False,
                execute=read_file.execute,
                required_parameters=frozenset({"path"}),
                optional_parameters=frozenset(),
            ),
            "create_file": _ToolDefinition(
                action_type="CREATE_FILE",
                requires_approval=True,
                execute=create_file.execute,
                required_parameters=frozenset({"path", "content"}),
                optional_parameters=frozenset(),
            ),
            "write_file": _ToolDefinition(
                action_type="WRITE_FILE",
                requires_approval=True,
                execute=write_file.execute,
                required_parameters=frozenset({"path", "content"}),
                optional_parameters=frozenset(),
            ),
            "get_diff": _ToolDefinition(
                action_type="GET_DIFF",
                requires_approval=False,
                execute=get_diff.execute,
                required_parameters=frozenset({
                    "path",
                    "original_content",
                }),
                optional_parameters=frozenset(),
            ),
            "run_command": _ToolDefinition(
                action_type="RUN_COMMAND",
                requires_approval=True,
                execute=run_command.execute,
                required_parameters=frozenset({"command"}),
                optional_parameters=frozenset(),
            ),
            "run_tests": _ToolDefinition(
                action_type="RUN_TESTS",
                requires_approval=True,
                execute=run_tests.execute,
                required_parameters=frozenset(),
                optional_parameters=frozenset(),
            ),
        }

    def execute(
            self,
            task_id: str,
            tool_name: str,
            parameters: dict[str, Any],
    ) -> ToolExecutionResult:
        if not isinstance(task_id, str) or not task_id.strip():
            return self._failed("Task ID must be a non-empty string")

        if not isinstance(tool_name, str) or not tool_name:
            return self._failed("Tool name must be a non-empty string")

        definition = self._tools.get(tool_name)

        if definition is None:
            return self._failed(f"Unknown tool: {tool_name}")

        validation_error = self._validate_parameters(
            parameters,
            definition,
        )

        if validation_error is not None:
            return self._failed(validation_error)

        if definition.requires_approval:
            try:
                approval = self._approval_manager.request_approval(
                    task_id=task_id,
                    action_type=definition.action_type,
                    parameters=parameters,
                )
            except (ValueError, TypeError, RuntimeError, OSError) as error:
                return self._failed(
                    f"Approval request failed. "
                    f"The tool was not executed: {error}"
                )

            if approval.status == ApprovalStatus.PENDING:
                return ToolExecutionResult(
                    status=ToolExecutionStatus.WAITING_FOR_APPROVAL,
                    approval_id=approval.approval_id,
                )

            if approval.status != ApprovalStatus.APPROVED:
                return self._failed(
                    f"Action is not approved: {approval.status}"
                )

        try:
            execution_id = self._execution_recorder.start_execution(
                task_id
            )
        except ExecutionRecorderError as error:
            return self._failed(
                f"Could not start execution record. "
                f"The tool was not executed: {error}"
            )

        try:
            if definition.requires_approval:
                try:
                    is_approved = self._approval_manager.is_approved(
                        task_id=task_id,
                        action_type=definition.action_type,
                        parameters=parameters,
                    )
                except (
                    ValueError,
                    TypeError,
                    RuntimeError,
                    OSError,
                ) as error:
                    raise RuntimeError(
                        "Approval verification failed. "
                        f"The tool was not executed: {error}"
                    ) from error

                if not is_approved:
                    raise RuntimeError(
                        "Approval verification failed. "
                        "The tool was not executed."
                    )

            result = definition.execute(**parameters)
        except (
                ValueError,
                TypeError,
                OSError,
                RuntimeError,
                KeyError,
                AttributeError,
                LookupError,
                ArithmeticError,
        ) as error:
            try:
                self._execution_recorder.fail_execution(
                    execution_id,
                    str(error),
                )
            except ExecutionRecorderError as recorder_error:
                return ToolExecutionResult(
                    status=ToolExecutionStatus.FAILED,
                    execution_id=execution_id,
                    error=(
                        f"Tool execution failed: {error}. "
                        f"Additionally, execution failure could not "
                        f"be recorded: {recorder_error}"
                    ),
                )

            return ToolExecutionResult(
                status=ToolExecutionStatus.FAILED,
                execution_id=execution_id,
                error=str(error),
            )

        try:
            self._execution_recorder.finish_execution(
                execution_id,
                {"tool_name": tool_name},
            )
        except ExecutionRecorderError as error:
            return ToolExecutionResult(
                status=ToolExecutionStatus.FAILED,
                execution_id=execution_id,
                result=result,
                error=(
                    "Tool execution succeeded, but its completion "
                    f"could not be recorded: {error}"
                ),
            )

        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCEEDED,
            execution_id=execution_id,
            result=result,
        )

    @staticmethod
    def _validate_parameters(
        parameters: dict[str, Any],
        definition: _ToolDefinition,
    ) -> str | None:
        if not isinstance(parameters, dict):
            return "Tool parameters must be a dictionary"

        if not all(isinstance(key, str) for key in parameters):
            return "Parameter names must be strings"

        actual = set(parameters)
        missing = definition.required_parameters - actual
        unexpected = (
            actual
            - definition.required_parameters
            - definition.optional_parameters
        )

        if missing:
            return f"Missing parameters: {', '.join(sorted(missing))}"

        if unexpected:
            return f"Unexpected parameters: {', '.join(sorted(unexpected))}"

        return None

    @staticmethod
    def _failed(error: str) -> ToolExecutionResult:
        return ToolExecutionResult(
            status=ToolExecutionStatus.FAILED,
            error=error,
        )
