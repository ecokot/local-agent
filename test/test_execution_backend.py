import subprocess

import pytest

from local_agent.execution.backend import (
    ExecutionBackend,
    ExecutionBackendError,
    ExecutionResult,
)
from local_agent.execution.workspace import Workspace


def make_docker_mock(monkeypatch, process):
    """Создаёт имитацию Docker CLI и управления контейнером."""

    state = {"container_exists": False, "removed": False}
    calls = []

    def fake_popen(command, **kwargs):
        calls.append(command)
        state["container_exists"] = True
        return process

    def fake_run(command, **kwargs):
        if command[:2] == ["docker", "inspect"]:
            return subprocess.CompletedProcess(
                command,
                0 if state["container_exists"] else 1,
                stdout="{}" if state["container_exists"] else "",
                stderr=(
                    ""
                    if state["container_exists"]
                    else "Error: No such container"
                ),
            )

        if command[:2] == ["docker", "rm"]:
            state["container_exists"] = False
            state["removed"] = True

        return subprocess.CompletedProcess(
            command,
            0,
            stdout="",
            stderr="",
        )

    monkeypatch.setattr(
        "local_agent.execution.backend.subprocess.Popen",
        fake_popen,
    )
    monkeypatch.setattr(
        "local_agent.execution.backend.subprocess.run",
        fake_run,
    )

    return state, calls


class FakeProcess:
    def __init__(self, stdout="", stderr="", returncode=None):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.killed = False
        self.communicate_calls = 0

    def poll(self):
        return self.returncode

    def communicate(self, timeout=None):
        self.communicate_calls += 1
        self.returncode = 0
        return self.stdout, self.stderr

    def kill(self):
        self.killed = True
        self.returncode = -9


def test_execute_builds_restricted_docker_command(
    tmp_path,
    monkeypatch,
):
    workspace = Workspace(tmp_path)
    backend = ExecutionBackend(workspace=workspace)

    process = FakeProcess(stdout="Hello\n")
    state, calls = make_docker_mock(monkeypatch, process)

    result = backend.execute(["python", "-c", "print('Hello')"])

    docker_command = calls[0]

    assert result == ExecutionResult(
        stdout="Hello\n",
        stderr="",
        exit_code=0,
        timed_out=False,
    )

    assert docker_command[:2] == ["docker", "run"]
    assert "--pull=never" in docker_command

    assert docker_command[
        docker_command.index("--network") + 1
    ] == "none"

    assert docker_command[
        docker_command.index("--memory") + 1
    ] == "512m"

    assert docker_command[
        docker_command.index("--cpus") + 1
    ] == "1"

    assert docker_command[
        docker_command.index("--pids-limit") + 1
    ] == "64"

    assert docker_command[
        docker_command.index("--cap-drop") + 1
    ] == "ALL"

    assert docker_command[
        docker_command.index("--security-opt") + 1
    ] == "no-new-privileges"

    assert docker_command[
        docker_command.index("--user") + 1
    ] == "10000:10000"

    assert str(workspace.root) in docker_command[
        docker_command.index("--mount") + 1
    ]

    assert docker_command[-3:] == [
        "python",
        "-c",
        "print('Hello')",
    ]

    assert state["removed"] is True
    assert state["container_exists"] is False
    assert process.killed is False


@pytest.mark.parametrize(
    "command",
    [
        [],
        "",
        "python",
        ["python", "\x00"],
        ["python", 123],
    ],
)
def test_execute_rejects_invalid_command(tmp_path, command):
    backend = ExecutionBackend(workspace=Workspace(tmp_path))

    with pytest.raises(ValueError):
        backend.execute(command)


@pytest.mark.parametrize(
    "image",
    [
        "",
        " ",
        "--privileged",
        "--volume=/:/host",
        "--mount=type=bind,source=/,target=/host",
        "-v",
        "ubuntu --privileged",
        "ubuntu; --privileged",
        "ubuntu:latest --network=host",
        "/host",
        "../image",
        "UPPERCASE/IMAGE",
        "image with spaces",
    ],
)
def test_execute_rejects_invalid_image(tmp_path, image):
    with pytest.raises(ValueError, match="image"):
        ExecutionBackend(
            workspace=Workspace(tmp_path),
            image=image,
        )


@pytest.mark.parametrize(
    "image",
    [
        "local-agent-python-test:3.12",
        "python:3.12-slim",
        "ubuntu",
        "ghcr.io/example/project:latest",
        "localhost:5000/example/project:v1.2",
        "example/project_name:tag_1",
        f"example/image@sha256:{'a' * 64}",
    ],
)
def test_execute_accepts_valid_image_reference(tmp_path, image):
    backend = ExecutionBackend(
        workspace=Workspace(tmp_path),
        image=image,
    )

    assert backend._image == image


