from pathlib import Path
from unittest.mock import Mock

import pytest

from local_agent.execution.tools.run_command import (
    RunCommandError,
    RunCommandResult,
    RunCommandTool,
)
from local_agent.execution.tools.run_tests import (
    RunTestsResult,
    RunTestsTool,
)
from local_agent.execution.verification import (
    RUFF_COMMAND,
    VerificationExecutor,
    VerificationStatus,
)
from local_agent.execution.workspace import Workspace, WorkspaceError
from local_agent.models.plan import Plan


def make_plan(criteria: list[dict]) -> Plan:
    return Plan(
        summary="Verify project",
        steps=[{
            "description": "Do work",
            "action": "write_file",
            "parameters": {},
        }],
        verification_criteria=criteria,
    )


def pytest_criterion(*, required: bool = True) -> dict:
    return {
        "id": "tests",
        "type": "pytest",
        "description": "Run tests",
        "required": required,
        "parameters": {},
    }


def ruff_criterion(*, required: bool = True) -> dict:
    return {
        "id": "lint",
        "type": "ruff",
        "description": "Run Ruff",
        "required": required,
        "parameters": {},
    }


def file_criterion(
    path: str,
    *,
    criterion_id: str = "artifact",
    required: bool = True,
) -> dict:
    return {
        "id": criterion_id,
        "type": "file_exists",
        "description": "Check artifact",
        "required": required,
        "parameters": {"path": path},
    }


def make_pytest_result(
    *,
    exit_code: int | None = 0,
    timed_out: bool = False,
) -> RunTestsResult:
    return RunTestsResult(
        command=("uv", "run", "pytest", "-v"),
        exit_code=exit_code,
        stdout="pytest output",
        stderr="pytest stderr",
        timed_out=timed_out,
        stdout_truncated=False,
        stderr_truncated=False,
        passed=exit_code == 0 and not timed_out,
    )


def make_command_result(
    *,
    exit_code: int | None = 0,
    timed_out: bool = False,
) -> RunCommandResult:
    return RunCommandResult(
        command=RUFF_COMMAND,
        exit_code=exit_code,
        stdout="ruff output",
        stderr="ruff stderr",
        timed_out=timed_out,
        stdout_truncated=True,
        stderr_truncated=False,
    )


@pytest.fixture
def tools(tmp_path: Path):
    pytest_tool = Mock(spec=RunTestsTool)
    command_tool = Mock(spec=RunCommandTool)
    workspace = Workspace(tmp_path)
    executor = VerificationExecutor(
        run_tests_tool=pytest_tool,
        run_command_tool=command_tool,
        workspace=workspace,
    )
    return executor, pytest_tool, command_tool, tmp_path


def test_pytest_success(tools):
    executor, pytest_tool, _, _ = tools
    pytest_tool.execute.return_value = make_pytest_result()

    result = executor.execute(make_plan([pytest_criterion()])).results[0]

    assert result.passed is True
    assert result.status is VerificationStatus.PASSED
    assert result.details.exit_code == 0
    assert result.details.stdout == "pytest output"


def test_pytest_nonzero_exit_is_failure(tools):
    executor, pytest_tool, _, _ = tools
    pytest_tool.execute.return_value = make_pytest_result(exit_code=1)

    result = executor.execute(make_plan([pytest_criterion()])).results[0]

    assert result.passed is False
    assert result.status is VerificationStatus.FAILED
    assert result.details.exit_code == 1


def test_pytest_timeout(tools):
    executor, pytest_tool, _, _ = tools
    pytest_tool.execute.return_value = make_pytest_result(
        exit_code=None,
        timed_out=True,
    )

    result = executor.execute(make_plan([pytest_criterion()])).results[0]

    assert result.passed is False
    assert result.status is VerificationStatus.TIMED_OUT
    assert result.details.timed_out is True


def test_ruff_runs_only_fixed_command_and_preserves_output(tools):
    executor, _, command_tool, _ = tools
    command_tool.execute.return_value = make_command_result()

    result = executor.execute(make_plan([ruff_criterion()])).results[0]

    command_tool.execute.assert_called_once_with(RUFF_COMMAND)
    assert result.passed is True
    assert result.details.command == RUFF_COMMAND
    assert result.details.stdout == "ruff output"
    assert result.details.stderr == "ruff stderr"
    assert result.details.stdout_truncated is True


