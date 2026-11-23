
import subprocess
from pathlib import Path

import pytest

from local_agent.execution.backend import ExecutionBackend
from local_agent.execution.workspace import Workspace

IMAGE = "local-agent-python-test:3.12"
WORKSPACE_PATH = Path(__file__).parent / ".workspace"


@pytest.fixture(scope="session", autouse=True)
def prepare_workspace():
    WORKSPACE_PATH.mkdir(parents=True, exist_ok=True)


@pytest.fixture
def workspace_path():
    WORKSPACE_PATH.mkdir(parents=True, exist_ok=True)

    for path in WORKSPACE_PATH.iterdir():
        if path.is_file() or path.is_symlink():
            path.unlink()
        elif path.is_dir():
            import shutil
            shutil.rmtree(path)

    return WORKSPACE_PATH


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
