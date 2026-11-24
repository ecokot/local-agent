from local_agent.agents.stubs import StubPlanner\nfrom local_agent.agents.tool_executor_adapter import ToolExecutorAdapter\nfrom local_agent.models.agent import ReviewResult\nfrom local_agent.orchestrator import Orchestrator
from local_agent.approval_manager import ApprovalManager
from local_agent.execution.recorder import ExecutionRecorder
from local_agent.execution.tool_executor import ToolExecutor
from local_agent.models.plan import Plan
from local_agent.state_manager import StateManager, TaskStatus


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
    state, _, _, tools, adapter, task_id = environment

    try:
        plan = Plan(
            summary="Unknown tool",
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
        )

        plan_approval = orchestrator.prepare_task("Create a file")
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
