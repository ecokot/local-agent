from pydantic import BaseModel, ConfigDict, Field


class ExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool
    output: str = ""
    errors: list[str] = Field(default_factory=list)


class ReviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: bool
    feedback: str = ""
