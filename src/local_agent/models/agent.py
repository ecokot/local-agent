from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    success: bool
    output: str = ""
    errors: list[str] = Field(default_factory=list)
    waiting_for_approval: bool = False
    pending_approval_id: str | None = None
    step_results: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_approval_wait(self) -> Self:
        if self.waiting_for_approval:
            if self.success:
                raise ValueError(
                    "An execution waiting for approval cannot be successful"
                )
            if not self.pending_approval_id:
                raise ValueError(
                    "pending_approval_id is required while waiting for approval"
                )
        elif self.pending_approval_id is not None:
            raise ValueError(
                "pending_approval_id is only valid while waiting for approval"
            )
        return self


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
