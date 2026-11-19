import pytest

from local_agent.agents.stubs import StubExecutor, StubPlanner, StubReviewer
from local_agent.approval_manager import ApprovalManager
from local_agent.models.agent import ExecutionResult, ReviewResult
from local_agent.models.approval import ApprovalStatus
from local_agent.orchestrator import Orchestrator
from local_agent.state_manager import StateManager, TaskStatus


class RecordingExecutor(StubExecutor):
    def __init__(self) -> None:
        self.received_plan = None

    def execute(self, plan):
        self.received_plan = plan
        return super().execute(plan)


class RecordingReviewer(StubReviewer):
    def __init__(self) -> None:
        self.received_plan = None
        self.received_result = None

    def review(self, plan, result):
        self.received_plan = plan
        self.received_result = result
        return super().review(plan, result)


class FailingExecutor(StubExecutor):
    def execute(self, plan):
        return ExecutionResult(
            success=False,
            output="Execution failed",
            errors=["File creation failed"],
        )


class RaisingExecutor(StubExecutor):
    def execute(self, plan):
        raise RuntimeError("Executor failed")


class RaisingReviewer(StubReviewer):
    def review(self, plan, result):
        raise RuntimeError("Reviewer failed")


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


def test_approved_plan_is_passed_to_executor_and_reviewer(tmp_path) -> None:
    database_path = tmp_path / "agent.db"

    with StateManager(database_path) as state_manager, ApprovalManager(
            connection=state_manager.connection,
            state_manager=state_manager,
    ) as approval_manager:
        executor = RecordingExecutor()
        reviewer = RecordingReviewer()

        orchestrator = Orchestrator(
            state_manager=state_manager,
            approval_manager=approval_manager,
            planner=StubPlanner(),
            executor=executor,
            reviewer=reviewer,
        )

        approval = orchestrator.prepare_task("Create a test file")
        approval_manager.approve(approval.approval_id)

        orchestrator.execute_approved_plan(approval.approval_id)

        assert executor.received_plan is not None
        assert executor.received_plan.summary == (
            "Plan for: Create a test file"
        )

        assert reviewer.received_plan is executor.received_plan
        assert isinstance(reviewer.received_result, ExecutionResult)
        assert reviewer.received_result.success is True
        assert state_manager.get_state(approval.task_id) == TaskStatus.SUCCEEDED


def test_reviewer_rejects_failed_execution(tmp_path) -> None:
    database_path = tmp_path / "agent.db"

    with StateManager(database_path) as state_manager, ApprovalManager(
            connection=state_manager.connection,
            state_manager=state_manager,
    ) as approval_manager:
        orchestrator = Orchestrator(
            state_manager=state_manager,
            approval_manager=approval_manager,
            planner=StubPlanner(),
            executor=FailingExecutor(),
            reviewer=StubReviewer(),
        )

        approval = orchestrator.prepare_task("Create a test file")
        approval_manager.approve(approval.approval_id)

        review = orchestrator.execute_approved_plan(approval.approval_id)

        assert isinstance(review, ReviewResult)
        assert review.approved is False
        assert state_manager.get_state(approval.task_id) == TaskStatus.FAILED


def test_executor_exception_marks_task_failed(tmp_path) -> None:
    database_path = tmp_path / "agent.db"

    with StateManager(database_path) as state_manager, ApprovalManager(
            connection=state_manager.connection,
            state_manager=state_manager,
    ) as approval_manager:
        orchestrator = Orchestrator(
            state_manager=state_manager,
            approval_manager=approval_manager,
            planner=StubPlanner(),
            executor=RaisingExecutor(),
            reviewer=StubReviewer(),
        )

        approval = orchestrator.prepare_task("Create a test file")
        approval_manager.approve(approval.approval_id)

        with pytest.raises(RuntimeError, match="Executor failed"):
            orchestrator.execute_approved_plan(approval.approval_id)

        assert state_manager.get_state(approval.task_id) == TaskStatus.FAILED


def test_reviewer_exception_marks_task_failed(tmp_path) -> None:
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
            reviewer=RaisingReviewer(),
        )

        approval = orchestrator.prepare_task("Create a test file")
        approval_manager.approve(approval.approval_id)

        with pytest.raises(RuntimeError, match="Reviewer failed"):
            orchestrator.execute_approved_plan(approval.approval_id)

        assert state_manager.get_state(approval.task_id) == TaskStatus.FAILED


def test_rejected_plan_cancels_task(tmp_path) -> None:
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

        rejected = orchestrator.reject_plan(approval.approval_id)

        assert rejected.status == ApprovalStatus.REJECTED
        assert state_manager.get_state(approval.task_id) == TaskStatus.CANCELLED