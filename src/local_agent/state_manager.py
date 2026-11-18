import sqlite3
from enum import StrEnum
from pathlib import Path
from typing import ClassVar, Self
from uuid import uuid4


class TaskStatus(StrEnum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class StateManager:
    ALLOWED_TRANSITIONS: ClassVar[dict[TaskStatus, set[TaskStatus]]] = {
        TaskStatus.CREATED: {
            TaskStatus.RUNNING,
            TaskStatus.CANCELLED,
        },
        TaskStatus.RUNNING: {
            TaskStatus.WAITING_FOR_APPROVAL,
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        },
        TaskStatus.WAITING_FOR_APPROVAL: {
            TaskStatus.RUNNING,
            TaskStatus.CANCELLED
        },
    }

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        self.connection = sqlite3.connect(self.database_path)
        self.connection.row_factory = sqlite3.Row
        self._create_tables()

        # self.tasks: dict[str, dict] = {}

    def _create_tables(self) -> None:
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                description TEXT NOT NULL,
                status TEXT NOT NULL
            )
            """)
        self.connection.commit()

    def create_task(self, description: str) -> str:
        if not description.strip():
            raise ValueError("Task description cannot be empty")

        task_id = str(uuid4())

        self.connection.execute(
            """
            INSERT INTO tasks (task_id, description, status)
            VALUES (?, ?, ?)
            """,
            (task_id, description, TaskStatus.CREATED.value),
        )
        self.connection.commit()
        return task_id

    def get_state(self, task_id: str) -> TaskStatus:
        cursor = self.connection.execute(
            """
            SELECT status
            FROM tasks
            WHERE task_id = ?
            """,
            (task_id,),
        )
        row = cursor.fetchone()

        if row is None:
            raise ValueError(f"Unknown task: {task_id}")
        return TaskStatus(row["status"])

    def transition(
            self,
            task_id: str,
            new_status: TaskStatus,
    ) -> None:
        current_status = self.get_state(task_id)

        allowed = self.ALLOWED_TRANSITIONS.get(current_status, set())

        if new_status not in allowed:
            raise ValueError(
                f"Invalid transition: {current_status} -> {new_status}"
            )

        self.connection.execute(
            """
            UPDATE tasks
            SET status = ?
            WHERE task_id = ?
            """,
            (new_status.value, task_id)
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def get_tasks(self) -> list[dict[str, str]]:
        rows = self.connection.execute(
            """
            SELECT task_id, description, status
            FROM tasks
            ORDER BY rowid
            """
        ).fetchall()

        return [
            {
                "task_id": row["task_id"],
                "description": row["description"],
                "status": row["status"],
            }
            for row in rows
        ]