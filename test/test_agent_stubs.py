from local_agent.agents.stubs import (
    StubExecutor,
    StubPlanner,
    StubReviewer,
    get_stub_agents,
)
from local_agent.models.agent import ExecutionResult
from local_agent.models.task import Task


def test_stub_planner_creates_plan() -> None:
    planner = StubPlanner()
    task = Task(task_id="test-task", description="Test task", status="CREATED")

    plan = planner.create_plan(task)

    assert plan.summary == "Plan for: Test task"
    assert len(plan.steps) == 1
    assert plan.steps[0].action == "create_file"
    assert plan.verification_criteria[0].type == "file_exists"
    assert plan.verification_criteria[0].parameters.path == "test.txt"


def test_stub_executor_returns_success() -> None:
    executor = StubExecutor()
    plan = StubPlanner().create_plan(
        Task(task_id="test-task", description="Test", status="CREATED")
    )

    result = executor.execute("test-task", plan)

    assert result.success is True
    assert "Executed 1 steps" in result.output


def test_stub_reviewer_approves_success() -> None:
    reviewer = StubReviewer()
    plan = StubPlanner().create_plan(
        Task(task_id="test-task", description="Test", status="CREATED")
    )

    result = reviewer.review(plan, ExecutionResult(success=True))

    assert result.approved is True


def test_stub_reviewer_rejects_failure() -> None:
    reviewer = StubReviewer()

    result = reviewer.review(
        StubPlanner().create_plan(
            Task(task_id="test-task", description="Test", status="CREATED")
        ),
        ExecutionResult(success=False, errors=["Test error"]),
    )

    assert result.approved is False
    assert result.feedback == "Execution failed"


def test_get_stub_agents() -> None:
    planner, executor, reviewer = get_stub_agents()

    assert planner is not None
    assert executor is not None
    assert reviewer is not None


def test_stub_planner_revises_plan() -> None:
    planner = StubPlanner()
    task = Task(task_id="test-task", description="Test task", status="CREATED")
    previous_plan = planner.create_plan(task)

    revised_plan = planner.revise_plan(
        task,
        previous_plan,
        "Add error handling",
    )

    assert revised_plan.summary == "Revised plan for: Test task"
    assert revised_plan.steps == previous_plan.steps
    assert (
        revised_plan.verification_criteria
        == previous_plan.verification_criteria
    )
