import difflib
from dataclasses import dataclass
from pathlib import Path

from local_agent.execution.workspace import Workspace, WorkspaceError

MAX_FILE_SIZE = 1024 * 1024


class GetDiffError(Exception):
    """Ошибка получения различий файла."""


@dataclass(frozen=True, slots=True)
class GetDiffResult:
    """Результат сравнения содержимого файла."""

    path: str
    diff: str


class GetDiffTool:
    """Формирует unified diff для файла внутри Workspace."""

    def __init__(self, workspace: Workspace) -> None:
        self._workspace = workspace

    def execute(
        self,
        path: str,
        original_content: str,
    ) -> GetDiffResult:
        if not isinstance(path, str):
            raise GetDiffError("Path must be a string")

        if not isinstance(original_content, str):
            raise GetDiffError("Original content must be a string")

        try:
            original_bytes = original_content.encode("utf-8")
        except UnicodeEncodeError as error:
            raise GetDiffError(
                "Original content cannot be encoded as UTF-8"
            ) from error

        if len(original_bytes) > MAX_FILE_SIZE:
            raise GetDiffError(
                f"Original content exceeds maximum size of "
                f"{MAX_FILE_SIZE} bytes"
            )

        try:
            file_path = self._workspace.resolve_path(path)

            if not file_path.exists():
                raise GetDiffError("File does not exist")

            if not file_path.is_file():
                raise GetDiffError("Path is not a regular file")

            with file_path.open("rb") as file:
                current_bytes = file.read(MAX_FILE_SIZE + 1)

            if len(current_bytes) > MAX_FILE_SIZE:
                raise GetDiffError(
                    f"File exceeds maximum size of {MAX_FILE_SIZE} bytes"
                )

            try:
                current_content = current_bytes.decode("utf-8")
            except UnicodeDecodeError as error:
                raise GetDiffError(
                    "File is not valid UTF-8"
                ) from error

            relative_path = Path(path).as_posix()

            original_content = original_content.replace(
                "\r\n", "\n"
            ).replace("\r", "\n")

            current_content = current_content.replace(
                "\r\n", "\n"
            ).replace("\r", "\n")

            diff = "".join(
                difflib.unified_diff(
                    original_content.splitlines(keepends=True),
                    current_content.splitlines(keepends=True),
                    fromfile=f"a/{relative_path}",
                    tofile=f"b/{relative_path}",
                )
            )

            return GetDiffResult(
                path=relative_path,
                diff=diff,
            )

        except WorkspaceError as error:
            raise GetDiffError(str(error)) from error
        except OSError as error:
            raise GetDiffError(
                f"Cannot read file: {error}"
            ) from error