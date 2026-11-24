import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from local_agent.approval_manager import ApprovalManager
from local_agent.models import ApprovalStatus
from local_agent.state_manager import StateManager, TaskStatus


@pytest.fixture
def managers(tmp_path):
    database_path = tmp_path / "test.db"
    connection = sqlite3.connect(database_path)

    state_manager = StateManager(connection=connection)
    task_id = state_manager.create_task("Test task")
    state_manager.transition(task_id, TaskStatus.RUNNING)

    approval_manager = ApprovalManager(
        connection=connection,
        state_manager=state_manager,
    )

    try:
        yield task_id, approval_manager, state_manager
    finally:
        approval_manager.close()
        state_manager.close()
        connection.close()


def test_request_approval_creates_pending_approval(managers):
    task_id, manager, _ = managers

    approval = manager.request_approval(
        task_id,
        "CREATE_FILE",
        {"path": "main.py"},
    )

    assert approval.status == ApprovalStatus.PENDING
    assert approval.task_id == task_id
    assert approval.action_type == "CREATE_FILE"


def test_same_request_reuses_pending_approval(managers):
    task_id, manager, _ = managers

    first = manager.request_approval(
        task_id, "CREATE_FILE", {"path": "main.py"}
    )
    second = manager.request_approval(
        task_id, "CREATE_FILE", {"path": "main.py"}
    )

    assert first.approval_id == second.approval_id


def test_approved_request_is_reused(managers):
    task_id, manager, _ = managers

    first = manager.request_approval(
        task_id, "CREATE_FILE", {"path": "main.py"}
    )
    manager.approve(first.approval_id)

    second = manager.request_approval(
        task_id, "CREATE_FILE", {"path": "main.py"}
    )

    assert second.approval_id == first.approval_id
    assert second.status == ApprovalStatus.APPROVED


def test_changed_parameters_require_new_approval(managers):
    task_id, manager, _ = managers

    first = manager.request_approval(
        task_id, "CREATE_FILE", {"path": "main.py"}
    )
    second = manager.request_approval(
        task_id, "CREATE_FILE", {"path": "other.py"}
    )

    assert first.approval_id != second.approval_id


def test_approve_changes_status(managers):
    task_id, manager, _ = managers

    approval = manager.request_approval(
        task_id, "CREATE_FILE", {"path": "main.py"}
    )

    result = manager.approve(approval.approval_id)

    assert result.status == ApprovalStatus.APPROVED
    assert result.decided_at is not None
    assert manager.is_approved(
        task_id, "CREATE_FILE", {"path": "main.py"}
    )


def test_reject_changes_status(managers):
    task_id, manager, _ = managers

    approval = manager.request_approval(
        task_id, "CREATE_FILE", {"path": "main.py"}
    )

    result = manager.reject(approval.approval_id)

    assert result.status == ApprovalStatus.REJECTED
    assert result.decided_at is not None
    assert not manager.is_approved(
        task_id, "CREATE_FILE", {"path": "main.py"}
    )


def test_rejected_action_marks_task_failed(managers):
    task_id, manager, _ = managers

    first = manager.request_approval(
        task_id, "CREATE_FILE", {"path": "main.py"}
    )
    manager.reject(first.approval_id)

    assert manager.get_approval(first.approval_id).status == ApprovalStatus.REJECTED
    assert manager.state_manager.get_state(task_id) == TaskStatus.FAILED


def test_cannot_decide_approval_twice(managers):
    task_id, manager, _ = managers

    approval = manager.request_approval(
        task_id, "CREATE_FILE", {"path": "main.py"}
    )
    manager.approve(approval.approval_id)

    with pytest.raises(ValueError):
        manager.reject(approval.approval_id)


def test_get_unknown_approval_raises(managers):
    _, manager, _ = managers

    with pytest.raises(ValueError):
        manager.get_approval("unknown")


def test_parameter_order_does_not_change_hash(managers):
    task_id, manager, _ = managers

    first = manager.request_approval(
        task_id,
        "CREATE_FILE",
        {"path": "main.py", "encoding": "utf-8"},
    )
    second = manager.request_approval(
        task_id,
        "CREATE_FILE",
        {"encoding": "utf-8", "path": "main.py"},
    )

    assert first.approval_id == second.approval_id


