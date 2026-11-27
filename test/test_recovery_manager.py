import sqlite3

import pytest

from local_agent.agents.stubs import StubExecutor, StubPlanner, StubReviewer
from local_agent.execution.tool_executor import ToolExecutor
from local_agent.execution.tools.run_command import RunCommandTool
from local_agent.execution.tools.run_tests import RunTestsTool

from local_agent.approval_manager import ApprovalManager
from local_agent.execution.recorder import ExecutionRecorder
from local_agent.models import ApprovalStatus
from local_agent.recovery_manager import RecoveryError, RecoveryManager
from local_agent.state_manager import StateManager, TaskStatus


@pytest.fixture
def components(tmp_path):
    database_path = tmp_path / "agent.db"
    trace_directory = tmp_path / "traces"

    connection = sqlite3.connect(database_path)

    state_manager = StateManager(connection=connection)

    approval_manager = ApprovalManager(
        connection=connection,
        state_manager=state_manager,
    )

    execution_recorder = ExecutionRecorder(
        database_path,
        trace_directory,
    )

    recovery_manager = RecoveryManager(
        state_manager,
        execution_recorder,
        approval_manager,
    )

    yield state_manager, execution_recorder, recovery_manager

    execution_recorder.close()
    approval_manager.close()
    state_manager.close()
    connection.close()

@pytest.fixture
def running_task(components):
    state_manager, _, _ = components

    task_id = state_manager.create_task("Test recovery")
    state_manager.transition(task_id, TaskStatus.RUNNING)

    return task_id

def test_recovers_successful_execution(
    components,
    running_task,
):
    state_manager, recorder, recovery = components

    execution_id = recorder.start_execution(running_task)
    recorder.finish_execution(execution_id)

    recovery.recover()

    assert state_manager.get_state(running_task) == TaskStatus.SUCCEEDED


def test_failed_execution_does_not_fail_task(
    components,
    running_task,
):
    state_manager, recorder, recovery = components

    execution_id = recorder.start_execution(running_task)
    recorder.fail_execution(execution_id, "Test error")

    recovery.recover()

    assert state_manager.get_state(running_task) == TaskStatus.RUNNING


def test_running_execution_is_not_recovered(
    components,
    running_task,
):
    state_manager, recorder, recovery = components

    recorder.start_execution(running_task)

    recovery.recover()

    assert state_manager.get_state(running_task) == TaskStatus.RUNNING


def test_only_latest_execution_is_considered(
    components,
    running_task,
):
    state_manager, recorder, recovery = components

    first_execution = recorder.start_execution(running_task)
    recorder.fail_execution(first_execution, "First attempt failed")

    second_execution = recorder.start_execution(running_task)

    recovery.recover()

    assert state_manager.get_state(running_task) == TaskStatus.RUNNING
    assert recorder.get_status(second_execution) == "RUNNING"


def test_recovery_detects_inconsistent_execution(
    components,
    running_task,
):
    state_manager, recorder, recovery = components

    execution_id = recorder.start_execution(running_task)
    recorder.finish_execution(execution_id)

    recorder._connection.execute(
        """
        UPDATE execution_events
        SET event_type = 'INVALID_EVENT'
        WHERE execution_id = ?
          AND event_type = 'EXECUTION_FINISHED'
        """,
        (execution_id,),
    )
    recorder._connection.commit()

    with pytest.raises(RecoveryError):
        recovery.recover()

    assert state_manager.get_state(running_task) == TaskStatus.RUNNING


def test_recovery_is_idempotent(
    components,
    running_task,
):
    state_manager, recorder, recovery = components

    execution_id = recorder.start_execution(running_task)
    recorder.finish_execution(execution_id)

    recovery.recover()
    assert state_manager.get_state(running_task) == TaskStatus.SUCCEEDED

    recovery.recover()
    assert state_manager.get_state(running_task) == TaskStatus.SUCCEEDED

