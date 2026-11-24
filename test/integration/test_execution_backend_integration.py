import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from local_agent.execution.backend import ExecutionBackend
from local_agent.execution.tools.run_command import RunCommandTool
from local_agent.execution.tools.run_tests import RunTestsTool
from local_agent.execution.workspace import Workspace

IMAGE = "local-agent-python-test:3.12"
WORKSPACE_PATH = Path(__file__).parent / ".workspace"


@pytest.fixture
def workspace_path():
    WORKSPACE_PATH.mkdir(parents=True, exist_ok=True)

    try:
        yield WORKSPACE_PATH
    finally:
        shutil.rmtree(WORKSPACE_PATH, ignore_errors=True)


@pytest.fixture
def backend(workspace_path):
    workspace = Workspace(workspace_path)

    return ExecutionBackend(
        workspace=workspace,
        image=IMAGE,
        timeout_seconds=60,
    )


@pytest.fixture(scope="session", autouse=True)
def require_docker_image():
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", IMAGE],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        pytest.fail(f"Docker is unavailable: {error}")

    if result.returncode != 0:
        pytest.fail(f"Docker image is unavailable: {IMAGE}")


def assert_container_removed(container_name):
    result = subprocess.run(
        ["docker", "container", "inspect", container_name],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode != 0
    assert (
            "no such" in result.stderr.lower()
    ), result.stderr


def test_executes_python_command(backend):
    result = backend.execute(
        ["python", "-c", "print('Hello from Docker')"]
    )

    assert result.stdout == "Hello from Docker\n"
    assert result.stderr == ""
    assert result.exit_code == 0
    assert result.timed_out is False


def test_captures_stdout_and_stderr(backend):
    result = backend.execute(
        [
            "python",
            "-c",
            "import sys; print('out'); print('err', file=sys.stderr)",
        ]
    )

    assert result.stdout == "out\n"
    assert result.stderr == "err\n"
    assert result.exit_code == 0


def test_returns_nonzero_exit_code(backend):
    result = backend.execute(
        ["python", "-c", "raise SystemExit(7)"]
    )

    assert result.exit_code == 7
    assert result.timed_out is False


def test_reads_and_writes_workspace_files(backend, workspace_path):
    result = backend.execute(
        [
            "python",
            "-c",
            (
                "from pathlib import Path; "
                "Path('result.txt').write_text('workspace works'); "
                "print(Path('result.txt').read_text())"
            ),
        ]
    )

    assert result.exit_code == 0
    assert result.stdout == "workspace works\n"
    assert (workspace_path / "result.txt").read_text() == "workspace works"


def test_runs_as_unprivileged_user(backend):
    result = backend.execute(
        [
            "python",
            "-c",
            "import os; print(os.getuid(), os.getgid())",
        ]
    )

    assert result.exit_code == 0
    assert result.stdout.strip() == "10000 10000"


def test_container_has_no_network_access(backend):
    result = backend.execute(
        [
            "python",
            "-c",
            (
                "import socket; "
                "s = socket.socket(); "
                "s.settimeout(2); "
                "s.connect(('1.1.1.1', 443))"
            ),
        ]
    )

    assert result.exit_code != 0


def test_host_files_outside_workspace_are_not_accessible(
        backend,
        workspace_path,
):
    secret_path = workspace_path.parent / "host-secret.txt"
    secret_content = "HOST_SECRET_6B_TEST"

    secret_path.write_text(secret_content, encoding="utf-8")

    try:
        result = backend.execute(
            [
                "python",
                "-c",
                (
                    "from pathlib import Path; "
                    "paths = ["
                    "Path('/workspace/../host-secret.txt'), "
                    "Path('/mnt/c'), "
                    "Path('/host_mnt/c'), "
                    "Path('/run/desktop/mnt/host/c')"
                    "]; "
                    "visible = [str(p) for p in paths if p.exists()]; "
                    "print(visible)"
                ),
            ]
        )

        assert result.exit_code == 0
        assert result.stdout.strip() == "[]"
        assert secret_content not in result.stdout
    finally:
        secret_path.unlink(missing_ok=True)


def test_container_has_no_linux_capabilities(backend):
    result = backend.execute(
        [
            "python",
            "-c",
            (
                "from pathlib import Path; "
                "status = Path('/proc/self/status').read_text(); "
                "values = dict("
                "line.split(':', 1) "
                "for line in status.splitlines() "
                "if ':' in line"
                "); "
                "print('CapEff=' + values['CapEff'].strip()); "
                "print('NoNewPrivs=' + values['NoNewPrivs'].strip())"
            ),
        ]
    )

    assert result.exit_code == 0

    values = dict(
        line.split("=", 1)
        for line in result.stdout.splitlines()
    )

    assert values["CapEff"] == "0000000000000000"
    assert values["NoNewPrivs"] == "1"


def test_container_has_resource_limits_applied(backend):
    result = backend.execute(
        [
            "python",
            "-c",
            (
                "from pathlib import Path; "
                "root = Path('/sys/fs/cgroup'); "
                "print('memory.max=' + (root / 'memory.max').read_text().strip()); "
                "print('pids.max=' + (root / 'pids.max').read_text().strip()); "
                "print('cpu.max=' + (root / 'cpu.max').read_text().strip())"
            ),
        ]
    )

    assert result.exit_code == 0

    values = dict(
        line.split("=", 1)
        for line in result.stdout.splitlines()
    )

    assert values["memory.max"] == "536870912"
    assert values["pids.max"] == "64"
    assert values["cpu.max"] == "100000 100000"


def test_container_is_removed_after_normal_execution(
        workspace_path,
        monkeypatch,
):
    container_name = "local-agent-integration-normal-6b"

    monkeypatch.setattr(
        "local_agent.execution.backend.uuid.uuid4",
        lambda: SimpleNamespace(hex="integration-normal-6b"),
    )

    backend = ExecutionBackend(
        workspace=Workspace(workspace_path),
        image=IMAGE,
        timeout_seconds=60,
    )

    result = backend.execute(
        ["python", "-c", "print('normal execution')"]
    )

    assert result.exit_code == 0
    assert result.timed_out is False
    assert result.stdout == "normal execution\n"

    assert_container_removed(container_name)


def test_container_is_removed_after_timeout(
        workspace_path,
        monkeypatch,
):
    container_name = "local-agent-integration-timeout-6b"

    monkeypatch.setattr(
        "local_agent.execution.backend.uuid.uuid4",
        lambda: SimpleNamespace(hex="integration-timeout-6b"),
    )

    backend = ExecutionBackend(
        workspace=Workspace(workspace_path),
        image=IMAGE,
        timeout_seconds=2,
    )

    result = backend.execute(
        [
            "python",
            "-c",
            "import time; time.sleep(30)",
        ]
    )

    assert result.timed_out is True
    assert result.exit_code is None

    assert_container_removed(container_name)


def test_run_command_tool_executes_through_docker_backend(backend):
    tool = RunCommandTool(backend)

    result = tool.execute(
        [
            "python",
            "-c",
            (
                "import os, sys; "
                "print('stdout from Docker'); "
                "print('stderr from Docker', file=sys.stderr); "
                "print(f'{sys.platform} {os.getuid()} {os.getgid()}')"
            ),
        ]
    )

    assert result.command[0] == "python"
    assert result.stdout == (
        "stdout from Docker\n"
        "linux 10000 10000\n"
    )
    assert result.stderr == "stderr from Docker\n"
    assert result.exit_code == 0
    assert result.timed_out is False


def test_run_tests_tool_executes_pytest_through_docker_backend(
        backend,
        workspace_path,
):
    test_file = workspace_path / "test_sample.py"
    test_file.write_text(
        (
            "import os\n"
            "import sys\n"
            "\n"
            "def test_runs_inside_docker():\n"
            "    assert sys.platform == 'linux'\n"
            "    assert os.getuid() == 10000\n"
            "    assert os.getgid() == 10000\n"
        ),
        encoding="utf-8",
    )

    command_tool = RunCommandTool(backend)
    tool = RunTestsTool(command_tool)

    result = tool.execute()

    assert result.command == ("uv", "run", "pytest", "-v")
    assert result.passed is True
    assert result.exit_code == 0
    assert "test_sample.py" in result.stdout
    assert "1 passed" in result.stdout
    assert result.stderr == ""
    assert result.timed_out is False
    assert result.stdout_truncated is False
    assert result.stderr_truncated is False
