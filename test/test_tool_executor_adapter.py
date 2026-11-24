from local_agent.agents.stubs import StubPlanner
from local_agent.agents.tool_executor_adapter import ToolExecutorAdapter
from local_agent.approval_manager import ApprovalManager
from local_agent.execution.models import ToolExecutionResult, ToolExecutionStatus
from local_agent.execution.recorder import ExecutionRecorder
from local_agent.execution.tool_executor import ToolExecutor
from local_agent.execution.verification import (
    CriterionVerificationResult,
    VerificationDetails,
    VerificationReport,
    VerificationStatus,
)
from local_agent.models.agent import ReviewResult
from local_agent.models.plan import Plan
from local_agent.orchestrator import Orchestrator
from local_agent.state_manager import StateManager, TaskStatus
from local_agent.user_interface import UserInterface


class PassingVerificationExecutor:
    def execute(self, plan):
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
            all_required_passed=True,
        )


class FailingVerificationExecutor:
    def execute(self, plan):
        results = tuple(
            CriterionVerificationResult(
                criterion_id=criterion.id,
                criterion_type=criterion.type,
                required=criterion.required,
                passed=False,
                status=VerificationStatus.FAILED,
                details=VerificationDetails(),
                diagnostic="Required file does not exist",
            )
            for criterion in plan.verification_criteria
        )
        return VerificationReport(
            results=results,
            all_required_passed=False,
        )


class FakeTool:
    def __init__(self, name):
        self.name = name
        self.calls = []

    def execute(self, **parameters):
        self.calls.append(parameters)
        return {"tool": self.name, "parameters": parameters}


def build_environment(tmp_path):
    database = tmp_path / "agent.db"
    state = StateManager(database)
    approvals = ApprovalManager(
        connection=state.connection,
        state_manager=state,
    )
    recorder = ExecutionRecorder(database, tmp_path / "traces")
    tools = {
        name: FakeTool(name)
        for name in (
            "list_directory",
            "read_file",
            "create_file",
            "write_file",
            "get_diff",
            "run_command",
            "run_tests",
        )
    }
    tool_executor = ToolExecutor(
        approval_manager=approvals,
        execution_recorder=recorder,
        **tools,
    )
    adapter = ToolExecutorAdapter(tool_executor)
    task_id = state.create_task("Execute approved plan")
    state.transition(task_id, TaskStatus.RUNNING)
    return state, approvals, recorder, tools, adapter, task_id


def close_environment(environment):
    state, approvals, recorder, *_ = environment
    recorder.close()
    approvals.close()
    state.close()


def test_executes_plan_steps_in_order_and_returns_real_results(tmp_path):
    environment = build_environment(tmp_path)
    state, approvals, recorder, tools, adapter, task_id = environment

    try:
        plan = Plan(
            summary="Inspect and create",
            verification_criteria=[
                {
                    "id": "tests",
                    "type": "pytest",
                    "description": "Run the project test suite",
                    "parameters": {},
                }
            ],
            steps=[
                {
                    "description": "List files",
                    "action": "list_directory",
                    "parameters": {},
                },
                {
                    "description": "Create file",
                    "action": "create_file",
                    "parameters": {"path": "result.txt", "content": "hello"},
                },
            ],
        )

        waiting = adapter.execute(task_id, plan)

        assert waiting.waiting_for_approval is True
        assert waiting.pending_approval_id is not None
        assert tools["list_directory"].calls == [{}]
        assert tools["create_file"].calls == []
        assert state.get_state(task_id) == TaskStatus.WAITING_FOR_APPROVAL

        approvals.approve(waiting.pending_approval_id)
        completed = adapter.execute(task_id, plan)

        assert completed.success is True
        assert completed.waiting_for_approval is False
        assert len(completed.step_results) == 2
        assert tools["list_directory"].calls == [{}]
        assert tools["create_file"].calls == [
            {"path": "result.txt", "content": "hello"}
        ]
        assert len(recorder.get_executions_for_task(task_id)) == 2
    finally:
        close_environment(environment)


