"""Read-only Git access scoped to a Workspace."""
from __future__ import annotations

import math
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from local_agent.execution.tools._windows_runner import (
    MAX_OUTPUT_SIZE, ProcessResult, WindowsRunnerError, run_windows,
)
from local_agent.execution.workspace import Workspace, WorkspaceError

DEFAULT_TIMEOUT_SECONDS = 15.0
MAX_TIMEOUT_SECONDS = 120.0
DEFAULT_LOG_LIMIT = 20
MAX_LOG_LIMIT = 100


class GitToolError(RuntimeError):
    """Base error for GitTool failures."""


class GitNotFoundError(GitToolError):
    """Git executable was not found."""


class GitRepositoryError(GitToolError):
    """Workspace is not the root of a Git repository."""


class GitCommandError(GitToolError):
    """A fixed Git operation failed."""

    def __init__(self, operation: str, exit_code: int | None, stderr: str):
        self.operation = operation
        self.exit_code = exit_code
        self.stderr = stderr
        super().__init__(f"Git operation {operation!r} failed (exit_code={exit_code}): {stderr.strip()}")


@dataclass(frozen=True, slots=True)
class GitCommandResult:
    operation: str
    exit_code: int
    stdout: str
    stderr: str
    stdout_bytes: bytes
    stderr_bytes: bytes
    stdout_truncated: bool
    stderr_truncated: bool


@dataclass(frozen=True, slots=True)
class GitBranch:
    name: str
    commit: str
    current: bool


@dataclass(frozen=True, slots=True)
class GitCommit:
    sha: str
    author: str
    date: str
    subject: str


@dataclass(frozen=True, slots=True)
class GitStatus:
    raw: str
    branch: str | None
    head: str | None
    entries: tuple[str, ...]


Runner = Callable[[str, tuple[str, ...], str, float], ProcessResult]


