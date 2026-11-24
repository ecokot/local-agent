from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    description: str = Field(min_length=1)
    action: str = Field(min_length=1)
    parameters: dict[str, Any]


class _EmptyParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PytestParameters(_EmptyParameters):
    """Параметры фиксированной проверки pytest."""


class RuffParameters(_EmptyParameters):
    """Параметры фиксированной проверки Ruff."""


class FileExistsParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    path: str = Field(min_length=1)

    @field_validator("path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("path must not be empty")

        posix_path = PurePosixPath(value)
        windows_path = PureWindowsPath(value)

        if (
            posix_path.is_absolute()
            or windows_path.is_absolute()
            or windows_path.drive
            or not posix_path.parts
            or any(part == ".." for part in posix_path.parts)
            or any(part == ".." for part in windows_path.parts)
        ):
            raise ValueError("path must be a safe relative path")

        return value


class _VerificationCriterionBase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    description: str = Field(min_length=1)
    required: bool = True


class PytestCriterion(_VerificationCriterionBase):
    type: Literal["pytest"]
    parameters: PytestParameters


class RuffCriterion(_VerificationCriterionBase):
    type: Literal["ruff"]
    parameters: RuffParameters


class FileExistsCriterion(_VerificationCriterionBase):
    type: Literal["file_exists"]
    parameters: FileExistsParameters


VerificationCriterion = Annotated[
    PytestCriterion | RuffCriterion | FileExistsCriterion,
    Field(discriminator="type"),
]


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    summary: str = Field(min_length=1)
    steps: list[PlanStep] = Field(min_length=1)
    verification_criteria: list[VerificationCriterion] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_criterion_ids(self) -> "Plan":
        criterion_ids = [
            criterion.id for criterion in self.verification_criteria
        ]
        if len(criterion_ids) != len(set(criterion_ids)):
            raise ValueError("verification criterion IDs must be unique")
        return self