def test_rejected_action_marks_task_failed_and_never_runs_tool(tmp_path):
    environment = build_environment(tmp_path)
    state, approvals, _, tools, adapter, task_id = environment

    try:
        plan = Plan(
            summary="Create file",
            verification_criteria=[
                {
                    "id": "tests",
                    "type": "pytest",
                    "description": "Run the project test suite",
                    "parameters": {},
                }
            ],
            steps=[
                {
                    "description": "Create file",
                    "action": "create_file",
                    "parameters": {"path": "result.txt", "content": "hello"},
                }
            ],
        )

        waiting = adapter.execute(task_id, plan)
        approvals.reject(waiting.pending_approval_id)

        assert state.get_state(task_id) == TaskStatus.FAILED
        assert tools["create_file"].calls == []
    finally:
        close_environment(environment)


def test_unknown_tool_fails_without_running_any_tool(tmp_path):
    environment = build_environment(tmp_path)
    _, _, _, tools, adapter, task_id = environment

    try:
        plan = Plan(
            summary="Unknown tool",
            verification_criteria=[
                {
                    "id": "tests",
                    "type": "pytest",
                    "description": "Run the project test suite",
                    "parameters": {},
                }
            ],
            steps=[
                {
                    "description": "Run unknown tool",
                    "action": "delete_everything",
                    "parameters": {},
                }
            ],
        )

        result = adapter.execute(task_id, plan)

        assert result.success is False
        assert result.errors
        assert all(not tool.calls for tool in tools.values())
    finally:
        close_environment(environment)


def test_changed_plan_is_rejected_while_execution_is_suspended(tmp_path):
    environment = build_environment(tmp_path)
    _, _, _, tools, adapter, task_id = environment

    try:
        original = Plan(
            summary="Original",
            verification_criteria=[
                {
                    "id": "tests",
                    "type": "pytest",
                    "description": "Run the project test suite",
                    "parameters": {},
                }
            ],
            steps=[
                {
                    "description": "Create file",
                    "action": "create_file",
                    "parameters": {"path": "result.txt", "content": "hello"},
                }
            ],
        )
        changed = Plan(
            summary="Changed",
            verification_criteria=[
                {
                    "id": "tests",
                    "type": "pytest",
                    "description": "Run the project test suite",
                    "parameters": {},
                }
            ],
            steps=[
                {
                    "description": "Create another file",
                    "action": "create_file",
                    "parameters": {"path": "other.txt", "content": "hello"},
                }
            ],
        )

        waiting = adapter.execute(task_id, original)
        result = adapter.execute(task_id, changed)

        assert waiting.waiting_for_approval is True
        assert result.success is False
        assert "plan changed" in result.errors[0].lower()
        assert tools["create_file"].calls == []
    finally:
        close_environment(environment)



def test_changed_criteria_are_rejected_while_execution_is_suspended(
    tmp_path,
):
    environment = build_environment(tmp_path)
    _, _, _, tools, adapter, task_id = environment

    try:
        original = Plan(
            summary="Create file",
            verification_criteria=[
                {
                    "id": "tests",
                    "type": "pytest",
                    "description": "Run the project test suite",
                    "parameters": {},
                }
            ],
            steps=[
                {
                    "description": "Create file",
                    "action": "create_file",
                    "parameters": {"path": "result.txt", "content": "hello"},
                }
            ],
        )
        changed = Plan(
            summary="Create file",
            verification_criteria=[
                {
                    "id": "tests",
                    "type": "pytest",
                    "description": "Run the updated project test suite",
                    "parameters": {},
                }
            ],
            steps=[
                {
                    "description": "Create file",
                    "action": "create_file",
                    "parameters": {"path": "result.txt", "content": "hello"},
                }
            ],
        )

        waiting = adapter.execute(task_id, original)
        result = adapter.execute(task_id, changed)

        assert waiting.waiting_for_approval is True
        assert waiting.pending_approval_id is not None
        assert result.success is False
        assert result.waiting_for_approval is False
        assert "plan changed" in result.errors[0].lower()
        assert tools["create_file"].calls == []
    finally:
        close_environment(environment)


