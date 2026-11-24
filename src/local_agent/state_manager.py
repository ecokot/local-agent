import json
import sqlite3
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar, Self
from uuid import uuid4

from local_agent.models import Task


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
            TaskStatus.WAITING_FOR_APPROVAL,
            TaskStatus.CANCELLED,
            TaskStatus.FAILED,
        },
        TaskStatus.RUNNING: {
            TaskStatus.WAITING_FOR_APPROVAL,
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        },
        TaskStatus.WAITING_FOR_APPROVAL: {
            TaskStatus.RUNNING,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        },
    }

    def __init__(
            self,
            database_path: str | Path | None = None,
            *,
            connection: sqlite3.Connection | None = None,
    ) -> None:
        self._owns_connection = connection is None

        if connection is not None:
            self.connection = connection
        elif database_path is not None:
            self.database_path = Path(database_path)
            self.database_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )
            self.connection = sqlite3.connect(self.database_path)
        else:
            raise ValueError(
                "Either database_path or connection must be provided"
            )

        self.connection.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self) -> None:
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                description TEXT NOT NULL,
                status TEXT NOT NULL
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS review_history (
                review_id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL REFERENCES tasks(task_id),
                revision INTEGER NOT NULL CHECK (revision >= 0),
                plan_json TEXT NOT NULL,
                approved INTEGER NOT NULL CHECK (approved IN (0, 1)),
                feedback TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
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
        if self.connection.in_transaction:
            raise RuntimeError(
                "Cannot transition while a transaction is active"
            )

        try:
            self.connection.execute("BEGIN IMMEDIATE")

            row = self.connection.execute(
                """
                SELECT status
                FROM tasks
                WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()

            if row is None:
                raise ValueError(f"Unknown task: {task_id}")

            current_status = TaskStatus(row["status"])
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
                (new_status.value, task_id),
            )

            self.connection.commit()

        except Exception:
            if self.connection.in_transaction:
                self.connection.rollback()
            raise

    def transition_in_transaction(
            self,
            task_id: str,
            new_status: TaskStatus,
    ) -> None:
        row = self.connection.execute(
            """
            SELECT status
            FROM tasks
            WHERE task_id = ?
            """,
            (task_id,),
        ).fetchone()

        if row is None:
            raise ValueError(f"Unknown task: {task_id}")

        current_status = TaskStatus(row["status"])
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
            (new_status.value, task_id),
        )


    def record_review(
            self,
            task_id: str,
            revision: int,
            plan: dict[str, Any],
            approved: bool,
            feedback: str,
    ) -> None:
        if (
            not isinstance(revision, int)
            or isinstance(revision, bool)
            or revision < 0
        ):
            raise ValueError(
                "Review revision must be a non-negative integer"
            )
        if not isinstance(approved, bool):
            raise TypeError("Review approval must be a boolean")

        self.get_task(task_id)
        self.connection.execute(
            """
            INSERT INTO review_history (
                task_id, revision, plan_json, approved, feedback, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                revision,
                json.dumps(plan, ensure_ascii=False, sort_keys=True),
                int(approved),
                feedback,
                datetime.now(UTC).isoformat(),
            ),
        )
        self.connection.commit()

    def get_review_history(self, task_id: str) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT revision, plan_json, approved, feedback, created_at
            FROM review_history
            WHERE task_id = ?
            ORDER BY review_id
            """,
            (task_id,),
        ).fetchall()

        return [
            {
                "revision": row["revision"],
                "plan": json.loads(row["plan_json"]),
                "approved": bool(row["approved"]),
                "feedback": row["feedback"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def close(self) -> None:
        if self._owns_connection:
            self.connection.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def get_task(self, task_id: str) -> Task:
        row = self.connection.execute(
            """
            SELECT task_id, description, status
            FROM tasks
            WHERE task_id = ?
            """,
            (task_id,),
        ).fetchone()

        if row is None:
            raise ValueError(f"Unknown task: {task_id}")

        return Task(
            task_id=row["task_id"],
            description=row["description"],
            status=TaskStatus(row["status"]),
        )

    def get_tasks(self) -> list[Task]:
        rows = self.connection.execute(
            """
            SELECT task_id, description, status
            FROM tasks
            ORDER BY rowid
            """
        ).fetchall()

        return [
            Task(
                task_id=row["task_id"],
                description=row["description"],
                status=TaskStatus(row["status"]),
            )
            for row in rows
        ]
