import pytest
from pydantic import ValidationError

from local_agent.approval_manager import ApprovalManager
from local_agent.models.approval import ApprovalStatus
from local_agent.models.plan import Plan
from local_agent.state_manager import StateManager


def make_plan() -> Plan:
    return Plan(
        summary="Test plan",
        steps=[
            {
                "description": "Create test file",
                "action": "create_file",
                "parameters": {"path": "test.txt"},
            }
        ],
        verification_criteria=[
            {
                "id": "tests",
                "type": "pytest",
                "description": "Run project tests",
                "parameters": {},
            },
            {
                "id": "created-file",
                "type": "file_exists",
                "description": "Confirm generated file exists",
                "parameters": {"path": "test.txt"},
            },
        ],
    )


def test_plan_can_be_restored_from_approval(tmp_path) -> None:
    database_path = tmp_path / "agent.db"
    plan = make_plan()

    with StateManager(database_path) as state_manager:
        task_id = state_manager.create_task("Test task")

    with ApprovalManager(database_path) as approval_manager:
        approval = approval_manager.request_approval(
            task_id=task_id,
            action_type="PLAN_APPROVAL",
            parameters={"plan": plan.model_dump(mode="json")},
        )
        approval_id = approval.approval_id

    # Имитируем перезапуск: открываем новое соединение с той же БД.
    with ApprovalManager(database_path) as approval_manager:
        restored_approval = approval_manager.get_approval(approval_id)

    assert restored_approval.status == ApprovalStatus.PENDING
    assert restored_approval.action_type == "PLAN_APPROVAL"

    restored_plan = Plan.model_validate(
        restored_approval.parameters["plan"]
    )

    assert restored_plan == plan
    assert restored_plan.verification_criteria == plan.verification_criteria


def test_legacy_approval_is_preserved_but_legacy_plan_is_not_accepted(
    tmp_path,
) -> None:
    database_path = tmp_path / "agent.db"

    with StateManager(database_path) as state_manager:
        task_id = state_manager.create_task("Legacy task")

    legacy_plan = {
        "summary": "Legacy plan",
        "steps": [{
            "description": "Create file",
            "action": "create_file",
            "parameters": {"path": "legacy.txt"},
        }],
    }

    with ApprovalManager(database_path) as approval_manager:
        approval = approval_manager.request_approval(
            task_id=task_id,
            action_type="PLAN_APPROVAL",
            parameters={"plan": legacy_plan},
        )
        approval_id = approval.approval_id

    with ApprovalManager(database_path) as approval_manager:
        restored_approval = approval_manager.get_approval(approval_id)

    assert restored_approval.parameters["plan"] == legacy_plan
    with pytest.raises(ValidationError, match="verification_criteria"):
        Plan.model_validate(restored_approval.parameters["plan"])