class RecordingReviewer:
    def __init__(self):
        self.result = None

    def review(self, plan, result):
        self.result = result
        return ReviewResult(approved=True)


def test_orchestrator_resumes_after_action_approval_and_reviews_real_results(
    tmp_path,
):
    environment = build_environment(tmp_path)
    state, approvals, recorder, tools, adapter, task_id = environment
    reviewer = RecordingReviewer()

    try:
        orchestrator = Orchestrator(
            state_manager=state,
            approval_manager=approvals,
            planner=StubPlanner(),
            executor=adapter,
            reviewer=reviewer,
            verification_executor=PassingVerificationExecutor(),
        )

        plan_approval = orchestrator.prepare_task("Create a file")
        task_id = plan_approval.task_id
        approvals.approve(plan_approval.approval_id)

        first_result = orchestrator.execute_approved_plan(
            plan_approval.approval_id
        )

        assert first_result.waiting_for_approval is True
        assert reviewer.result is None
        assert state.get_state(task_id) == TaskStatus.WAITING_FOR_APPROVAL
        assert tools["create_file"].calls == []

        approvals.approve(first_result.pending_approval_id)

        review = orchestrator.execute_approved_plan(
            plan_approval.approval_id
        )

        assert review.approved is True
        assert reviewer.result.success is True
        assert len(reviewer.result.step_results) == 1
        assert reviewer.result.step_results[0]["result"]["tool"] == "create_file"
        assert len(recorder.get_executions_for_task(task_id)) == 1
        assert state.get_state(task_id) == TaskStatus.SUCCEEDED
    finally:
        close_environment(environment)



def test_user_interface_handles_plan_and_action_approvals(
    tmp_path,
    monkeypatch,
):
    environment = build_environment(tmp_path)
    state, approvals, _, tools, adapter, _task_id = environment

    try:
        orchestrator = Orchestrator(
            state_manager=state,
            approval_manager=approvals,
            planner=StubPlanner(),
            executor=adapter,
            reviewer=RecordingReviewer(),
            verification_executor=PassingVerificationExecutor(),
        )
        answers = iter(["y", "y"])
        monkeypatch.setattr("builtins.input", lambda _: next(answers))

        UserInterface(orchestrator, approvals)._run_task("Create a file")

        task = state.get_tasks()[-1]
        assert task.status == TaskStatus.SUCCEEDED
        assert len(tools["create_file"].calls) == 1
    finally:
        close_environment(environment)




def test_user_interface_reports_required_verification_failure_after_reviewer_approval(
    tmp_path,
    monkeypatch,
    capsys,
):
    environment = build_environment(tmp_path)
    state, approvals, _, _, adapter, _task_id = environment

    try:
        orchestrator = Orchestrator(
            state_manager=state,
            approval_manager=approvals,
            planner=StubPlanner(),
            executor=adapter,
            reviewer=RecordingReviewer(),
            verification_executor=FailingVerificationExecutor(),
        )
        answers = iter(["y", "y"])
        monkeypatch.setattr("builtins.input", lambda _: next(answers))

        UserInterface(orchestrator, approvals)._run_task("Create a file")

        output = capsys.readouterr().out
        task = state.get_tasks()[-1]

        assert "Решение Reviewer: одобрено" in output
        assert "Статус задачи: FAILED" in output
        assert "Диагностика: Required file does not exist" in output
        assert "Ошибка выполнения:" in output
        assert "Задача успешно завершена" not in output
        assert task.status is TaskStatus.FAILED
    finally:
        close_environment(environment)


class RevisingListPlanner:
    def create_plan(self, task):
        return Plan(
            summary="Initial",
            verification_criteria=[
                {
                    "id": "tests",
                    "type": "pytest",
                    "description": "Run the project test suite",
                    "parameters": {},
                }
            ],
            steps=[
                {
                    "description": "List root",
                    "action": "list_directory",
                    "parameters": {"path": ".", "max_entries": 10},
                }
            ],
        )

    def revise_plan(self, task, previous_plan, feedback):
        return Plan(
            summary="Revised",
            steps=[
                previous_plan.steps[0],
                {
                    "description": "List a subdirectory",
                    "action": "list_directory",
                    "parameters": {"path": "src", "max_entries": 10},
                },
            ],
            verification_criteria=previous_plan.verification_criteria,
        )


