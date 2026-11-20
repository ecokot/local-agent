import pytest

from local_agent.execution.tools.list_directory import (
    ListDirectoryError,
    ListDirectoryTool,
)
from local_agent.execution.workspace import Workspace


@pytest.fixture
def workspace(tmp_path):
    return Workspace(tmp_path)


@pytest.fixture
def tool(workspace):
    return ListDirectoryTool(workspace)


def test_lists_files_and_directories(tool, workspace):
    (workspace.root / "file.txt").write_text("test", encoding="utf-8")
    (workspace.root / "folder").mkdir()

    result = tool.execute()

    assert [(entry.path, entry.kind) for entry in result.entries] == [
        ("file.txt", "file"),
        ("folder", "directory"),
    ]
    assert result.truncated is False


def test_lists_nested_directory(tool, workspace):
    nested = workspace.root / "src" / "local_agent"
    nested.mkdir(parents=True)
    (nested / "main.py").write_text("print('hello')", encoding="utf-8")

    result = tool.execute(max_depth=3)

    assert [
        (entry.path, entry.kind) for entry in result.entries
    ] == [
        ("src", "directory"),
        ("src/local_agent", "directory"),
        ("src/local_agent/main.py", "file"),
    ]
    assert result.truncated is False


def test_respects_max_depth(tool, workspace):
    nested = workspace.root / "src" / "package"
    nested.mkdir(parents=True)
    (nested / "main.py").write_text("test", encoding="utf-8")

    result = tool.execute(max_depth=1)

    assert [entry.path for entry in result.entries] == ["src"]
    assert result.truncated is True


def test_respects_max_entries(tool, workspace):
    for name in ("a.txt", "b.txt", "c.txt"):
        (workspace.root / name).write_text("test", encoding="utf-8")

    result = tool.execute(max_entries=2)

    assert [entry.path for entry in result.entries] == [
        "a.txt",
        "b.txt",
    ]
    assert result.truncated is True


def test_sorting_is_deterministic(tool, workspace):
    for name in ("z.txt", "B.txt", "a.txt"):
        (workspace.root / name).write_text("test", encoding="utf-8")

    result = tool.execute()

    assert [entry.path for entry in result.entries] == [
        "a.txt",
        "B.txt",
        "z.txt",
    ]


def test_lists_selected_subdirectory(tool, workspace):
    source = workspace.root / "src"
    source.mkdir()
    (source / "main.py").write_text("test", encoding="utf-8")

    result = tool.execute(path="src")

    assert result.path == "src"
    assert [(entry.path, entry.kind) for entry in result.entries] == [
        ("src/main.py", "file"),
    ]


def test_rejects_path_outside_workspace(tool):
    with pytest.raises(ListDirectoryError, match="Parent directory"):
        tool.execute("../outside")


def test_rejects_absolute_path(tool, workspace):
    with pytest.raises(ListDirectoryError, match="Absolute paths"):
        tool.execute(str(workspace.root))


def test_rejects_nonexistent_directory(tool):
    with pytest.raises(ListDirectoryError, match="does not exist"):
        tool.execute("missing")


def test_rejects_file_instead_of_directory(tool, workspace):
    (workspace.root / "file.txt").write_text("test", encoding="utf-8")

    with pytest.raises(ListDirectoryError, match="not a directory"):
        tool.execute("file.txt")


@pytest.mark.parametrize("max_depth", [0, -1, True, 1.5])
def test_rejects_invalid_max_depth(tool, max_depth):
    with pytest.raises(ListDirectoryError, match="max_depth"):
        tool.execute(max_depth=max_depth)


@pytest.mark.parametrize("max_entries", [0, -1, True, 1.5])
def test_rejects_invalid_max_entries(tool, max_entries):
    with pytest.raises(ListDirectoryError, match="max_entries"):
        tool.execute(max_entries=max_entries)


def test_rejects_non_string_path(tool):
    with pytest.raises(ListDirectoryError, match="Path must be a string"):
        tool.execute(path=None)
