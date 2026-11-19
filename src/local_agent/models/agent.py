from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    success: bool
    output: str = ""
    errors: list[str] = Field(default_factory=list)


class ReviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    approved: bool
    feedback: str = ""

    @model_validator(mode="after")
    def feedback_required_for_rejection(self) -> Self:
        if not self.approved and not self.feedback.strip():
            raise ValueError(
                "feedback must not be empty when approved is false"
            )
        return self
