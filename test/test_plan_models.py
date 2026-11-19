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
    )


def test_plan_creation() -> None:
    plan = make_plan()

    assert plan.summary == "Create a test file"
    assert len(plan.steps) == 1
    assert plan.steps[0].action == "create_file"


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
        Plan(summary="", steps=[make_plan().steps[0]])


def test_plan_rejects_empty_steps() -> None:
    with pytest.raises(ValidationError):
        Plan(summary="Test", steps=[])


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
    }
    data[field] = value

    with pytest.raises(ValidationError):
        Plan(**data)
