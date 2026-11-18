from pathlib import Path

from local_agent.execution.recorder import ExecutionRecorder
from local_agent.state_manager import StateManager


def main() -> None:
    project_root = Path(__file__).resolve().parents[2]
    data_directory = project_root / "data"
    database_path = data_directory / "agent.db"
    trace_directory = data_directory / "traces"

    data_directory.mkdir(parents=True, exist_ok=True)

    with (
        StateManager(database_path) as state_manager,
        ExecutionRecorder(
            database_path,
            trace_directory,
        ) as execution_recorder,
    ):
        task_id = state_manager.create_task(
            "Проверка совместной работы компонентов"
        )
        execution_id = execution_recorder.start_execution(task_id)

        print(f"Task ID: {task_id}")
        print(f"Execution ID: {execution_id}")