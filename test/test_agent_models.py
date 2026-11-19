import pytest
from pydantic import ValidationError

from local_agent.models.agent import ExecutionResult, ReviewResult


def test_execution_result_success() -> None:
    result = ExecutionResult(success=True, output="Files created")

    assert result.success is True
    assert result.output == "Files created"
    assert result.errors == []


def test_execution_result_failure() -> None:
    result = ExecutionResult(
        success=False,
        errors=["Permission denied"],
    )

    assert result.success is False
    assert result.errors == ["Permission denied"]


def test_execution_result_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ExecutionResult(success=True, unknown="value")


def test_review_result_approved() -> None:
    result = ReviewResult(approved=True, feedback="Looks good")

    assert result.approved is True
    assert result.feedback == "Looks good"


def test_review_result_rejected() -> None:
    result = ReviewResult(
        approved=False,
        feedback="Missing error handling",
    )

    assert result.approved is False
    assert result.feedback == "Missing error handling"


def test_review_result_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ReviewResult(approved=True, unknown="value")