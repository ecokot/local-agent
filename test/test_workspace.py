from pathlib import Path

import pytest

from local_agent.execution.workspace import Workspace, WorkspaceError


def test_workspace_accepts_absolute_existing_directory(tmp_path):
    workspace = Workspace(tmp_path)

    assert workspace.root == tmp_path.resolve()


def test_workspace_rejects_relative_root():
    with pytest.raises(WorkspaceError, match="absolute path"):
        Workspace(Path("project"))


def test_workspace_rejects_nonexistent_root(tmp_path):
    with pytest.raises(WorkspaceError, match="Cannot resolve"):
        Workspace(tmp_path / "missing")


def test_workspace_rejects_file_as_root(tmp_path):
    file_path = tmp_path / "file.txt"
    file_path.write_text("test", encoding="utf-8")

    with pytest.raises(WorkspaceError, match="directory"):
        Workspace(file_path)


def test_resolve_path_returns_workspace_root(tmp_path):
    workspace = Workspace(tmp_path)

    assert workspace.resolve_path(".") == tmp_path.resolve()


def test_resolve_path_returns_path_inside_workspace(tmp_path):
    workspace = Workspace(tmp_path)

    result = workspace.resolve_path("src/main.py")

    assert result == tmp_path / "src" / "main.py"


def test_resolve_path_accepts_path_object(tmp_path):
    workspace = Workspace(tmp_path)

    result = workspace.resolve_path(Path("src/main.py"))

    assert result == tmp_path / "src" / "main.py"


def test_resolve_path_rejects_absolute_path(tmp_path):
    workspace = Workspace(tmp_path)

    with pytest.raises(WorkspaceError, match="Absolute paths"):
        workspace.resolve_path(tmp_path / "outside.txt")


@pytest.mark.parametrize(
    "relative_path",
    [
        "..",
        "../outside.txt",
        "src/../../outside.txt",
    ],
)
def test_resolve_path_rejects_parent_traversal(
    tmp_path,
    relative_path,
):
    workspace = Workspace(tmp_path)

    with pytest.raises(WorkspaceError, match="Parent directory"):
        workspace.resolve_path(relative_path)


def test_resolve_path_rejects_symlink_inside_workspace(tmp_path):
    workspace = Workspace(tmp_path)

    outside = tmp_path.parent / "outside"
    outside.mkdir(exist_ok=True)

    link = tmp_path / "link"

    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Symbolic links are unavailable")

    with pytest.raises(WorkspaceError, match="Symbolic links"):
        workspace.resolve_path("link/file.txt")


def test_resolve_path_rejects_symlink_to_file(tmp_path):
    workspace = Workspace(tmp_path)

    target = tmp_path / "target.txt"
    target.write_text("test", encoding="utf-8")

    link = tmp_path / "link.txt"

    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("Symbolic links are unavailable")

    with pytest.raises(WorkspaceError, match="Symbolic links"):
        workspace.resolve_path("link.txt")


def test_resolve_path_allows_nonexistent_file_inside_workspace(tmp_path):
    workspace = Workspace(tmp_path)

    result = workspace.resolve_path("new_folder/new_file.py")

    assert result == tmp_path / "new_folder" / "new_file.py"
    assert not result.exists()