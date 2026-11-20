from pathlib import Path


class WorkspaceError(Exception):
    """Ошибка доступа к рабочему пространству."""


class Workspace:
    """Ограничивает файловый доступ заданным корнем проекта."""

    def __init__(self, root: str | Path) -> None:
        root_path = Path(root)

        if not root_path.is_absolute():
            raise WorkspaceError(
                "Workspace root must be an absolute path"
            )

        try:
            resolved_root = root_path.resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as error:
            raise WorkspaceError(
                f"Cannot resolve workspace root: {error}"
            ) from error

        if not resolved_root.is_dir():
            raise WorkspaceError(
                "Workspace root must be an existing directory"
            )

        self._root = resolved_root

    @property
    def root(self) -> Path:
        return self._root

    def resolve_path(self, relative_path: str | Path) -> Path:
        """Проверяет путь и возвращает его абсолютное расположение."""

        path = Path(relative_path)

        if path.is_absolute():
            raise WorkspaceError(
                "Absolute paths are not allowed"
            )

        if ".." in path.parts:
            raise WorkspaceError(
                "Parent directory traversal is not allowed"
            )

        candidate = self._root

        try:
            for part in path.parts:
                candidate = candidate / part
                if candidate.is_symlink() or candidate.is_junction():
                    raise WorkspaceError(
                        "Symbolic links are not allowed"
                    )

            resolved = candidate.resolve(strict=False)
            resolved.relative_to(self._root)

        except WorkspaceError:
            raise
        except ValueError as error:
            raise WorkspaceError(
                "Path is outside the workspace"
            ) from error
        except (OSError, RuntimeError) as error:
            raise WorkspaceError(
                f"Cannot resolve workspace path: {error}"
            ) from error

        return resolved