def test_execute_rejects_nonpositive_timeout(tmp_path):
    with pytest.raises(ValueError, match="timeout"):
        ExecutionBackend(
            workspace=Workspace(tmp_path),
            timeout_seconds=0,
        )


@pytest.mark.parametrize(
    "timeout",
    [
        0,
        -1,
        True,
        False,
        float("nan"),
        float("inf"),
        float("-inf"),
        "60",
        None,
    ],
)
def test_execute_rejects_invalid_timeout(tmp_path, timeout):
    with pytest.raises(ValueError, match="timeout_seconds"):
        ExecutionBackend(
            workspace=Workspace(tmp_path),
            timeout_seconds=timeout,
        )


def test_execute_reports_docker_start_failure(
    tmp_path,
    monkeypatch,
):
    backend = ExecutionBackend(workspace=Workspace(tmp_path))

    def fake_popen(*args, **kwargs):
        raise OSError("Docker is unavailable")

    monkeypatch.setattr(
        "local_agent.execution.backend.subprocess.Popen",
        fake_popen,
    )

    with pytest.raises(ExecutionBackendError, match="Docker CLI"):
        backend.execute(["python", "--version"])


def test_execute_handles_timeout_and_removes_container(
    tmp_path,
    monkeypatch,
):
    backend = ExecutionBackend(
        workspace=Workspace(tmp_path),
        timeout_seconds=1,
    )

    class TimeoutProcess(FakeProcess):
        def communicate(self, timeout=None):
            self.communicate_calls += 1

            if self.communicate_calls == 1:
                raise subprocess.TimeoutExpired(
                    cmd="docker run",
                    timeout=timeout,
                )

            return "Partial output\n", ""

    process = TimeoutProcess()
    state, _ = make_docker_mock(monkeypatch, process)

    result = backend.execute(
        ["python", "-c", "import time; time.sleep(10)"]
    )

    assert result == ExecutionResult(
        stdout="Partial output\n",
        stderr="",
        exit_code=None,
        timed_out=True,
    )

    assert process.killed is True
    assert process.communicate_calls == 2
    assert state["removed"] is True
    assert state["container_exists"] is False


@pytest.mark.parametrize(
    "exception",
    [
        RuntimeError("Unexpected execution error"),
        OSError("Process communication failed"),
        KeyboardInterrupt(),
    ],
    ids=[
        "runtime-error",
        "os-error",
        "keyboard-interrupt",
    ],
)
def test_execute_cleans_up_after_execution_exception(
    tmp_path,
    monkeypatch,
    exception,
):
    backend = ExecutionBackend(workspace=Workspace(tmp_path))

    class FailingProcess(FakeProcess):
        def communicate(self, timeout=None):
            self.communicate_calls += 1

            if self.communicate_calls == 1:
                raise exception

            return self.stdout, self.stderr

    process = FailingProcess()
    state, _ = make_docker_mock(monkeypatch, process)

    with pytest.raises(type(exception)):
        backend.execute(["python", "--version"])

    assert process.killed is True
    assert state["removed"] is True
    assert state["container_exists"] is False


def test_execute_stops_docker_process_when_cleanup_fails(
    tmp_path,
    monkeypatch,
):
    backend = ExecutionBackend(
        workspace=Workspace(tmp_path),
        timeout_seconds=1,
    )

    class HangingProcess(FakeProcess):
        def communicate(self, timeout=None):
            self.communicate_calls += 1

            if self.communicate_calls == 1:
                raise subprocess.TimeoutExpired(
                    cmd="docker run",
                    timeout=timeout,
                )

            return "", ""

    process = HangingProcess()

    monkeypatch.setattr(
        "local_agent.execution.backend.subprocess.Popen",
        lambda *args, **kwargs: process,
    )

    def fake_run(command, **kwargs):
        if command[:2] == ["docker", "rm"]:
            return subprocess.CompletedProcess(
                command,
                1,
                stdout="",
                stderr="Docker cleanup failed",
            )

        if command[:2] == ["docker", "inspect"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="{}",
                stderr="",
            )

        raise AssertionError(f"Unexpected command: {command}")

    monkeypatch.setattr(
        "local_agent.execution.backend.subprocess.run",
        fake_run,
    )

    with pytest.raises(
        ExecutionBackendError,
        match="Docker container was not removed",
    ):
        backend.execute(["python", "--version"])

    assert process.killed is True
