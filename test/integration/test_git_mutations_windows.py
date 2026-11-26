from __future__ import annotations

import os
import shutil
import subprocess
import threading
from dataclasses import replace

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
    environment = {
        "approval": approval_manager,
        "executor": executor,
        "git": git_tool,
        "git_executable": executable,
        "repository": repository,
        "recorder": recorder,
        "state": state_manager,
        "task_id": task_id,
    }
    yield environment
    environment["recorder"].close()
    environment["state"].close()



def _reopen_execution_services(environment, tmp_path):
    environment["recorder"].close()
    environment["state"].close()
    state_manager = StateManager(tmp_path / "state.db")
    recorder = ExecutionRecorder(
        database_path=tmp_path / "state.db",
        trace_directory=tmp_path / "traces",
    )
    approval_manager = ApprovalManager(
        connection=state_manager.connection,
        state_manager=state_manager,
    )
    executor = ToolExecutor(
        approval_manager=approval_manager,
        list_directory=_UnusedTool(),
        read_file=_UnusedTool(),
        create_file=_UnusedTool(),
        write_file=_UnusedTool(),
        get_diff=_UnusedTool(),
        run_command=_UnusedTool(),
        run_tests=_UnusedTool(),
        git_tool=environment["git"],
        execution_recorder=recorder,
    )
    environment["state"] = state_manager
    environment["recorder"] = recorder
    environment["approval"] = approval_manager
    environment["executor"] = executor


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




def test_repository_lock_serializes_same_repo_only(mutation_environment, tmp_path):
    env = mutation_environment
    other_repository = tmp_path / "other-repository"
    _initialize_repository(env["git_executable"], other_repository)
    other_git = GitTool(Workspace(other_repository))
    linked_worktree = tmp_path / "linked-worktree"
    _git(
        env["git_executable"], env["repository"],
        "worktree", "add", "-q", "-b", "linked", str(linked_worktree),
    )
    same_repository_git = GitTool(Workspace(linked_worktree))
    assert same_repository_git._repository_lock_key == env["git"]._repository_lock_key

    same_entered = threading.Event()
    other_entered = threading.Event()

    def enter_same_repository():
        with same_repository_git.mutation_lock():
            same_entered.set()

    def enter_other_repository():
        with other_git.mutation_lock():
            other_entered.set()

    with env["git"].mutation_lock():
        same_thread = threading.Thread(target=enter_same_repository)
        other_thread = threading.Thread(target=enter_other_repository)
        same_thread.start()
        other_thread.start()

        assert other_entered.wait(2)
        assert not same_entered.wait(0.1)

    assert same_entered.wait(2)
    same_thread.join(timeout=2)
    other_thread.join(timeout=2)
    assert not same_thread.is_alive()
    assert not other_thread.is_alive()


def test_branch_creation_rejects_dirty_and_detached_repositories(mutation_environment):
    env = mutation_environment
    repository = env["repository"]
    (repository / "dirty.txt").write_text("keep\n", encoding="utf-8")

    dirty = env["executor"].execute(
        env["task_id"],
        "git_create_and_switch_branch",
        {"branch_name": "feature/dirty"},
    )
    assert dirty.status == ToolExecutionStatus.FAILED
    assert env["git"].branch() == "main"
    assert (repository / "dirty.txt").read_text(encoding="utf-8") == "keep\n"

    (repository / "dirty.txt").unlink()
    _git(env["git_executable"], repository, "checkout", "--detach", "HEAD")
    detached = env["executor"].execute(
        env["task_id"],
        "git_create_and_switch_branch",
        {"branch_name": "feature/detached"},
    )
    assert detached.status == ToolExecutionStatus.FAILED
    assert env["git"].branch() is None


def test_branch_creation_rejects_existing_and_invalid_names(mutation_environment):
    env = mutation_environment
    _git(
        env["git_executable"], env["repository"],
        "branch", "existing",
    )
    for name in ("existing", "-invalid", "not a branch"):
        result = env["executor"].execute(
            env["task_id"],
            "git_create_and_switch_branch",
            {"branch_name": name},
        )
        assert result.status == ToolExecutionStatus.FAILED
    assert env["git"].branch() == "main"


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



def test_pending_git_approval_resumes_after_sqlite_restart(
    mutation_environment, tmp_path
):
    env = mutation_environment
    repository = env["repository"]
    (repository / "resume.txt").write_text("resume after restart\n", encoding="utf-8")
    parameters = {"paths": ["resume.txt"]}

    pending = env["executor"].execute(
        env["task_id"], "git_stage", parameters
    )
    assert pending.status == ToolExecutionStatus.WAITING_FOR_APPROVAL

    _reopen_execution_services(env, tmp_path)
    env["approval"].approve(pending.approval_id)
    resumed = env["executor"].execute(
        env["task_id"], "git_stage", parameters
    )

    assert resumed.status == ToolExecutionStatus.SUCCEEDED
    assert resumed.result["staged_paths"] == ["resume.txt"]


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
        "file.txt:stream",
        "NUL.txt",
        "trailing.",
        "trailing ",
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



