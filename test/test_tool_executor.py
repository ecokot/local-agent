
import pytest

from local_agent.approval_manager import ApprovalManager
from local_agent.execution.models import ToolExecutionStatus
from local_agent.execution.recorder import (
    ExecutionRecorder,
    ExecutionRecorderError,
)
from local_agent.execution.tool_executor import ToolExecutor
from local_agent.state_manager import StateManager


class FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[dict] = []
        self.error: Exception | None = None

    def execute(self, **parameters: object) -> dict:
        self.calls.append(parameters)

        if self.error is not None:
            raise self.error

        return {
            "tool": self.name,
            "parameters": parameters,
        }


@pytest.fixture
def state_manager(tmp_path):
    manager = StateManager(tmp_path / "state.db")
    yield manager
    manager.close()


@pytest.fixture
def execution_recorder(tmp_path, state_manager):
    recorder = ExecutionRecorder(
        database_path=tmp_path / "state.db",
        trace_directory=tmp_path / "traces",
    )
    yield recorder
    recorder.close()


@pytest.fixture
def executor_environment(state_manager, execution_recorder):
    approval_manager = ApprovalManager(
        connection=state_manager.connection,
        state_manager=state_manager,
    )

    task_id = state_manager.create_task("Test ToolExecutor")

    tools = {
        name: FakeTool(name)
        for name in (
            "list_directory",
            "read_file",
            "create_file",
            "write_file",
            "get_diff",
            "run_command",
            "run_tests",
        )
    }

    executor = ToolExecutor(
        approval_manager=approval_manager,
        execution_recorder=execution_recorder,
        list_directory=tools["list_directory"],
        read_file=tools["read_file"],
        create_file=tools["create_file"],
        write_file=tools["write_file"],
        get_diff=tools["get_diff"],
        run_command=tools["run_command"],
        run_tests=tools["run_tests"],
    )

    return executor, approval_manager, task_id, tools


def test_unknown_tool_is_rejected(executor_environment):
    executor, _, task_id, tools = executor_environment

    result = executor.execute(task_id, "unknown_tool", {})

    assert result.status == ToolExecutionStatus.FAILED
    assert "Unknown tool" in result.error
    assert all(not tool.calls for tool in tools.values())


def test_missing_parameters_are_rejected(executor_environment):
    executor, _, task_id, tools = executor_environment

    result = executor.execute(
        task_id, "create_file", {"path": "example.txt"}
    )

    assert result.status == ToolExecutionStatus.FAILED
    assert "Missing parameters" in result.error
    assert not tools["create_file"].calls


def test_unexpected_parameters_are_rejected(executor_environment):
    executor, _, task_id, tools = executor_environment

    result = executor.execute(
        task_id,
        "read_file",
        {"path": "example.txt", "unexpected": True},
    )

    assert result.status == ToolExecutionStatus.FAILED
    assert "Unexpected parameters" in result.error
    assert not tools["read_file"].calls


def test_safe_tool_executes_without_approval(executor_environment):
    executor, _, task_id, tools = executor_environment

    result = executor.execute(task_id, "list_directory", {})

    assert result.status == ToolExecutionStatus.SUCCEEDED
    assert result.result["tool"] == "list_directory"
    assert len(tools["list_directory"].calls) == 1


def test_risky_tool_waits_for_approval(executor_environment):
    executor, _, task_id, tools = executor_environment

    result = executor.execute(
        task_id,
        "create_file",
        {"path": "example.txt", "content": "Hello"},
    )

    assert result.status == ToolExecutionStatus.WAITING_FOR_APPROVAL
    assert result.approval_id is not None
    assert not tools["create_file"].calls


