from dataclasses import dataclass
from pathlib import Path

from local_agent.execution.workspace import Workspace, WorkspaceError


class ListDirectoryError(Exception):
    """Ошибка просмотра каталога."""


@dataclass(frozen=True, slots=True)
class DirectoryEntry:
    """Элемент рабочей области."""

    path: str
    kind: str


@dataclass(frozen=True, slots=True)
class DirectoryListing:
    """Результат просмотра каталога."""

    path: str
    entries: tuple[DirectoryEntry, ...]
    truncated: bool


class ListDirectoryTool:
    """Просматривает содержимое каталогов внутри Workspace."""

    def __init__(self, workspace: Workspace) -> None:
        self._workspace = workspace

    def execute(
            self,
            path: str = ".",
            max_depth: int = 1,
            max_entries: int = 500,
    ) -> DirectoryListing:
        if not isinstance(path, str):
            raise ListDirectoryError("Path must be a string")

        if isinstance(max_depth, bool) or not isinstance(max_depth, int):
            raise ListDirectoryError("max_depth must be an integer")

        if max_depth < 1:
            raise ListDirectoryError("max_depth must be at least 1")

        if isinstance(max_entries, bool) or not isinstance(max_entries, int):
            raise ListDirectoryError("max_entries must be an integer")

        if max_entries < 1:
            raise ListDirectoryError("max_entries must be at least 1")

        try:
            directory = self._workspace.resolve_path(path)

            if not directory.exists():
                raise ListDirectoryError("Directory does not exist")

            if not directory.is_dir():
                raise ListDirectoryError("Path is not a directory")

            entries: list[DirectoryEntry] = []
            truncated = False

            def scan(current: Path, depth: int) -> None:
                nonlocal truncated

                try:
                    children = sorted(
                        current.iterdir(),
                        key=lambda item: (item.name.casefold(), item.name),
                    )
                except OSError as error:
                    raise ListDirectoryError(
                        f"Cannot read directory: {error}"
                    ) from error

                for child in children:
                    if len(entries) >= max_entries:
                        truncated = True
                        return

                    if child.is_symlink() or child.is_junction():
                        kind = "symlink"
                    elif child.is_dir():
                        kind = "directory"
                    elif child.is_file():
                        kind = "file"
                    else:
                        kind = "other"

                    relative_path = child.relative_to(
                        self._workspace.root
                    ).as_posix()

                    entries.append(
                        DirectoryEntry(
                            path=relative_path,
                            kind=kind,
                        )
                    )

                    if kind != "directory":
                        continue

                    if depth < max_depth:
                        scan(child, depth + 1)

                        if truncated:
                            return
                    else:
                        # Проверяем, есть ли содержимое, которое не попало в результат.
                        try:
                            if next(child.iterdir(), None) is not None:
                                truncated = True
                        except OSError as error:
                            raise ListDirectoryError(
                                f"Cannot inspect directory: {error}"
                            ) from error

            scan(directory, 1)

            return DirectoryListing(
                path=Path(path).as_posix(),
                entries=tuple(entries),
                truncated=truncated,
            )

        except WorkspaceError as error:
            raise ListDirectoryError(str(error)) from error
        except OSError as error:
            raise ListDirectoryError(
                f"Cannot inspect directory: {error}"
            ) from error
