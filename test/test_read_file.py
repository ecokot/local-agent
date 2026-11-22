import pytest

from local_agent.execution.tools.read_file import (
    MAX_FILE_SIZE,
    ReadFileError,
    ReadFileTool,
)
from local_agent.execution.workspace import Workspace


@pytest.fixture
def workspace(tmp_path):
    return Workspace(tmp_path)


@pytest.fixture
def tool(workspace):
    return ReadFileTool(workspace)


def test_reads_utf8_file(tool, workspace):
    file_path = workspace.root / "hello.txt"
    file_path.write_text("Привет, мир!", encoding="utf-8")

    result = tool.execute("hello.txt")

    assert result.path == "hello.txt"
    assert result.content == "Привет, мир!"
    assert result.size_bytes == len("Привет, мир!".encode())


def test_reads_file_from_subdirectory(tool, workspace):
    directory = workspace.root / "src"
    directory.mkdir()
    (directory / "main.py").write_text(
        "print('hello')",
        encoding="utf-8",
    )

    result = tool.execute("src/main.py")

    assert result.path == "src/main.py"
    assert result.content == "print('hello')"


def test_reads_empty_file(tool, workspace):
    (workspace.root / "empty.txt").write_bytes(b"")

    result = tool.execute("empty.txt")

    assert result.content == ""
    assert result.size_bytes == 0


def test_accepts_file_at_maximum_size(tool, workspace):
    content = b"a" * MAX_FILE_SIZE
    (workspace.root / "large.txt").write_bytes(content)

    result = tool.execute("large.txt")

    assert result.size_bytes == MAX_FILE_SIZE
    assert len(result.content) == MAX_FILE_SIZE


def test_rejects_file_exceeding_maximum_size(tool, workspace):
    content = b"a" * (MAX_FILE_SIZE + 1)
    (workspace.root / "large.txt").write_bytes(content)

    with pytest.raises(ReadFileError, match="exceeds maximum size"):
        tool.execute("large.txt")


def test_rejects_nonexistent_file(tool):
    with pytest.raises(ReadFileError, match="does not exist"):
        tool.execute("missing.txt")


def test_rejects_directory(tool, workspace):
    (workspace.root / "folder").mkdir()

    with pytest.raises(ReadFileError, match="not a regular file"):
        tool.execute("folder")


def test_rejects_invalid_utf8(tool, workspace):
    (workspace.root / "invalid.txt").write_bytes(b"\xff\xfe")

    with pytest.raises(ReadFileError, match="not valid UTF-8"):
        tool.execute("invalid.txt")


def test_rejects_parent_traversal(tool):
    with pytest.raises(ReadFileError, match="Parent directory"):
        tool.execute("../outside.txt")


def test_rejects_absolute_path(tool, workspace):
    with pytest.raises(ReadFileError, match="Absolute paths"):
        tool.execute(str(workspace.root / "file.txt"))


def test_rejects_non_string_path(tool):
    with pytest.raises(ReadFileError, match="Path must be a string"):
        tool.execute(None)