def test_approved_tool_executes(executor_environment):
    executor, approval_manager, task_id, tools = executor_environment

    parameters = {"path": "example.txt", "content": "Hello"}

    pending = executor.execute(task_id, "create_file", parameters)

    assert pending.status == ToolExecutionStatus.WAITING_FOR_APPROVAL
    assert not tools["create_file"].calls

    approval_manager.approve(pending.approval_id)

    result = executor.execute(task_id, "create_file", parameters)

    assert result.status == ToolExecutionStatus.SUCCEEDED
    assert len(tools["create_file"].calls) == 1


def test_changed_parameters_require_new_approval(executor_environment):
    executor, approval_manager, task_id, tools = executor_environment

    first_parameters = {"path": "example.txt", "content": "First"}

    pending = executor.execute(
        task_id, "create_file", first_parameters
    )
    approval_manager.approve(pending.approval_id)

    changed_parameters = {
        "path": "example.txt",
        "content": "Second",
    }

    result = executor.execute(
        task_id, "create_file", changed_parameters
    )

    assert result.status == ToolExecutionStatus.WAITING_FOR_APPROVAL
    assert result.approval_id != pending.approval_id
    assert not tools["create_file"].calls


def test_cancelled_action_cannot_execute(executor_environment):
    executor, approval_manager, task_id, tools = executor_environment

    parameters = {"path": "example.txt", "content": "Hello"}

    pending = executor.execute(task_id, "create_file", parameters)
    approval_manager.cancel(pending.approval_id)

    result = executor.execute(task_id, "create_file", parameters)

    assert result.status == ToolExecutionStatus.FAILED
    assert "cancelled" in result.error.lower()
    assert not tools["create_file"].calls


def test_run_tests_requires_approval(executor_environment):
    executor, _, task_id, tools = executor_environment

    result = executor.execute(task_id, "run_tests", {})

    assert result.status == ToolExecutionStatus.WAITING_FOR_APPROVAL
    assert not tools["run_tests"].calls


def test_successful_execution_is_recorded(
    executor_environment,
    execution_recorder,
):
    executor, _, task_id, _ = executor_environment

    result = executor.execute(task_id, "list_directory", {})

    assert result.status == ToolExecutionStatus.SUCCEEDED
    assert result.execution_id is not None
    assert execution_recorder.get_status(result.execution_id) == "SUCCEEDED"

    events = execution_recorder.get_events(result.execution_id)
    assert [event.event_type for event in events] == [
        "EXECUTION_STARTED",
        "EXECUTION_FINISHED",
    ]


def test_failed_tool_execution_is_recorded(
    executor_environment,
    execution_recorder,
):
    executor, _, task_id, tools = executor_environment
    tools["read_file"].error = RuntimeError("Test tool failure")

    result = executor.execute(
        task_id,
        "read_file",
        {"path": "example.txt"},
    )

    assert result.status == ToolExecutionStatus.FAILED
    assert result.execution_id is not None
    assert execution_recorder.get_status(result.execution_id) == "FAILED"

    events = execution_recorder.get_events(result.execution_id)
    assert [event.event_type for event in events] == [
        "EXECUTION_STARTED",
        "EXECUTION_FAILED",
    ]


def test_waiting_for_approval_is_not_recorded(
    executor_environment,
    execution_recorder,
):
    executor, _, task_id, tools = executor_environment

    result = executor.execute(
        task_id,
        "create_file",
        {"path": "example.txt", "content": "Hello"},
    )

    assert result.status == ToolExecutionStatus.WAITING_FOR_APPROVAL
    assert execution_recorder.get_executions_for_task(task_id) == []
    assert not tools["create_file"].calls


def test_recorder_start_failure_prevents_tool_execution(
    executor_environment,
    execution_recorder,
    monkeypatch,
):
    executor, _, task_id, tools = executor_environment

    def fail_start(task_id: str) -> str:
        raise ExecutionRecorderError("Database unavailable")

    monkeypatch.setattr(
        execution_recorder,
        "start_execution",
        fail_start,
    )

    result = executor.execute(
        task_id,
        "list_directory",
        {},
    )

    assert result.status == ToolExecutionStatus.FAILED
    assert "Database unavailable" in result.error
    assert result.execution_id is None
    assert not tools["list_directory"].calls



