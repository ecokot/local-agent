
from local_agent.approval_manager import ApprovalManager
from local_agent.models.plan import Plan
from local_agent.orchestrator import Orchestrator


class UserInterface:
    def __init__(
        self,
        orchestrator: Orchestrator,
        approval_manager: ApprovalManager,
    ) -> None:
        self.orchestrator = orchestrator
        self.approval_manager = approval_manager

    def run(self) -> None:
        print("Local Agent")
        print("Введите задачу или 'exit' для выхода.")

        while True:
            description = input("\nЗадача: ").strip()

            if description.lower() == "exit":
                break

            if not description:
                print("Задача не может быть пустой.")
                continue

            self._run_task(description)
    def _run_task(self, description: str) -> None:
        approval = self.orchestrator.prepare_task(description)

        plan = Plan.model_validate(
            approval.parameters["plan"]
        )

        print(f"\nПлан: {plan.summary}")

        for index, step in enumerate(plan.steps, start=1):
            print(f"\n{index}. {step.description}")
            print(f"   Действие: {step.action}")
            print(f"   Параметры: {step.parameters}")

        answer = input("\nВыполнить план? [y/n]: ").strip().lower()

        if answer == "y":
            self.approval_manager.approve(approval.approval_id)

            review = self.orchestrator.execute_approved_plan(
                approval.approval_id
            )

            print(f"\nРезультат проверки: {review.approved}")
            print(f"Комментарий: {review.feedback}")

        else:
            self.orchestrator.reject_plan(approval.approval_id)
            print("\nПлан отклонён. Задача отменена.")
