import pytest
from pydantic import ValidationError

from local_agent.models.plan import Plan, PlanStep


def make_plan() -> Plan:
    return Plan(
        summary="Create a test file",
        steps=[
            PlanStep(
                description="Create file",
                action="create_file",
                parameters={"path": "test.txt"},
            )
        ],
        verification_criteria=[
            {
                "id": "created-file",
                "type": "file_exists",
                "description": "Check that test.txt exists",
                "parameters": {"path": "test.txt"},
            }
        ],
    )


def test_plan_creation() -> None:
    plan = make_plan()

    assert plan.summary == "Create a test file"
    assert len(plan.steps) == 1
    assert plan.steps[0].action == "create_file"
    assert plan.verification_criteria[0].type == "file_exists"


def test_plan_step_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        PlanStep(
            description="Create file",
            action="create_file",
            parameters={},
            unknown="value",
        )


def test_plan_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        Plan(
            summary="Test",
            steps=[
                {
                    "description": "Create file",
                    "action": "create_file",
                    "parameters": {},
                }
            ],
            verification_criteria=[{
                "id": "check",
                "type": "pytest",
                "description": "Run tests",
                "parameters": {},
            }],
            unknown="value",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("description", ""),
        ("action", ""),
    ],
)
def test_plan_step_rejects_empty_required_strings(
    field: str,
    value: str,
) -> None:
    data = {
        "description": "Create file",
        "action": "create_file",
        "parameters": {},
    }
    data[field] = value

    with pytest.raises(ValidationError):
        PlanStep(**data)


def test_plan_rejects_empty_summary() -> None:
    with pytest.raises(ValidationError):
        Plan(summary="", steps=[make_plan().steps[0]],
             verification_criteria=make_plan().verification_criteria)


def test_plan_rejects_empty_steps() -> None:
    with pytest.raises(ValidationError):
        Plan(
            summary="Test",
            steps=[],
            verification_criteria=make_plan().verification_criteria,
        )


def test_plan_json_round_trip() -> None:
    original = make_plan()

    serialized = original.model_dump_json()
    restored = Plan.model_validate_json(serialized)

    assert restored == original


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("description", 123),
        ("action", 123),
        ("parameters", []),
    ],
)
def test_plan_step_rejects_invalid_types(
    field: str,
    value: object,
) -> None:
    data = {
        "description": "Create file",
        "action": "create_file",
        "parameters": {},
    }
    data[field] = value

    with pytest.raises(ValidationError):
        PlanStep(**data)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("summary", 123),
        ("steps", {}),
    ],
)
def test_plan_rejects_invalid_types(
    field: str,
    value: object,
) -> None:
    data = {
        "summary": "Test",
        "steps": [make_plan().steps[0]],
        "verification_criteria": make_plan().verification_criteria,
    }
    data[field] = value

    with pytest.raises(ValidationError):
        Plan(**data)


def test_plan_rejects_missing_verification_criteria() -> None:
    with pytest.raises(ValidationError, match="verification_criteria"):
        Plan.model_validate({
            "summary": "Legacy plan",
            "steps": [{
                "description": "Create file",
                "action": "create_file",
                "parameters": {"path": "test.txt"},
            }],
        })


def test_plan_rejects_empty_verification_criteria() -> None:
    with pytest.raises(ValidationError):
        Plan(
            summary="No criteria",
            steps=[make_plan().steps[0]],
            verification_criteria=[],
        )


def test_plan_rejects_duplicate_criterion_ids() -> None:
    criterion = make_plan().verification_criteria[0]
    with pytest.raises(ValidationError, match="must be unique"):
        Plan(
            summary="Duplicate IDs",
            steps=make_plan().steps,
            verification_criteria=[criterion, criterion],
        )


@pytest.mark.parametrize(
    "criterion",
    [
        {
            "id": "run-tests",
            "type": "unknown",
            "description": "Unknown check",
            "parameters": {},
        },
        {
            "id": "run-tests",
            "type": "pytest",
            "description": "Run tests",
            "parameters": {"command": "rm -rf"},
        },
        {
            "id": "run-tests",
            "type": "pytest",
            "description": "Run tests",
            "parameters": {"passed": True},
        },
        {
            "id": "run-tests",
            "type": "pytest",
            "description": "Run tests",
            "parameters": {},
            "passed": True,
        },
    ],
)
def test_plan_rejects_unknown_or_unsafe_criterion_data(
    criterion: dict,
) -> None:
    with pytest.raises(ValidationError):
        Plan(
            summary="Invalid criterion",
            steps=make_plan().steps,
            verification_criteria=[criterion],
        )


@pytest.mark.parametrize("path", ["", " ", "../secret.txt", "C:\\secret.txt", "/etc/passwd", "..\\secret.txt", "\\secret.txt", "/secret.txt"])
def test_file_exists_criterion_rejects_unsafe_paths(path: str) -> None:
    with pytest.raises(ValidationError):
        Plan(
            summary="Unsafe path",
            steps=make_plan().steps,
            verification_criteria=[{
                "id": "file-check",
                "type": "file_exists",
                "description": "Check file",
                "parameters": {"path": path},
            }],
        )


def test_plan_accepts_all_supported_criterion_types() -> None:
    plan = Plan(
        summary="Supported checks",
        steps=make_plan().steps,
        verification_criteria=[
            {
                "id": "tests",
                "type": "pytest",
                "description": "Run the project test suite",
                "parameters": {},
            },
            {
                "id": "lint",
                "type": "ruff",
                "description": "Run Ruff",
                "parameters": {},
                "required": False,
            },
            {
                "id": "artifact",
                "type": "file_exists",
                "description": "Check generated file",
                "parameters": {"path": "output/result.txt"},
            },
        ],
    )

    assert [item.type for item in plan.verification_criteria] == [
        "pytest", "ruff", "file_exists"
    ]
    assert plan.verification_criteria[1].required is False