def test_finish_recording_failure_preserves_tool_result(
    executor_environment,
    execution_recorder,
    monkeypatch,
):
    executor, _, task_id, tools = executor_environment

    def fail_finish(
        execution_id: str,
        result: dict | None = None,
    ) -> None:
        raise ExecutionRecorderError("Database unavailable")

    monkeypatch.setattr(
        execution_recorder,
        "finish_execution",
        fail_finish,
    )

    result = executor.execute(
        task_id,
        "list_directory",
        {},
    )

    # Сам инструмент действительно выполнился.
    assert len(tools["list_directory"].calls) == 1

    # Но запись о завершении сохранить не удалось.
    assert result.status == ToolExecutionStatus.FAILED
    assert result.execution_id is not None
    assert result.result is not None
    assert "completion could not be recorded" in result.error

    # Recorder сохранил исполнение как незавершённое.
    assert execution_recorder.get_status(result.execution_id) == "RUNNING"
    assert result.execution_id in execution_recorder.get_incomplete_executions()



def test_failure_recording_error_preserves_both_errors(
    executor_environment,
    execution_recorder,
    monkeypatch,
):
    executor, _, task_id, tools = executor_environment
    tools["read_file"].error = RuntimeError("Original tool failure")

    def fail_recording(
        execution_id: str,
        error: str,
    ) -> None:
        raise ExecutionRecorderError("Database unavailable")

    monkeypatch.setattr(
        execution_recorder,
        "fail_execution",
        fail_recording,
    )

    result = executor.execute(
        task_id,
        "read_file",
        {"path": "example.txt"},
    )

    assert len(tools["read_file"].calls) == 1
    assert result.status == ToolExecutionStatus.FAILED
    assert result.execution_id is not None
    assert "Original tool failure" in result.error
    assert "Database unavailable" in result.error

    assert execution_recorder.get_status(result.execution_id) == "RUNNING"
    assert result.execution_id in execution_recorder.get_incomplete_executions()



def test_is_approved_false_prevents_execution(
    executor_environment,
    execution_recorder,
    monkeypatch,
):
    executor, approval_manager, task_id, tools = executor_environment
    parameters = {"path": "example.txt", "content": "Hello"}

    pending = executor.execute(task_id, "create_file", parameters)
    approval_manager.approve(pending.approval_id)

    monkeypatch.setattr(
        approval_manager,
        "is_approved",
        lambda **kwargs: False,
    )

    result = executor.execute(task_id, "create_file", parameters)

    assert result.status == ToolExecutionStatus.FAILED
    assert "Approval verification failed" in result.error
    assert not tools["create_file"].calls
    executions = execution_recorder.get_executions_for_task(task_id)

    assert len(executions) == 1
    assert execution_recorder.get_status(executions[0].execution_id) == "FAILED"


def test_is_approved_exception_prevents_execution(
    executor_environment,
    execution_recorder,
    monkeypatch,
):
    executor, approval_manager, task_id, tools = executor_environment
    parameters = {"path": "example.txt", "content": "Hello"}

    pending = executor.execute(task_id, "create_file", parameters)
    approval_manager.approve(pending.approval_id)

    def fail_verification(**kwargs):
        raise RuntimeError("Approval database unavailable")

    monkeypatch.setattr(
        approval_manager,
        "is_approved",
        fail_verification,
    )

    result = executor.execute(task_id, "create_file", parameters)

    assert result.status == ToolExecutionStatus.FAILED
    assert "Approval database unavailable" in result.error
    assert not tools["create_file"].calls
    executions = execution_recorder.get_executions_for_task(task_id)

    assert len(executions) == 1
    assert execution_recorder.get_status(executions[0].execution_id) == "FAILED"
