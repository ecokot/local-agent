import pytest

from local_agent.agents.stubs import StubExecutor, StubPlanner, StubReviewer
from local_agent.approval_manager import ApprovalManager
from local_agent.models.agent import ExecutionResult, ReviewResult
from local_agent.execution.verification import CriterionVerificationResult, VerificationDetails, VerificationReport, VerificationStatus
from local_agent.models.approval import Approval, ApprovalStatus
from local_agent.models.plan import Plan
from local_agent.orchestrator import Orchestrator
from local_agent.state_manager import StateManager, TaskStatus


class PassingVerificationExecutor:
    def __init__(self):
        self.calls = 0

    def execute(self, plan):
        self.calls += 1
        results = tuple(
            CriterionVerificationResult(
                criterion_id=criterion.id,
                criterion_type=criterion.type,
                required=criterion.required,
                passed=True,
                status=VerificationStatus.PASSED,
                details=VerificationDetails(),
            )
            for criterion in plan.verification_criteria
        )
        return VerificationReport(
            results=results,
            all_required_passed=all(
                result.passed for result in results if result.required
            ),
        )


class RecordingExecutor(StubExecutor):
    def __init__(self) -> None:
        self.received_plan = None

    def execute(self, task_id, plan):
        self.received_plan = plan
        return super().execute(task_id, plan)


class RecordingReviewer(StubReviewer):
    def __init__(self) -> None:
        self.received_plan = None
        self.received_result = None

    def review(self, plan, result):
        self.received_plan = plan
        self.received_result = result
        return super().review(plan, result)


class FailingExecutor(StubExecutor):
    def execute(self, task_id, plan):
        return ExecutionResult(
            success=False,
            output="Execution failed",
            errors=["File creation failed"],
        )


class RaisingExecutor(StubExecutor):
    def execute(self, task_id, plan):
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
            verification_executor=PassingVerificationExecutor(),
            verification_executor=PassingVerificationExecutor(),
        )

        approval = orchestrator.prepare_task("Create a test file")

        assert approval.action_type == "PLAN_APPROVAL"
        assert approval.status == ApprovalStatus.PENDING
        assert approval.parameters["plan"]["summary"] == (
            "Plan for: Create a test file"
        )
        assert approval.parameters["plan"]["verification_criteria"][0] == {
            "id": "created-file",
            "type": "file_exists",
            "description": "Убедиться, что файл test.txt создан",
            "required": True,
            "parameters": {"path": "test.txt"},
        }
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
        executor = CountingExecutor()
        orchestrator = Orchestrator(
            state_manager=state_manager,
            approval_manager=approval_manager,
            planner=StubPlanner(),
            executor=executor,
            reviewer=StubReviewer(),
            verification_executor=PassingVerificationExecutor(),
        )

        approval = orchestrator.prepare_task("Create a test file")

        with pytest.raises(ValueError, match="not been approved"):
            orchestrator.execute_approved_plan(approval.approval_id)

        assert state_manager.get_state(approval.task_id) == (
            TaskStatus.WAITING_FOR_APPROVAL
        )
        assert executor.calls == 0


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
            verification_executor=PassingVerificationExecutor(),
            verification_executor=PassingVerificationExecutor(),
        )

        approval = orchestrator.prepare_task("Create a test file")
        approval_manager.approve(approval.approval_id)

        orchestrator.execute_approved_plan(approval.approval_id)

        assert executor.received_plan is not None
        assert executor.received_plan.summary == (
            "Plan for: Create a test file"
        )
        assert executor.received_plan.verification_criteria == Plan.model_validate(
            approval.parameters["plan"]
        ).verification_criteria

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
            verification_executor=PassingVerificationExecutor(),
        )

        approval = orchestrator.prepare_task("Create a test file")
        approval_manager.approve(approval.approval_id)

        review = orchestrator.execute_approved_plan(approval.approval_id)

        assert isinstance(review, ExecutionResult)
        assert review.success is False
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
            verification_executor=PassingVerificationExecutor(),
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
            verification_executor=PassingVerificationExecutor(),
            verification_executor=PassingVerificationExecutor(),
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
        executor = CountingExecutor()
        orchestrator = Orchestrator(
            state_manager=state_manager,
            approval_manager=approval_manager,
            planner=StubPlanner(),
            executor=executor,
            reviewer=StubReviewer(),
            verification_executor=PassingVerificationExecutor(),
        )
        approval = orchestrator.prepare_task("Create a test file")

        rejected = orchestrator.reject_plan(approval.approval_id)

        assert rejected.status == ApprovalStatus.REJECTED
        assert state_manager.get_state(approval.task_id) == TaskStatus.CANCELLED
        assert executor.calls == 0


