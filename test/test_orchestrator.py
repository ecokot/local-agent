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


class RaisingPlanner(StubPlanner):
    def create_plan(self, task):
        raise RuntimeError("Planner failed")


class CountingExecutor(StubExecutor):
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, plan):
        self.calls += 1
        return super().execute(plan)


class ApprovingReviewer(StubReviewer):
    def review(self, plan, result):
        return ReviewResult(approved=True)


class RejectingReviewer(StubReviewer):
    def review(self, plan, result):
        return ReviewResult(approved=False, feedback="Rejected by reviewer")


def _make_orchestrator(state_manager, approval_manager, *, planner=None,
                       executor=None, reviewer=None):
    return Orchestrator(
        state_manager=state_manager,
        approval_manager=approval_manager,
        planner=planner or StubPlanner(),
        executor=executor or CountingExecutor(),
        reviewer=reviewer or StubReviewer(),
    )


def test_planner_exception_marks_task_failed(tmp_path) -> None:
    with StateManager(tmp_path / "agent.db") as state_manager, ApprovalManager(
            connection=state_manager.connection,
            state_manager=state_manager,
    ) as approval_manager:
        orchestrator = _make_orchestrator(
            state_manager, approval_manager, planner=RaisingPlanner()
        )

        with pytest.raises(RuntimeError, match="Planner failed"):
            orchestrator.prepare_task("Fail during planning")

        tasks = state_manager.get_tasks()
        assert len(tasks) == 1
        assert tasks[0].status == TaskStatus.FAILED


def test_approval_request_exception_marks_task_failed(
        tmp_path, monkeypatch
) -> None:
    with StateManager(tmp_path / "agent.db") as state_manager, ApprovalManager(
            connection=state_manager.connection,
            state_manager=state_manager,
    ) as approval_manager:
        def fail_request(**kwargs):
            raise RuntimeError("Approval request failed")

        monkeypatch.setattr(approval_manager, "request_approval", fail_request)
        orchestrator = _make_orchestrator(state_manager, approval_manager)

        with pytest.raises(RuntimeError, match="Approval request failed"):
            orchestrator.prepare_task("Fail during approval request")

        tasks = state_manager.get_tasks()
        assert len(tasks) == 1
        assert tasks[0].status == TaskStatus.FAILED


def test_execution_failure_is_not_overridden_by_approved_review(
        tmp_path
) -> None:
    with StateManager(tmp_path / "agent.db") as state_manager, ApprovalManager(
            connection=state_manager.connection,
            state_manager=state_manager,
    ) as approval_manager:
        executor = FailingExecutor()
        reviewer = ApprovingReviewer()
        orchestrator = _make_orchestrator(
            state_manager, approval_manager,
            executor=executor, reviewer=reviewer,
        )
        approval = orchestrator.prepare_task("Execution must fail")
        approval_manager.approve(approval.approval_id)

        review = orchestrator.execute_approved_plan(approval.approval_id)

        assert review.approved is True
        assert state_manager.get_state(approval.task_id) == TaskStatus.FAILED


@pytest.mark.parametrize(
    "terminal_status",
    [TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED],
)
def test_terminal_task_cannot_execute_again(tmp_path, terminal_status) -> None:
    with StateManager(tmp_path / "agent.db") as state_manager, ApprovalManager(
            connection=state_manager.connection,
            state_manager=state_manager,
    ) as approval_manager:
        executor = CountingExecutor()
        orchestrator = _make_orchestrator(
            state_manager, approval_manager, executor=executor
        )
        approval = orchestrator.prepare_task("Terminal task")
        approval_manager.approve(approval.approval_id)

        state_manager.transition(approval.task_id, terminal_status)

        with pytest.raises(ValueError, match="state"):
            orchestrator.execute_approved_plan(approval.approval_id)

        assert executor.calls == 0


def test_wrong_approval_type_cannot_execute(tmp_path) -> None:
    with StateManager(tmp_path / "agent.db") as state_manager, ApprovalManager(
            connection=state_manager.connection,
            state_manager=state_manager,
    ) as approval_manager:
        executor = CountingExecutor()
        orchestrator = _make_orchestrator(
            state_manager, approval_manager, executor=executor
        )
        task_id = state_manager.create_task("Wrong approval type")
        approval = approval_manager.request_approval(
            task_id=task_id,
            action_type="OTHER_ACTION",
            parameters={},
        )
        approval_manager.approve(approval.approval_id)

        with pytest.raises(ValueError, match="not a plan approval"):
            orchestrator.execute_approved_plan(approval.approval_id)

        assert executor.calls == 0


def test_multiple_tasks_can_wait_for_approval(tmp_path) -> None:
    with StateManager(tmp_path / "agent.db") as state_manager, ApprovalManager(
            connection=state_manager.connection,
            state_manager=state_manager,
    ) as approval_manager:
        orchestrator = _make_orchestrator(state_manager, approval_manager)

        first = orchestrator.prepare_task("First task")
        second = orchestrator.prepare_task("Second task")

        assert first.task_id != second.task_id
        assert state_manager.get_state(first.task_id) == (
            TaskStatus.WAITING_FOR_APPROVAL
        )
        assert state_manager.get_state(second.task_id) == (
            TaskStatus.WAITING_FOR_APPROVAL
        )



def test_reviewer_rejection_marks_successful_execution_failed(tmp_path) -> None:
    with StateManager(tmp_path / "agent.db") as state_manager, ApprovalManager(
            connection=state_manager.connection,
            state_manager=state_manager,
    ) as approval_manager:
        orchestrator = _make_orchestrator(
            state_manager, approval_manager,
            reviewer=RejectingReviewer(),
        )
        approval = orchestrator.prepare_task("Reviewer rejects")
        approval_manager.approve(approval.approval_id)

        review = orchestrator.execute_approved_plan(approval.approval_id)

        assert review.approved is False
        assert state_manager.get_state(approval.task_id) == TaskStatus.FAILED