def test_running_task_without_executions_remains_running(components):
    state_manager, _, recovery_manager = components

    task_id = state_manager.create_task("Task without execution")
    state_manager.transition(task_id, TaskStatus.RUNNING)

    recovery_manager.recover()

    assert state_manager.get_state(task_id) == TaskStatus.RUNNING

def test_waiting_task_with_pending_approval_remains_waiting(components):
    state_manager, _, recovery_manager = components

    task_id = state_manager.create_task("Task awaiting approval")
    state_manager.transition(task_id, TaskStatus.RUNNING)

    recovery_manager.approval_manager.request_approval(
        task_id,
        "write_file",
        {"path": "example.py"},
    )

    recovery_manager.recover()

    assert state_manager.get_state(task_id) == TaskStatus.WAITING_FOR_APPROVAL

def test_waiting_task_without_pending_approval_raises_error(components):
    state_manager, _, recovery_manager = components

    task_id = state_manager.create_task("Task without approval")
    state_manager.transition(task_id, TaskStatus.RUNNING)
    state_manager.transition(task_id, TaskStatus.WAITING_FOR_APPROVAL)

    with pytest.raises(RecoveryError, match="has no pending approvals"):
        recovery_manager.recover()

def test_running_task_with_pending_approval_raises_error(components):
    state_manager, _, recovery_manager = components

    task_id = state_manager.create_task("Inconsistent task")
    state_manager.transition(task_id, TaskStatus.RUNNING)

    recovery_manager.approval_manager.request_approval(
        task_id,
        "write_file",
        {"path": "example.py"},
    )

    state_manager.connection.execute(
        "UPDATE tasks SET status = ? WHERE task_id = ?",
        (TaskStatus.RUNNING.value, task_id),
    )
    state_manager.connection.commit()

    with pytest.raises(RecoveryError, match="has pending approvals"):
        recovery_manager.recover()

def test_pending_approval_survives_restart(tmp_path):
    database_path = tmp_path / "agent.db"
    trace_directory = tmp_path / "traces"

    # Первый запуск
    connection = sqlite3.connect(database_path)
    state_manager = StateManager(connection=connection)

    approval_manager = ApprovalManager(
        connection=connection,
        state_manager=state_manager,
    )
    execution_recorder = ExecutionRecorder(
        database_path,
        trace_directory,
    )

    task_id = state_manager.create_task("Persistent approval")
    state_manager.transition(task_id, TaskStatus.RUNNING)

    approval = approval_manager.request_approval(
        task_id,
        "write_file",
        {"path": "example.py"},
    )

    assert state_manager.get_state(task_id) == TaskStatus.WAITING_FOR_APPROVAL

    # Имитируем завершение приложения
    execution_recorder.close()
    approval_manager.close()
    state_manager.close()
    connection.close()

    # Второй запуск — новые соединения и экземпляры менеджеров
    connection = sqlite3.connect(database_path)
    state_manager = StateManager(connection=connection)

    approval_manager = ApprovalManager(
        connection=connection,
        state_manager=state_manager,
    )
    execution_recorder = ExecutionRecorder(
        database_path,
        trace_directory,
    )

    recovery_manager = RecoveryManager(
        state_manager,
        execution_recorder,
        approval_manager,
    )

    try:
        pending = approval_manager.get_pending_approvals(task_id)

        assert len(pending) == 1
        assert pending[0].approval_id == approval.approval_id
        assert pending[0].status == ApprovalStatus.PENDING
        assert (
            state_manager.get_state(task_id)
            == TaskStatus.WAITING_FOR_APPROVAL
        )

        recovery_manager.recover()

        assert (
            state_manager.get_state(task_id)
            == TaskStatus.WAITING_FOR_APPROVAL
        )
    finally:
        execution_recorder.close()
        approval_manager.close()
        state_manager.close()
        connection.close()


def test_latest_failed_execution_does_not_fail_task(
    components,
    running_task,
):
    state_manager, recorder, recovery = components

    execution_id = recorder.start_execution(running_task)
    recorder.fail_execution(execution_id, "Test error")

    recovery.recover()

    assert state_manager.get_state(running_task) == TaskStatus.RUNNING


