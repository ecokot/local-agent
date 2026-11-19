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


def test_transition_in_transaction_changes_status(tmp_path):
    with StateManager(tmp_path / "test.db") as manager:
        task_id = manager.create_task("Test task")
        manager.transition(task_id, TaskStatus.RUNNING)

        with manager.connection:
            manager.transition_in_transaction(
                task_id,
                TaskStatus.WAITING_FOR_APPROVAL,
            )

        assert manager.get_state(task_id) == TaskStatus.WAITING_FOR_APPROVAL


def test_transition_in_transaction_rejects_invalid_transition(tmp_path):
    with StateManager(tmp_path / "test.db") as manager:
        task_id = manager.create_task("Test task")

        with pytest.raises(ValueError):
            manager.transition_in_transaction(
                task_id,
                TaskStatus.SUCCEEDED,
            )


def test_close_does_not_close_external_connection():
    connection = sqlite3.connect(":memory:")

    manager = StateManager(connection=connection)
    manager.close()

    # Соединение должно оставаться доступным.
    connection.execute("SELECT 1")

    connection.close()


def test_close_closes_owned_connection(tmp_path):
    manager = StateManager(tmp_path / "test.db")
    connection = manager.connection

    manager.close()

    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")


def test_transition_rejects_unknown_task(tmp_path):
    with (
        StateManager(tmp_path / "agent.db") as manager,
        pytest.raises(ValueError, match="Unknown task"),
    ):
        manager.transition("unknown", TaskStatus.RUNNING)


@pytest.mark.parametrize(
    "terminal_status",
    [
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    ],
)
def test_all_terminal_states_are_immutable(tmp_path, terminal_status):
    with StateManager(tmp_path / "agent.db") as manager:
        task_id = manager.create_task("Test task")
        manager.transition(task_id, TaskStatus.RUNNING)
        manager.transition(task_id, terminal_status)

        with pytest.raises(ValueError, match="Invalid transition"):
            manager.transition(task_id, TaskStatus.RUNNING)


def test_transition_does_not_commit_existing_transaction(tmp_path):
    with StateManager(tmp_path / "agent.db") as manager:
        task_id = manager.create_task("Test task")

        manager.connection.execute("BEGIN")
        manager.transition_in_transaction(
            task_id,
            TaskStatus.RUNNING,
        )
        manager.connection.commit()

        manager.connection.execute("BEGIN")
        manager.transition_in_transaction(
            task_id,
            TaskStatus.WAITING_FOR_APPROVAL,
        )

        assert manager.connection.in_transaction

        manager.connection.rollback()

        assert manager.get_state(task_id) == TaskStatus.RUNNING


def test_transition_rolls_back_after_database_error(tmp_path):
    with StateManager(tmp_path / "agent.db") as manager:
        task_id = manager.create_task("Test task")

        manager.connection.execute(
            """
            CREATE TRIGGER reject_task_update
            BEFORE UPDATE ON tasks
            BEGIN
                SELECT RAISE(ABORT, 'Forced database error');
            END;
            """
        )
        manager.connection.commit()

        with pytest.raises(sqlite3.IntegrityError):
            manager.transition(task_id, TaskStatus.RUNNING)

        assert not manager.connection.in_transaction
        assert manager.get_state(task_id) == TaskStatus.CREATED


def test_transition_rejects_active_transaction(tmp_path):
    with StateManager(tmp_path / "agent.db") as manager:
        task_id = manager.create_task("Test task")

        manager.connection.execute("BEGIN")

        with pytest.raises(RuntimeError, match="transaction is active"):
            manager.transition(task_id, TaskStatus.RUNNING)

        assert manager.connection.in_transaction

        manager.connection.rollback()

        assert manager.get_state(task_id) == TaskStatus.CREATED
