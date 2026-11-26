
from contextlib import nullcontext

import pytest

from local_agent.approval_manager import ApprovalManager
from local_agent.execution.models import ToolExecutionStatus
from local_agent.execution.recorder import (
    ExecutionRecorder,
    ExecutionRecorderError,
)
from local_agent.execution.tool_executor import ToolExecutor
from local_agent.execution.tools.git import GitSnapshotChangedError
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



class FakeGitTool:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.error: Exception | None = None
        self.snapshot_version = 1
        self.change_during_execute = False

    def mutation_lock(self):
        return nullcontext()

    def prepare_mutation(self, operation, parameters, previous_stage_receipt=None):
        return dict(parameters), {"version": self.snapshot_version}

    def capture_mutation_snapshot(self, operation, parameters, previous_stage_receipt=None):
        return dict(parameters), {"version": self.snapshot_version}

    def _validate_mutation_preconditions(self, *args):
        return None

    def _execute_approved_mutation(self, operation, parameters, snapshot, previous_stage_receipt=None):
        if self.change_during_execute:
            self.snapshot_version += 1
            raise GitSnapshotChangedError({"version": self.snapshot_version})
        result = self._call(operation, **parameters)
        if operation == "git_stage":
            result["_stage_receipt"] = {
                "repository_root": "fake",
                "head": "fake-head",
                "index_fingerprint": "fake-index",
                "staged_diff_fingerprint": "fake-diff",
                "staged_paths": parameters["paths"],
                "file_fingerprints": {},
            }
        return result

    def _call(self, method: str, **parameters: object) -> dict:
        self.calls.append((method, parameters))
        if self.error is not None:
            raise self.error
        return {"method": method, "parameters": parameters}

    def status(self) -> dict:
        return self._call("status")

    def branch(self) -> dict:
        return self._call("branch")

    def head(self) -> dict:
        return self._call("head")

    def branches(self) -> dict:
        return self._call("branches")

    def log(self, limit: int = 20) -> dict:
        return self._call("log", limit=limit)

    def diff(self, paths=()) -> dict:
        return self._call("diff", paths=paths)

    def staged_diff(self, paths=()) -> dict:
        return self._call("staged_diff", paths=paths)

    def changed_files(self) -> dict:
        return self._call("changed_files")


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
    tools["git"] = FakeGitTool()

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
        git_tool=tools["git"],
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



@pytest.mark.parametrize(
    ("action", "method", "action_type"),
    [
        ("git_status", "status", "GIT_STATUS"),
        ("git_branch", "branch", "GIT_BRANCH"),
        ("git_head", "head", "GIT_HEAD"),
        ("git_branches", "branches", "GIT_BRANCHES"),
        ("git_log", "log", "GIT_LOG"),
        ("git_diff", "diff", "GIT_DIFF"),
        ("git_staged_diff", "staged_diff", "GIT_STAGED_DIFF"),
        ("git_changed_files", "changed_files", "GIT_CHANGED_FILES"),
    ],
)
def test_git_actions_are_registered_and_use_injected_tool(
    executor_environment,
    action,
    method,
    action_type,
):
    executor, _, task_id, tools = executor_environment

    definition = executor._tools[action]
    result = executor.execute(task_id, action, {})

    assert definition.action_type == action_type
    assert definition.requires_approval is False
    assert result.status == ToolExecutionStatus.SUCCEEDED
    assert tools["git"].calls[0][0] == method


def test_git_actions_do_not_request_approval(executor_environment, monkeypatch):
    executor, approval_manager, task_id, _ = executor_environment

    def unexpected_approval(**kwargs):
        raise AssertionError("read-only Git action requested approval")

    monkeypatch.setattr(approval_manager, "request_approval", unexpected_approval)
    monkeypatch.setattr(approval_manager, "is_approved", unexpected_approval)

    for action in (
        "git_status",
        "git_branch",
        "git_head",
        "git_branches",
        "git_log",
        "git_diff",
        "git_staged_diff",
        "git_changed_files",
    ):
        result = executor.execute(task_id, action, {})
        assert result.status == ToolExecutionStatus.SUCCEEDED


@pytest.mark.parametrize(
    "action",
    (
        "git_status",
        "git_branch",
        "git_head",
        "git_branches",
        "git_changed_files",
    ),
)
def test_git_actions_without_parameters_reject_extra_values(
    executor_environment,
    action,
):
    executor, _, task_id, tools = executor_environment

    result = executor.execute(task_id, action, {"unexpected": True})

    assert result.status == ToolExecutionStatus.FAILED
    assert "Unexpected parameters" in result.error
    assert tools["git"].calls == []


@pytest.mark.parametrize(
    ("parameters", "expected_limit"),
    [({}, 20), ({"limit": 1}, 1), ({"limit": 100}, 100)],
)
def test_git_log_accepts_default_and_boundary_limits(
    executor_environment,
    parameters,
    expected_limit,
):
    executor, _, task_id, tools = executor_environment

    result = executor.execute(task_id, "git_log", parameters)

    assert result.status == ToolExecutionStatus.SUCCEEDED
    assert tools["git"].calls == [("log", {"limit": expected_limit})]


