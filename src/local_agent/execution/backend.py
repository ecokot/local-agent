import math
import subprocess
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from local_agent.execution.workspace import Workspace


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """Результат выполнения команды в изолированном контейнере."""

    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool


class ExecutionBackendError(RuntimeError):
    """Ошибка запуска или управления изолированным выполнением."""


class ExecutionBackend:
    """Выполняет команды в ограниченном Docker-контейнере."""

    def __init__(
        self,
        *,
        workspace: Workspace,
        image: str = "local-agent-python-test:3.12",
        timeout_seconds: float = 60,
    ) -> None:
        if not isinstance(workspace, Workspace):
            raise TypeError("workspace must be a Workspace instance")

        if not isinstance(image, str) or not image.strip():
            raise ValueError("image must be a non-empty string")

        if (
                isinstance(timeout_seconds, bool)
                or not isinstance(timeout_seconds, (int, float))
                or not math.isfinite(timeout_seconds)
                or timeout_seconds <= 0
        ):
            raise ValueError(
                "timeout_seconds must be a finite number greater than zero"
            )

        self._workspace = workspace
        self._image = image
        self._timeout_seconds = timeout_seconds
        self._docker = "docker"
        self._cleanup_timeout = 10

    def execute(self, command: Sequence[str]) -> ExecutionResult:
        """Запускает команду в отдельном Docker-контейнере."""

        if (
            isinstance(command, (str, bytes))
            or not isinstance(command, Sequence)
            or not command
        ):
            raise ValueError("command must be a non-empty sequence of strings")

        if not all(isinstance(arg, str) for arg in command):
            raise ValueError("All command arguments must be strings")

        if not command[0] or any("\x00" in arg for arg in command):
            raise ValueError("Invalid command argument")

        workspace_path = self._workspace.root

        if "," in str(workspace_path):
            raise ExecutionBackendError(
                "Workspace path cannot contain commas"
            )

        container_name = f"local-agent-{uuid.uuid4().hex}"

        docker_command = [
            self._docker,
            "run",
            "--pull=never",
            "--name",
            container_name,
            "--network",
            "none",
            "--memory",
            "512m",
            "--cpus",
            "1",
            "--pids-limit",
            "64",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            "10000:10000",
            "--mount",
            f"type=bind,source={workspace_path},target=/workspace",
            "--workdir",
            "/workspace",
            self._image,
            *command,
        ]

        try:
            process = subprocess.Popen(
                docker_command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as error:
            raise ExecutionBackendError(
                f"Could not start Docker CLI: {error}"
            ) from error

        try:
            stdout, stderr = process.communicate(
                timeout=self._timeout_seconds
            )
        except subprocess.TimeoutExpired:
            cleanup_error = None

            try:
                self._remove_and_verify(container_name)
            except ExecutionBackendError as error:
                cleanup_error = error

            try:
                stdout, stderr = process.communicate(
                    timeout=self._cleanup_timeout
                )
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()

            if cleanup_error is not None:
                raise cleanup_error

            return ExecutionResult(
                stdout=stdout,
                stderr=stderr,
                exit_code=None,
                timed_out=True,
            )

        container_exists = self._container_exists(container_name)

        if not container_exists:
            raise ExecutionBackendError(
                "Docker did not create the container or execution failed "
                f"before the container started. "
                f"Exit code: {process.returncode}. "
                f"Stderr: {stderr.strip()}"
            )

        self._remove_and_verify(container_name)

        return ExecutionResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=process.returncode,
            timed_out=False,
        )

    def _container_exists(self, container_name: str) -> bool:
        """Проверяет наличие контейнера, отличая его отсутствие от ошибки Docker."""

        try:
            result = subprocess.run(
                [self._docker, "inspect", container_name],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self._cleanup_timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ExecutionBackendError(
                f"Could not inspect Docker container: {error}"
            ) from error

        if result.returncode == 0:
            return True

        if (
            result.returncode == 1
            and "no such object" in result.stderr.lower()
        ):
            return False

        raise ExecutionBackendError(
            "Could not determine Docker container state: "
            f"{result.stderr.strip()}"
        )

    def _remove_and_verify(self, container_name: str) -> None:
        """Удаляет контейнер и проверяет, что он действительно исчез."""

        try:
            subprocess.run(
                [self._docker, "rm", "-f", container_name],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self._cleanup_timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ExecutionBackendError(
                f"Could not remove Docker container: {error}"
            ) from error

        if self._container_exists(container_name):
            raise ExecutionBackendError(
                f"Docker container was not removed: {container_name}"
            )