def test_previous_running_execution_is_inconsistent(
    components,
    running_task,
):
    _, recorder, recovery = components

    recorder.start_execution(running_task)
    recorder.start_execution(running_task)

    with pytest.raises(RecoveryError):
        recovery.recover()


def test_failed_execution_followed_by_successful_execution(
    components,
    running_task,
):
    state_manager, recorder, recovery = components

    first = recorder.start_execution(running_task)
    recorder.fail_execution(first, "First attempt failed")

    second = recorder.start_execution(running_task)
    recorder.finish_execution(second)

    recovery.recover()

    assert state_manager.get_state(running_task) == TaskStatus.SUCCEEDED


def test_recovery_detects_unknown_event_type(
    components,
    running_task,
):
    _, recorder, recovery = components

    execution_id = recorder.start_execution(running_task)

    recorder._connection.execute(
        """
        UPDATE execution_events
        SET event_type = 'UNKNOWN_EVENT'
        WHERE execution_id = ?
          AND event_type = 'EXECUTION_STARTED'
        """,
        (execution_id,),
    )
    recorder._connection.commit()

    with pytest.raises(RecoveryError, match="Unknown event type"):
        recovery.recover()


def test_previous_successful_execution_is_inconsistent(
    components,
    running_task,
):
    _, recorder, recovery = components

    first = recorder.start_execution(running_task)
    recorder.finish_execution(first)

    recorder.start_execution(running_task)

    with pytest.raises(RecoveryError):
        recovery.recover()



def test_recover_returns_classification_for_multiple_tasks(components):
    state_manager, recorder, recovery = components

    created_task = state_manager.create_task("Created without approval")

    running_without_execution = state_manager.create_task(
        "Running without execution"
    )
    state_manager.transition(running_without_execution, TaskStatus.RUNNING)

    running_failed = state_manager.create_task("Running after failed attempt")
    state_manager.transition(running_failed, TaskStatus.RUNNING)
    failed_execution = recorder.start_execution(running_failed)
    recorder.fail_execution(failed_execution, "Attempt failed")

    running_incomplete = state_manager.create_task(
        "Running with incomplete execution"
    )
    state_manager.transition(running_incomplete, TaskStatus.RUNNING)
    recorder.start_execution(running_incomplete)

    waiting_task = state_manager.create_task("Waiting for two approvals")
    state_manager.transition(waiting_task, TaskStatus.RUNNING)
    first_approval = recovery.approval_manager.request_approval(
        waiting_task,
        "write_file",
        {"path": "first.txt"},
    )
    second_approval = recovery.approval_manager.request_approval(
        waiting_task,
        "write_file",
        {"path": "second.txt"},
    )

    successful_task = state_manager.create_task("Successfully completed")
    state_manager.transition(successful_task, TaskStatus.RUNNING)
    successful_execution = recorder.start_execution(successful_task)
    recorder.finish_execution(successful_execution)

    result = recovery.recover()

    assert result.recovered_task_ids == (successful_task,)
    assert result.waiting_for_approval_task_ids == (waiting_task,)
    assert {
        item.task_id: (item.status, item.reason)
        for item in result.unresolved_tasks
    } == {
        created_task: (
            TaskStatus.CREATED,
            ("Task is still CREATED without a pending approval; its "
            "execution outcome cannot be inferred."),
        ),
        running_without_execution: (
            TaskStatus.RUNNING,
            ("Task is RUNNING but has no recorded executions; "
            "its outcome cannot be inferred."),
        ),
        running_failed: (
            TaskStatus.RUNNING,
            ("The latest execution is FAILED; a failed attempt does "
            "not prove that the task itself is terminal."),
        ),
        running_incomplete: (
            TaskStatus.RUNNING,
            ("The latest execution is still RUNNING; its external "
            "effect may be incomplete or uncertain."),
        ),
    }
    assert len(
        recovery.approval_manager.get_pending_approvals(waiting_task)
    ) == 2
    assert {
        first_approval.approval_id,
        second_approval.approval_id,
    } == {
        approval.approval_id
        for approval in recovery.approval_manager.get_pending_approvals(
            waiting_task
        )
    }


