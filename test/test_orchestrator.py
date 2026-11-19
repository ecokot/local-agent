
from local_agent.agents.stubs import StubExecutor, StubPlanner, StubReviewer
from local_agent.approval_manager import ApprovalManager
from local_agent.models.approval import ApprovalStatus
from local_agent.orchestrator import Orchestrator
from local_agent.state_manager import StateManager, TaskStatus


def test_prepare_task_creates_pending_plan_approval(tmp_path) -> None:
    database_path = tmp_path / "agent.db"

    with StateManager(database_path) as state_manager, ApprovalManager(
        connection=state_manager.connection,
        state_manager=state_manager,
    ) as approval_manager:
        orchestrator = Orchestrator(
            state_manager=state_manager,
            approval_manager=approval_manager,
            planner=StubPlanner(),
            executor=StubExecutor(),
            reviewer=StubReviewer(),
        )

        approval = orchestrator.prepare_task("Create a test file")

        assert approval.action_type == "PLAN_APPROVAL"
        assert approval.status == ApprovalStatus.PENDING
        assert approval.parameters["plan"]["summary"] == (
            "Plan for: Create a test file"
        )
        assert (
            state_manager.get_state(approval.task_id)
            == TaskStatus.WAITING_FOR_APPROVAL
        )

def test_task_cannot_execute_without_plan_approval(tmp_path) -> None:
    database_path = tmp_path / "agent.db"

    with StateManager(database_path) as state_manager, ApprovalManager(
        connection=state_manager.connection,
        state_manager=state_manager,
    ) as approval_manager:
        orchestrator = Orchestrator(
            state_manager=state_manager,
            approval_manager=approval_manager,
            planner=StubPlanner(),
            executor=StubExecutor(),
            reviewer=StubReviewer(),
        )

        approval = orchestrator.prepare_task("Create a test file")

        try:
            orchestrator.execute_approved_plan(approval.approval_id)
        except ValueError:
            pass
        else:
            raise AssertionError("Unapproved plan must not be executed")

        assert state_manager.get_state(approval.task_id) == (
            TaskStatus.WAITING_FOR_APPROVAL
        )

