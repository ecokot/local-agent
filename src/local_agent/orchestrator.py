from local_agent.agents.protocols import Executor, Planner, Reviewer
from local_agent.approval_manager import ApprovalManager
from local_agent.models.approval import Approval, ApprovalStatus
from local_agent.models.plan import Plan
from local_agent.state_manager import StateManager


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

        task = self.state_manager.get_task(task_id)
        plan: Plan = self.planner.create_plan(task)

        return self.approval_manager.request_approval(
            task_id=task_id,
            action_type="PLAN_APPROVAL",
            parameters={
                "plan": plan.model_dump(mode="json"),
            },
        )
    def execute_approved_plan(self, approval_id: str) -> None:
        approval = self.approval_manager.get_approval(approval_id)

        if approval.action_type != "PLAN_APPROVAL":
            raise ValueError("Approval is not a plan approval")

        if approval.status != ApprovalStatus.APPROVED:
            raise ValueError("Plan has not been approved")
