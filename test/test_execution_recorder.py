import json
import sqlite3

import pytest

from local_agent.execution import (
    ExecutionEvent,
    ExecutionRecorder,
    ExecutionRecorderError,
)


class FailingConnection(sqlite3.Connection):
    def execute(self, sql, parameters=()):
        if "INSERT INTO execution_events" in sql:
            raise sqlite3.OperationalError(
                "Simulated database failure"
            )

        return super().execute(sql, parameters)

@pytest.fixture
def recorder(tmp_path):
    instance = ExecutionRecorder(
        tmp_path / "agent.db",
        tmp_path / "traces",
    )
    yield instance
    instance.close()


def test_create_event():
    event = ExecutionEvent.create(
        "execution-001",
        "TASK_STARTED",
        {"task": "Create calculator"},
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

    with pytest.raises(AttributeError):
        event.event_type = "FAILED"


def test_start_execution(recorder):
    execution_id = recorder.start_execution()

    assert recorder.get_status(execution_id) == "RUNNING"


def test_record_event(recorder):
    execution_id = recorder.start_execution()

    event = recorder.record_event(
        execution_id,
        "FILE_CREATED",
        {"path": "main.py"},
    )

    events = recorder.get_events(execution_id)

    assert events[-1].event_id == event.event_id
    assert events[-1].event_type == "FILE_CREATED"


def test_finish_execution(recorder):
    execution_id = recorder.start_execution()

    recorder.finish_execution(
        execution_id,
        {"result": "success"},
    )

    assert recorder.get_status(execution_id) == "SUCCEEDED"


def test_fail_execution(recorder):
    execution_id = recorder.start_execution()

    recorder.fail_execution(execution_id, "Test error")

    assert recorder.get_status(execution_id) == "FAILED"


def test_cannot_finish_execution_twice(recorder):
    execution_id = recorder.start_execution()

    recorder.finish_execution(execution_id)

    with pytest.raises(ExecutionRecorderError):
        recorder.finish_execution(execution_id)


def test_incomplete_execution_is_detected(tmp_path):
    database = tmp_path / "agent.db"
    traces = tmp_path / "traces"

    recorder = ExecutionRecorder(database, traces)
    execution_id = recorder.start_execution()
    recorder.close()

    recorder = ExecutionRecorder(database, traces)

    assert execution_id in recorder.get_incomplete_executions()

    recorder.close()


def test_trace_file_contains_events(recorder, tmp_path):
    execution_id = recorder.start_execution()

    recorder.record_event(
        execution_id,
        "TEST_EVENT",
        {"value": 123},
    )

    trace_path = tmp_path / "traces" / f"{execution_id}.jsonl"
    lines = trace_path.read_text(encoding="utf-8").splitlines()

    assert len(lines) == 2
    assert json.loads(lines[1])["event_type"] == "TEST_EVENT"


def test_secrets_are_redacted(recorder):
    execution_id = recorder.start_execution()

    recorder.record_event(
        execution_id,
        "API_CALL",
        {
            "api_key": "my-secret-key",
            "endpoint": "/users",
            "nested": {
                "password": "12345",
            },
        },
    )

    event = recorder.get_events(execution_id)[-1]

    assert event.data["api_key"] == "***REDACTED***"
    assert event.data["nested"]["password"] == "***REDACTED***"
    assert event.data["endpoint"] == "/users"


def test_unknown_execution_is_rejected(recorder):
    with pytest.raises(ExecutionRecorderError):
        recorder.get_status("00000000-0000-0000-0000-000000000000")

def test_trace_is_recovered_after_write_failure(tmp_path, monkeypatch):
    database = tmp_path / "agent.db"
    traces = tmp_path / "traces"

    recorder = ExecutionRecorder(database, traces)
    execution_id = recorder.start_execution()

    original_open = type(recorder._trace_path(execution_id)).open
    failed = False

    def failing_open(path, mode="r", *args, **kwargs):
        nonlocal failed

        if mode == "a" and not failed:
            failed = True
            raise OSError("Simulated write failure")

        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(
        type(recorder._trace_path(execution_id)),
        "open",
        failing_open,
    )

    with pytest.raises(ExecutionRecorderError):
        recorder.record_event(execution_id, "TEST_EVENT")

    recorder.close()

    monkeypatch.undo()

    recovered = ExecutionRecorder(database, traces)

    events = recovered.get_events(execution_id)
    trace_path = traces / f"{execution_id}.jsonl"
    trace_events = [
        json.loads(line)
        for line in trace_path.read_text(encoding="utf-8").splitlines()
    ]

    event_id = events[-1].event_id

    assert sum(e["event_id"] == event_id for e in trace_events) == 1

    recovered.close()


def test_recovery_does_not_duplicate_trace_events(recorder, tmp_path):
    execution_id = recorder.start_execution()

    event = recorder.record_event(execution_id, "TEST_EVENT")

    with recorder._connection:
        recorder._connection.execute(
            """
            UPDATE execution_events
            SET trace_written = 0
            WHERE event_id = ?
            """,
            (event.event_id,),
        )

    recorder._write_pending_traces()

    trace_path = tmp_path / "traces" / f"{execution_id}.jsonl"
    trace_events = [
        json.loads(line)
        for line in trace_path.read_text(encoding="utf-8").splitlines()
    ]

    assert sum(
        item["event_id"] == event.event_id
        for item in trace_events
    ) == 1


def test_start_execution_is_atomic(tmp_path):
    database = tmp_path / "agent.db"
    traces = tmp_path / "traces"

    recorder = ExecutionRecorder(database, traces)
    recorder.close()

    recorder._connection = sqlite3.connect(
        database,
        factory=FailingConnection,
        check_same_thread=False,
    )
    recorder._connection.row_factory = sqlite3.Row

    with pytest.raises(ExecutionRecorderError):
        recorder.start_execution()

    executions = recorder._connection.execute(
        "SELECT COUNT(*) FROM executions"
    ).fetchone()[0]

    events = recorder._connection.execute(
        "SELECT COUNT(*) FROM execution_events"
    ).fetchone()[0]

    assert executions == 0
    assert events == 0

    recorder.close()

def test_record_event_rejects_unknown_execution(recorder):
    with pytest.raises(ExecutionRecorderError):
        recorder.record_event(
            "00000000-0000-0000-0000-000000000000",
            "TEST_EVENT",
        )

def test_recovery_repairs_incomplete_last_trace_line(
    tmp_path,
    monkeypatch,
):
    database = tmp_path / "agent.db"
    traces = tmp_path / "traces"

    recorder = ExecutionRecorder(database, traces)
    execution_id = recorder.start_execution()

    trace_path = traces / f"{execution_id}.jsonl"

    # Имитируем сбой записи трассы после сохранения события в SQLite.
    def simulate_crash():
        raise OSError("Simulated crash")

    monkeypatch.setattr(
        recorder,
        "_write_pending_traces",
        simulate_crash,
    )

    with pytest.raises(OSError, match="Simulated crash"):
        recorder.record_event(execution_id, "TEST_EVENT")

    # Проверяем, что событие сохранилось в SQLite,
    # но ещё не записано в JSONL.
    pending = recorder._connection.execute(
        """
        SELECT trace_written
        FROM execution_events
        WHERE event_type = 'TEST_EVENT'
        """
    ).fetchone()

    assert pending["trace_written"] == 0

    # Имитируем обрыв записи последней строки JSONL.
    with trace_path.open("a", encoding="utf-8") as file:
        file.write('{"event_id": "incomplete')

    recorder._connection.close()

    # Перезапуск агента должен восстановить трассу.
    recovered = ExecutionRecorder(database, traces)

    events = recovered.get_events(execution_id)
    trace_events = [
        json.loads(line)
        for line in trace_path.read_text(encoding="utf-8").splitlines()
    ]

    assert len(events) == 2
    assert len(trace_events) == 2
    assert {event["event_id"] for event in trace_events} == {
        event.event_id for event in events
    }

    recovered.close()

def test_recovery_rejects_corrupted_middle_trace_line(tmp_path):
    database = tmp_path / "agent.db"
    traces = tmp_path / "traces"

    recorder = ExecutionRecorder(database, traces)
    execution_id = recorder.start_execution()

    trace_path = traces / f"{execution_id}.jsonl"

    recorder.record_event(execution_id, "EVENT_1")
    recorder.record_event(execution_id, "EVENT_2")
    recorder.close()

    # Помечаем последнее событие как ожидающее записи в JSONL.
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            UPDATE execution_events
            SET trace_written = 0
            WHERE event_type = 'EVENT_2'
            """
        )

    # Повреждаем вторую строку.
    lines = trace_path.read_text(encoding="utf-8").splitlines()
    lines[1] = '{"event_id": "broken"} invalid'

    trace_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(ExecutionRecorderError):
        ExecutionRecorder(database, traces)
def test_recovery_handles_valid_json_without_trailing_newline(tmp_path):
    database = tmp_path / "agent.db"
    traces = tmp_path / "traces"

    recorder = ExecutionRecorder(database, traces)
    execution_id = recorder.start_execution()

    trace_path = traces / f"{execution_id}.jsonl"

    recorder.close()

    content = trace_path.read_text(encoding="utf-8").rstrip("\n")
    trace_path.write_text(content, encoding="utf-8")

    recovered = ExecutionRecorder(database, traces)
    recovered.record_event(execution_id, "NEXT_EVENT")

    lines = trace_path.read_text(encoding="utf-8").splitlines()

    assert len(lines) == 2
    assert all(isinstance(json.loads(line), dict) for line in lines)

    recovered.close()