from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from local_agent.execution.tools.run_command import RunCommandTool
from local_agent.execution.tools.run_tests import RunTestsTool
from local_agent.execution.workspace import Workspace
from local_agent.models.plan import (
    FileExistsCriterion,
    Plan,
    PytestCriterion,
    RuffCriterion,
)

RUFF_COMMAND = ("uv", "run", "ruff", "check", ".")


class VerificationStatus(StrEnum):
    """Фактическое состояние отдельной проверки."""

    PASSED = "passed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class VerificationDetails:
    """Доступные технические сведения о выполнении проверки."""

    command: tuple[str, ...] | None = None
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    file_path: Path | None = None


@dataclass(frozen=True, slots=True)
class CriterionVerificationResult:
    """Объективный результат выполнения одного критерия Plan."""

    criterion_id: str
    criterion_type: str
    required: bool
    passed: bool
    status: VerificationStatus
    details: VerificationDetails
    diagnostic: str | None = None


@dataclass(frozen=True, slots=True)
class VerificationReport:
    """Результаты всех критериев и итог обязательных проверок."""

    results: tuple[CriterionVerificationResult, ...]
    all_required_passed: bool


class VerificationExecutor:
    """Выполняет только типы проверок, определённые контрактом Plan."""

    def __init__(
        self,
        *,
        run_tests_tool: RunTestsTool,
        run_command_tool: RunCommandTool,
        workspace: Workspace,
    ) -> None:
        self._run_tests_tool = run_tests_tool
        self._run_command_tool = run_command_tool
        self._workspace = workspace

    def execute(self, plan: Plan) -> VerificationReport:
        """Выполняет все критерии плана, не прерываясь из-за ошибки одного из них."""

        if not isinstance(plan, Plan):
            raise TypeError("plan must be a Plan instance")

        results = tuple(
            self._execute_criterion(criterion)
            for criterion in plan.verification_criteria
        )
        all_required_passed = all(
            result.passed
            for result in results
            if result.required
        )

        return VerificationReport(
            results=results,
            all_required_passed=all_required_passed,
        )

    def _execute_criterion(self, criterion) -> CriterionVerificationResult:
        if isinstance(criterion, PytestCriterion):
            return self._execute_pytest(criterion)

        if isinstance(criterion, RuffCriterion):
            return self._execute_ruff(criterion)

        if isinstance(criterion, FileExistsCriterion):
            return self._execute_file_exists(criterion)

        return CriterionVerificationResult(
            criterion_id=criterion.id,
            criterion_type=criterion.type,
            required=criterion.required,
            passed=False,
            status=VerificationStatus.ERROR,
            details=VerificationDetails(),
            diagnostic="Unsupported verification criterion type",
        )

    def _execute_pytest(
        self,
        criterion: PytestCriterion,
    ) -> CriterionVerificationResult:
        try:
            result = self._run_tests_tool.execute()
        except Exception as error:
            return self._error_result(criterion, error)

        passed = result.exit_code == 0 and not result.timed_out
        status = self._command_status(passed, result.timed_out)
        diagnostic = self._command_diagnostic(
            status=status,
            exit_code=result.exit_code,
        )

        return CriterionVerificationResult(
            criterion_id=criterion.id,
            criterion_type=criterion.type,
            required=criterion.required,
            passed=passed,
            status=status,
            details=VerificationDetails(
                command=result.command,
                exit_code=result.exit_code,
                stdout=result.stdout,
                stderr=result.stderr,
                timed_out=result.timed_out,
                stdout_truncated=result.stdout_truncated,
                stderr_truncated=result.stderr_truncated,
            ),
            diagnostic=diagnostic,
        )

    def _execute_ruff(
        self,
        criterion: RuffCriterion,
    ) -> CriterionVerificationResult:
        try:
            result = self._run_command_tool.execute(RUFF_COMMAND)
        except Exception as error:
            return self._error_result(criterion, error)

        passed = result.exit_code == 0 and not result.timed_out
        status = self._command_status(passed, result.timed_out)
        diagnostic = self._command_diagnostic(
            status=status,
            exit_code=result.exit_code,
        )

        return CriterionVerificationResult(
            criterion_id=criterion.id,
            criterion_type=criterion.type,
            required=criterion.required,
            passed=passed,
            status=status,
            details=VerificationDetails(
                command=result.command,
                exit_code=result.exit_code,
                stdout=result.stdout,
                stderr=result.stderr,
                timed_out=result.timed_out,
                stdout_truncated=result.stdout_truncated,
                stderr_truncated=result.stderr_truncated,
            ),
            diagnostic=diagnostic,
        )

    def _execute_file_exists(
        self,
        criterion: FileExistsCriterion,
    ) -> CriterionVerificationResult:
        try:
            path = self._workspace.resolve_path(
                criterion.parameters.path
            )
            passed = path.is_file()
        except Exception as error:
            return self._error_result(criterion, error)

        if passed:
            status = VerificationStatus.PASSED
            diagnostic = None
        else:
            status = VerificationStatus.FAILED
            diagnostic = (
                "Path exists but is not a regular file"
                if path.exists()
                else "File does not exist"
            )

        return CriterionVerificationResult(
            criterion_id=criterion.id,
            criterion_type=criterion.type,
            required=criterion.required,
            passed=passed,
            status=status,
            details=VerificationDetails(file_path=path),
            diagnostic=diagnostic,
        )

    @staticmethod
    def _command_status(
        passed: bool,
        timed_out: bool,
    ) -> VerificationStatus:
        if timed_out:
            return VerificationStatus.TIMED_OUT
        if passed:
            return VerificationStatus.PASSED
        return VerificationStatus.FAILED

    @staticmethod
    def _command_diagnostic(
        *,
        status: VerificationStatus,
        exit_code: int | None,
    ) -> str | None:
        if status is VerificationStatus.TIMED_OUT:
            return "Verification command timed out"
        if status is VerificationStatus.FAILED:
            return f"Verification command exited with code {exit_code}"
        return None

    @staticmethod
    def _error_result(criterion, error: Exception) -> CriterionVerificationResult:
        return CriterionVerificationResult(
            criterion_id=criterion.id,
            criterion_type=criterion.type,
            required=criterion.required,
            passed=False,
            status=VerificationStatus.ERROR,
            details=VerificationDetails(),
            diagnostic=f"{type(error).__name__}: {error}",
        )