def test_approval_survives_manager_restart(tmp_path):
    database_path = tmp_path / "test.db"

    with StateManager(database_path) as state_manager:
        task_id = state_manager.create_task("Test task")

    with ApprovalManager(database_path) as manager:
        approval = manager.request_approval(
            task_id, "CREATE_FILE", {"path": "main.py"}
        )
        manager.approve(approval.approval_id)
        approval_id = approval.approval_id

    with ApprovalManager(database_path) as manager:
        restored = manager.get_approval(approval_id)

        assert restored.status == ApprovalStatus.APPROVED
        assert manager.is_approved(
            task_id, "CREATE_FILE", {"path": "main.py"}
        )


def test_unknown_task_cannot_request_approval(tmp_path):
    database_path = tmp_path / "test.db"

    with (
        StateManager(database_path),
        ApprovalManager(database_path) as manager,
        pytest.raises(sqlite3.IntegrityError),
    ):
        manager.request_approval(
            "unknown-task",
            "CREATE_FILE",
            {"path": "main.py"},
        )


def test_non_dict_parameters_are_rejected(managers):
    task_id, manager, _ = managers

    with pytest.raises(TypeError):
        manager.request_approval(
            task_id,
            "CREATE_FILE",
            ["main.py"],
        )


def test_non_json_parameters_are_rejected(managers):
    task_id, manager, _ = managers

    with pytest.raises(ValueError):
        manager.request_approval(
            task_id,
            "CREATE_FILE",
            {"value": object()},
        )


def test_nan_parameters_are_rejected(managers):
    task_id, manager, _ = managers

    with pytest.raises(ValueError):
        manager.request_approval(
            task_id,
            "CREATE_FILE",
            {"value": float("nan")},
        )


def test_concurrent_requests_reuse_same_approval(tmp_path):
    database_path = tmp_path / "test.db"

    with StateManager(database_path) as state_manager:
        task_id = state_manager.create_task("Test task")

    barrier = threading.Barrier(2)

    def request():
        with ApprovalManager(database_path) as manager:
            barrier.wait()
            approval = manager.request_approval(
                task_id,
                "CREATE_FILE",
                {"path": "main.py"},
            )
            return approval.approval_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(request),
            executor.submit(request),
        ]

        approval_ids = [future.result() for future in futures]

    assert approval_ids[0] == approval_ids[1]


def test_close_does_not_close_external_connection():
    connection = sqlite3.connect(":memory:")

    # Для внешнего соединения сначала создаём таблицу tasks,
    # поскольку approvals ссылается на неё через внешний ключ.
    connection.execute(
        """
        CREATE TABLE tasks (
            task_id TEXT PRIMARY KEY,
            description TEXT NOT NULL,
            status TEXT NOT NULL
        )
        """
    )

    manager = ApprovalManager(connection=connection)
    manager.close()

    connection.execute("SELECT 1")
    connection.close()


def test_close_closes_owned_connection(tmp_path):
    manager = ApprovalManager(tmp_path / "test.db")
    connection = manager.connection

    manager.close()

    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")


def test_request_approval_moves_task_to_waiting(managers):
    task_id, manager, state_manager = managers

    manager.request_approval(
        task_id,
        "CREATE_FILE",
        {"path": "main.py"},
    )

    assert state_manager.get_state(task_id) == TaskStatus.WAITING_FOR_APPROVAL


def test_approve_resumes_task(managers):
    task_id, manager, state_manager = managers

    approval = manager.request_approval(
        task_id,
        "CREATE_FILE",
        {"path": "main.py"},
    )

    manager.approve(approval.approval_id)

    assert state_manager.get_state(task_id) == TaskStatus.RUNNING


def test_reject_marks_task_failed(managers):
    task_id, manager, state_manager = managers

    approval = manager.request_approval(
        task_id,
        "CREATE_FILE",
        {"path": "main.py"},
    )

    manager.reject(approval.approval_id)

    assert state_manager.get_state(task_id) == TaskStatus.FAILED


def test_request_approval_rolls_back_if_task_transition_fails(
        managers, monkeypatch
):
    _, manager, state_manager = managers

    task_id = state_manager.create_task("Task in CREATED state")

    def fail_transition(*args, **kwargs):
        raise ValueError("Invalid transition")

    monkeypatch.setattr(
        state_manager,
        "transition_in_transaction",
        fail_transition,
    )

    with pytest.raises(ValueError, match="Invalid transition"):
        manager.request_approval(
            task_id,
            "CREATE_FILE",
            {"path": "main.py"},
        )

    assert state_manager.get_state(task_id) == TaskStatus.CREATED

    count = manager.connection.execute(
        """
        SELECT COUNT(*)
        FROM approvals
        WHERE task_id = ?
        """,
        (task_id,),
    ).fetchone()[0]

    assert count == 0


