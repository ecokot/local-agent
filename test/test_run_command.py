import pytest

from local_agent.execution.backend import (
    ExecutionBackendError,
    ExecutionResult,
)
from local_agent.execution.tools.run_command import (
    MAX_OUTPUT_SIZE,
    RunCommandError,
    RunCommandTool,
)


class FakeExecutionBackend:
    """Тестовая замена ExecutionBackend без запуска процессов."""

    def __init__(self, result=None, error=None):
        self.result = result or ExecutionResult(
            stdout="",
            stderr="",
            exit_code=0,
            timed_out=False,
        )
        self.error = error
        self.commands = []

    def execute(self, command):
        self.commands.append(command)

        if self.error is not None:
            raise self.error

        return self.result


@pytest.fixture
def backend():
    return FakeExecutionBackend()


@pytest.fixture
def tool(backend):
    return RunCommandTool(backend)


def test_passes_command_to_backend(tool, backend):
    command = ["python", "-c", "print('hello')"]

    tool.execute(command)

    assert backend.commands == [tuple(command)]


def test_accepts_tuple_command(tool, backend):
    command = ("python", "-c", "print('hello')")

    result = tool.execute(command)

    assert backend.commands == [command]
    assert result.command == command


def test_returns_stdout(tool):
    tool._backend.result = ExecutionResult(
        stdout="hello\n",
        stderr="",
        exit_code=0,
        timed_out=False,
    )

    result = tool.execute(["python", "script.py"])

    assert result.stdout == "hello\n"
    assert result.stderr == ""
    assert result.exit_code == 0
    assert result.timed_out is False


def test_returns_stderr(tool):
    tool._backend.result = ExecutionResult(
        stdout="",
        stderr="error\n",
        exit_code=0,
        timed_out=False,
    )

    result = tool.execute(["python", "script.py"])

    assert result.stderr == "error\n"
    assert result.exit_code == 0


def test_returns_nonzero_exit_code(tool):
    tool._backend.result = ExecutionResult(
        stdout="",
        stderr="failure",
        exit_code=5,
        timed_out=False,
    )

    result = tool.execute(["python", "script.py"])

    assert result.exit_code == 5
    assert result.timed_out is False


def test_returns_timeout(tool):
    tool._backend.result = ExecutionResult(
        stdout="started",
        stderr="",
        exit_code=None,
        timed_out=True,
    )

    result = tool.execute(["python", "script.py"])

    assert result.timed_out is True
    assert result.exit_code is None
    assert result.stdout == "started"


def test_normalizes_newlines(tool):
    tool._backend.result = ExecutionResult(
        stdout="first\r\nsecond\rthird",
        stderr="",
        exit_code=0,
        timed_out=False,
    )

    result = tool.execute(["python", "script.py"])

    assert result.stdout == "first\nsecond\nthird"


def test_truncates_stdout(tool):
    tool._backend.result = ExecutionResult(
        stdout="a" * (MAX_OUTPUT_SIZE + 100),
        stderr="",
        exit_code=0,
        timed_out=False,
    )

    result = tool.execute(["python", "script.py"])

    assert len(result.stdout.encode("utf-8")) == MAX_OUTPUT_SIZE
    assert result.stdout_truncated is True


def test_truncates_stderr(tool):
    tool._backend.result = ExecutionResult(
        stdout="",
        stderr="a" * (MAX_OUTPUT_SIZE + 100),
        exit_code=0,
        timed_out=False,
    )

    result = tool.execute(["python", "script.py"])

    assert len(result.stderr.encode("utf-8")) == MAX_OUTPUT_SIZE
    assert result.stderr_truncated is True


def test_handles_backend_error(backend):
    backend.error = ExecutionBackendError("Docker unavailable")
    tool = RunCommandTool(backend)

    with pytest.raises(RunCommandError, match="Cannot execute command"):
        tool.execute(["python", "script.py"])


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
        tool.execute(["python", 123])


def test_rejects_nul_in_arguments(tool):
    with pytest.raises(RunCommandError, match="NUL"):
        tool.execute(["python", "\x00"])
