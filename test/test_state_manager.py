
import pytest

from local_agent.state_manager import StateManager, TaskStatus


def test_create_task():
    manager = StateManager()

    task_id = manager.create_task("Create calculator")

    assert manager.get_state(task_id) == TaskStatus.CREATED


def test_task_ids_are_unique():
    manager = StateManager()

    first = manager.create_task("Task 1")
    second = manager.create_task("Task 2")

    assert first != second


def test_valid_transition():
    manager = StateManager()
    task_id = manager.create_task("Create calculator")

    manager.transition(task_id, TaskStatus.RUNNING)

    assert manager.get_state(task_id) == TaskStatus.RUNNING


def test_waiting_for_approval_and_resume():
    manager = StateManager()
    task_id = manager.create_task("Modify project")

    manager.transition(task_id, TaskStatus.RUNNING)
    manager.transition(task_id, TaskStatus.WAITING_FOR_APPROVAL)
    manager.transition(task_id, TaskStatus.RUNNING)

    assert manager.get_state(task_id) == TaskStatus.RUNNING


def test_invalid_transition_is_rejected():
    manager = StateManager()
    task_id = manager.create_task("Create calculator")

    with pytest.raises(ValueError, match="Invalid transition"):
        manager.transition(task_id, TaskStatus.SUCCEEDED)


def test_terminal_state_cannot_be_changed():
    manager = StateManager()
    task_id = manager.create_task("Create calculator")

    manager.transition(task_id, TaskStatus.RUNNING)
    manager.transition(task_id, TaskStatus.SUCCEEDED)

    with pytest.raises(ValueError, match="Invalid transition"):
        manager.transition(task_id, TaskStatus.RUNNING)


def test_empty_description_is_rejected():
    manager = StateManager()

    with pytest.raises(ValueError):
        manager.create_task(" ")


def test_unknown_task_is_rejected():
    manager = StateManager()

    with pytest.raises(ValueError, match="Unknown task"):
        manager.get_state("unknown")