def test_ruff_nonzero_exit_is_failure(tools):
    executor, _, command_tool, _ = tools
    command_tool.execute.return_value = make_command_result(exit_code=2)

    result = executor.execute(make_plan([ruff_criterion()])).results[0]

    assert result.passed is False
    assert result.status is VerificationStatus.FAILED
    assert result.details.exit_code == 2


def test_ruff_timeout(tools):
    executor, _, command_tool, _ = tools
    command_tool.execute.return_value = make_command_result(
        exit_code=None,
        timed_out=True,
    )

    result = executor.execute(make_plan([ruff_criterion()])).results[0]

    assert result.passed is False
    assert result.status is VerificationStatus.TIMED_OUT


def test_file_exists_success(tools):
    executor, _, _, root = tools
    target = root / "artifact.txt"
    target.write_text("done", encoding="utf-8")

    result = executor.execute(
        make_plan([file_criterion("artifact.txt")])
    ).results[0]

    assert result.passed is True
    assert result.status is VerificationStatus.PASSED
    assert result.details.file_path == target


def test_missing_file_is_failure_not_exception(tools):
    executor, _, _, _ = tools

    result = executor.execute(
        make_plan([file_criterion("missing.txt")])
    ).results[0]

    assert result.passed is False
    assert result.status is VerificationStatus.FAILED
    assert result.diagnostic == "File does not exist"


def test_directory_instead_of_file_is_failure(tools):
    executor, _, _, root = tools
    (root / "folder").mkdir()

    result = executor.execute(
        make_plan([file_criterion("folder")])
    ).results[0]

    assert result.passed is False
    assert result.status is VerificationStatus.FAILED
    assert result.diagnostic == "Path exists but is not a regular file"


def test_symlink_path_is_rejected_by_workspace(tools):
    executor, _, _, root = tools
    outside = root.parent / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    link = root / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Symbolic links are unavailable")

    result = executor.execute(
        make_plan([file_criterion("link/secret.txt")])
    ).results[0]

    assert result.passed is False
    assert result.status is VerificationStatus.ERROR
    assert "WorkspaceError" in result.diagnostic
    assert result.details.file_path is None


def test_tool_error_is_reported_as_result(tools):
    executor, pytest_tool, _, _ = tools
    pytest_tool.execute.side_effect = RunCommandError("Docker unavailable")

    result = executor.execute(make_plan([pytest_criterion()])).results[0]

    assert result.passed is False
    assert result.status is VerificationStatus.ERROR
    assert "Docker unavailable" in result.diagnostic


def test_multiple_criteria_run_and_keep_success_after_failure(tools):
    executor, pytest_tool, command_tool, root = tools
    pytest_tool.execute.return_value = make_pytest_result()
    command_tool.execute.side_effect = RunCommandError("Ruff unavailable")
    (root / "artifact.txt").write_text("ok", encoding="utf-8")

    report = executor.execute(
        make_plan([
            pytest_criterion(),
            ruff_criterion(),
            file_criterion("artifact.txt"),
        ])
    )

    assert [result.status for result in report.results] == [
        VerificationStatus.PASSED,
        VerificationStatus.ERROR,
        VerificationStatus.PASSED,
    ]
    assert report.results[0].passed is True
    assert report.results[2].passed is True
    assert report.all_required_passed is False


def test_optional_error_does_not_fail_overall_result(tools):
    executor, pytest_tool, command_tool, _ = tools
    pytest_tool.execute.return_value = make_pytest_result()
    command_tool.execute.side_effect = RunCommandError("Ruff unavailable")

    report = executor.execute(
        make_plan([
            pytest_criterion(required=True),
            ruff_criterion(required=False),
        ])
    )

    assert report.results[1].status is VerificationStatus.ERROR
    assert report.results[1].passed is False
    assert report.results[1].required is False
    assert report.all_required_passed is True


def test_required_failure_fails_overall_result_even_if_optional_passes(tools):
    executor, pytest_tool, command_tool, _ = tools
    pytest_tool.execute.return_value = make_pytest_result(exit_code=1)
    command_tool.execute.return_value = make_command_result()

    report = executor.execute(
        make_plan([
            pytest_criterion(required=True),
            ruff_criterion(required=False),
        ])
    )

    assert report.all_required_passed is False


