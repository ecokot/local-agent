import pytest

from local_agent.agents.stubs import StubExecutor, StubPlanner
from local_agent.approval_manager import ApprovalManager
from local_agent.execution.verification import (
    CriterionVerificationResult,
    VerificationDetails,
    VerificationReport,
    VerificationStatus,
)
from local_agent.models.agent import ExecutionResult, ReviewResult
from local_agent.models.approval import Approval, ApprovalStatus
from local_agent.models.plan import Plan
from local_agent.orchestrator import Orchestrator
from local_agent.state_manager import StateManager, TaskStatus


class FakeVerificationExecutor:
    def __init__(self, statuses=None, reports=None):
        self.statuses = statuses
        self.reports = iter(reports) if reports is not None else None
        self.calls = 0

    def execute(self, plan):
        self.calls += 1
        if self.reports is not None:
            return next(self.reports)
        statuses = self.statuses or [
            VerificationStatus.PASSED
            for _ in plan.verification_criteria
        ]
        results = tuple(
            CriterionVerificationResult(
                criterion_id=criterion.id,
                criterion_type=criterion.type,
                required=criterion.required,
                passed=status is VerificationStatus.PASSED,
                status=status,
                details=VerificationDetails(),
                diagnostic=(
                    None if status is VerificationStatus.PASSED
                    else f"{status.value} diagnostic"
                ),
            )
            for criterion, status in zip(
                plan.verification_criteria, statuses, strict=True
            )
        )
        return VerificationReport(
            results=results,
            all_required_passed=all(
                result.passed for result in results if result.required
            ),
        )


class ApprovingReviewer:
    def __init__(self):
        self.received = None

    def review(self, plan, result):
        self.received = result
        return ReviewResult(approved=True)


class RejectingReviewer:
    def review(self, plan, result):
        return ReviewResult(approved=False, feedback="Please revise")


class TwoStageReviewer:
    def __init__(self):
        self.calls = 0
        self.reports = []

    def review(self, plan, result):
        self.calls += 1
        self.reports.append(result.verification_report)
        if self.calls == 1:
            return ReviewResult(approved=False, feedback="Revise")
        return ReviewResult(approved=True)


class RevisingPlanner(StubPlanner):
    def revise_plan(self, task, previous_plan, feedback):
        return Plan(
            summary="Revised plan",
            steps=previous_plan.steps,
            verification_criteria=previous_plan.verification_criteria,
        )


class WaitingExecutor(StubExecutor):
    def execute(self, task_id, plan):
        return ExecutionResult(
            success=False,
            waiting_for_approval=True,
            pending_approval_id="pending-action",
        )


class FailedExecutor(StubExecutor):
    def execute(self, task_id, plan):
        return ExecutionResult(success=False, errors=["step failed"])


def _setup(tmp_path, *, verifier, reviewer=None, executor=None, planner=None):
    state = StateManager(tmp_path / "orchestrator-verification.db")
    approvals = ApprovalManager(
        connection=state.connection,
        state_manager=state,
    )
    orchestrator = Orchestrator(
        state_manager=state,
        approval_manager=approvals,
        planner=planner or StubPlanner(),
        executor=executor or StubExecutor(),
        reviewer=reviewer or ApprovingReviewer(),
        verification_executor=verifier,
    )
    approval = orchestrator.prepare_task("Verify implementation")
    approvals.approve(approval.approval_id)
    return state, approvals, orchestrator, approval


def _close(state, approvals):
    approvals.close()
    state.close()


def test_full_success_requires_execution_verification_and_reviewer(tmp_path):
    reviewer = ApprovingReviewer()
    verifier = FakeVerificationExecutor()
    state, approvals, orchestrator, approval = _setup(
        tmp_path, verifier=verifier, reviewer=reviewer
    )
    try:
        result = orchestrator.execute_approved_plan(approval.approval_id)
        assert result.approved is True
        assert reviewer.received.verification_report is not None
        assert verifier.calls == 1
        assert state.get_state(approval.task_id) is TaskStatus.SUCCEEDED
    finally:
        _close(state, approvals)


