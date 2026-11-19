from local_agent.approval_manager import ApprovalManager
from local_agent.models.approval import ApprovalStatus
from local_agent.models.plan import Plan
from local_agent.state_manager import StateManager


def test_plan_can_be_restored_from_approval(tmp_path) -> None:
    database_path = tmp_path / "agent.db"

    plan = Plan(
        summary="Test plan",
        steps=[
            {
                "description": "Create test file",
                "action": "create_file",
                "parameters": {"path": "test.txt"},
            }
        ],
    )

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