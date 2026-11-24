from local_agent.agents.protocols import Executor, Planner, Reviewer
from local_agent.approval_manager import ApprovalManager
from local_agent.models.approval import Approval, ApprovalStatus
from local_agent.models.plan import Plan
from local_agent.state_manager import StateManager, TaskStatus


class Orchestrator:
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
                },
            )
        except Exception:
            if self.state_manager.get_state(task_id) == TaskStatus.CREATED:
                self.state_manager.transition(task_id, TaskStatus.FAILED)
            raise

    def execute_approved_plan(self, approval_id: str):
        approval = self.approval_manager.get_approval(approval_id)

        if approval.action_type != "PLAN_APPROVAL":
            raise ValueError("Approval is not a plan approval")

        if approval.status != ApprovalStatus.APPROVED:
            raise ValueError("Plan has not been approved")

        if self.state_manager.get_state(approval.task_id) != TaskStatus.RUNNING:
            raise ValueError("Task is not in a state that allows execution")

        plan = Plan.model_validate(approval.parameters["plan"])

        try:
            result = self.executor.execute(plan)
        except Exception:
            self.state_manager.transition(
                approval.task_id,
                TaskStatus.FAILED,
            )
            raise

        try:
            review = self.reviewer.review(plan, result)
        except Exception:
            self.state_manager.transition(
                approval.task_id,
                TaskStatus.FAILED,
            )
            raise

        if result.success and review.approved:
            final_status = TaskStatus.SUCCEEDED
        else:
            final_status = TaskStatus.FAILED

        self.state_manager.transition(approval.task_id, final_status)
        return review

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
