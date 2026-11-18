
import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Self
from uuid import uuid4

from local_agent.models import Approval, ApprovalStatus
from local_agent.state_manager import StateManager, TaskStatus


class ApprovalManager:
    def __init__(
            self,
            database_path: str | Path | None = None,
            *,
            connection: sqlite3.Connection | None = None,
            state_manager: StateManager | None = None,
    ) -> None:
        self._owns_connection = connection is None

        if connection is not None:
            self.connection = connection
        elif database_path is not None:
            self.connection = sqlite3.connect(database_path)
        else:
            raise ValueError(
                "Either database_path or connection must be provided"
            )

        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")

        if (
                state_manager is not None
                and state_manager.connection is not self.connection
        ):
            raise ValueError(
                "ApprovalManager and StateManager must use the same connection"
            )

        self.state_manager = state_manager

        self._create_table()

    def _create_table(self) -> None:
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS approvals (
                approval_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                action_type TEXT NOT NULL,
                parameters_json TEXT NOT NULL,
                parameters_hash TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                decided_at TEXT,
                FOREIGN KEY (task_id) REFERENCES tasks(task_id)
            )
            """
        )
        self.connection.commit()


    @staticmethod
    def _serialize_parameters(parameters: dict) -> tuple[str, str]:
        if not isinstance(parameters, dict):
            raise TypeError("Approval parameters must be a dictionary")

        try:
            serialized = json.dumps(
                parameters,
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "Approval parameters must contain valid JSON values"
            ) from exc

        parameters_hash = hashlib.sha256(
            serialized.encode("utf-8")
        ).hexdigest()

        return serialized, parameters_hash


    @staticmethod
    def _row_to_approval(row: sqlite3.Row) -> Approval:
        return Approval(
            approval_id=row["approval_id"],
            task_id=row["task_id"],
            action_type=row["action_type"],
            parameters=json.loads(row["parameters_json"]),
            parameters_hash=row["parameters_hash"],
            status=ApprovalStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            decided_at=(
                datetime.fromisoformat(row["decided_at"])
                if row["decided_at"]
                else None
            ),
        )

    def request_approval(
            self,
            task_id: str,
            action_type: str,
            parameters: dict,
    ) -> Approval:
        parameters_json, parameters_hash = self._serialize_parameters(
            parameters
        )

        if self.connection.in_transaction:
            raise RuntimeError(
                "Cannot request approval inside an active transaction"
            )

        self.connection.execute("BEGIN IMMEDIATE")

        try:
            existing = self.connection.execute(
                """
                SELECT *
                FROM approvals
                WHERE task_id = ?
                  AND action_type = ?
                  AND parameters_hash = ?
                  AND status IN (?, ?)
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (
                    task_id,
                    action_type,
                    parameters_hash,
                    ApprovalStatus.PENDING.value,
                    ApprovalStatus.APPROVED.value,
                ),
            ).fetchone()

            if existing is not None:
                self.connection.commit()
                return self._row_to_approval(existing)

            approval_id = str(uuid4())
            created_at = datetime.now(UTC).isoformat()

            self.connection.execute(
                """
                INSERT INTO approvals (
                    approval_id,
                    task_id,
                    action_type,
                    parameters_json,
                    parameters_hash,
                    status,
                    created_at,
                    decided_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    approval_id,
                    task_id,
                    action_type,
                    parameters_json,
                    parameters_hash,
                    ApprovalStatus.PENDING.value,
                    created_at,
                ),
            )

            if self.state_manager is not None:
                current_status = self.state_manager.get_state(task_id)

                if current_status != TaskStatus.WAITING_FOR_APPROVAL:
                    self.state_manager.transition_in_transaction(
                        task_id,
                        TaskStatus.WAITING_FOR_APPROVAL,
                    )

            self.connection.commit()

        except Exception:
            self.connection.rollback()
            raise

        return self.get_approval(approval_id)


    def get_approval(self, approval_id: str) -> Approval:
        row = self.connection.execute(
            "SELECT * FROM approvals WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()

        if row is None:
            raise ValueError(f"Approval not found: {approval_id}")

        return self._row_to_approval(row)

    def _decide(
            self,
            approval_id: str,
            status: ApprovalStatus,
    ) -> Approval:
        if self.connection.in_transaction:
            raise RuntimeError(
                "Cannot decide approval inside an active transaction"
            )

        self.connection.execute("BEGIN IMMEDIATE")

        try:
            row = self.connection.execute(
                """
                SELECT task_id
                FROM approvals
                WHERE approval_id = ?
                  AND status = ?
                """,
                (
                    approval_id,
                    ApprovalStatus.PENDING.value,
                ),
            ).fetchone()

            if row is None:
                raise ValueError(
                    f"Approval is not pending: {approval_id}"
                )

            task_id = row["task_id"]
            decided_at = datetime.now(UTC).isoformat()

            self.connection.execute(
                """
                UPDATE approvals
                SET status = ?, decided_at = ?
                WHERE approval_id = ?
                  AND status = ?
                """,
                (
                    status.value,
                    decided_at,
                    approval_id,
                    ApprovalStatus.PENDING.value,
                ),
            )

            if self.state_manager is not None:
                pending = self.connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM approvals
                    WHERE task_id = ?
                      AND status = ?
                    """,
                    (
                        task_id,
                        ApprovalStatus.PENDING.value,
                    ),
                ).fetchone()[0]

                if pending == 0:
                    self.state_manager.transition_in_transaction(
                        task_id,
                        TaskStatus.RUNNING,
                    )

            self.connection.commit()

        except Exception:
            self.connection.rollback()
            raise

        return self.get_approval(approval_id)

    def approve(self, approval_id: str) -> Approval:
        return self._decide(approval_id, ApprovalStatus.APPROVED)

    def reject(self, approval_id: str) -> Approval:
        return self._decide(approval_id, ApprovalStatus.REJECTED)

    def is_approved(
        self,
        task_id: str,
        action_type: str,
        parameters: dict,
    ) -> bool:
        _, parameters_hash = self._serialize_parameters(parameters)

        row = self.connection.execute(
            """
            SELECT 1
            FROM approvals
            WHERE task_id = ?
              AND action_type = ?
              AND parameters_hash = ?
              AND status = ?
            LIMIT 1
            """,
            (
                task_id,
                action_type,
                parameters_hash,
                ApprovalStatus.APPROVED.value,
            ),
        ).fetchone()

        return row is not None

    def get_pending_approvals(self, task_id: str) -> list[Approval]:
        rows = self.connection.execute(
            """
            SELECT *
            FROM approvals
            WHERE task_id = ?
              AND status = ?
            ORDER BY created_at, rowid
            """,
            (task_id, ApprovalStatus.PENDING.value),
        ).fetchall()

        return [self._row_to_approval(row) for row in rows]

    def close(self) -> None:
        if self._owns_connection:
            self.connection.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
