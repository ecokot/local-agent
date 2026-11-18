
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from local_agent.models import Approval, ApprovalStatus


def create_approval(parameters: dict) -> Approval:
    return Approval(
        approval_id="approval-001",
        task_id="task-001",
        action_type="CREATE_FILE",
        parameters=parameters,
        parameters_hash="test-hash",
        status=ApprovalStatus.PENDING,
        created_at=datetime.now(UTC),
        decided_at=None,
    )


def test_approval_creation():
    approval = create_approval({"path": "main.py"})

    assert approval.approval_id == "approval-001"
    assert approval.status == ApprovalStatus.PENDING
    assert approval.decided_at is None


def test_approval_deep_copies_parameters():
    parameters = {
        "file": {
            "path": "main.py",
        }
    }

    approval = create_approval(parameters)
    parameters["file"]["path"] = "other.py"

    assert approval.parameters["file"]["path"] == "main.py"


def test_approval_is_frozen():
    approval = create_approval({})

    with pytest.raises(FrozenInstanceError):
        approval.status = ApprovalStatus.APPROVED
