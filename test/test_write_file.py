import pytest

from local_agent.execution.tools.write_file import (
    MAX_FILE_SIZE,
    WriteFileError,
    WriteFileTool,
)
from local_agent.execution.workspace import Workspace


@pytest.fixture
def workspace(tmp_path):
    return Workspace(tmp_path)


@pytest.fixture
def tool(workspace):
    return WriteFileTool(workspace)


def test_writes_utf8_file(tool, workspace):
    file_path = workspace.root / "hello.txt"
    file_path.write_text("Original content", encoding="utf-8")

    result = tool.execute("hello.txt", "Привет, мир!")

    assert file_path.read_text(encoding="utf-8") == "Привет, мир!"
    assert result.path == "hello.txt"
    assert result.size_bytes == len("Привет, мир!".encode())


def test_writes_file_in_subdirectory(tool, workspace):
    directory = workspace.root / "src"
    directory.mkdir()
    file_path = directory / "main.py"
    file_path.write_text("old", encoding="utf-8")

    result = tool.execute("src/main.py", "print('hello')")

    assert file_path.read_text(encoding="utf-8") == "print('hello')"
    assert result.path == "src/main.py"


def test_truncates_old_content(tool, workspace):
    file_path = workspace.root / "file.txt"
    file_path.write_text("A much longer original content", encoding="utf-8")

    tool.execute("file.txt", "new")

    assert file_path.read_text(encoding="utf-8") == "new"


def test_writes_empty_content(tool, workspace):
    file_path = workspace.root / "file.txt"
    file_path.write_text("Original content", encoding="utf-8")

    result = tool.execute("file.txt", "")

    assert file_path.read_bytes() == b""
    assert result.size_bytes == 0


def test_accepts_content_at_maximum_size(tool, workspace):
    file_path = workspace.root / "large.txt"
    file_path.write_text("old", encoding="utf-8")
    content = "a" * MAX_FILE_SIZE

    result = tool.execute("large.txt", content)

    assert result.size_bytes == MAX_FILE_SIZE
    assert file_path.stat().st_size == MAX_FILE_SIZE


def test_rejects_content_exceeding_maximum_size(tool, workspace):
    file_path = workspace.root / "large.txt"
    file_path.write_text("original", encoding="utf-8")
    content = "a" * (MAX_FILE_SIZE + 1)

    with pytest.raises(WriteFileError, match="exceeds maximum size"):
        tool.execute("large.txt", content)

    assert file_path.read_text(encoding="utf-8") == "original"


def test_rejects_nonexistent_file_without_creating_it(tool, workspace):
    with pytest.raises(WriteFileError, match="does not exist"):
        tool.execute("missing.txt", "content")

    assert not (workspace.root / "missing.txt").exists()


def test_rejects_directory(tool, workspace):
    (workspace.root / "folder").mkdir()

    with pytest.raises(WriteFileError, match="not a regular file"):
        tool.execute("folder", "content")


def test_rejects_parent_traversal(tool):
    with pytest.raises(WriteFileError, match="Parent directory"):
        tool.execute("../outside.txt", "content")


def test_rejects_absolute_path(tool, workspace):
    absolute_path = str(workspace.root / "file.txt")

    with pytest.raises(WriteFileError, match="Absolute paths"):
        tool.execute(absolute_path, "content")


def test_rejects_non_string_path(tool):
    with pytest.raises(WriteFileError, match="Path must be a string"):
        tool.execute(None, "content")


def test_rejects_non_string_content(tool):
    with pytest.raises(WriteFileError, match="Content must be a string"):
        tool.execute("file.txt", None)


def test_rejects_content_that_cannot_be_encoded_as_utf8(tool):
    with pytest.raises(WriteFileError, match="cannot be encoded as UTF-8"):
        tool.execute("file.txt", "\ud800")


def test_rejects_boolean_path(tool):
    with pytest.raises(WriteFileError, match="Path must be a string"):
        tool.execute(True, "content")