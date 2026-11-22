import sys

import pytest

from local_agent.execution.tools.run_command import (
    MAX_OUTPUT_SIZE,
    RunCommandError,
    RunCommandTool,
)
from local_agent.execution.workspace import Workspace


@pytest.fixture
def workspace(tmp_path):
    return Workspace(tmp_path)


@pytest.fixture
def tool(workspace):
    return RunCommandTool(workspace)


def python_command(code):
    return [sys.executable, "-c", code]


def test_returns_stdout(tool):
    result = tool.execute(python_command("print('hello')"))

    assert result.stdout == "hello\n"
    assert result.stderr == ""
    assert result.exit_code == 0
    assert result.timed_out is False


def test_returns_stderr(tool):
    result = tool.execute(
        python_command("import sys; print('error', file=sys.stderr)")
    )

    assert result.stderr == "error\n"
    assert result.exit_code == 0


def test_returns_nonzero_exit_code(tool):
    result = tool.execute(python_command("raise SystemExit(5)"))

    assert result.exit_code == 5
    assert result.timed_out is False


def test_runs_command_in_workspace_directory(tool, workspace):
    result = tool.execute(
        python_command("import os; print(os.getcwd())")
    )

    assert result.stdout.strip() == str(workspace.root)
    assert result.exit_code == 0


def test_accepts_tuple_command(tool):
    command = (sys.executable, "-c", "print('hello')")

    result = tool.execute(command)

    assert result.command == command
    assert result.stdout == "hello\n"


def test_times_out_command(workspace):
    tool = RunCommandTool(workspace, timeout_seconds=0.1)

    result = tool.execute(
        python_command("import time; time.sleep(2)")
    )

    assert result.timed_out is True
    assert result.exit_code is None


def test_preserves_output_before_timeout(workspace):
    tool = RunCommandTool(workspace, timeout_seconds=0.5)

    result = tool.execute(
        python_command(
            "import time; print('started', flush=True); time.sleep(2)"
        )
    )

    assert result.timed_out is True
    assert "started" in result.stdout


def test_truncates_stdout(tool):
    code = f"print('a' * {MAX_OUTPUT_SIZE + 100}, end='')"

    result = tool.execute(python_command(code))

    assert len(result.stdout.encode("utf-8")) == MAX_OUTPUT_SIZE
    assert result.stdout_truncated is True


def test_truncates_stderr(tool):
    code = (
        "import sys; "
        f"print('a' * {MAX_OUTPUT_SIZE + 100}, file=sys.stderr, end='')"
    )

    result = tool.execute(python_command(code))

    assert len(result.stderr.encode("utf-8")) == MAX_OUTPUT_SIZE
    assert result.stderr_truncated is True


def test_rejects_non_list_or_tuple_command(tool):
    with pytest.raises(RunCommandError, match="list or tuple"):
        tool.execute("python")


def test_rejects_empty_command(tool):
    with pytest.raises(RunCommandError, match="must not be empty"):
        tool.execute([])


def test_rejects_empty_executable(tool):
    with pytest.raises(RunCommandError, match="Executable"):
        tool.execute(["", "argument"])


def test_rejects_non_string_arguments(tool):
    with pytest.raises(RunCommandError, match="must be strings"):
        tool.execute([sys.executable, 123])


def test_rejects_nul_in_arguments(tool):
    with pytest.raises(RunCommandError, match="NUL"):
        tool.execute([sys.executable, "\x00"])


def test_rejects_invalid_timeout(workspace):
    with pytest.raises(RunCommandError, match="positive finite"):
        RunCommandTool(workspace, timeout_seconds=0)

    with pytest.raises(RunCommandError, match="positive finite"):
        RunCommandTool(workspace, timeout_seconds=float("inf"))


def test_returns_error_for_missing_executable(tool):
    with pytest.raises(RunCommandError, match="Cannot execute command"):
        tool.execute(["this_executable_does_not_exist_123456"])