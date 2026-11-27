import sqlite3

import pytest

import local_agent
from local_agent.recovery_manager import (
    RecoveryError,
    RecoveryResult,
    UnresolvedTask,
)
from local_agent.state_manager import TaskStatus


def _stub_application_dependencies(monkeypatch, app_module):
    monkeypatch.setattr(app_module, "get_stub_agents", lambda: (object(), object(), object()))
    monkeypatch.setattr(app_module, "Workspace", lambda project_root: object())
    monkeypatch.setattr(app_module, "ExecutionBackend", lambda **kwargs: object())
    monkeypatch.setattr(app_module, "RunCommandTool", lambda backend: object())
    monkeypatch.setattr(app_module, "RunTestsTool", lambda command_tool: object())
    monkeypatch.setattr(
        app_module,
        "VerificationExecutor",
        lambda **kwargs: object(),
    )
    monkeypatch.setattr(app_module, "Orchestrator", lambda **kwargs: object())


def _set_project_root(monkeypatch, tmp_path, app_module):
    project_root = tmp_path / "project"
    package_file = project_root / "src" / "local_agent" / "__init__.py"
    package_file.parent.mkdir(parents=True)
    monkeypatch.setattr(app_module, "__file__", str(package_file))
    return project_root


def test_main_recovers_before_ui_and_closes_recorder(
    monkeypatch,
    tmp_path,
    capsys,
):
    calls = []
    captured = {}
    _set_project_root(monkeypatch, tmp_path, local_agent)
    _stub_application_dependencies(monkeypatch, local_agent)

    class FakeRecoveryManager:
        def __init__(
            self,
            *,
            state_manager,
            execution_recorder,
            approval_manager,
        ):
            captured["state_manager"] = state_manager
            captured["execution_recorder"] = execution_recorder
            captured["approval_manager"] = approval_manager

        def recover(self):
            calls.append("recover")
            return RecoveryResult(
                recovered_task_ids=(),
                waiting_for_approval_task_ids=(),
                unresolved_tasks=(
                    UnresolvedTask(
                        task_id="task-123",
                        status=TaskStatus.RUNNING,
                        reason="Execution outcome is unknown.",
                    ),
                ),
            )

    class FakeUserInterface:
        def __init__(self, **kwargs):
            pass

        def run(self):
            assert calls == ["recover"]
            calls.append("ui")

    monkeypatch.setattr(local_agent, "RecoveryManager", FakeRecoveryManager)
    monkeypatch.setattr(local_agent, "UserInterface", FakeUserInterface)

    local_agent.main()

    state_manager = captured["state_manager"]
    execution_recorder = captured["execution_recorder"]
    approval_manager = captured["approval_manager"]

    assert calls == ["recover", "ui"]
    assert execution_recorder.database_path == str(state_manager.database_path)
    assert approval_manager.connection is state_manager.connection
    assert "task-123" in capsys.readouterr().out

    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        execution_recorder._connection.execute("SELECT 1")


def test_main_does_not_start_ui_when_recovery_fails(
    monkeypatch,
    tmp_path,
):
    interface_constructed = False
    _set_project_root(monkeypatch, tmp_path, local_agent)
    _stub_application_dependencies(monkeypatch, local_agent)

    class FailingRecoveryManager:
        def __init__(self, **kwargs):
            pass

        def recover(self):
            raise RecoveryError("Task task-456 has inconsistent state")

    class ForbiddenUserInterface:
        def __init__(self, **kwargs):
            nonlocal interface_constructed
            interface_constructed = True

    monkeypatch.setattr(
        local_agent,
        "RecoveryManager",
        FailingRecoveryManager,
    )
    monkeypatch.setattr(
        local_agent,
        "UserInterface",
        ForbiddenUserInterface,
    )

    with pytest.raises(RecoveryError, match="task-456"):
        local_agent.main()

    assert interface_constructed is False