class RejectThenApproveReviewer:
    def __init__(self):
        self.calls = 0

    def review(self, plan, result):
        self.calls += 1
        if self.calls == 1:
            return ReviewResult(
                approved=False,
                feedback="Inspect the source directory too",
            )
        return ReviewResult(approved=True)


def test_revised_plan_reuses_completed_steps_without_running_them_twice(
    tmp_path,
    monkeypatch,
):
    environment = build_environment(tmp_path)
    state, approvals, recorder, tools, adapter, _ = environment

    try:
        reviewer = RejectThenApproveReviewer()
        orchestrator = Orchestrator(
            state_manager=state,
            approval_manager=approvals,
            planner=RevisingListPlanner(),
            executor=adapter,
            reviewer=reviewer,
            verification_executor=PassingVerificationExecutor(),
        )
        answers = iter(["y", "y"])
        monkeypatch.setattr("builtins.input", lambda _: next(answers))

        UserInterface(orchestrator, approvals)._run_task("Inspect source")

        task = state.get_tasks()[-1]
        assert task.status == TaskStatus.SUCCEEDED
        assert len(tools["list_directory"].calls) == 2
        assert tools["list_directory"].calls == [
            {"path": ".", "max_entries": 10},
            {"path": "src", "max_entries": 10},
        ]
        assert len(recorder.get_executions_for_task(task.task_id)) == 2
        assert len(state.get_review_history(task.task_id)) == 2
        assert task.task_id not in adapter._progress
    finally:
        close_environment(environment)



class StatefulFakeToolExecutor:
    def __init__(self):
        self.file_content = None
        self.calls = []

    def execute(self, task_id, action, parameters):
        self.calls.append((action, dict(parameters)))
        if action == "write_file":
            self.file_content = parameters["content"]
            result = {"written": self.file_content}
        elif action == "read_file":
            result = {"content": self.file_content}
        else:
            raise AssertionError(f"Unexpected action: {action}")
        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCEEDED,
            execution_id=f"execution-{len(self.calls)}",
            result=result,
        )


def test_changed_step_invalidates_reuse_of_later_dependent_results():
    tool_executor = StatefulFakeToolExecutor()
    adapter = ToolExecutorAdapter(tool_executor)
    original = Plan(
        summary="Write A and read it",
        verification_criteria=[
                {
                    "id": "tests",
                    "type": "pytest",
                    "description": "Run the project test suite",
                    "parameters": {},
                }
            ],
            steps=[
            {
                "description": "Write A",
                "action": "write_file",
                "parameters": {"path": "data.txt", "content": "A"},
            },
            {
                "description": "Read file",
                "action": "read_file",
                "parameters": {"path": "data.txt"},
            },
        ],
    )
    revised = Plan(
        summary="Write B and read it",
        verification_criteria=[
                {
                    "id": "tests",
                    "type": "pytest",
                    "description": "Run the project test suite",
                    "parameters": {},
                }
            ],
            steps=[
            {
                "description": "Write B",
                "action": "write_file",
                "parameters": {"path": "data.txt", "content": "B"},
            },
            {
                "description": "Read file",
                "action": "read_file",
                "parameters": {"path": "data.txt"},
            },
        ],
    )

    first_result = adapter.execute("task-1", original)
    revised_result = adapter.execute("task-1", revised)

    assert first_result.success is True
    assert first_result.step_results[1]["result"]["content"] == "A"
    assert revised_result.success is True
    assert revised_result.step_results[1]["result"]["content"] == "B"
    assert tool_executor.calls == [
        ("write_file", {"path": "data.txt", "content": "A"}),
        ("read_file", {"path": "data.txt"}),
        ("write_file", {"path": "data.txt", "content": "B"}),
        ("read_file", {"path": "data.txt"}),
    ]
    assert "task-1" in adapter._progress
    adapter.clear_progress("task-1")
    assert "task-1" not in adapter._progress
