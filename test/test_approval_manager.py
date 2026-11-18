import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from local_agent.approval_manager import ApprovalManager
from local_agent.models import ApprovalStatus
from local_agent.state_manager import StateManager


@pytest.fixture
def managers(tmp_path):
    database_path = tmp_path / "test.db"

    with StateManager(database_path) as state_manager:
        task_id = state_manager.create_task("Test task")

        with ApprovalManager(database_path) as approval_manager:
            yield task_id, approval_manager


def test_request_approval_creates_pending_approval(managers):
    task_id, manager = managers

    approval = manager.request_approval(
        task_id,
        "CREATE_FILE",
        {"path": "main.py"},
    )

    assert approval.status == ApprovalStatus.PENDING
    assert approval.task_id == task_id
    assert approval.action_type == "CREATE_FILE"


def test_same_request_reuses_pending_approval(managers):
    task_id, manager = managers

    first = manager.request_approval(
        task_id, "CREATE_FILE", {"path": "main.py"}
    )
    second = manager.request_approval(
        task_id, "CREATE_FILE", {"path": "main.py"}
    )

    assert first.approval_id == second.approval_id


def test_approved_request_is_reused(managers):
    task_id, manager = managers

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
    task_id, manager = managers

    first = manager.request_approval(
        task_id, "CREATE_FILE", {"path": "main.py"}
    )
    second = manager.request_approval(
        task_id, "CREATE_FILE", {"path": "other.py"}
    )

    assert first.approval_id != second.approval_id


def test_approve_changes_status(managers):
    task_id, manager = managers

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
    task_id, manager = managers

    approval = manager.request_approval(
        task_id, "CREATE_FILE", {"path": "main.py"}
    )

    result = manager.reject(approval.approval_id)

    assert result.status == ApprovalStatus.REJECTED
    assert result.decided_at is not None
    assert not manager.is_approved(
        task_id, "CREATE_FILE", {"path": "main.py"}
    )


def test_rejected_request_can_be_created_again(managers):
    task_id, manager = managers

    first = manager.request_approval(
        task_id, "CREATE_FILE", {"path": "main.py"}
    )
    manager.reject(first.approval_id)

    second = manager.request_approval(
        task_id, "CREATE_FILE", {"path": "main.py"}
    )

    assert first.approval_id != second.approval_id
    assert second.status == ApprovalStatus.PENDING


def test_cannot_decide_approval_twice(managers):
    task_id, manager = managers

    approval = manager.request_approval(
        task_id, "CREATE_FILE", {"path": "main.py"}
    )
    manager.approve(approval.approval_id)

    with pytest.raises(ValueError):
        manager.reject(approval.approval_id)


def test_get_unknown_approval_raises(managers):
    _, manager = managers

    with pytest.raises(ValueError):
        manager.get_approval("unknown")


def test_parameter_order_does_not_change_hash(managers):
    task_id, manager = managers

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
    task_id, manager = managers

    with pytest.raises(TypeError):
        manager.request_approval(
            task_id,
            "CREATE_FILE",
            ["main.py"],
        )


def test_non_json_parameters_are_rejected(managers):
    task_id, manager = managers

    with pytest.raises(ValueError):
        manager.request_approval(
            task_id,
            "CREATE_FILE",
            {"value": object()},
        )


def test_nan_parameters_are_rejected(managers):
    task_id, manager = managers

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