@pytest.mark.parametrize(
    "paths",
    [
        [],
        ["same.txt", "same.txt"],
        ["nul\x00path.txt"],
        ["C:/absolute.txt"],
    ],
)
def test_stage_rejects_empty_duplicate_nul_and_absolute_path_lists(
    mutation_environment, paths
):
    env = mutation_environment
    result = env["executor"].execute(
        env["task_id"], "git_stage", {"paths": paths}
    )
    assert result.status == ToolExecutionStatus.FAILED
    assert _git(
        env["git_executable"], env["repository"],
        "diff", "--cached", "--name-only",
    ) == b""


def test_stage_accepts_leading_dash_as_a_literal_filename(mutation_environment):
    env = mutation_environment
    filename = "-literal file-雪.txt"
    (env["repository"] / filename).write_text("literal\n", encoding="utf-8")

    result = _approve_and_resume(
        env, "git_stage", {"paths": [filename]}
    )

    assert result.status == ToolExecutionStatus.SUCCEEDED
    assert result.result["staged_paths"] == [filename]


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




def test_stage_receipt_survives_sqlite_and_executor_restart(
    mutation_environment, tmp_path
):
    env = mutation_environment
    repository = env["repository"]
    (repository / "first.txt").write_text("one\n", encoding="utf-8")
    first = _approve_and_resume(
        env, "git_stage", {"paths": ["first.txt"]}
    )
    assert first.status == ToolExecutionStatus.SUCCEEDED

    _reopen_execution_services(env, tmp_path)
    (repository / "second.txt").write_text("two\n", encoding="utf-8")
    second = _approve_and_resume(
        env, "git_stage", {"paths": ["second.txt"]}
    )
    assert second.status == ToolExecutionStatus.SUCCEEDED
    assert second.result["staged_paths"] == ["first.txt", "second.txt"]

    committed = _approve_and_resume(
        env,
        "git_commit",
        {"message": "Commit after restart", "paths": ["first.txt", "second.txt"]},
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
    assert "active Git hooks" in result.error
    assert env["git"].branch() == "main"
    branch_check = subprocess.run(
        [
            env["git_executable"], "-C", str(env["repository"]),
            "show-ref", "--verify", "--quiet",
            "refs/heads/feature/with-hook",
        ],
        check=False,
        capture_output=True,
    )
    assert branch_check.returncode == 1


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


def test_stage_timeout_reports_failed_without_rollback(mutation_environment):
    env = mutation_environment
    repository = env["repository"]
    target = repository / "timeout.txt"
    target.write_text("stage may have completed before timeout\n", encoding="utf-8")
    original_runner = env["git"]._runner

    def timeout_after_stage(executable, arguments, cwd, timeout, max_output_size):
        result = original_runner(executable, arguments, cwd, timeout, max_output_size)
        if arguments[3:4] == ("add",) and result.exit_code == 0:
            return replace(result, timed_out=True)
        return result

    env["git"]._runner = timeout_after_stage
    result = _approve_and_resume(env, "git_stage", {"paths": ["timeout.txt"]})

    assert result.status == ToolExecutionStatus.FAILED
    assert "Timed out" in result.error
    assert _git(
        env["git_executable"], repository, "diff", "--cached", "--name-only"
    ) == b"timeout.txt\n"
    assert target.read_text(encoding="utf-8") == "stage may have completed before timeout\n"
    assert _git(
        env["git_executable"], repository, "status", "--porcelain"
    ).startswith(b"A  timeout.txt")


@pytest.mark.parametrize("truncated_stream", ["stdout", "stderr"])
def test_stage_output_truncation_reports_failed_without_rollback(
    mutation_environment, truncated_stream
):
    env = mutation_environment
    repository = env["repository"]
    target = repository / f"{truncated_stream}-truncated.txt"
    target.write_text("stage output was truncated\n", encoding="utf-8")
    original_runner = env["git"]._runner

    def truncate_after_stage(executable, arguments, cwd, timeout, max_output_size):
        result = original_runner(executable, arguments, cwd, timeout, max_output_size)
        if arguments[3:4] == ("add",) and result.exit_code == 0:
            return replace(result, **{f"{truncated_stream}_truncated": True})
        return result

    env["git"]._runner = truncate_after_stage
    result = _approve_and_resume(
        env, "git_stage", {"paths": [target.name]}
    )

    assert result.status == ToolExecutionStatus.FAILED
    assert "output was truncated" in result.error
    assert _git(
        env["git_executable"], repository, "diff", "--cached", "--name-only"
    ) == f"{target.name}\n".encode()
    assert target.read_text(encoding="utf-8") == "stage output was truncated\n"
    assert _git(
        env["git_executable"], repository, "status", "--porcelain"
    ).startswith(b"A  ")


def test_commit_postcondition_failure_keeps_executed_commits_for_inspection(
    mutation_environment
):
    env = mutation_environment
    repository = env["repository"]
    target = repository / "approved.txt"
    target.write_text("approved content\n", encoding="utf-8")
    staged = _approve_and_resume(env, "git_stage", {"paths": ["approved.txt"]})
    assert staged.status == ToolExecutionStatus.SUCCEEDED
    old_head = env["git"].head()
    original_runner = env["git"]._runner
    injected = repository / "external-after-commit.txt"

    def add_external_commit_after_approved_commit(
        executable, arguments, cwd, timeout, max_output_size
    ):
        result = original_runner(executable, arguments, cwd, timeout, max_output_size)
        command = arguments[3:]
        if "commit" in command and result.exit_code == 0:
            injected.write_text("external concurrent change\n", encoding="utf-8")
            _git(env["git_executable"], repository, "add", "--", injected.name)
            _git(
                env["git_executable"], repository, "commit", "-m",
                "external concurrent commit",
            )
        return result

    env["git"]._runner = add_external_commit_after_approved_commit
    result = _approve_and_resume(
        env,
        "git_commit",
        {"message": "approved commit", "paths": ["approved.txt"]},
    )

    assert result.status == ToolExecutionStatus.FAILED
    assert "git_commit postcondition failed" in result.error
    assert env["git"].head() != old_head
    assert _git(
        env["git_executable"], repository, "show", "-s", "--format=%s", "HEAD^"
    ).decode().strip() == "approved commit"
    assert _git(
        env["git_executable"], repository, "show", "-s", "--format=%s", "HEAD"
    ).decode().strip() == "external concurrent commit"
    assert _git(
        env["git_executable"], repository, "show", "HEAD^:approved.txt"
    ) == b"approved content\n"
    assert injected.read_text(encoding="utf-8") == "external concurrent change\n"
    # The failed postcondition is not converted into success or an automatic rollback.
    assert _git(
        env["git_executable"], repository, "status", "--porcelain"
    ) == b""


def test_commit_blocks_tilde_expanded_hooks_path_before_approval(
    mutation_environment, monkeypatch, tmp_path
):
    env = mutation_environment
    repository = env["repository"]
    target = repository / "tilde-hook-protected.txt"
    target.write_text("staged content\\n", encoding="utf-8")
    staged = _approve_and_resume(
        env, "git_stage", {"paths": ["tilde-hook-protected.txt"]}
    )
    assert staged.status == ToolExecutionStatus.SUCCEEDED
    original_head = env["git"].head()
    original_index = _git(
        env["git_executable"], repository, "ls-files", "--stage", "-z"
    )

    git_home = tmp_path / "git-home"
    hooks = git_home / ".local-agent-hooks"
    hooks.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(git_home))
    _git(
        env["git_executable"], repository,
        "config", "core.hooksPath", "~/.local-agent-hooks",
    )
    hook = hooks / "pre-commit"
    hook.write_text(
        "#!/bin/sh\\necho executed > hook-ran.txt\\nexit 1\\n",
        encoding="utf-8",
    )
    effective_hooks = Path(
        os.fsdecode(
            _git(env["git_executable"], repository, "rev-parse", "--git-path", "hooks")
        ).strip()
    )
    assert os.path.normcase(str(effective_hooks.resolve())) == os.path.normcase(
        str(hooks.resolve())
    )

    result = env["executor"].execute(
        env["task_id"],
        "git_commit",
        {
            "message": "must be blocked by expanded hooks path",
            "paths": ["tilde-hook-protected.txt"],
        },
    )

    assert result.status == ToolExecutionStatus.FAILED
    assert "active Git hooks" in result.error
    assert env["git"].head() == original_head
    assert _git(
        env["git_executable"], repository, "ls-files", "--stage", "-z"
    ) == original_index
    assert hook.exists()
    assert not (repository / "hook-ran.txt").exists()


def test_commit_blocks_active_git_hooks_before_approval(mutation_environment):
    env = mutation_environment
    repository = env["repository"]
    target = repository / "hook-protected.txt"
    target.write_text("staged content\n", encoding="utf-8")
    staged = _approve_and_resume(
        env, "git_stage", {"paths": ["hook-protected.txt"]}
    )
    assert staged.status == ToolExecutionStatus.SUCCEEDED
    original_head = env["git"].head()

    hooks = repository / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    hook = hooks / "pre-commit"
    hook.write_text("#!/bin/sh\necho executed > hook-ran.txt\nexit 1\n", encoding="utf-8")

    result = env["executor"].execute(
        env["task_id"],
        "git_commit",
        {"message": "must be blocked by hook", "paths": ["hook-protected.txt"]},
    )

    assert result.status == ToolExecutionStatus.FAILED
    assert "active Git hooks" in result.error
    assert env["git"].head() == original_head
    assert _git(
        env["git_executable"], repository, "diff", "--cached", "--name-only"
    ) == b"hook-protected.txt\n"
    assert hook.exists()
    assert not (repository / "hook-ran.txt").exists()
