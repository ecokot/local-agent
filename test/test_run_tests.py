
from unittest.mock import Mock

import pytest

from local_agent.execution.tools.run_command import (
    RunCommandError,
    RunCommandResult,
    RunCommandTool,
)
from local_agent.execution.tools.run_tests import (
    TEST_COMMAND,
    RunTestsTool,
)


@pytest.fixture
def command_tool():
    return Mock(spec=RunCommandTool)


@pytest.fixture
def tool(command_tool):
    return RunTestsTool(command_tool)


def make_result(
    exit_code=0,
    stdout="",
    stderr="",
    timed_out=False,
):
    return RunCommandResult(
        command=TEST_COMMAND,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        stdout_truncated=False,
        stderr_truncated=False,
    )


def test_runs_fixed_pytest_command(tool, command_tool):
    command_tool.execute.return_value = make_result()

    tool.execute()

    command_tool.execute.assert_called_once_with(TEST_COMMAND)


def test_returns_success_when_tests_pass(tool, command_tool):
    command_tool.execute.return_value = make_result(
        stdout="10 passed",
    )

    result = tool.execute()

    assert result.passed is True
    assert result.exit_code == 0
    assert result.stdout == "10 passed"
    assert result.timed_out is False


def test_returns_failure_when_tests_fail(tool, command_tool):
    command_tool.execute.return_value = make_result(
        exit_code=1,
        stdout="1 failed, 9 passed",
    )

    result = tool.execute()

    assert result.passed is False
    assert result.exit_code == 1
    assert "1 failed" in result.stdout


def test_returns_failure_when_command_times_out(tool, command_tool):
    command_tool.execute.return_value = make_result(
        timed_out=True,
    )

    result = tool.execute()

    assert result.passed is False
    assert result.timed_out is True


def test_preserves_stderr_and_truncation_flags(tool, command_tool):
    command_tool.execute.return_value = RunCommandResult(
        command=TEST_COMMAND,
        exit_code=1,
        stdout="output",
        stderr="error",
        timed_out=False,
        stdout_truncated=True,
        stderr_truncated=True,
    )

    result = tool.execute()

    assert result.stdout == "output"
    assert result.stderr == "error"
    assert result.stdout_truncated is True
    assert result.stderr_truncated is True


def test_propagates_command_execution_error(tool, command_tool):
    command_tool.execute.side_effect = RunCommandError(
        "Cannot execute command"
    )

    with pytest.raises(RunCommandError, match="Cannot execute"):
        tool.execute()
