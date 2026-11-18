import sqlite3

import pytest

from local_agent.state_manager import StateManager, TaskStatus


def test_database_is_created(tmp_path):
    db_path = tmp_path / "agent.db"

    StateManager(db_path)

    assert db_path.exists()


def test_task_persists_after_reopening_database(tmp_path):
    db_path = tmp_path / "agent.db"

    manager = StateManager(db_path)
    task_id = manager.create_task("Create calculator")
    manager.connection.close()

    manager = StateManager(db_path)

    assert manager.get_state(task_id) == TaskStatus.CREATED

    manager.close()


def test_task_status_persists_after_reopening_database(tmp_path):
    db_path = tmp_path / "agent.db"

    manager = StateManager(db_path)
    task_id = manager.create_task("Create calculator")

    manager.transition(task_id, TaskStatus.RUNNING)
    manager.connection.close()

    manager = StateManager(db_path)

    assert manager.get_state(task_id) == TaskStatus.RUNNING

    manager.close()


def test_create_task(tmp_path):
    manager = StateManager(tmp_path / "agent.db")

    task_id = manager.create_task("Create calculator")

    assert manager.get_state(task_id) == TaskStatus.CREATED


def test_task_ids_are_unique(tmp_path):
    manager = StateManager(tmp_path / "agent.db")

    first = manager.create_task("Task 1")
    second = manager.create_task("Task 2")

    assert first != second


def test_valid_transition(tmp_path):
    manager = StateManager(tmp_path / "agent.db")
    task_id = manager.create_task("Create calculator")

    manager.transition(task_id, TaskStatus.RUNNING)

    assert manager.get_state(task_id) == TaskStatus.RUNNING


def test_waiting_for_approval_and_resume(tmp_path):
    manager = StateManager(tmp_path / "agent.db")
    task_id = manager.create_task("Modify project")

    manager.transition(task_id, TaskStatus.RUNNING)
    manager.transition(task_id, TaskStatus.WAITING_FOR_APPROVAL)
    manager.transition(task_id, TaskStatus.RUNNING)

    assert manager.get_state(task_id) == TaskStatus.RUNNING


def test_invalid_transition_is_rejected(tmp_path):
    manager = StateManager(tmp_path / "agent.db")
    task_id = manager.create_task("Create calculator")

    with pytest.raises(ValueError, match="Invalid transition"):
        manager.transition(task_id, TaskStatus.SUCCEEDED)


def test_terminal_state_cannot_be_changed(tmp_path):
    manager = StateManager(tmp_path / "agent.db")
    task_id = manager.create_task("Create calculator")

    manager.transition(task_id, TaskStatus.RUNNING)
    manager.transition(task_id, TaskStatus.SUCCEEDED)

    with pytest.raises(ValueError, match="Invalid transition"):
        manager.transition(task_id, TaskStatus.RUNNING)


def test_empty_description_is_rejected(tmp_path):
    manager = StateManager(tmp_path / "agent.db")

    with pytest.raises(ValueError):
        manager.create_task(" ")


def test_unknown_task_is_rejected(tmp_path):
    manager = StateManager(tmp_path / "agent.db")

    with pytest.raises(ValueError, match="Unknown task"):
        manager.get_state("unknown")

def test_context_manager_closes_connection(tmp_path):
    db_path = tmp_path / "agent.db"

    with StateManager(db_path) as manager:
        task_id = manager.create_task("Test task")
        assert manager.get_state(task_id) == TaskStatus.CREATED

    with pytest.raises(sqlite3.ProgrammingError):
        manager.get_state(task_id)


def test_context_manager_closes_connection_after_exception(tmp_path):
    db_path = tmp_path / "agent.db"

    with StateManager(db_path) as manager:
        connection = manager.connection

        with pytest.raises(RuntimeError):
            raise RuntimeError("Test exception")

    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")