@pytest.mark.parametrize(
    "status",
    [
        VerificationStatus.FAILED,
        VerificationStatus.TIMED_OUT,
        VerificationStatus.ERROR,
    ],
)
def test_required_check_failure_blocks_success_even_when_reviewer_approves(
    tmp_path, status
):
    verifier = FakeVerificationExecutor([status])
    state, approvals, orchestrator, approval = _setup(
        tmp_path, verifier=verifier, reviewer=ApprovingReviewer()
    )
    try:
        result = orchestrator.execute_approved_plan(approval.approval_id)
        assert result.approved is True
        check = result.verification_report.results[0]
        assert check.status is status
        if status is VerificationStatus.ERROR:
            assert check.diagnostic == "error diagnostic"
        assert state.get_state(approval.task_id) is TaskStatus.FAILED
    finally:
        _close(state, approvals)


def test_optional_check_error_does_not_block_success(tmp_path):
    class OptionalPlanner(StubPlanner):
        def create_plan(self, task):
            return Plan(
                summary="Required and optional checks",
                steps=super().create_plan(task).steps,
                verification_criteria=[
                    {
                        "id": "required",
                        "type": "file_exists",
                        "description": "Required artifact",
                        "parameters": {"path": "required.txt"},
                    },
                    {
                        "id": "optional",
                        "type": "ruff",
                        "description": "Optional lint",
                        "required": False,
                        "parameters": {},
                    },
                ],
            )

    verifier = FakeVerificationExecutor([
        VerificationStatus.PASSED,
        VerificationStatus.ERROR,
    ])
    state, approvals, orchestrator, approval = _setup(
        tmp_path, verifier=verifier, planner=OptionalPlanner()
    )
    try:
        result = orchestrator.execute_approved_plan(approval.approval_id)
        assert result.verification_report.results[1].diagnostic == (
            "error diagnostic"
        )
        assert result.verification_report.all_required_passed is True
        assert state.get_state(approval.task_id) is TaskStatus.SUCCEEDED
    finally:
        _close(state, approvals)


def test_reviewer_rejection_prevents_success(tmp_path):
    state, approvals, orchestrator, approval = _setup(
        tmp_path,
        verifier=FakeVerificationExecutor(),
        reviewer=RejectingReviewer(),
    )
    try:
        result = orchestrator.execute_approved_plan(approval.approval_id)
        assert isinstance(result, Approval)
        assert result.status is ApprovalStatus.PENDING
        assert result.parameters["revision"] == 1
        assert state.get_state(approval.task_id) is TaskStatus.WAITING_FOR_APPROVAL
    finally:
        _close(state, approvals)


@pytest.mark.parametrize("executor", [WaitingExecutor(), FailedExecutor()])
def test_verification_is_skipped_when_execution_is_not_complete(
    tmp_path, executor
):
    verifier = FakeVerificationExecutor()
    state, approvals, orchestrator, approval = _setup(
        tmp_path, verifier=verifier, executor=executor
    )
    try:
        result = orchestrator.execute_approved_plan(approval.approval_id)
        assert verifier.calls == 0
        if result.waiting_for_approval:
            assert state.get_state(approval.task_id) is TaskStatus.RUNNING
        else:
            assert state.get_state(approval.task_id) is TaskStatus.FAILED
    finally:
        _close(state, approvals)


def test_revised_execution_gets_a_fresh_verification_report(tmp_path):
    reviewer = TwoStageReviewer()
    verifier = FakeVerificationExecutor([
        VerificationStatus.PASSED,
    ])
    state, approvals, orchestrator, approval = _setup(
        tmp_path,
        verifier=verifier,
        reviewer=reviewer,
        planner=RevisingPlanner(),
    )
    try:
        first_review = orchestrator.execute_approved_plan(approval.approval_id)
        assert isinstance(first_review, Approval)
        assert verifier.calls == 1

        approvals.approve(first_review.approval_id)
        final_review = orchestrator.execute_approved_plan(
            first_review.approval_id
        )
        assert final_review.approved is True
        assert verifier.calls == 2
        assert reviewer.reports[0] is not reviewer.reports[1]
        assert state.get_state(approval.task_id) is TaskStatus.SUCCEEDED
    finally:
        _close(state, approvals)


def test_invalid_or_incomplete_verification_report_fails_closed(tmp_path):
    invalid_report = VerificationReport(results=(), all_required_passed=True)
    verifier = FakeVerificationExecutor(reports=[invalid_report])
    state, approvals, orchestrator, approval = _setup(
        tmp_path, verifier=verifier
    )
    try:
        with pytest.raises(ValueError, match="exactly one result"):
            orchestrator.execute_approved_plan(approval.approval_id)
        assert state.get_state(approval.task_id) is TaskStatus.FAILED
    finally:
        _close(state, approvals)
