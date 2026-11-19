
from pathlib import Path

from local_agent.agents.stubs import get_stub_agents
from local_agent.approval_manager import ApprovalManager
from local_agent.orchestrator import Orchestrator
from local_agent.state_manager import StateManager
from local_agent.user_interface import UserInterface


def main() -> None:
    project_root = Path(__file__).resolve().parents[2]
    data_directory = project_root / "data"
    database_path = data_directory / "agent.db"

    data_directory.mkdir(parents=True, exist_ok=True)

    with (
        StateManager(database_path) as state_manager,
        ApprovalManager(
            connection=state_manager.connection,
            state_manager=state_manager,
        ) as approval_manager,
    ):
        planner, executor, reviewer = get_stub_agents()

        orchestrator = Orchestrator(
            state_manager=state_manager,
            approval_manager=approval_manager,
            planner=planner,
            executor=executor,
            reviewer=reviewer,
        )

        interface = UserInterface(
            orchestrator=orchestrator,
            approval_manager=approval_manager,
        )

        interface.run()
