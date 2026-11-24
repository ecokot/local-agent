from datetime import UTC, datetime

from local_agent.models.approval import Approval, ApprovalStatus
from local_agent.models.plan import Plan
from local_agent.user_interface import UserInterface


def test_show_plan_displays_verification_criteria(capsys) -> None:
    plan = Plan(
        summary="Create output",
        steps=[{
            "description": "Create output file",
            "action": "create_file",
            "parameters": {"path": "output.txt"},
        }],
        verification_criteria=[{
            "id": "output-file",
            "type": "file_exists",
            "description": "Убедиться, что output.txt существует",
            "parameters": {"path": "output.txt"},
        }],
    )
    approval = Approval(
        approval_id="approval-id",
        task_id="task-id",
        action_type="PLAN_APPROVAL",
        parameters={"plan": plan.model_dump(mode="json"), "revision": 0},
        parameters_hash="hash",
        status=ApprovalStatus.PENDING,
        created_at=datetime.now(UTC),
        decided_at=None,
    )

    UserInterface._show_plan(approval)

    output = capsys.readouterr().out
    assert "Критерии проверки:" in output
    assert "[output-file]" in output
    assert "Убедиться, что output.txt существует" in output
    assert "file_exists" in output
    assert "обязательный" in output
