
from dataclasses import dataclass

from local_agent.execution.tools.run_command import (
    RunCommandResult,
    RunCommandTool,
)

TEST_COMMAND = ("uv", "run", "pytest", "-v")


@dataclass(frozen=True, slots=True)
class RunTestsResult:
    """Результат запуска тестов."""

    command: tuple[str, ...]
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    stdout_truncated: bool
    stderr_truncated: bool
    passed: bool


class RunTestsTool:
    """Запускает pytest в рабочей директории через RunCommandTool."""

    def __init__(self, command_tool: RunCommandTool) -> None:
        self._command_tool = command_tool

    def execute(self) -> RunTestsResult:
        result: RunCommandResult = self._command_tool.execute(
            TEST_COMMAND
        )

        return RunTestsResult(
            command=result.command,
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            timed_out=result.timed_out,
            stdout_truncated=result.stdout_truncated,
            stderr_truncated=result.stderr_truncated,
            passed=result.exit_code == 0 and not result.timed_out,
        )
