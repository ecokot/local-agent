from local_agent.agents.protocols import Executor, Planner, Reviewer
from local_agent.models.agent import ExecutionResult, ReviewResult
from local_agent.models.plan import Plan
from local_agent.models.task import Task


class StubPlanner:
    def create_plan(self, task: Task) -> Plan:
        return Plan(
            summary=f"Plan for: {task.description}",
            steps=[
                {
                    "description": "Create test file",
                    "action": "create_file",
                    "parameters": {"path": "test.txt", "content": "test"},
                }
            ],
            verification_criteria=[
                {
                    "id": "created-file",
                    "type": "file_exists",
                    "description": "Убедиться, что файл test.txt создан",
                    "parameters": {"path": "test.txt"},
                }
            ],
        )

    def revise_plan(
        self,
        task: Task,
        previous_plan: Plan,
        feedback: str,
    ) -> Plan:
        return Plan(
            summary=f"Revised plan for: {task.description}",
            steps=previous_plan.steps,
            verification_criteria=previous_plan.verification_criteria,
        )


class StubExecutor:
    def execute(self, task_id: str, plan: Plan) -> ExecutionResult:
        return ExecutionResult(
            success=True,
            output=f"Executed {len(plan.steps)} steps (stub)",
        )


class StubReviewer:
    def review(
        self,
        plan: Plan,
        result: ExecutionResult,
    ) -> ReviewResult:
        if not result.success:
            return ReviewResult(
                approved=False,
                feedback="Execution failed",
            )

        return ReviewResult(
            approved=True,
            feedback="Execution result accepted (stub)",
        )


# Проверка совместимости реализаций с контрактами
def get_stub_agents() -> tuple[Planner, Executor, Reviewer]:
    return StubPlanner(), StubExecutor(), StubReviewer()
