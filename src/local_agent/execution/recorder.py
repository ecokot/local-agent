import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any, Self
from uuid import uuid4

from local_agent.execution.models import ExecutionEvent, ExecutionEventType
from local_agent.models import Execution, ExecutionStatus


class ExecutionRecorderError(Exception):
    """Base error for execution recording."""


class ExecutionRecorder:
    def __init__(
            self,
            database_path: str | Path,
            trace_directory: str | Path,
    ) -> None:
        self.database_path = str(database_path)
        self.trace_directory = Path(trace_directory)
        self.trace_directory.mkdir(parents=True, exist_ok=True)

        if self.database_path != ":memory:":
            Path(self.database_path).parent.mkdir(
                parents=True,
                exist_ok=True,
            )

        self._lock = RLock()
        self._connection = sqlite3.connect(
            self.database_path,
            check_same_thread=False,
        )
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.row_factory = sqlite3.Row
        self._initialize_database()
        self._recover_traces()

    def _initialize_database(self) -> None:
        with self._connection:
            self._connection.execute("""
                CREATE TABLE IF NOT EXISTS executions (
                    execution_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES tasks(task_id),
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT
                )
            """)

            columns = {
                row["name"]
                for row in self._connection.execute(
                    "PRAGMA table_info(executions)"
                ).fetchall()
            }

            # Миграция старой таблицы: старые выполнения
            # сохраняются, но остаются без привязанной задачи.
            if "task_id" not in columns:
                self._connection.execute("""
                    ALTER TABLE executions
                    ADD COLUMN task_id TEXT REFERENCES tasks(task_id)
                """)

            self._connection.execute("""
                CREATE TABLE IF NOT EXISTS execution_events (
                    event_id TEXT PRIMARY KEY,
                    execution_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    data TEXT NOT NULL,
                    trace_written INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY (execution_id)
                        REFERENCES executions(execution_id)
                )
            """)

            self._connection.execute("""
                CREATE INDEX IF NOT EXISTS
                idx_execution_events_execution_id
                ON execution_events(execution_id)
            """)

    def _read_existing_event_ids(self, trace_path: Path) -> set[str]:
        if not trace_path.exists():
            return set()

        content = trace_path.read_bytes()
        lines = content.splitlines(keepends=True)

        existing_event_ids = set()
        offset = 0

        for index, line in enumerate(lines):
            line_is_complete = line.endswith(b"\n")
            is_last_line = index == len(lines) - 1
            line_content = line.rstrip(b"\r\n")

            if not line_content.strip():
                offset += len(line)
                continue

            try:
                saved_event = json.loads(line_content)
            except json.JSONDecodeError as exc:
                if is_last_line and not line_is_complete:
                    # Удаляем только незавершённую последнюю строку.
                    with trace_path.open("r+b") as file:
                        file.truncate(offset)

                    break

                raise ExecutionRecorderError(
                    f"Corrupted trace file: {trace_path}"
                ) from exc

            if not isinstance(saved_event, dict):
                raise ExecutionRecorderError(
                    f"Invalid event format in trace file: {trace_path}"
                )

            event_id = saved_event.get("event_id")

            if not isinstance(event_id, str) or not event_id:
                raise ExecutionRecorderError(
                    f"Invalid event_id in trace file: {trace_path}"
                )

            existing_event_ids.add(event_id)

            if is_last_line and not line_is_complete:
                with trace_path.open("ab") as file:
                    file.write(b"\n")

            offset += len(line)

        return existing_event_ids

    def start_execution(self, task_id: str) -> str:
        execution_id = str(uuid4())
        timestamp = datetime.now(UTC).isoformat()

        event = ExecutionEvent.create(
            execution_id,
            ExecutionEventType.EXECUTION_STARTED,
        )

        try:
            with self._lock:
                with self._connection:
                    self._connection.execute(
                        """
                        INSERT INTO executions (
                            execution_id,
                            task_id,
                            status,
                            started_at
                        )
                        VALUES (?, ?, 'RUNNING', ?)
                        """,
                        (execution_id, task_id, timestamp),
                    )

                    self._connection.execute(
                        """
                        INSERT INTO execution_events (
                            event_id,
                            execution_id,
                            event_type,
                            timestamp,
                            data
                        )
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            event.event_id,
                            event.execution_id,
                            event.event_type,
                            event.timestamp,
                            json.dumps(event.data, ensure_ascii=False),
                        ),
                    )

                self._write_pending_traces()

        except sqlite3.Error as exc:
            raise ExecutionRecorderError(
                f"Could not start execution: {exc}"
            ) from exc

        return execution_id

    def record_event(
            self,
            execution_id: str,
            event_type: str,
            data: dict[str, Any] | None = None,
    ) -> ExecutionEvent:
        event = ExecutionEvent.create(
            execution_id,
            event_type,
            self._sanitize(data or {}),
        )

        serialized_data = json.dumps(
            event.data,
            ensure_ascii=False,
        )

        with self._lock:
            try:
                with self._connection:
                    self._connection.execute(
                        """
                    INSERT INTO execution_events (
                        event_id,
                        execution_id,
                        event_type,
                        timestamp,
                        data
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                        (
                            event.event_id,
                            event.execution_id,
                            event.event_type,
                            event.timestamp,
                            serialized_data,
                        ),
                    )
            except sqlite3.Error as exc:
                raise ExecutionRecorderError(
                    f"Could not record event: {exc}"
                ) from exc

            self._write_pending_traces()

        return event

    def finish_execution(
            self,
            execution_id: str,
            result: dict[str, Any] | None = None,
    ) -> None:
        self._finish(
            execution_id,
            "SUCCEEDED",
            ExecutionEventType.EXECUTION_FINISHED,
            result,
        )

    def fail_execution(
            self,
            execution_id: str,
            error: str,
    ) -> None:
        self._finish(
            execution_id,
            "FAILED",
            ExecutionEventType.EXECUTION_FAILED,
            {"error": error},
        )

    def _finish(
            self,
            execution_id: str,
            status: str,
            event_type: str,
            data: dict[str, Any] | None,
    ) -> None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT status
                FROM executions
                WHERE execution_id = ?
                """,
                (execution_id,),
            ).fetchone()

            if row is None:
                raise ExecutionRecorderError(
                    f"Unknown execution: {execution_id}"
                )
            if row["status"] != "RUNNING":
                raise ExecutionRecorderError(
                    f"Execution is already {row['status']}"
                )

            event = ExecutionEvent.create(
                execution_id,
                event_type,
                self._sanitize(data or {}),
            )

            try:
                with self._connection:
                    self._connection.execute(
                        """
                        INSERT INTO execution_events (
                        event_id,
                        execution_id,
                        event_type,
                        timestamp,
                        data
                    )
                    VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            event.event_id,
                            event.execution_id,
                            event.event_type,
                            event.timestamp,
                            json.dumps(event.data, ensure_ascii=False),
                        ),
                    )

                    self._connection.execute(
                        """
                        UPDATE executions
                        SET status = ?, finished_at = ?
                        WHERE execution_id = ?
                        """,
                        (
                            status,
                            event.timestamp,
                            execution_id,
                        ),
                    )
            except sqlite3.Error as exc:
                raise ExecutionRecorderError(
                    f"Could not finish execution: {exc}"
                ) from exc

            self._write_pending_traces()

    def get_events(
            self,
            execution_id: str,
    ) -> list[ExecutionEvent]:
        rows = self._connection.execute(
            """
            SELECT *
            FROM execution_events
            WHERE execution_id = ?
            ORDER BY rowid
            """,
            (execution_id,),
        ).fetchall()

        return [
            ExecutionEvent(
                event_id=row["event_id"],
                execution_id=row["execution_id"],
                event_type=row["event_type"],
                timestamp=row["timestamp"],
                data=json.loads(row["data"]),
            )
            for row in rows
        ]

    def get_incomplete_executions(self) -> list[str]:
        rows = self._connection.execute(
            """
            SELECT execution_id
            FROM executions
            WHERE status = 'RUNNING'
            ORDER BY started_at
            """
        ).fetchall()
        return [row["execution_id"] for row in rows]

    def get_status(self, execution_id: str) -> str:
        row = self._connection.execute(
            """
            SELECT status
            FROM executions
            WHERE execution_id = ?
            """,
            (execution_id,),
        ).fetchone()

        if row is None:
            raise ExecutionRecorderError(
                f"Unknown execution: {execution_id}"
            )

        return row["status"]

    def _write_pending_traces(self) -> None:
        rows = self._connection.execute(
            """
            SELECT *
            FROM execution_events
            WHERE trace_written = 0
            ORDER BY rowid
            """
        ).fetchall()

        for row in rows:
            event = {
                "event_id": row["event_id"],
                "execution_id": row["execution_id"],
                "event_type": row["event_type"],
                "timestamp": row["timestamp"],
                "data": json.loads(row["data"]),
            }

            trace_path = self._trace_path(row["execution_id"])

            try:
                existing_event_ids = self._read_existing_event_ids(trace_path)

                if row["event_id"] not in existing_event_ids:
                    with trace_path.open("a", encoding="utf-8") as file:
                        file.write(
                            json.dumps(event, ensure_ascii=False) + "\n"
                        )
                        file.flush()

                with self._connection:
                    self._connection.execute(
                        """
                        UPDATE execution_events
                        SET trace_written = 1
                        WHERE event_id = ?
                        """,
                        (row["event_id"],),
                    )

            except (OSError, sqlite3.Error, json.JSONDecodeError, KeyError) as exc:
                raise ExecutionRecorderError(
                    f"Could not write trace for event "
                    f"{row['event_id']}: {exc}"
                ) from exc

    def _recover_traces(self) -> None:
        with self._lock:
            self._write_pending_traces()

    def _trace_path(self, execution_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f-]{36}", execution_id):
            raise ExecutionRecorderError(
                "Invalid execution ID"
            )
        return self.trace_directory / f"{execution_id}.jsonl"

    @staticmethod
    def _sanitize(value: Any) -> Any:
        secret_pattern = re.compile(
            r"(password|token|secret|api[_-]?key)",
            re.IGNORECASE,
        )

        if isinstance(value, dict):
            return {
                key: (
                    "***REDACTED***"
                    if secret_pattern.search(str(key))
                    else ExecutionRecorder._sanitize(item)
                )
                for key, item in value.items()
            }

        if isinstance(value, list):
            return [
                ExecutionRecorder._sanitize(item)
                for item in value
            ]

        return value

    def close(self) -> None:
        with self._lock:
            try:
                self._write_pending_traces()
            finally:
                self._connection.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc_val, traceback) -> None:
        self.close()

    def get_executions_for_task(
            self,
            task_id: str,
    ) -> list[Execution]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT execution_id, task_id, status, started_at, finished_at
                FROM executions
                WHERE task_id = ?
                ORDER BY rowid
                """,
                (task_id,),
            ).fetchall()

        return [
            Execution(
                execution_id=row["execution_id"],
                task_id=row["task_id"],
                status=ExecutionStatus(row["status"]),
                started_at=row["started_at"],
                finished_at=row["finished_at"],
            )
            for row in rows
        ]
