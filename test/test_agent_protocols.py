from local_agent.agents.protocols import Executor, Planner, Reviewer
from local_agent.models.agent import ExecutionResult, ReviewResult
from local_agent.models.plan import Plan
from local_agent.models.task import Task


class FakePlanner:
    def create_plan(self, task: Task) -> Plan:
        return Plan(
            summary="Test plan",
            steps=[
                {
                    "description": "Create file",
                    "action": "create_file",
                    "parameters": {"path": "test.txt"},
                }
            ],
        )


class FakeExecutor:
    def execute(self, plan: Plan) -> ExecutionResult:
        return ExecutionResult(success=True, output="Done")


class FakeReviewer:
    def review(
        self,
        plan: Plan,
        result: ExecutionResult,
    ) -> ReviewResult:
        return ReviewResult(approved=True, feedback="Looks good")


def test_fake_planner_matches_protocol() -> None:
    planner: Planner = FakePlanner()

    plan = planner.create_plan(
        Task(task_id="test-task", description="Test", status="CREATED")
    )

    assert plan.summary == "Test plan"


def test_fake_executor_matches_protocol() -> None:
    executor: Executor = FakeExecutor()
    plan = Plan(
        summary="Test plan",
        steps=[
            {
                "description": "Create file",
                "action": "create_file",
                "parameters": {"path": "test.txt"},
            }
        ],
    )

    result = executor.execute(plan)

    assert result.success is True


def test_fake_reviewer_matches_protocol() -> None:
    reviewer: Reviewer = FakeReviewer()
    plan = Plan(
        summary="Test plan",
        steps=[
            {
                "description": "Create file",
                "action": "create_file",
                "parameters": {"path": "test.txt"},
            }
        ],
    )

    result = reviewer.review(plan, ExecutionResult(success=True))

    assert result.approved is True