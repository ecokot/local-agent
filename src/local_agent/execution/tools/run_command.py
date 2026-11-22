import math
import subprocess
from dataclasses import dataclass

from local_agent.execution.workspace import Workspace

MAX_OUTPUT_SIZE = 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 60.0


class RunCommandError(Exception):
    """Ошибка запуска команды."""


@dataclass(frozen=True, slots=True)
class RunCommandResult:
    """Результат выполнения команды."""

    command: tuple[str, ...]
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    stdout_truncated: bool
    stderr_truncated: bool


class RunCommandTool:
    """Запускает команду в рабочей директории Workspace."""

    def __init__(
        self,
        workspace: Workspace,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise RunCommandError(
                "Timeout must be a positive finite number"
            )

        self._workspace = workspace
        self._timeout_seconds = timeout_seconds

    def execute(self, command: list[str] | tuple[str, ...]) -> RunCommandResult:
        if not isinstance(command, (list, tuple)):
            raise RunCommandError("Command must be a list or tuple")

        if not command:
            raise RunCommandError("Command must not be empty")

        if not all(isinstance(argument, str) for argument in command):
            raise RunCommandError("All command arguments must be strings")

        if not command[0]:
            raise RunCommandError("Executable must not be empty")

        if any("\x00" in argument for argument in command):
            raise RunCommandError("Command arguments must not contain NUL")

        command_args = tuple(command)

        try:
            try:
                completed = subprocess.run(
                    command_args,
                    cwd=self._workspace.root,
                    capture_output=True,
                    timeout=self._timeout_seconds,
                    check=False,
                    shell=False,
                )

                stdout, stdout_truncated = self._decode_output(
                    completed.stdout
                )
                stderr, stderr_truncated = self._decode_output(
                    completed.stderr
                )

                return RunCommandResult(
                    command=command_args,
                    exit_code=completed.returncode,
                    stdout=stdout,
                    stderr=stderr,
                    timed_out=False,
                    stdout_truncated=stdout_truncated,
                    stderr_truncated=stderr_truncated,
                )

            except subprocess.TimeoutExpired as error:
                stdout, stdout_truncated = self._decode_output(
                    error.stdout or b""
                )
                stderr, stderr_truncated = self._decode_output(
                    error.stderr or b""
                )

                return RunCommandResult(
                    command=command_args,
                    exit_code=None,
                    stdout=stdout,
                    stderr=stderr,
                    timed_out=True,
                    stdout_truncated=stdout_truncated,
                    stderr_truncated=stderr_truncated,
                )

        except OSError as error:
            raise RunCommandError(
                f"Cannot execute command: {error}"
            ) from error

    @staticmethod
    def _decode_output(output: bytes) -> tuple[str, bool]:
        truncated = len(output) > MAX_OUTPUT_SIZE
        output = output[:MAX_OUTPUT_SIZE]

        decoded = output.decode("utf-8", errors="replace")
        decoded = decoded.replace("\r\n", "\n").replace("\r", "\n")

        return decoded, truncated