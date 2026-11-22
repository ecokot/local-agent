from dataclasses import dataclass
from pathlib import Path

from local_agent.execution.workspace import Workspace, WorkspaceError

MAX_FILE_SIZE = 1024 * 1024


class ReadFileError(Exception):
    """Ошибка чтения файла."""


@dataclass(frozen=True, slots=True)
class ReadFileResult:
    """Результат чтения файла."""

    path: str
    content: str
    size_bytes: int

class ReadFileTool:
    """Читает UTF-8 файлы внутри Workspace."""

    def __init__(self, workspace: Workspace) -> None:
        self._workspace = workspace

    def execute(self, path: str) -> ReadFileResult:
        if not isinstance(path, str):
            raise ReadFileError("Path must be a string")

        try:
            file_path = self._workspace.resolve_path(path)

            if not file_path.exists():
                raise ReadFileError("File does not exist")

            if not file_path.is_file():
                raise ReadFileError("Path is not a regular file")

            with file_path.open("rb") as file:
                content_bytes = file.read(MAX_FILE_SIZE + 1)

            if len(content_bytes) > MAX_FILE_SIZE:
                raise ReadFileError(
                    f"File exceeds maximum size of {MAX_FILE_SIZE} bytes"
                )

            try:
                content = content_bytes.decode("utf-8")
            except UnicodeDecodeError as error:
                raise ReadFileError(
                    "File is not valid UTF-8"
                ) from error

            return ReadFileResult(
                path=Path(path).as_posix(),
                content=content,
                size_bytes=len(content_bytes),
            )

        except WorkspaceError as error:
            raise ReadFileError(str(error)) from error
        except OSError as error:
            raise ReadFileError(
                f"Cannot read file: {error}"
            ) from error