@pytest.mark.parametrize(
    "limit",
    [0, 101, -1, "20", 1.5, True, False],
)
def test_git_log_rejects_invalid_limits_before_tool_call(
    executor_environment,
    limit,
):
    executor, _, task_id, tools = executor_environment

    result = executor.execute(task_id, "git_log", {"limit": limit})

    assert result.status == ToolExecutionStatus.FAILED
    assert "limit" in result.error
    assert tools["git"].calls == []


@pytest.mark.parametrize(
    ("action", "method"),
    [("git_diff", "diff"), ("git_staged_diff", "staged_diff")],
)
def test_git_diff_actions_default_to_empty_path_list(
    executor_environment,
    action,
    method,
):
    executor, _, task_id, tools = executor_environment

    result = executor.execute(task_id, action, {})

    assert result.status == ToolExecutionStatus.SUCCEEDED
    assert tools["git"].calls == [(method, {"paths": []})]


@pytest.mark.parametrize(
    ("action", "method"),
    [("git_diff", "diff"), ("git_staged_diff", "staged_diff")],
)
def test_git_diff_actions_forward_paths_without_loss(
    executor_environment,
    action,
    method,
):
    executor, _, task_id, tools = executor_environment
    paths = ["src/a.py", "docs/a b.md"]

    result = executor.execute(task_id, action, {"paths": paths})

    assert result.status == ToolExecutionStatus.SUCCEEDED
    assert tools["git"].calls == [(method, {"paths": paths})]


@pytest.mark.parametrize(
    ("action", "parameters"),
    [
        ("git_log", {"unknown": 1}),
        ("git_diff", {"unknown": []}),
        ("git_staged_diff", {"unknown": []}),
        ("git_diff", {"paths": "file.py"}),
        ("git_staged_diff", {"paths": ("file.py",)}),
    ],
)
def test_git_actions_reject_unknown_parameters_and_invalid_path_container(
    executor_environment,
    action,
    parameters,
):
    executor, _, task_id, tools = executor_environment

    result = executor.execute(task_id, action, parameters)

    assert result.status == ToolExecutionStatus.FAILED
    assert tools["git"].calls == []



@pytest.mark.parametrize(
    ("action", "parameters"),
    [
        ("git_create_and_switch_branch", {"branch_name": "feature/test"}),
        ("git_stage", {"paths": ["file.txt"]}),
        ("git_commit", {"message": "Test commit", "paths": ["file.txt"]}),
    ],
)
def test_git_mutations_wait_for_approval_before_execution(
    executor_environment, action, parameters
):
    executor, approval_manager, task_id, tools = executor_environment
    git = tools["git"]

    pending = executor.execute(task_id, action, parameters)

    assert pending.status == ToolExecutionStatus.WAITING_FOR_APPROVAL
    assert git.calls == []

    approval_manager.approve(pending.approval_id)
    result = executor.execute(task_id, action, parameters)

    assert result.status == ToolExecutionStatus.SUCCEEDED
    assert len(git.calls) == 1


def test_git_mutation_snapshot_change_requires_new_approval(
    executor_environment,
):
    executor, approval_manager, task_id, tools = executor_environment
    git = tools["git"]
    parameters = {"paths": ["file.txt"]}

    pending = executor.execute(task_id, "git_stage", parameters)
    assert pending.status == ToolExecutionStatus.WAITING_FOR_APPROVAL
    approval_manager.approve(pending.approval_id)

    git.snapshot_version += 1
    resumed = executor.execute(task_id, "git_stage", parameters)

    assert resumed.status == ToolExecutionStatus.WAITING_FOR_APPROVAL
    assert resumed.approval_id != pending.approval_id
    assert git.calls == []



def test_git_snapshot_change_immediately_before_command_requests_reapproval(
    executor_environment,
):
    executor, approval_manager, task_id, tools = executor_environment
    git = tools["git"]
    parameters = {"paths": ["file.txt"]}

    pending = executor.execute(task_id, "git_stage", parameters)
    assert pending.status == ToolExecutionStatus.WAITING_FOR_APPROVAL
    approval_manager.approve(pending.approval_id)

    git.change_during_execute = True
    resumed = executor.execute(task_id, "git_stage", parameters)

    assert resumed.status == ToolExecutionStatus.WAITING_FOR_APPROVAL
    assert resumed.approval_id != pending.approval_id
    assert git.calls == []


def test_git_tool_error_is_recorded(executor_environment, execution_recorder):
    executor, _, task_id, tools = executor_environment
    tools["git"].error = RuntimeError("Git command failed")

    result = executor.execute(task_id, "git_status", {})

    assert result.status == ToolExecutionStatus.FAILED
    assert result.execution_id is not None
    assert execution_recorder.get_status(result.execution_id) == "FAILED"
    events = execution_recorder.get_events(result.execution_id)
    assert [event.event_type for event in events] == [
        "EXECUTION_STARTED",
        "EXECUTION_FAILED",
    ]



def test_git_parameter_validation_failure_is_recorded(
    executor_environment,
    execution_recorder,
):
    executor, _, task_id, tools = executor_environment

    result = executor.execute(task_id, "git_log", {"limit": True})

    assert result.status == ToolExecutionStatus.FAILED
    assert result.execution_id is not None
    assert tools["git"].calls == []
    assert execution_recorder.get_status(result.execution_id) == "FAILED"
    events = execution_recorder.get_events(result.execution_id)
    assert [event.event_type for event in events] == [
        "EXECUTION_STARTED",
        "EXECUTION_FAILED",
    ]