def test_approve_rolls_back_if_task_transition_fails(managers):
    task_id, manager, state_manager = managers

    approval = manager.request_approval(
        task_id,
        "CREATE_FILE",
        {"path": "main.py"},
    )

    state_manager.transition(task_id, TaskStatus.CANCELLED)

    with pytest.raises(ValueError, match="Invalid transition"):
        manager.approve(approval.approval_id)

    restored = manager.get_approval(approval.approval_id)

    assert restored.status == ApprovalStatus.PENDING
    assert restored.decided_at is None
    assert state_manager.get_state(task_id) == TaskStatus.CANCELLED


def test_get_pending_approvals_returns_only_pending(managers):
    task_id, approval_manager, _ = managers

    pending = approval_manager.request_approval(
        task_id,
        "write_file",
        {"path": "example.py"},
    )
    approved = approval_manager.request_approval(
        task_id,
        "delete_file",
        {"path": "old.py"},
    )

    approval_manager.approve(approved.approval_id)

    result = approval_manager.get_pending_approvals(task_id)

    assert len(result) == 1
    assert result[0].approval_id == pending.approval_id


def test_cancel_changes_status(managers):
    task_id, manager, state_manager = managers

    approval = manager.request_approval(
        task_id,
        "CREATE_FILE",
        {"path": "main.py"},
    )

    result = manager.cancel(approval.approval_id)

    assert result.status == ApprovalStatus.CANCELLED
    assert result.decided_at is not None
    assert state_manager.get_state(task_id) == TaskStatus.CANCELLED


def test_cancelled_request_cannot_be_created_again(managers):
    task_id, manager, _ = managers

    approval = manager.request_approval(
        task_id,
        "CREATE_FILE",
        {"path": "main.py"},
    )
    manager.cancel(approval.approval_id)

    with pytest.raises(
        ValueError,
        match="permanently cancelled",
    ):
        manager.request_approval(
            task_id,
            "CREATE_FILE",
            {"path": "main.py"},
        )


def test_cancelled_task_cannot_request_changed_action(managers):
    task_id, manager, _ = managers

    first = manager.request_approval(
        task_id,
        "CREATE_FILE",
        {"path": "main.py"},
    )
    manager.cancel(first.approval_id)

    with pytest.raises(ValueError, match="Invalid transition"):
        manager.request_approval(
            task_id,
            "CREATE_FILE",
            {"path": "other.py"},
        )


def test_cancelled_approval_survives_manager_restart(tmp_path):
    database_path = tmp_path / "test.db"

    with StateManager(database_path) as state_manager:
        task_id = state_manager.create_task("Test task")

    with ApprovalManager(database_path) as manager:
        approval = manager.request_approval(
            task_id,
            "CREATE_FILE",
            {"path": "main.py"},
        )
        manager.cancel(approval.approval_id)
        approval_id = approval.approval_id

    with ApprovalManager(database_path) as manager:
        restored = manager.get_approval(approval_id)

        assert restored.status == ApprovalStatus.CANCELLED

        assert not manager.is_approved(
            task_id,
            "CREATE_FILE",
            {"path": "main.py"},
        )

        with pytest.raises(ValueError, match="not pending"):
            manager.approve(approval_id)

        with pytest.raises(
            ValueError,
            match="permanently cancelled",
        ):
            manager.request_approval(
                task_id,
                "CREATE_FILE",
                {"path": "main.py"},
            )


def test_cancelled_approval_is_not_approved(managers):
    task_id, manager, _ = managers

    approval = manager.request_approval(
        task_id,
        "CREATE_FILE",
        {"path": "main.py"},
    )
    manager.cancel(approval.approval_id)

    assert not manager.is_approved(
        task_id,
        "CREATE_FILE",
        {"path": "main.py"},
    )


def test_cancelled_approval_cannot_be_approved(managers):
    task_id, manager, _ = managers

    approval = manager.request_approval(
        task_id,
        "CREATE_FILE",
        {"path": "main.py"},
    )
    manager.cancel(approval.approval_id)

    with pytest.raises(ValueError, match="not pending"):
        manager.approve(approval.approval_id)


def test_cancel_marks_task_cancelled_without_approving_action(managers):
    task_id, manager, state_manager = managers

    approval = manager.request_approval(
        task_id,
        "CREATE_FILE",
        {"path": "main.py"},
    )

    manager.cancel(approval.approval_id)

    assert state_manager.get_state(task_id) == TaskStatus.CANCELLED
    assert not manager.is_approved(
        task_id,
        "CREATE_FILE",
        {"path": "main.py"},
    )