def test_recover_reports_pending_approvals_on_non_waiting_task_as_error(
    components,
):
    state_manager, _, recovery = components

    task_id = state_manager.create_task("Inconsistent pending approval")
    state_manager.transition(task_id, TaskStatus.RUNNING)
    approval = recovery.approval_manager.request_approval(
        task_id,
        "write_file",
        {"path": "example.txt"},
    )

    state_manager.connection.execute(
        "UPDATE tasks SET status = ? WHERE task_id = ?",
        (TaskStatus.CREATED.value, task_id),
    )
    state_manager.connection.commit()

    with pytest.raises(
        RecoveryError,
        match=f"Task {task_id} has pending approvals while its status is CREATED",
    ):
        recovery.recover()

    assert recovery.approval_manager.get_approval(
        approval.approval_id
    ).status == ApprovalStatus.PENDING


def test_recovery_error_for_invalid_execution_includes_task_id(
    components,
    running_task,
):
    _, recorder, recovery = components

    execution_id = recorder.start_execution(running_task)
    recorder.finish_execution(execution_id)
    recorder._connection.execute(
        """
        UPDATE execution_events
        SET event_type = 'INVALID_EVENT'
        WHERE execution_id = ?
          AND event_type = 'EXECUTION_FINISHED'
        """,
        (execution_id,),
    )
    recorder._connection.commit()

    with pytest.raises(
        RecoveryError,
        match=f"Task {running_task}: Unknown event type",
    ):
        recovery.recover()


def test_recover_second_call_does_not_report_recovered_task_again(
    components,
    running_task,
):
    state_manager, recorder, recovery = components

    execution_id = recorder.start_execution(running_task)
    recorder.finish_execution(execution_id)

    executions_before = recorder.get_executions_for_task(running_task)
    events_before = recorder.get_events(execution_id)

    first_result = recovery.recover()
    second_result = recovery.recover()

    assert first_result.recovered_task_ids == (running_task,)
    assert second_result.recovered_task_ids == ()
    assert state_manager.get_state(running_task) == TaskStatus.SUCCEEDED
    assert recorder.get_executions_for_task(running_task) == executions_before
    assert recorder.get_events(execution_id) == events_before



def test_recovery_does_not_change_terminal_tasks(components):
    state_manager, _, recovery = components

    terminal_statuses = {}
    for status in (
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    ):
        task_id = state_manager.create_task(
            f"Already terminal: {status.value}"
        )
        state_manager.transition(task_id, TaskStatus.RUNNING)
        state_manager.transition(task_id, status)
        terminal_statuses[task_id] = status

    result = recovery.recover()

    assert result.recovered_task_ids == ()
    assert {
        task_id: state_manager.get_state(task_id)
        for task_id in terminal_statuses
    } == terminal_statuses



def test_recovery_does_not_invoke_agents_or_tools(
    monkeypatch,
    components,
    running_task,
):
    _, recorder, recovery = components

    def fail_if_called(*args, **kwargs):
        pytest.fail("Recovery must not invoke agents or execution tools")

    monkeypatch.setattr(StubPlanner, "create_plan", fail_if_called)
    monkeypatch.setattr(StubPlanner, "revise_plan", fail_if_called)
    monkeypatch.setattr(StubExecutor, "execute", fail_if_called)
    monkeypatch.setattr(StubReviewer, "review", fail_if_called)
    monkeypatch.setattr(ToolExecutor, "execute", fail_if_called)
    monkeypatch.setattr(RunCommandTool, "execute", fail_if_called)
    monkeypatch.setattr(RunTestsTool, "execute", fail_if_called)

    execution_id = recorder.start_execution(running_task)
    recorder.finish_execution(execution_id)

    result = recovery.recover()

    assert result.recovered_task_ids == (running_task,)
