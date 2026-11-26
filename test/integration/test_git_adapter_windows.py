from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from local_agent.agents.tool_executor_adapter import ToolExecutorAdapter
from local_agent.approval_manager import ApprovalManager
from local_agent.execution.recorder import ExecutionRecorder
from local_agent.execution.tool_executor import ToolExecutor
from local_agent.execution.tools._windows_runner import run_windows
from local_agent.execution.tools.git import GitTool
from local_agent.execution.workspace import Workspace
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

pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="GitTool mutations use the Windows runner",
)


class _UnusedTool:
    def execute(self, **parameters):
        return parameters


class _CountingListDirectory:
    def __init__(self):
        self.calls = []

    def execute(self, **parameters):
        self.calls.append(parameters)
        return {"entries": ["approved.txt"]}


class _GitPlanner:
    def create_plan(self, task):
        return Plan(
            summary="Run a safe step, then stage one approved file",
            verification_criteria=[
                {
                    "id": "pytest",
                    "type": "pytest",
                    "description": "Run project tests",
                    "parameters": {},
                }
            ],
            steps=[
                {
                    "description": "List the workspace",
                    "action": "list_directory",
                    "parameters": {"path": ".", "max_depth": 1, "max_entries": 10},
                },
                {
                    "description": "Stage the approved file",
                    "action": "git_stage",
                    "parameters": {"paths": ["approved.txt"]},
                },
            ],
        )


class _PassingVerificationExecutor:
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
        return VerificationReport(results=results, all_required_passed=True)


class _ApprovingReviewer:
    def review(self, plan, result):
        return ReviewResult(approved=True)


def _git(executable, repository, *arguments):
    return subprocess.run(
        [executable, "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
    ).stdout


def test_orchestrator_resumes_real_git_stage_through_adapter_after_approval(
    tmp_path,
):
    executable = shutil.which("git")
    if executable is None:
        pytest.skip("Git for Windows is not installed")

    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(
        [executable, "init", "-q", "-b", "main", str(repository)],
        check=True,
    )
    _git(executable, repository, "config", "user.name", "Local Agent Test")
    _git(
        executable, repository, "config", "user.email",
        "local-agent@example.invalid",
    )
    (repository / "base.txt").write_text("base\n", encoding="utf-8")
    _git(executable, repository, "add", "--", "base.txt")
    _git(executable, repository, "commit", "-qm", "initial")

    target = repository / "approved.txt"
    target.write_text("approved content\n", encoding="utf-8")

    runner_calls = []

    def recording_runner(git_executable, arguments, cwd, timeout, max_output_size):
        runner_calls.append(tuple(arguments))
        return run_windows(
            git_executable, arguments, cwd, timeout, max_output_size
        )

    git_tool = GitTool(
        Workspace(repository),
        runner=recording_runner,
    )
    database = tmp_path / "agent.db"
    state = StateManager(database)
    approvals = ApprovalManager(
        connection=state.connection,
        state_manager=state,
    )
    recorder = ExecutionRecorder(database, tmp_path / "traces")
    list_directory = _CountingListDirectory()
    tool_executor = ToolExecutor(
        approval_manager=approvals,
        execution_recorder=recorder,
        list_directory=list_directory,
        read_file=_UnusedTool(),
        create_file=_UnusedTool(),
        write_file=_UnusedTool(),
        get_diff=_UnusedTool(),
        run_command=_UnusedTool(),
        run_tests=_UnusedTool(),
        git_tool=git_tool,
    )
    adapter = ToolExecutorAdapter(tool_executor)
    orchestrator = Orchestrator(
        state_manager=state,
        approval_manager=approvals,
        planner=_GitPlanner(),
        executor=adapter,
        reviewer=_ApprovingReviewer(),
        verification_executor=_PassingVerificationExecutor(),
    )

    try:
        plan_approval = orchestrator.prepare_task("Stage one approved file")
        approvals.approve(plan_approval.approval_id)

        waiting = orchestrator.execute_approved_plan(plan_approval.approval_id)

        assert waiting.waiting_for_approval is True
        assert waiting.pending_approval_id is not None
        assert list_directory.calls == [
            {"path": ".", "max_depth": 1, "max_entries": 10}
        ]
        assert not any("add" in call for call in runner_calls)
        assert _git(
            executable, repository, "diff", "--cached", "--name-only"
        ) == b""
        assert target.read_text(encoding="utf-8") == "approved content\n"
        assert state.get_state(plan_approval.task_id) is TaskStatus.WAITING_FOR_APPROVAL

        approvals.approve(waiting.pending_approval_id)
        review = orchestrator.execute_approved_plan(plan_approval.approval_id)

        assert isinstance(review, ReviewResult)
        assert review.approved is True
        assert list_directory.calls == [
            {"path": ".", "max_depth": 1, "max_entries": 10}
        ]
        assert any("add" in call for call in runner_calls)
        assert _git(
            executable, repository, "diff", "--cached", "--name-only"
        ) == b"approved.txt\n"
        assert "approved content" in git_tool.staged_diff(["approved.txt"])
        assert state.get_state(plan_approval.task_id) is TaskStatus.SUCCEEDED

        executions = recorder.get_executions_for_task(plan_approval.task_id)
        assert len(executions) == 2
        assert [recorder.get_status(item.execution_id) for item in executions] == [
            "SUCCEEDED",
            "SUCCEEDED",
        ]
    finally:
        recorder.close()
        state.close()
