from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from local_agent.models.agent import ExecutionResult, ReviewResult
    from local_agent.models.plan import Plan
    from local_agent.models.task import Task


class Planner(Protocol):
    def create_plan(self, task: Task) -> Plan:
        ...

    def revise_plan(
        self,
        task: Task,
        previous_plan: Plan,
        feedback: str,
    ) -> Plan:
        ...


class Executor(Protocol):
    def execute(self, task_id: str, plan: Plan) -> ExecutionResult:
        ...


class Reviewer(Protocol):
    def review(
        self,
        plan: Plan,
        result: ExecutionResult,
    ) -> ReviewResult:
        ...