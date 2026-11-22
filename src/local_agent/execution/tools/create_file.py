from dataclasses import dataclass
from pathlib import Path

from local_agent.execution.workspace import Workspace, WorkspaceError

MAX_FILE_SIZE = 1024 * 1024


class CreateFileError(Exception):
    """Ошибка создания файла."""


@dataclass(frozen=True, slots=True)
class CreateFileResult:
    """Результат создания файла."""

    path: str
    size_bytes: int


class CreateFileTool:
    """Создаёт новый UTF-8 файл внутри Workspace."""

    def __init__(self, workspace: Workspace) -> None:
        self._workspace = workspace

    def execute(self, path: str, content: str) -> CreateFileResult:
        if not isinstance(path, str):
            raise CreateFileError("Path must be a string")

        if not isinstance(content, str):
            raise CreateFileError("Content must be a string")

        try:
            content_bytes = content.encode("utf-8")
        except UnicodeEncodeError as error:
            raise CreateFileError(
                "Content cannot be encoded as UTF-8"
            ) from error

        if len(content_bytes) > MAX_FILE_SIZE:
            raise CreateFileError(
                f"Content exceeds maximum size of {MAX_FILE_SIZE} bytes"
            )

        try:
            file_path = self._workspace.resolve_path(path)

            if not file_path.parent.is_dir():
                raise CreateFileError(
                    "Parent directory does not exist"
                )

            if file_path.exists():
                raise CreateFileError("File already exists")

            try:
                with file_path.open("xb") as file:
                    file.write(content_bytes)
            except FileExistsError as error:
                raise CreateFileError(
                    "File already exists"
                ) from error

            return CreateFileResult(
                path=Path(path).as_posix(),
                size_bytes=len(content_bytes),
            )

        except WorkspaceError as error:
            raise CreateFileError(str(error)) from error
        except OSError as error:
            raise CreateFileError(
                f"Cannot create file: {error}"
            ) from error