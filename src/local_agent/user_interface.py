from local_agent.approval_manager import ApprovalManager
from local_agent.models.agent import ExecutionResult, ReviewResult
from local_agent.models.approval import Approval
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
        plan_approval = self.orchestrator.prepare_task(description)

        while True:
            self._show_plan(plan_approval)

            answer = input(
                "\nВыполнить этот план? [y/n]: "
            ).strip().lower()

            if answer != "y":
                self.orchestrator.reject_plan(plan_approval.approval_id)
                print("\nПлан отклонён. Задача отменена.")
                return

            self.approval_manager.approve(plan_approval.approval_id)
            result = self.orchestrator.execute_approved_plan(
                plan_approval.approval_id
            )

            while (
                isinstance(result, ExecutionResult)
                and result.waiting_for_approval
            ):
                pending = self.approval_manager.get_approval(
                    result.pending_approval_id
                )

                print("\nТребуется подтверждение действия:")
                print(f"   Тип: {pending.action_type}")
                print(f"   Параметры: {pending.parameters}")

                decision = input(
                    "Разрешить действие? [y/n/c] "
                ).strip().lower()

                if decision == "y":
                    self.approval_manager.approve(pending.approval_id)
                elif decision == "n":
                    self.approval_manager.reject(pending.approval_id)
                    self.orchestrator.clear_execution_progress(pending.task_id)
                    print(
                        "\nДействие отклонено. "
                        "Задача завершена с ошибкой."
                    )
                    return
                else:
                    self.approval_manager.cancel(pending.approval_id)
                    self.orchestrator.clear_execution_progress(pending.task_id)
                    print("\nДействие отменено. Задача отменена.")
                    return

                result = self.orchestrator.execute_approved_plan(
                    plan_approval.approval_id
                )

            if isinstance(result, Approval):
                plan_approval = result
                print(
                    "\nReviewer вернул замечания. "
                    "Planner подготовил изменённый план."
                )
                continue

            if isinstance(result, ReviewResult):
                print(f"\nРезультат проверки: {result.approved}")
                print(f"Комментарий: {result.feedback}")
                return

            print("\nВыполнение не завершено успешно.")
            if result.output:
                print(result.output)
            for error in result.errors:
                print(f"Ошибка: {error}")
            return

    @staticmethod
    def _show_plan(plan_approval: Approval) -> None:
        plan = Plan.model_validate(plan_approval.parameters["plan"])

        revision = plan_approval.parameters.get("revision", 0)
        if revision:
            print(
                f"\nИзменённый план (пересмотр {revision}): "
                f"{plan.summary}"
            )
        else:
            print(f"\nПлан: {plan.summary}")

        for index, step in enumerate(plan.steps, start=1):
            print(f"\n{index}. {step.description}")
            print(f"   Действие: {step.action}")
            print(f"   Параметры: {step.parameters}")

        print("\nКритерии проверки:")
        for index, criterion in enumerate(
            plan.verification_criteria, start=1
        ):
            requirement = (
                "обязательный" if criterion.required else "необязательный"
            )
            print(
                f"   {index}. [{criterion.id}] {criterion.description} "
                f"({criterion.type}; {requirement})"
            )
            parameters = criterion.parameters.model_dump(mode="json")
            if parameters:
                print(f"      Параметры: {parameters}")
