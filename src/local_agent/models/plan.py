from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    description: str = Field(min_length=1)
    action: str = Field(min_length=1)
    parameters: dict[str, Any]


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    summary: str = Field(min_length=1)
    steps: list[PlanStep] = Field(min_length=1)
