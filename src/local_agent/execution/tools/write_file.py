from dataclasses import dataclass
from pathlib import Path

from local_agent.execution.workspace import Workspace, WorkspaceError

MAX_FILE_SIZE = 1024 * 1024


class WriteFileError(Exception):
    """Ошибка записи файла."""


@dataclass(frozen=True, slots=True)
class WriteFileResult:
    """Результат записи файла."""

    path: str
    size_bytes: int


class WriteFileTool:
    """Заменяет содержимое существующего UTF-8 файла внутри Workspace."""

    def __init__(self, workspace: Workspace) -> None:
        self._workspace = workspace

    def execute(self, path: str, content: str) -> WriteFileResult:
        if not isinstance(path, str):
            raise WriteFileError("Path must be a string")

        if not isinstance(content, str):
            raise WriteFileError("Content must be a string")

        try:
            content_bytes = content.encode("utf-8")
        except UnicodeEncodeError as error:
            raise WriteFileError(
                "Content cannot be encoded as UTF-8"
            ) from error

        if len(content_bytes) > MAX_FILE_SIZE:
            raise WriteFileError(
                f"Content exceeds maximum size of {MAX_FILE_SIZE} bytes"
            )

        try:
            file_path = self._workspace.resolve_path(path)

            if not file_path.exists():
                raise WriteFileError("File does not exist")

            if not file_path.is_file():
                raise WriteFileError("Path is not a regular file")

            try:
                with file_path.open("r+b") as file:
                    file.seek(0)
                    file.truncate()
                    file.write(content_bytes)
            except FileNotFoundError as error:
                raise WriteFileError("File does not exist") from error

            return WriteFileResult(
                path=Path(path).as_posix(),
                size_bytes=len(content_bytes),
            )

        except WorkspaceError as error:
            raise WriteFileError(str(error)) from error
        except OSError as error:
            raise WriteFileError(
                f"Cannot write file: {error}"
            ) from error