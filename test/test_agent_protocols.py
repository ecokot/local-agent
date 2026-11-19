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

    def revise_plan(
        self,
        task: Task,
        previous_plan: Plan,
        feedback: str,
    ) -> Plan:
        return Plan(
            summary=f"Revised plan for: {task.description}",
            steps=previous_plan.steps,
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


def test_fake_planner_can_revise_plan() -> None:
    planner: Planner = FakePlanner()
    task = Task(
        task_id="test-task",
        description="Test",
        status="CREATED",
    )
    original_plan = planner.create_plan(task)

    revised_plan = planner.revise_plan(
        task,
        original_plan,
        "Add error handling",
    )

    assert revised_plan.summary == "Revised plan for: Test"
    assert revised_plan.steps == original_plan.steps
