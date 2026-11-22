import pytest

from local_agent.execution.tools.create_file import (
    MAX_FILE_SIZE,
    CreateFileError,
    CreateFileTool,
)
from local_agent.execution.workspace import Workspace


@pytest.fixture
def workspace(tmp_path):
    return Workspace(tmp_path)


@pytest.fixture
def tool(workspace):
    return CreateFileTool(workspace)


def test_creates_utf8_file(tool, workspace):
    result = tool.execute("hello.txt", "Привет, мир!")

    file_path = workspace.root / "hello.txt"

    assert file_path.read_text(encoding="utf-8") == "Привет, мир!"
    assert result.path == "hello.txt"
    assert result.size_bytes == len("Привет, мир!".encode())


def test_creates_file_in_subdirectory(tool, workspace):
    directory = workspace.root / "src"
    directory.mkdir()

    result = tool.execute("src/main.py", "print('hello')")

    assert (directory / "main.py").read_text(encoding="utf-8") == (
        "print('hello')"
    )
    assert result.path == "src/main.py"


def test_creates_empty_file(tool, workspace):
    result = tool.execute("empty.txt", "")

    file_path = workspace.root / "empty.txt"

    assert file_path.exists()
    assert file_path.read_bytes() == b""
    assert result.size_bytes == 0


def test_accepts_content_at_maximum_size(tool, workspace):
    content = "a" * MAX_FILE_SIZE

    result = tool.execute("large.txt", content)

    assert result.size_bytes == MAX_FILE_SIZE
    assert (workspace.root / "large.txt").stat().st_size == MAX_FILE_SIZE


def test_rejects_content_exceeding_maximum_size(tool, workspace):
    content = "a" * (MAX_FILE_SIZE + 1)

    with pytest.raises(CreateFileError, match="exceeds maximum size"):
        tool.execute("large.txt", content)

    assert not (workspace.root / "large.txt").exists()


def test_rejects_existing_file_without_overwriting(tool, workspace):
    file_path = workspace.root / "existing.txt"
    file_path.write_text("original", encoding="utf-8")

    with pytest.raises(CreateFileError, match="already exists"):
        tool.execute("existing.txt", "replacement")

    assert file_path.read_text(encoding="utf-8") == "original"


def test_rejects_existing_directory(tool, workspace):
    (workspace.root / "folder").mkdir()

    with pytest.raises(CreateFileError, match="already exists"):
        tool.execute("folder", "content")


def test_rejects_missing_parent_directory(tool):
    with pytest.raises(CreateFileError, match="Parent directory"):
        tool.execute("missing/file.txt", "content")


def test_rejects_parent_traversal(tool):
    with pytest.raises(CreateFileError, match="Parent directory"):
        tool.execute("../outside.txt", "content")


def test_rejects_absolute_path(tool, workspace):
    absolute_path = str(workspace.root / "file.txt")

    with pytest.raises(CreateFileError, match="Absolute paths"):
        tool.execute(absolute_path, "content")


def test_rejects_non_string_path(tool):
    with pytest.raises(CreateFileError, match="Path must be a string"):
        tool.execute(None, "content")


def test_rejects_non_string_content(tool):
    with pytest.raises(CreateFileError, match="Content must be a string"):
        tool.execute("file.txt", None)


def test_rejects_content_that_cannot_be_encoded_as_utf8(tool):
    with pytest.raises(CreateFileError, match="cannot be encoded as UTF-8"):
        tool.execute("file.txt", "\ud800")


def test_rejects_boolean_path(tool):
    with pytest.raises(CreateFileError, match="Path must be a string"):
        tool.execute(True, "content")