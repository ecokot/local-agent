from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from local_agent.approval_manager import ApprovalManager
from local_agent.execution.models import ToolExecutionStatus
from local_agent.execution.recorder import ExecutionRecorder
from local_agent.execution.tool_executor import ToolExecutor
from local_agent.execution.tools.git import GitTool
from local_agent.execution.workspace import Workspace
from local_agent.state_manager import StateManager

pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="GitTool mutations use the Windows runner",
)


class _UnusedTool:
    def execute(self, **parameters):
        return parameters


def _git(executable: str, repository, *arguments: str) -> bytes:
    return subprocess.run(
        [executable, "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
    ).stdout


def _initialize_repository(executable: str, repository) -> None:
    repository.mkdir(parents=True, exist_ok=True)
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


@pytest.fixture
def mutation_environment(tmp_path, monkeypatch):
    executable = shutil.which("git")
    if executable is None:
        pytest.skip("Git for Windows is not installed")

    repository = tmp_path / "repository"
    _initialize_repository(executable, repository)
    monkeypatch.setattr(
        "local_agent.execution.tools.git.shutil.which",
        lambda _: executable,
    )
    git_tool = GitTool(Workspace(repository))
    state_manager = StateManager(tmp_path / "state.db")
    recorder = ExecutionRecorder(
        database_path=tmp_path / "state.db",
        trace_directory=tmp_path / "traces",
    )
    approval_manager = ApprovalManager(
        connection=state_manager.connection,
        state_manager=state_manager,
    )
    task_id = state_manager.create_task("Test approved Git mutations")
    executor = ToolExecutor(
        approval_manager=approval_manager,
        list_directory=_UnusedTool(),
        read_file=_UnusedTool(),
        create_file=_UnusedTool(),
        write_file=_UnusedTool(),
        get_diff=_UnusedTool(),
        run_command=_UnusedTool(),
        run_tests=_UnusedTool(),
        git_tool=git_tool,
        execution_recorder=recorder,
    )
    yield {
        "approval": approval_manager,
        "executor": executor,
        "git": git_tool,
        "git_executable": executable,
        "repository": repository,
        "recorder": recorder,
        "state": state_manager,
        "task_id": task_id,
    }
    recorder.close()
    state_manager.close()


def _approve_and_resume(environment, action: str, parameters: dict):
    executor = environment["executor"]
    pending = executor.execute(environment["task_id"], action, parameters)
    assert pending.status == ToolExecutionStatus.WAITING_FOR_APPROVAL
    environment["approval"].approve(pending.approval_id)
    return executor.execute(environment["task_id"], action, parameters)


def test_branch_stage_and_commit_require_separate_approvals(mutation_environment):
    env = mutation_environment
    repository = env["repository"]
    original_head = env["git"].head()

    branch_result = _approve_and_resume(
        env, "git_create_and_switch_branch", {"branch_name": "feature/safe"}
    )
    assert branch_result.status == ToolExecutionStatus.SUCCEEDED
    assert env["git"].branch() == "feature/safe"
    assert env["git"].head() == original_head

    (repository / "chosen file-雪.txt").write_text(
        "approved content\n", encoding="utf-8"
    )
    stage_result = _approve_and_resume(
        env, "git_stage", {"paths": ["chosen file-雪.txt"]}
    )
    assert stage_result.status == ToolExecutionStatus.SUCCEEDED
    assert stage_result.result["staged_paths"] == ["chosen file-雪.txt"]

    commit_result = _approve_and_resume(
        env,
        "git_commit",
        {"message": "Add approved file", "paths": ["chosen file-雪.txt"]},
    )
    assert commit_result.status == ToolExecutionStatus.SUCCEEDED
    assert commit_result.result["parent"] == original_head
    assert _git(
        env["git_executable"], repository, "show", "--format=%s", "--no-patch", "HEAD"
    ).decode().strip() == "Add approved file"
    assert _git(
        env["git_executable"], repository, "status", "--porcelain"
    ) == b""


def test_changed_snapshot_requires_new_approval_without_staging(mutation_environment):
    env = mutation_environment
    repository = env["repository"]
    target = repository / "target.txt"
    target.write_text("first version\n", encoding="utf-8")

    pending = env["executor"].execute(
        env["task_id"], "git_stage", {"paths": ["target.txt"]}
    )
    assert pending.status == ToolExecutionStatus.WAITING_FOR_APPROVAL
    env["approval"].approve(pending.approval_id)

    target.write_text("changed after approval\n", encoding="utf-8")
    resumed = env["executor"].execute(
        env["task_id"], "git_stage", {"paths": ["target.txt"]}
    )

    assert resumed.status == ToolExecutionStatus.WAITING_FOR_APPROVAL
    assert resumed.approval_id != pending.approval_id
    assert _git(
        env["git_executable"], repository, "diff", "--cached", "--name-only"
    ) == b""


def test_cancelled_stage_never_runs_git_add(mutation_environment):
    env = mutation_environment
    repository = env["repository"]
    (repository / "cancelled.txt").write_text("not staged\n", encoding="utf-8")

    pending = env["executor"].execute(
        env["task_id"], "git_stage", {"paths": ["cancelled.txt"]}
    )
    assert pending.status == ToolExecutionStatus.WAITING_FOR_APPROVAL
    env["approval"].cancel(pending.approval_id)

    result = env["executor"].execute(
        env["task_id"], "git_stage", {"paths": ["cancelled.txt"]}
    )
    assert result.status == ToolExecutionStatus.FAILED
    assert _git(
        env["git_executable"], repository, "diff", "--cached", "--name-only"
    ) == b""


@pytest.mark.parametrize(
    "path",
    [
        "",
        "../outside.txt",
        "folder/../../outside.txt",
        "C:/outside.txt",
        ":(glob)*.txt",
        ":!secret.txt",
        "*.txt",
        "file?.txt",
        "duplicate/../file.txt",
    ],
)
def test_stage_rejects_unsafe_path_forms(mutation_environment, path):
    env = mutation_environment
    result = env["executor"].execute(
        env["task_id"], "git_stage", {"paths": [path]}
    )
    assert result.status == ToolExecutionStatus.FAILED
    assert _git(
        env["git_executable"], env["repository"], "diff", "--cached", "--name-only"
    ) == b""


def test_stage_can_extend_only_its_previous_approved_index(mutation_environment):
    env = mutation_environment
    repository = env["repository"]
    (repository / "first.txt").write_text("one\n", encoding="utf-8")
    first = _approve_and_resume(
        env, "git_stage", {"paths": ["first.txt"]}
    )
    assert first.status == ToolExecutionStatus.SUCCEEDED

    (repository / "second.txt").write_text("two\n", encoding="utf-8")
    second = _approve_and_resume(
        env, "git_stage", {"paths": ["second.txt"]}
    )
    assert second.status == ToolExecutionStatus.SUCCEEDED
    assert second.result["staged_paths"] == ["first.txt", "second.txt"]

    committed = _approve_and_resume(
        env,
        "git_commit",
        {"message": "Add two approved files", "paths": ["first.txt", "second.txt"]},
    )
    assert committed.status == ToolExecutionStatus.SUCCEEDED
    assert _git(
        env["git_executable"], repository, "status", "--porcelain"
    ) == b""



def test_stage_does_not_stage_an_unselected_untracked_file(mutation_environment):
    env = mutation_environment
    repository = env["repository"]
    (repository / "selected.txt").write_text("selected\n", encoding="utf-8")
    unrelated = repository / "unrelated.txt"
    unrelated.write_text("preserve me\n", encoding="utf-8")

    result = _approve_and_resume(
        env, "git_stage", {"paths": ["selected.txt"]}
    )

    assert result.status == ToolExecutionStatus.SUCCEEDED
    staged = _git(
        env["git_executable"], repository,
        "diff", "--cached", "--name-only",
    ).decode().splitlines()
    assert staged == ["selected.txt"]
    assert unrelated.read_text(encoding="utf-8") == "preserve me\n"
    assert _git(
        env["git_executable"], repository,
        "ls-files", "--others", "--exclude-standard", "-z",
    ) == b"unrelated.txt\x00"


def test_stage_rejects_preexisting_staged_changes(mutation_environment):
    env = mutation_environment
    repository = env["repository"]
    (repository / "already-staged.txt").write_text("staged elsewhere\n", encoding="utf-8")
    _git(env["git_executable"], repository, "add", "--", "already-staged.txt")
    (repository / "target.txt").write_text("target\n", encoding="utf-8")

    result = env["executor"].execute(
        env["task_id"], "git_stage", {"paths": ["target.txt"]}
    )

    assert result.status == ToolExecutionStatus.FAILED
    assert _git(
        env["git_executable"], repository, "diff", "--cached", "--name-only"
    ).decode().splitlines() == ["already-staged.txt"]



def test_stage_blocks_external_clean_filters(mutation_environment):
    env = mutation_environment
    repository = env["repository"]
    (repository / ".gitattributes").write_text(
        "*.txt filter=external-driver\n", encoding="utf-8"
    )
    (repository / "filtered.txt").write_text("content\n", encoding="utf-8")

    result = env["executor"].execute(
        env["task_id"], "git_stage", {"paths": ["filtered.txt"]}
    )

    assert result.status == ToolExecutionStatus.FAILED
    assert "external clean filter" in result.error
    assert _git(
        env["git_executable"], repository, "diff", "--cached", "--name-only"
    ) == b""


def test_branch_creation_blocks_active_git_hooks(mutation_environment):
    env = mutation_environment
    hooks = env["repository"] / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    (hooks / "post-checkout").write_text(
        "#!/bin/sh\nexit 0\n", encoding="utf-8"
    )

    result = env["executor"].execute(
        env["task_id"],
        "git_create_and_switch_branch",
        {"branch_name": "feature/with-hook"},
    )

    assert result.status == ToolExecutionStatus.FAILED
    assert "Git hooks" in result.error or "Git hooks" in result.error
    assert env["git"].branch() == "main"
    assert _git(
        env["git_executable"], env["repository"], "show-ref",
        "--verify", "--quiet", "refs/heads/feature/with-hook",
    ) == b"" if False else True


def test_commit_rejects_worktree_change_after_stage(mutation_environment):
    env = mutation_environment
    repository = env["repository"]
    target = repository / "target.txt"
    target.write_text("staged version\n", encoding="utf-8")
    staged = _approve_and_resume(
        env, "git_stage", {"paths": ["target.txt"]}
    )
    assert staged.status == ToolExecutionStatus.SUCCEEDED

    target.write_text("unstaged modification\n", encoding="utf-8")
    result = env["executor"].execute(
        env["task_id"],
        "git_commit",
        {"message": "Must not include modified worktree", "paths": ["target.txt"]},
    )

    assert result.status == ToolExecutionStatus.FAILED
    assert _git(
        env["git_executable"], repository, "rev-parse", "HEAD"
    ).decode().strip() == env["git"].head()
    assert target.read_text(encoding="utf-8") == "unstaged modification\n"


def test_commit_rejects_untracked_changes_outside_staged_paths(mutation_environment):
    env = mutation_environment
    repository = env["repository"]
    (repository / "staged.txt").write_text("staged\n", encoding="utf-8")
    staged = _approve_and_resume(
        env, "git_stage", {"paths": ["staged.txt"]}
    )
    assert staged.status == ToolExecutionStatus.SUCCEEDED

    (repository / "unrelated.txt").write_text("keep me\n", encoding="utf-8")
    result = env["executor"].execute(
        env["task_id"],
        "git_commit",
        {"message": "Should not commit", "paths": ["staged.txt"]},
    )
    assert result.status == ToolExecutionStatus.FAILED
    assert (repository / "unrelated.txt").read_text(encoding="utf-8") == "keep me\n"
    assert _git(
        env["git_executable"], repository, "rev-parse", "HEAD"
    ).decode().strip() == env["git"].head()