def test_model_criterion_data_cannot_supply_command_or_code(tools):
    executor, _, command_tool, _ = tools
    command_tool.execute.return_value = make_command_result()

    malicious = ruff_criterion()
    malicious["parameters"] = {
        "command": ["python", "-c", "print('untrusted')"],
        "code": "raise SystemExit(1)",
    }
    with pytest.raises(ValueError):
        plan = make_plan([malicious])
        executor.execute(plan)

    command_tool.execute.assert_not_called()


def test_unexpected_tool_exception_is_not_masked(tools):
    executor, pytest_tool, command_tool, _ = tools
    pytest_tool.execute.side_effect = RuntimeError("unexpected")

    with pytest.raises(RuntimeError, match="unexpected"):
        executor.execute(
            make_plan([pytest_criterion(), ruff_criterion()])
        )

    command_tool.execute.assert_not_called()


def test_invalid_tool_result_is_not_masked(tools):
    executor, pytest_tool, command_tool, _ = tools
    pytest_tool.execute.return_value = None

    with pytest.raises(AttributeError):
        executor.execute(
            make_plan([pytest_criterion(), ruff_criterion()])
        )

    command_tool.execute.assert_not_called()



@pytest.mark.parametrize(
    "error",
    [RunCommandError("Command execution failed")],
    ids=["run-command-error"],
)
@pytest.mark.parametrize(
    "criterion_factory",
    [pytest_criterion, ruff_criterion],
    ids=["pytest", "ruff"],
)
def test_command_errors_become_error_and_next_criterion_runs(
    tools,
    error,
    criterion_factory,
):
    executor, pytest_tool, command_tool, root = tools
    (root / "artifact.txt").write_text("ok", encoding="utf-8")

    if criterion_factory is pytest_criterion:
        pytest_tool.execute.side_effect = error
    else:
        command_tool.execute.side_effect = error

    report = executor.execute(
        make_plan([criterion_factory(), file_criterion("artifact.txt")])
    )

    failed = report.results[0]
    following = report.results[1]
    assert failed.status is VerificationStatus.ERROR
    assert failed.passed is False
    assert failed.criterion_id == (
        "tests" if criterion_factory is pytest_criterion else "lint"
    )
    assert failed.criterion_type == (
        "pytest" if criterion_factory is pytest_criterion else "ruff"
    )
    assert failed.required is True
    assert error.__class__.__name__ in failed.diagnostic
    assert following.status is VerificationStatus.PASSED
    assert report.all_required_passed is False


def test_workspace_error_becomes_error_and_next_criterion_runs(tools):
    executor, _, _, root = tools
    target = root / "artifact.txt"
    target.write_text("ok", encoding="utf-8")
    executor._workspace.resolve_path = Mock(
        side_effect=[
            WorkspaceError("Workspace path cannot be resolved"),
            target,
        ]
    )

    report = executor.execute(
        make_plan([
            file_criterion("blocked.txt", criterion_id="blocked"),
            file_criterion("artifact.txt"),
        ])
    )

    failed, following = report.results
    assert failed.status is VerificationStatus.ERROR
    assert failed.criterion_id == "blocked"
    assert failed.criterion_type == "file_exists"
    assert failed.required is True
    assert "WorkspaceError" in failed.diagnostic
    assert following.status is VerificationStatus.PASSED
    assert report.all_required_passed is False


def test_file_system_os_error_becomes_error_and_next_criterion_runs(
    tools,
    monkeypatch,
):
    executor, _, _, root = tools
    (root / "artifact.txt").write_text("ok", encoding="utf-8")
    original_is_file = Path.is_file
    calls = 0

    def fail_first_is_file(path):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PermissionError("Access denied")
        return original_is_file(path)

    monkeypatch.setattr(Path, "is_file", fail_first_is_file)

    report = executor.execute(
        make_plan([
            file_criterion("blocked.txt", criterion_id="blocked"),
            file_criterion("artifact.txt"),
        ])
    )

    failed, following = report.results
    assert failed.status is VerificationStatus.ERROR
    assert failed.criterion_id == "blocked"
    assert failed.criterion_type == "file_exists"
    assert failed.required is True
    assert "PermissionError" in failed.diagnostic
    assert following.status is VerificationStatus.PASSED
    assert report.all_required_passed is False


def test_rejects_non_plan_input(tools):
    executor, _, _, _ = tools

    with pytest.raises(TypeError, match="Plan"):
        executor.execute({"verification_criteria": []})
