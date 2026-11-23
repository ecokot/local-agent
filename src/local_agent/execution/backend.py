import math
import re
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


_IMAGE_COMPONENT = (
    r"[a-z0-9]+(?:(?:[._]|__|[-]+)[a-z0-9]+)*"
)

_IMAGE_REFERENCE = re.compile(
    rf"^(?:"
    rf"[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?"
    rf"(?::[0-9]{{1,5}})?/"
    rf")?"
    rf"{_IMAGE_COMPONENT}(?:/{_IMAGE_COMPONENT})*"
    rf"(?::[A-Za-z0-9_][A-Za-z0-9_.-]{{0,127}})?"
    rf"(?:@sha256:[a-f0-9]{{64}})?$"
)


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

        if (
            not isinstance(image, str)
            or len(image) > 255
            or not _IMAGE_REFERENCE.fullmatch(image)
        ):
            raise ValueError("image must be a valid Docker image reference")

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

        stdout = ""
        stderr = ""
        timed_out = False
        container_exists = False

        try:
            try:
                stdout, stderr = process.communicate(
                    timeout=self._timeout_seconds
                )
            except subprocess.TimeoutExpired:
                timed_out = True

        finally:
            try:
                if timed_out or process.poll() is None:
                    stdout, stderr = self._stop_docker_process(process)
            finally:
                container_exists = self._container_exists(container_name)

                if container_exists:
                    self._remove_and_verify(container_name)

        if not timed_out and not container_exists:
            raise ExecutionBackendError(
                "Docker did not create the container or execution failed "
                "before the container started. "
                f"Exit code: {process.returncode}. "
                f"Stderr: {stderr.strip()}"
            )

        return ExecutionResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=None if timed_out else process.returncode,
            timed_out=timed_out,
        )

    def _stop_docker_process(
        self,
        process: subprocess.Popen,
    ) -> tuple[str, str]:
        """Останавливает Docker CLI и ограниченно ожидает его завершения."""

        if process.poll() is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            except OSError as error:
                raise ExecutionBackendError(
                    f"Could not terminate Docker CLI: {error}"
                ) from error

        try:
            return process.communicate(timeout=self._cleanup_timeout)
        except subprocess.TimeoutExpired as error:
            raise ExecutionBackendError(
                "Docker CLI did not terminate after being killed"
            ) from error
        except OSError as error:
            raise ExecutionBackendError(
                f"Could not wait for Docker CLI termination: {error}"
            ) from error

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

        if result.returncode == 1 and any(
            message in result.stderr.lower()
            for message in ("no such object", "no such container")
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
