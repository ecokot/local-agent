from local_agent.agents.protocols import Executor, Planner, Reviewer
from local_agent.approval_manager import ApprovalManager
from local_agent.models.agent import ExecutionResult, ReviewResult
from local_agent.models.approval import Approval, ApprovalStatus
from local_agent.models.plan import Plan
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
    ) -> None:
        self.state_manager = state_manager
        self.approval_manager = approval_manager
        self.planner = planner
        self.executor = executor
        self.reviewer = reviewer

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
            raise

        if result.waiting_for_approval:
            # Keep the task and executor progress suspended. Reviewer must not
            # see a partial plan as a completed execution.
            return result

        if not result.success:
            self.state_manager.transition(task_id, TaskStatus.FAILED)
            return result

        try:
            review = self.reviewer.review(plan, result)
        except Exception:
            self.state_manager.transition(task_id, TaskStatus.FAILED)
            raise

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
            raise

        if review.approved:
            self.state_manager.transition(task_id, TaskStatus.SUCCEEDED)
            return review

        if revision >= self.MAX_PLAN_REVISIONS:
            self.state_manager.transition(task_id, TaskStatus.FAILED)
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
            raise

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

        self.state_manager.transition(
            approval.task_id,
            TaskStatus.CANCELLED,
        )
        return rejected