class RaisingPlanner(StubPlanner):
    def create_plan(self, task):
        raise RuntimeError("Planner failed")


class CountingExecutor(StubExecutor):
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, task_id, plan):
        self.calls += 1
        return super().execute(task_id, plan)


class ApprovingReviewer(StubReviewer):
    def review(self, plan, result):
        return ReviewResult(approved=True)


class RejectingReviewer(StubReviewer):
    def review(self, plan, result):
        return ReviewResult(approved=False, feedback="Rejected by reviewer")


def _make_orchestrator(state_manager, approval_manager, *, planner=None,
                       executor=None, reviewer=None,
                       verification_executor=None):
    return Orchestrator(
        state_manager=state_manager,
        approval_manager=approval_manager,
        planner=planner or StubPlanner(),
        executor=executor or CountingExecutor(),
        reviewer=reviewer or StubReviewer(),
        verification_executor=verification_executor or PassingVerificationExecutor(),
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

        assert isinstance(review, ExecutionResult)
        assert review.success is False
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


def test_reviewer_rejection_requests_revised_plan_approval(
        tmp_path,
) -> None:
    with StateManager(tmp_path / "agent.db") as state_manager, ApprovalManager(
            connection=state_manager.connection,
            state_manager=state_manager,
    ) as approval_manager:
        orchestrator = _make_orchestrator(
            state_manager, approval_manager,
            reviewer=RejectingReviewer(),
            verification_executor=PassingVerificationExecutor(),
            verification_executor=PassingVerificationExecutor(),
        )
        approval = orchestrator.prepare_task("Reviewer rejects")
        approval_manager.approve(approval.approval_id)

        revised_approval = orchestrator.execute_approved_plan(
            approval.approval_id
        )

        assert isinstance(revised_approval, Approval)
        assert revised_approval.status == ApprovalStatus.PENDING
        assert revised_approval.parameters["revision"] == 1
        assert state_manager.get_state(approval.task_id) == (
            TaskStatus.WAITING_FOR_APPROVAL
        )


class RevisingPlanner(StubPlanner):
    def __init__(self):
        self.received_feedback = []

    def revise_plan(self, task, previous_plan, feedback):
        self.received_feedback.append(feedback)
        return Plan(
            summary=f"Revision {len(self.received_feedback)}",
            steps=previous_plan.steps,
            verification_criteria=previous_plan.verification_criteria,
        )


class SequenceReviewer:
    def __init__(self, results):
        self.results = iter(results)

    def review(self, plan, result):
        return next(self.results)


def test_reviewer_feedback_creates_new_pending_plan_approval(tmp_path):
    with StateManager(tmp_path / "agent.db") as state, ApprovalManager(
        connection=state.connection, state_manager=state
    ) as approvals:
        executor = CountingExecutor()
        planner = RevisingPlanner()
        reviewer = SequenceReviewer([
            ReviewResult(approved=False, feedback="Add validation"),
            ReviewResult(approved=True, feedback="Looks good"),
        ])
        orchestrator = _make_orchestrator(
            state, approvals, planner=planner,
            executor=executor, reviewer=reviewer,
        )

        first = orchestrator.prepare_task("Build feature")
        approvals.approve(first.approval_id)
        revised = orchestrator.execute_approved_plan(first.approval_id)

        assert revised.action_type == "PLAN_APPROVAL"
        assert revised.status == ApprovalStatus.PENDING
        assert revised.parameters["revision"] == 1
        assert revised.parameters["plan"]["verification_criteria"] == (
            first.parameters["plan"]["verification_criteria"]
        )
        assert planner.received_feedback == ["Add validation"]
        assert executor.calls == 1
        assert state.get_state(first.task_id) == TaskStatus.WAITING_FOR_APPROVAL

        approvals.approve(revised.approval_id)
        final_review = orchestrator.execute_approved_plan(revised.approval_id)

        assert final_review.approved is True
        assert executor.calls == 2
        assert state.get_state(first.task_id) == TaskStatus.SUCCEEDED
        history = state.get_review_history(first.task_id)
        assert [entry["feedback"] for entry in history] == [
            "Add validation", "Looks good"
        ]


def test_revised_plan_is_cancelled_when_user_rejects_it(tmp_path):
    with StateManager(tmp_path / "agent.db") as state, ApprovalManager(
        connection=state.connection, state_manager=state
    ) as approvals:
        executor = CountingExecutor()
        orchestrator = _make_orchestrator(
            state, approvals, executor=executor,
            planner=RevisingPlanner(),
            reviewer=RejectingReviewer(),
            verification_executor=PassingVerificationExecutor(),
        )

        initial = orchestrator.prepare_task("Build feature")
        approvals.approve(initial.approval_id)
        revised = orchestrator.execute_approved_plan(initial.approval_id)

        orchestrator.reject_plan(revised.approval_id)

        assert executor.calls == 1
        assert state.get_state(initial.task_id) == TaskStatus.CANCELLED


def test_plan_revision_limit_stops_after_two_revisions(tmp_path):
    with StateManager(tmp_path / "agent.db") as state, ApprovalManager(
        connection=state.connection, state_manager=state
    ) as approvals:
        executor = CountingExecutor()
        planner = RevisingPlanner()
        orchestrator = _make_orchestrator(
            state, approvals, executor=executor,
            planner=planner, reviewer=RejectingReviewer(),
        )

        approval = orchestrator.prepare_task("Build feature")
        task_id = approval.task_id

        for expected_revision in (1, 2):
            approvals.approve(approval.approval_id)
            approval = orchestrator.execute_approved_plan(
                approval.approval_id
            )
            assert approval.parameters["revision"] == expected_revision
            assert approval.status == ApprovalStatus.PENDING

        approvals.approve(approval.approval_id)
        final_review = orchestrator.execute_approved_plan(
            approval.approval_id
        )

        assert isinstance(final_review, ReviewResult)
        assert final_review.approved is False
        assert len(planner.received_feedback) == 2
        assert executor.calls == 3
        assert state.get_state(task_id) == TaskStatus.FAILED
        assert len(state.get_review_history(task_id)) == 3


def test_revised_plan_approval_is_required_before_second_execution(tmp_path):
    with StateManager(tmp_path / "agent.db") as state, ApprovalManager(
        connection=state.connection, state_manager=state
    ) as approvals:
        executor = CountingExecutor()
        orchestrator = _make_orchestrator(
            state, approvals, executor=executor,
            planner=RevisingPlanner(), reviewer=RejectingReviewer(),
        )

        initial = orchestrator.prepare_task("Build feature")
        approvals.approve(initial.approval_id)
        revised = orchestrator.execute_approved_plan(initial.approval_id)

        with pytest.raises(ValueError, match="not been approved"):
            orchestrator.execute_approved_plan(revised.approval_id)

        assert executor.calls == 1
        assert state.get_state(initial.task_id) == TaskStatus.WAITING_FOR_APPROVAL


class RaisingRevisingPlanner(StubPlanner):
    def revise_plan(self, task, previous_plan, feedback):
        raise RuntimeError("Plan revision failed")


def test_revision_planner_exception_marks_task_failed_and_keeps_feedback(
    tmp_path,
):
    with StateManager(tmp_path / "agent.db") as state, ApprovalManager(
        connection=state.connection, state_manager=state
    ) as approvals:
        orchestrator = _make_orchestrator(
            state, approvals,
            planner=RaisingRevisingPlanner(),
            reviewer=RejectingReviewer(),
            verification_executor=PassingVerificationExecutor(),
        )
        initial = orchestrator.prepare_task("Planner revision failure")
        approvals.approve(initial.approval_id)

        with pytest.raises(RuntimeError, match="Plan revision failed"):
            orchestrator.execute_approved_plan(initial.approval_id)

        assert state.get_state(initial.task_id) == TaskStatus.FAILED
        history = state.get_review_history(initial.task_id)
        assert len(history) == 1
        assert history[0]["feedback"] == "Rejected by reviewer"
