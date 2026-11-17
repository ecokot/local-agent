from dataclasses import FrozenInstanceError

import pytest

from local_agent.execution.models import ExecutionEvent


def test_create_event():
    event = ExecutionEvent.create(
        execution_id="execution-001",
        event_type="TASK_STARTED",
        data={"task": "Create calculator"},
    )

    assert event.execution_id == "execution-001"
    assert event.event_type == "TASK_STARTED"
    assert event.data == {"task": "Create calculator"}
    assert event.event_id
    assert event.timestamp


def test_event_ids_are_unique():
    first = ExecutionEvent.create("execution-001", "STARTED")
    second = ExecutionEvent.create("execution-001", "STARTED")

    assert first.event_id != second.event_id


def test_empty_execution_id_is_rejected():
    with pytest.raises(ValueError):
        ExecutionEvent.create("", "STARTED")


def test_empty_event_type_is_rejected():
    with pytest.raises(ValueError):
        ExecutionEvent.create("execution-001", "")


def test_event_is_immutable():
    event = ExecutionEvent.create("execution-001", "STARTED")

    with pytest.raises(FrozenInstanceError):
        event.event_type = "FAILED"
