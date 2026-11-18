import pytest

from local_agent.execution.recorder import ExecutionRecorder
from local_agent.recovery_manager import RecoveryError, RecoveryManager
from local_agent.state_manager import StateManager, TaskStatus


@pytest.fixture
def components(tmp_path):
    database_path = tmp_path / "agent.db"
    trace_directory = tmp_path / "traces"

    state_manager = StateManager(database_path)
    execution_recorder = ExecutionRecorder(
        database_path,
        trace_directory,
    )

    recovery_manager = RecoveryManager(
        state_manager,
        execution_recorder
    )

    yield state_manager, execution_recorder, recovery_manager

    execution_recorder.close()
    state_manager.close()

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
