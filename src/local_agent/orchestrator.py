from local_agent.agents.protocols import Executor, Planner, Reviewer
from local_agent.approval_manager import ApprovalManager
from local_agent.models.agent import ExecutionResult, ReviewResult
from local_agent.models.approval import Approval, ApprovalStatus
from local_agent.models.plan import Plan
from local_agent.execution.verification import (CriterionVerificationResult, VerificationExecutor, VerificationReport, VerificationStatus)
from local_agent.state_manager import StateManager, TaskStatus


class Orchestrator:
    MAX_PLAN_REVISIONS = 2

    def __init__(
            self,
            state_manager: StateManager,
            approval_manager: ApprovalManager,
            planner: Planner,
            executor: Executor,
            reviewer: Reviewer,
            verification_executor: VerificationExecutor,
    ) -> None:
        self.state_manager = state_manager
        self.approval_manager = approval_manager
        self.planner = planner
        self.executor = executor
        self.reviewer = reviewer
        self.verification_executor = verification_executor

    def prepare_task(self, description: str) -> Approval:
        task_id = self.state_manager.create_task(description)

        try:
            task = self.state_manager.get_task(task_id)
            plan: Plan = self.planner.create_plan(task)

            return self.approval_manager.request_approval(
                task_id=task_id,
                action_type="PLAN_APPROVAL",
                parameters={
                    "plan": plan.model_dump(mode="json"),
                    "revision": 0,
                },
            )
        except Exception:
            if self.state_manager.get_state(task_id) == TaskStatus.CREATED:
                self.state_manager.transition(task_id, TaskStatus.FAILED)
            raise

    def execute_approved_plan(
            self,
            approval_id: str,
    ) -> Approval | ReviewResult | ExecutionResult:
        approval = self.approval_manager.get_approval(approval_id)

        if approval.action_type != "PLAN_APPROVAL":
            raise ValueError("Approval is not a plan approval")

        if approval.status != ApprovalStatus.APPROVED:
            raise ValueError("Plan has not been approved")

        task_id = approval.task_id
        if self.state_manager.get_state(task_id) != TaskStatus.RUNNING:
            raise ValueError("Task is not in a state that allows execution")

        plan = Plan.model_validate(approval.parameters["plan"])

        try:
            result = self.executor.execute(task_id, plan)
        except Exception:
            self.state_manager.transition(task_id, TaskStatus.FAILED)
            self.clear_execution_progress(task_id)
            raise

        if result.waiting_for_approval:
            # Keep the task and executor progress suspended. Reviewer must not
            # see a partial plan as a completed execution.
            return result

        if not result.success:
            self.state_manager.transition(task_id, TaskStatus.FAILED)
            self.clear_execution_progress(task_id)
            return result

        try:
            verification_report = self.verification_executor.execute(plan)
            self._validate_verification_report(plan, verification_report)
        except Exception:
            self.state_manager.transition(task_id, TaskStatus.FAILED)
            self.clear_execution_progress(task_id)
            raise

        result = result.model_copy(
            update={"verification_report": verification_report}
        )

        try:
            review = self.reviewer.review(plan, result)
        except Exception:
            self.state_manager.transition(task_id, TaskStatus.FAILED)
            self.clear_execution_progress(task_id)
            raise

        # Keep the Python-produced report authoritative if Reviewer returns
        # a modified result object.
        review = review.model_copy(
            update={"verification_report": verification_report}
        )

        revision = approval.parameters.get("revision", 0)
        try:
            self.state_manager.record_review(
                task_id=task_id,
                revision=revision,
                plan=plan.model_dump(mode="json"),
                approved=review.approved,
                feedback=review.feedback,
            )
        except Exception:
            self.state_manager.transition(task_id, TaskStatus.FAILED)
            self.clear_execution_progress(task_id)
            raise

        if review.approved:
            final_status = (
                TaskStatus.SUCCEEDED
                if verification_report.all_required_passed
                else TaskStatus.FAILED
            )
            self.state_manager.transition(task_id, final_status)
            self.clear_execution_progress(task_id)
            return review

        if revision >= self.MAX_PLAN_REVISIONS:
            self.state_manager.transition(task_id, TaskStatus.FAILED)
            self.clear_execution_progress(task_id)
            return review

        try:
            task = self.state_manager.get_task(task_id)
            revised_plan = self.planner.revise_plan(
                task,
                plan,
                review.feedback,
            )
            return self.approval_manager.request_approval(
                task_id=task_id,
                action_type="PLAN_APPROVAL",
                parameters={
                    "plan": revised_plan.model_dump(mode="json"),
                    "revision": revision + 1,
                },
            )
        except Exception:
            if self.state_manager.get_state(task_id) == TaskStatus.RUNNING:
                self.state_manager.transition(task_id, TaskStatus.FAILED)
            self.clear_execution_progress(task_id)
            raise


    @staticmethod
    def _validate_verification_report(
        plan: Plan,
        report: VerificationReport,
    ) -> None:
        if not isinstance(report, VerificationReport):
            raise TypeError(
                "VerificationExecutor must return a VerificationReport"
            )
        criteria = plan.verification_criteria
        if not criteria or not any(item.required for item in criteria):
            raise ValueError("Plan must contain required verification criteria")
        if len(report.results) != len(criteria):
            raise ValueError(
                "Verification report must contain exactly one result "
                "for every plan criterion"
            )

        required_results: list[CriterionVerificationResult] = []
        for criterion, result in zip(criteria, report.results, strict=True):
            if not isinstance(result, CriterionVerificationResult):
                raise TypeError("Invalid criterion verification result")
            if (
                result.criterion_id != criterion.id
                or result.criterion_type != criterion.type
                or result.required is not criterion.required
            ):
                raise ValueError(
                    "Verification result does not match its plan criterion"
                )
            if not isinstance(result.status, VerificationStatus):
                raise TypeError("Verification result has an invalid status")
            if result.passed is not (
                result.status is VerificationStatus.PASSED
            ):
                raise ValueError(
                    "Verification result passed flag conflicts with status"
                )
            if result.required:
                required_results.append(result)

        all_required_passed = all(item.passed for item in required_results)
        if report.all_required_passed is not all_required_passed:
            raise ValueError(
                "Verification report summary conflicts with criterion results"
            )

    def reject_plan(self, approval_id: str) -> Approval:
        approval = self.approval_manager.get_approval(approval_id)

        if approval.action_type != "PLAN_APPROVAL":
            raise ValueError("Approval is not a plan approval")

        if approval.status != ApprovalStatus.PENDING:
            raise ValueError("Plan approval is not pending")

        if (
            self.state_manager.get_state(approval.task_id)
            != TaskStatus.WAITING_FOR_APPROVAL
        ):
            raise ValueError("Task is not waiting for plan approval")

        rejected = self.approval_manager.reject(approval_id)
        self.clear_execution_progress(approval.task_id)

        self.state_manager.transition(
            approval.task_id,
            TaskStatus.CANCELLED,
        )
        return rejected

    def clear_execution_progress(self, task_id: str) -> None:
        clear_progress = getattr(self.executor, "clear_progress", None)
        if callable(clear_progress):
            clear_progress(task_id)
