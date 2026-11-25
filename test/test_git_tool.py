from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from local_agent.execution.tools._windows_runner import ProcessResult
from local_agent.execution.tools.git import (
    GitCommandError,
    GitNotFoundError,
    GitTool,
)
from local_agent.execution.workspace import Workspace


class FakeRunner:
    def __init__(self, root: Path):
        self.root = str(root)
        self.calls = []

    def __call__(self, executable, arguments, cwd, timeout, max_output_size):
        self.calls.append((executable, arguments, cwd, timeout))
        if arguments[1:] == ("rev-parse", "--show-toplevel"):
            output = self.root.encode()
        elif arguments[1] == "status":
            output = b"# branch.oid abc123\0# branch.head main\0? new file.txt\0"
        elif arguments[1:] == ("branch", "--show-current"):
            output = b"main\n"
        elif arguments[1:] == ("rev-parse", "--verify", "HEAD"):
            output = b"abc123\n"
        elif arguments[1] == "for-each-ref":
            output = b"main\tabc123\t*\n"
        elif arguments[1] == "log":
            output = b"abc123\x00Author\x002026-01-01T00:00:00+00:00\x00subject\x00"
        elif arguments[1:4] == ("diff", "--name-only", "-z"):
            output = b"tracked file.txt\0"
        elif arguments[1:3] == ("ls-files", "--others"):
            output = b"new file.txt\0"
        else:
            output = b""
        return ProcessResult(output, b"", 0, False, False, False)


@pytest.fixture
def tool(tmp_path, monkeypatch):
    monkeypatch.setattr("local_agent.execution.tools.git.shutil.which", lambda _: "C:/Git/cmd/git.exe")
    runner = FakeRunner(tmp_path)
    return GitTool(Workspace(tmp_path), runner=runner), runner


def test_checks_workspace_root_and_runs_git_from_workspace(tool, tmp_path):
    _, runner = tool
    assert runner.calls[0][2] == str(tmp_path.resolve())
    assert runner.calls[0][1] == ("--literal-pathspecs", "rev-parse", "--show-toplevel")


def test_read_only_operations(tool):
    instance, _ = tool
    assert instance.branch() == "main"
    assert instance.head() == "abc123"
    assert instance.status().branch == "main"
    assert instance.status().entries == ("? new file.txt",)
    assert instance.branches()[0].current is True
    assert instance.log()[0].subject == "subject"
    assert instance.diff() == ""
    assert instance.staged_diff() == ""
    assert instance.changed_files() == ("tracked file.txt", "new file.txt")


def test_path_arguments_are_literal_and_after_separator(tool, tmp_path):
    instance, runner = tool
    instance.diff([" :(glob)*.py".strip()])
    args = runner.calls[-1][1]
    assert args[0] == "--literal-pathspecs"
    assert args[-2:] == ("--", ":(glob)*.py")


def test_rejects_invalid_log_limit_before_running(tool):
    instance, runner = tool
    count = len(runner.calls)
    with pytest.raises(ValueError):
        instance.log(0)
    assert len(runner.calls) == count


def test_nonzero_exit_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr("local_agent.execution.tools.git.shutil.which", lambda _: "git")

    def runner(executable, arguments, cwd, timeout, max_output_size):
        if arguments[1:] == ("rev-parse", "--show-toplevel"):
            return ProcessResult(str(tmp_path).encode(), b"", 0, False, False, False)
        return ProcessResult(b"", b"bad revision", 128, False, False, False)

    instance = GitTool(Workspace(tmp_path), runner=runner)
    with pytest.raises(GitCommandError):
        instance.head()


def test_missing_git_is_typed_error(tmp_path, monkeypatch):
    monkeypatch.setattr("local_agent.execution.tools.git.shutil.which", lambda _: None)
    with pytest.raises(GitNotFoundError):
        GitTool(Workspace(tmp_path), runner=lambda *args: None)


@pytest.mark.skipif(__import__("os").name != "nt", reason="GitTool uses the Windows runner")
def test_real_repository_operations_do_not_mutate(tmp_path, monkeypatch):
    git = __import__("shutil").which("git")
    if git is None:
        pytest.skip("Git is not installed")
    subprocess.run([git, "init", "-q", str(tmp_path)], check=True)
    subprocess.run([git, "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    subprocess.run([git, "-C", str(tmp_path), "config", "user.email", "test@example.invalid"], check=True)
    (tmp_path / "tracked.txt").write_text("initial\n", encoding="utf-8")
    subprocess.run([git, "-C", str(tmp_path), "add", "tracked.txt"], check=True)
    subprocess.run([git, "-C", str(tmp_path), "commit", "-qm", "initial"], check=True)
    (tmp_path / "tracked.txt").write_text("changed\n", encoding="utf-8")
    (tmp_path / "new file-☃.txt").write_text("new\n", encoding="utf-8")
    monkeypatch.setattr("local_agent.execution.tools.git.shutil.which", lambda _: git)
    instance = GitTool(Workspace(tmp_path))
    before = subprocess.run([git, "-C", str(tmp_path), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout
    assert instance.head() == before.strip()
    assert "tracked.txt" in instance.changed_files()
    assert "new file-☃.txt" in instance.changed_files()
    assert instance.diff()
    after = subprocess.run([git, "-C", str(tmp_path), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout
    assert before == after
