import sqlite3

import pytest

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


def test_recovers_failed_execution(
    components,
    running_task,
):
    state_manager, recorder, recovery = components

    execution_id = recorder.start_execution(running_task)
    recorder.fail_execution(execution_id, "Test error")

    recovery.recover()

    assert state_manager.get_state(running_task) == TaskStatus.FAILED


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
    recorder.finish_execution(first_execution)

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