class GitTool:
    """Provides a fixed allowlist of read-only Git operations."""

    def __init__(
        self,
        workspace: Workspace,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_output_size: int = MAX_OUTPUT_SIZE,
        runner: Runner | None = None,
    ) -> None:
        if not isinstance(workspace, Workspace):
            raise TypeError("workspace must be a Workspace instance")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= MAX_TIMEOUT_SECONDS
        ):
            raise ValueError(f"timeout_seconds must be greater than 0 and at most {MAX_TIMEOUT_SECONDS}")
        if (
            isinstance(max_output_size, bool)
            or not isinstance(max_output_size, int)
            or not 1 <= max_output_size <= MAX_OUTPUT_SIZE
        ):
            raise ValueError(f"max_output_size must be between 1 and {MAX_OUTPUT_SIZE}")

        executable = shutil.which("git")
        if executable is None:
            raise GitNotFoundError("Git executable was not found on PATH")
        self._workspace = workspace
        self._executable = str(Path(executable).resolve())
        self._timeout_seconds = float(timeout_seconds)
        self._max_output_size = max_output_size
        self._runner = runner or run_windows

        try:
            result = self._invoke("repository_root", ["rev-parse", "--show-toplevel"], check=False)
        except GitToolError as error:
            raise GitRepositoryError(f"Could not inspect Git repository: {error}") from error
        if result.exit_code != 0:
            raise GitRepositoryError(f"Workspace is not a Git repository: {result.stderr.strip()}")
        try:
            actual = Path(result.stdout.strip()).resolve(strict=True)
            expected = workspace.root.resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as error:
            raise GitRepositoryError(f"Could not resolve repository root: {error}") from error
        if os.path.normcase(str(actual)) != os.path.normcase(str(expected)):
            raise GitRepositoryError(f"Git root does not match Workspace.root: {actual}")

    def status(self) -> GitStatus:
        result = self._execute("status", ["status", "--porcelain=v2", "-z", "--branch"])
        branch = head = None
        entries = []
        for field in result.stdout.split("\x00"):
            if field.startswith("# branch.head "):
                value = field.removeprefix("# branch.head ")
                branch = None if value == "(detached)" else value
            elif field.startswith("# branch.oid "):
                value = field.removeprefix("# branch.oid ")
                head = None if value == "(initial)" else value
            elif field and not field.startswith("# "):
                entries.append(field)
        return GitStatus(result.stdout, branch, head, tuple(entries))

    def branch(self) -> str | None:
        value = self._execute("branch", ["branch", "--show-current"]).stdout.strip()
        return value or None

    def head(self) -> str:
        return self._execute("head", ["rev-parse", "--verify", "HEAD"]).stdout.strip()

    def branches(self) -> tuple[GitBranch, ...]:
        fmt = "%(refname:short)%09%(objectname)%09%(HEAD)"
        output = self._execute("branches", ["for-each-ref", f"--format={fmt}", "refs/heads"]).stdout
        result = []
        for line in output.splitlines():
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) != 3:
                raise GitToolError("Git returned malformed branch data")
            result.append(GitBranch(parts[0], parts[1], parts[2] == "*"))
        return tuple(result)

    def log(self, limit: int = DEFAULT_LOG_LIMIT) -> tuple[GitCommit, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LOG_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_LOG_LIMIT}")
        fmt = "%H%x00%an%x00%aI%x00%s%x00"
        output = self._execute("log", ["log", f"-n{limit}", f"--format={fmt}"]).stdout
        output = output.rstrip("\n")
        fields = output.split("\x00")
        if fields and fields[-1] == "":
            fields.pop()
        if len(fields) % 4:
            raise GitToolError("Git returned malformed log data")
        return tuple(GitCommit(*fields[i:i + 4]) for i in range(0, len(fields), 4))

    def diff(self, paths: tuple[str, ...] | list[str] = ()) -> str:
        return self._diff("diff", ["diff", "--no-ext-diff", "--no-color"], paths)

    def staged_diff(self, paths: tuple[str, ...] | list[str] = ()) -> str:
        return self._diff("staged_diff", ["diff", "--cached", "--no-ext-diff", "--no-color"], paths)

    def changed_files(self) -> tuple[str, ...]:
        tracked = self._execute("changed_files", ["diff", "--name-only", "-z", "HEAD", "--"]).stdout_bytes
        untracked = self._execute("changed_files", ["ls-files", "--others", "--exclude-standard", "-z", "--"]).stdout_bytes
        result = []
        seen = set()
        for data in (tracked, untracked):
            for item in data.split(b"\x00"):
                if item:
                    path = os.fsdecode(item)
                    if path not in seen:
                        seen.add(path)
                        result.append(path)
        return tuple(result)

    def _diff(self, operation: str, args: list[str], paths: tuple[str, ...] | list[str]) -> str:
        if not isinstance(paths, (tuple, list)):
            raise TypeError("paths must be a list or tuple of relative paths")
        validated = []
        for path in paths:
            if not isinstance(path, str) or not path or "\x00" in path:
                raise ValueError("Each path must be a non-empty relative string without NUL")
            try:
                self._workspace.resolve_path(path)
            except WorkspaceError as error:
                raise ValueError(f"Invalid workspace path {path!r}: {error}") from error
            validated.append(path.replace("\\", "/"))
        return self._execute(operation, [*args, "--", *validated]).stdout

    def _execute(self, operation: str, args: list[str]) -> GitCommandResult:
        result = self._invoke(operation, args)
        if result.exit_code != 0:
            raise GitCommandError(operation, result.exit_code, result.stderr)
        return result

    def _invoke(self, operation: str, args: list[str], *, check: bool = True) -> GitCommandResult:
        try:
            raw = self._runner(
                self._executable, ("--literal-pathspecs", *args),
                str(self._workspace.root), self._timeout_seconds,
            )
        except (OSError, WindowsRunnerError) as error:
            raise GitToolError(f"Could not run Git operation {operation!r}: {error}") from error
        stdout_bytes = raw.stdout[:self._max_output_size]
        stderr_bytes = raw.stderr[:self._max_output_size]
        stdout = stdout_bytes.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
        stderr = stderr_bytes.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
        if raw.timed_out:
            raise GitCommandError(operation, None, f"Timed out after {self._timeout_seconds}s")
        if check and raw.exit_code != 0:
            raise GitCommandError(operation, raw.exit_code, stderr)
        return GitCommandResult(
            operation, raw.exit_code if raw.exit_code is not None else -1,
            stdout, stderr, stdout_bytes, stderr_bytes,
            raw.stdout_truncated or len(raw.stdout) > self._max_output_size,
            raw.stderr_truncated or len(raw.stderr) > self._max_output_size,
        )
