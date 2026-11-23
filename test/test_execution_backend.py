import subprocess

import pytest

from local_agent.execution.backend import (
    ExecutionBackend,
    ExecutionBackendError,
    ExecutionResult,
)
from local_agent.execution.workspace import Workspace


def test_execute_builds_restricted_docker_command(
    tmp_path,
    monkeypatch,
):
    workspace = Workspace(tmp_path)
    backend = ExecutionBackend(workspace=workspace)

    calls = []
    container_exists = False

    class FakeProcess:
        returncode = 0

        def communicate(self, timeout=None):
            return "Hello\n", ""

    def fake_popen(command, **kwargs):
        nonlocal container_exists

        calls.append(command)
        container_exists = True
        return FakeProcess()

    def fake_run(command, **kwargs):
        nonlocal container_exists

        if command[:2] == ["docker", "inspect"]:
            return subprocess.CompletedProcess(
                command,
                0 if container_exists else 1,
                stdout="",
                stderr="" if container_exists else "Error: No such object",
            )

        if command[:2] == ["docker", "rm"]:
            container_exists = False

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

    result = backend.execute(
        ["python", "-c", "print('Hello')"]
    )

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

    assert container_exists is False


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
def test_execute_rejects_invalid_command(
    tmp_path,
    command,
):
    backend = ExecutionBackend(
        workspace=Workspace(tmp_path)
    )

    with pytest.raises(ValueError):
        backend.execute(command)


def test_execute_rejects_empty_image(tmp_path):
    with pytest.raises(ValueError, match="image"):
        ExecutionBackend(
            workspace=Workspace(tmp_path),
            image=" ",
        )


def test_execute_rejects_nonpositive_timeout(tmp_path):
    with pytest.raises(ValueError, match="timeout"):
        ExecutionBackend(
            workspace=Workspace(tmp_path),
            timeout_seconds=0,
        )


def test_execute_reports_docker_start_failure(
    tmp_path,
    monkeypatch,
):
    backend = ExecutionBackend(
        workspace=Workspace(tmp_path)
    )

    def fake_popen(*args, **kwargs):
        raise OSError("Docker is unavailable")

    monkeypatch.setattr(
        "local_agent.execution.backend.subprocess.Popen",
        fake_popen,
    )

    with pytest.raises(
        ExecutionBackendError,
        match="Docker CLI",
    ):
        backend.execute(["python", "--version"])


def test_execute_handles_timeout_and_removes_container(
    tmp_path,
    monkeypatch,
):
    workspace = Workspace(tmp_path)
    backend = ExecutionBackend(
        workspace=workspace,
        timeout_seconds=1,
    )

    container_exists = False
    communicate_calls = 0
    removed = False

    class FakeProcess:
        returncode = -9

        def communicate(self, timeout=None):
            nonlocal communicate_calls

            communicate_calls += 1

            if communicate_calls == 1:
                raise subprocess.TimeoutExpired(
                    cmd="docker run",
                    timeout=timeout,
                )

            return "Partial output\n", ""

        def kill(self):
            raise AssertionError(
                "Process should have finished after container removal"
            )

    def fake_popen(command, **kwargs):
        nonlocal container_exists

        container_exists = True
        return FakeProcess()

    def fake_run(command, **kwargs):
        nonlocal container_exists, removed

        if command[:2] == ["docker", "rm"]:
            container_exists = False
            removed = True

        if command[:2] == ["docker", "inspect"]:
            return subprocess.CompletedProcess(
                command,
                0 if container_exists else 1,
                stdout="",
                stderr="" if container_exists else "Error: No such object",
            )

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

    result = backend.execute(["python", "-c", "import time; time.sleep(10)"])

    assert result == ExecutionResult(
        stdout="Partial output\n",
        stderr="",
        exit_code=None,
        timed_out=True,
    )
    assert communicate_calls == 2
    assert removed is True
    assert container_exists is False


def test_execute_stops_docker_process_when_cleanup_fails(
    tmp_path,
    monkeypatch,
):
    backend = ExecutionBackend(
        workspace=Workspace(tmp_path),
        timeout_seconds=1,
    )

    class FakeProcess:
        returncode = None

        def __init__(self):
            self.communicate_calls = 0
            self.killed = False

        def communicate(self, timeout=None):
            self.communicate_calls += 1

            if self.communicate_calls == 1:
                raise subprocess.TimeoutExpired(
                    cmd="docker run",
                    timeout=timeout,
                )

            if self.communicate_calls == 2:
                raise subprocess.TimeoutExpired(
                    cmd="docker run",
                    timeout=timeout,
                )

            return "", ""

        def kill(self):
            self.killed = True

    process = FakeProcess()

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
