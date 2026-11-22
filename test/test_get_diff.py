import pytest

from local_agent.execution.tools.get_diff import (
    MAX_FILE_SIZE,
    GetDiffError,
    GetDiffTool,
)
from local_agent.execution.workspace import Workspace


@pytest.fixture
def workspace(tmp_path):
    return Workspace(tmp_path)


@pytest.fixture
def tool(workspace):
    return GetDiffTool(workspace)


def test_returns_unified_diff(tool, workspace):
    file_path = workspace.root / "hello.txt"
    file_path.write_text("Hello\nWorld\n", encoding="utf-8")

    result = tool.execute(
        "hello.txt",
        "Hello\nPython\n",
    )

    assert result.path == "hello.txt"
    assert "--- a/hello.txt" in result.diff
    assert "+++ b/hello.txt" in result.diff
    assert "-Python" in result.diff
    assert "+World" in result.diff


def test_returns_empty_diff_when_content_is_identical(tool, workspace):
    file_path = workspace.root / "hello.txt"
    file_path.write_text("Hello\nWorld\n", encoding="utf-8")

    result = tool.execute(
        "hello.txt",
        "Hello\nWorld\n",
    )

    assert result.diff == ""


def test_returns_diff_for_utf8_content(tool, workspace):
    file_path = workspace.root / "hello.txt"
    file_path.write_text("Привет, мир!\n", encoding="utf-8")

    result = tool.execute(
        "hello.txt",
        "Привет!\n",
    )

    assert "-Привет!" in result.diff
    assert "+Привет, мир!" in result.diff


def test_returns_diff_for_file_in_subdirectory(tool, workspace):
    directory = workspace.root / "src"
    directory.mkdir()
    (directory / "main.py").write_text(
        "print('new')\n",
        encoding="utf-8",
    )

    result = tool.execute(
        "src/main.py",
        "print('old')\n",
    )

    assert result.path == "src/main.py"
    assert "--- a/src/main.py" in result.diff
    assert "+++ b/src/main.py" in result.diff


def test_returns_diff_for_empty_original_content(tool, workspace):
    (workspace.root / "file.txt").write_text(
        "New content\n",
        encoding="utf-8",
    )

    result = tool.execute("file.txt", "")

    assert "+New content" in result.diff


def test_returns_diff_for_empty_current_file(tool, workspace):
    (workspace.root / "file.txt").write_bytes(b"")

    result = tool.execute("file.txt", "Original content\n")

    assert "-Original content" in result.diff


def test_accepts_original_content_at_maximum_size(tool, workspace):
    content = "a" * MAX_FILE_SIZE
    (workspace.root / "large.txt").write_text(
        content,
        encoding="utf-8",
    )

    result = tool.execute("large.txt", content)

    assert result.diff == ""


def test_rejects_original_content_exceeding_maximum_size(
    tool,
    workspace,
):
    (workspace.root / "file.txt").write_text(
        "content",
        encoding="utf-8",
    )
    original_content = "a" * (MAX_FILE_SIZE + 1)

    with pytest.raises(
        GetDiffError,
        match="Original content exceeds maximum size",
    ):
        tool.execute("file.txt", original_content)


def test_rejects_current_file_exceeding_maximum_size(tool, workspace):
    (workspace.root / "large.txt").write_text(
        "a" * (MAX_FILE_SIZE + 1),
        encoding="utf-8",
    )

    with pytest.raises(
        GetDiffError,
        match="File exceeds maximum size",
    ):
        tool.execute("large.txt", "")


def test_rejects_nonexistent_file(tool):
    with pytest.raises(GetDiffError, match="does not exist"):
        tool.execute("missing.txt", "")


def test_rejects_directory(tool, workspace):
    (workspace.root / "folder").mkdir()

    with pytest.raises(GetDiffError, match="not a regular file"):
        tool.execute("folder", "")


def test_rejects_invalid_utf8_file(tool, workspace):
    (workspace.root / "invalid.txt").write_bytes(b"\xff\xfe")

    with pytest.raises(GetDiffError, match="not valid UTF-8"):
        tool.execute("invalid.txt", "")


def test_rejects_parent_traversal(tool):
    with pytest.raises(GetDiffError, match="Parent directory"):
        tool.execute("../outside.txt", "")


def test_rejects_absolute_path(tool, workspace):
    absolute_path = str(workspace.root / "file.txt")

    with pytest.raises(GetDiffError, match="Absolute paths"):
        tool.execute(absolute_path, "")


def test_rejects_non_string_path(tool):
    with pytest.raises(GetDiffError, match="Path must be a string"):
        tool.execute(None, "")


def test_rejects_non_string_original_content(tool):
    with pytest.raises(
        GetDiffError,
        match="Original content must be a string",
    ):
        tool.execute("file.txt", None)


def test_rejects_original_content_that_cannot_be_encoded_as_utf8(
    tool,
    workspace,
):
    (workspace.root / "file.txt").write_text(
        "content",
        encoding="utf-8",
    )

    with pytest.raises(
        GetDiffError,
        match="Original content cannot be encoded as UTF-8",
    ):
        tool.execute("file.txt", "\ud800")