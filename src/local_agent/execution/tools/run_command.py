from __future__ import annotations

from dataclasses import dataclass

from local_agent.execution.backend import (
    ExecutionBackend,
    ExecutionBackendError,
)

MAX_OUTPUT_SIZE = 1024 * 1024


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
    """Запускает команду через ExecutionBackend в Workspace."""

    def __init__(self, backend: ExecutionBackend) -> None:
        if not callable(getattr(backend, "execute", None)):
            raise TypeError("backend must provide an execute method")

        self._backend = backend

    def execute(
        self,
        command: list[str] | tuple[str, ...],
    ) -> RunCommandResult:
        if not isinstance(command, (list, tuple)):
            raise RunCommandError("Command must be a list or tuple")

        if not command:
            raise RunCommandError("Command must not be empty")

        if not all(isinstance(argument, str) for argument in command):
            raise RunCommandError(
                "All command arguments must be strings"
            )

        if not command[0]:
            raise RunCommandError("Executable must not be empty")

        if any("\x00" in argument for argument in command):
            raise RunCommandError(
                "Command arguments must not contain NUL"
            )

        command_args = tuple(command)

        try:
            execution = self._backend.execute(command_args)
        except ExecutionBackendError as error:
            raise RunCommandError(
                f"Cannot execute command: {error}"
            ) from error

        stdout, stdout_truncated = self._truncate_output(
            execution.stdout
        )
        stderr, stderr_truncated = self._truncate_output(
            execution.stderr
        )

        return RunCommandResult(
            command=command_args,
            exit_code=execution.exit_code,
            stdout=stdout,
            stderr=stderr,
            timed_out=execution.timed_out,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
        )

    @staticmethod
    def _truncate_output(output: str) -> tuple[str, bool]:
        encoded = output.encode("utf-8")
        truncated = len(encoded) > MAX_OUTPUT_SIZE

        if truncated:
            encoded = encoded[:MAX_OUTPUT_SIZE]

        decoded = encoded.decode("utf-8", errors="replace")
        decoded = decoded.replace("\r\n", "\n").replace("\r", "\n")

        return decoded, truncated
