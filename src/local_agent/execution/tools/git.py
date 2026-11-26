"""Git access scoped to a Workspace with fixed read-only and approved mutations."""
from __future__ import annotations

import hashlib
import math
import os
import shutil
import stat
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from local_agent.execution.tools._windows_runner import (
    MAX_OUTPUT_SIZE,
    ProcessResult,
    WindowsRunnerError,
    run_windows,
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


class GitSnapshotChangedError(GitToolError):
    """The repository changed during the final pre-command verification."""

    def __init__(self, snapshot: dict):
        self.snapshot = snapshot
        super().__init__("Git repository snapshot changed after approval")


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


Runner = Callable[[str, tuple[str, ...], str, float, int], ProcessResult]

_REPOSITORY_LOCKS: dict[str, threading.RLock] = {}
_REPOSITORY_LOCKS_GUARD = threading.Lock()


class GitTool:
    """Provides fixed Git operations scoped to one workspace repository."""

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
        try:
            common_dir = self._invoke(
                "repository_common_dir", ["rev-parse", "--git-common-dir"]
            )
            common_path = Path(common_dir.stdout.strip())
            if not common_path.is_absolute():
                common_path = self._workspace.root / common_path
            self._repository_lock_key = os.path.normcase(
                str(common_path.resolve(strict=True))
            )
        except (GitToolError, OSError, RuntimeError, ValueError) as error:
            raise GitRepositoryError(
                f"Could not resolve common Git directory: {error}"
            ) from error

    def status(self) -> GitStatus:
        result = self._execute(
            "status", ["--no-optional-locks", "status", "--porcelain=v2", "-z", "--branch"]
        )
        branch = head = None
        entries = []
        fields = result.stdout.split("\x00")
        index = 0
        while index < len(fields):
            field = fields[index]
            if field.startswith("# branch.head "):
                value = field.removeprefix("# branch.head ")
                branch = None if value == "(detached)" else value
            elif field.startswith("# branch.oid "):
                value = field.removeprefix("# branch.oid ")
                head = None if value == "(initial)" else value
            elif field and not field.startswith("# "):
                entries.append(field)
                # Porcelain v2 type-2 rename/copy records put the original path
                # in the next NUL-delimited field. It is metadata for this entry,
                # not a second independent status record.
                if field.startswith("2 "):
                    index += 1
            index += 1
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
                self._executable,
                ("--literal-pathspecs", "-c", "core.fsmonitor=false", *args),
                str(self._workspace.root), self._timeout_seconds, self._max_output_size,
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


    @property
    def repository_root(self) -> str:
        return os.path.normcase(str(self._workspace.root.resolve(strict=True)))

    @contextmanager
    def mutation_lock(self) -> Iterator[None]:
        """Serialize local-agent mutations per canonical repository root."""
        key = self._repository_lock_key
        with _REPOSITORY_LOCKS_GUARD:
            lock = _REPOSITORY_LOCKS.setdefault(key, threading.RLock())
        with lock:
            yield

    def capture_mutation_snapshot(
        self, operation: str, parameters: dict,
        previous_stage_receipt: dict | None = None,
    ) -> tuple[dict, dict]:
        """Capture the exact state that will be bound to an approval."""
        normalized = self._normalize_mutation_parameters(operation, parameters)
        snapshot = self._capture_mutation_snapshot(
            operation, normalized, previous_stage_receipt
        )
        return normalized, snapshot

    def prepare_mutation(
        self, operation: str, parameters: dict,
        previous_stage_receipt: dict | None = None,
    ) -> tuple[dict, dict]:
        normalized, snapshot = self.capture_mutation_snapshot(
            operation, parameters, previous_stage_receipt
        )
        self._validate_mutation_preconditions(
            operation, normalized, snapshot, previous_stage_receipt
        )
        return normalized, snapshot

    def _normalize_mutation_parameters(
        self, operation: str, parameters: dict
    ) -> dict:
        if not isinstance(parameters, dict):
            raise TypeError("Git mutation parameters must be a dictionary")
        if operation == "git_create_and_switch_branch":
            if set(parameters) != {"branch_name"}:
                raise ValueError("branch_name is the only accepted parameter")
            name = parameters["branch_name"]
            if not isinstance(name, str) or not name or name.startswith("-") or "\x00" in name:
                raise ValueError("branch_name must be a valid non-empty branch name")
            check = self._execute(
                "check_branch_name", ["check-ref-format", f"refs/heads/{name}"]
            )
            if check.stdout_truncated:
                raise GitToolError("Git branch-name validation output was truncated")
            exists = self._invoke(
                "branch_exists", ["show-ref", "--verify", "--quiet", f"refs/heads/{name}"],
                check=False,
            )
            if exists.exit_code == 0:
                raise ValueError(f"Branch already exists: {name}")
            if exists.exit_code != 1:
                raise GitCommandError("branch_exists", exists.exit_code, exists.stderr)
            return {"branch_name": name}

        if operation in {"git_stage", "git_commit"}:
            required = {"paths"} if operation == "git_stage" else {"message", "paths"}
            if set(parameters) != required:
                raise ValueError(f"Expected parameters: {', '.join(sorted(required))}")
            paths = self._normalize_paths(parameters["paths"])
            if operation == "git_stage":
                return {"paths": paths}
            message = parameters["message"]
            if not isinstance(message, str) or not message.strip() or "\x00" in message:
                raise ValueError("message must be a non-empty string without NUL")
            return {"message": message, "paths": paths}

        raise ValueError(f"Unsupported Git mutation: {operation}")

    def _normalize_paths(self, paths: object) -> list[str]:
        if not isinstance(paths, list) or not paths:
            raise ValueError("paths must be a non-empty list of relative paths")
        normalized: list[str] = []
        seen: set[str] = set()
        for path in paths:
            if not isinstance(path, str) or not path or "\x00" in path:
                raise ValueError("Each path must be a non-empty string without NUL")
            windows_path = PureWindowsPath(path)
            if Path(path).is_absolute() or windows_path.is_absolute() or windows_path.drive:
                raise ValueError(f"Absolute paths are not allowed: {path!r}")
            value = path.replace("\\", "/")
            parts = value.split("/")
            if (
                value.startswith("/")
                or any(part in {"", ".", ".."} for part in parts)
                or value.startswith(":")
                or any(character in value for character in "*?[]")
                or any(part.casefold() == ".git" for part in parts)
            ):
                raise ValueError(f"Unsafe or ambiguous Git path: {path!r}")
            try:
                self._workspace.resolve_path(value)
            except WorkspaceError as error:
                raise ValueError(f"Invalid workspace path {path!r}: {error}") from error
            key = os.path.normcase(value)
            if key in seen:
                raise ValueError(f"Duplicate path: {path!r}")
            seen.add(key)
            normalized.append(value)
        return sorted(normalized)

    def _capture_mutation_snapshot(
        self,
        operation: str,
        parameters: dict,
        previous_stage_receipt: dict | None,
    ) -> dict:
        common_dir_value = self._snapshot_command(
            "mutation_snapshot_common_dir", ["rev-parse", "--git-common-dir"]
        )
        common_dir_path = Path(os.fsdecode(common_dir_value).strip())
        if not common_dir_path.is_absolute():
            common_dir_path = self._workspace.root / common_dir_path
        common_git_dir = os.path.normcase(
            str(common_dir_path.resolve(strict=True))
        )
        if common_git_dir != self._repository_lock_key:
            raise GitRepositoryError(
                "The repository common Git directory changed after GitTool initialization"
            )

        status = self._snapshot_command(
            "mutation_snapshot_status",
            ["--no-optional-locks", "status", "--porcelain=v2", "-z", "--branch", "--untracked-files=all"],
        )
        branch = head = None
        status_entries = []
        for field in status.split(b"\x00"):
            if field.startswith(b"# branch.head "):
                value = field.removeprefix(b"# branch.head ")
                branch = None if value == b"(detached)" else os.fsdecode(value)
            elif field.startswith(b"# branch.oid "):
                value = field.removeprefix(b"# branch.oid ")
                head = None if value == b"(initial)" else os.fsdecode(value)
            elif field:
                status_entries.append(field.hex())

        index_entries = self._snapshot_command(
            "mutation_snapshot_index", ["ls-files", "--stage", "-z", "--"]
        )
        staged_diff = self._snapshot_command(
            "mutation_snapshot_staged_diff",
            ["diff", "--cached", "--binary", "--no-ext-diff", "--no-color", "--"],
        )
        unstaged_diff = self._snapshot_command(
            "mutation_snapshot_unstaged_diff",
            ["diff", "--binary", "--no-ext-diff", "--no-color", "--"],
        )
        staged_paths = self._split_paths(self._snapshot_command(
            "mutation_snapshot_staged_paths",
            ["diff", "--cached", "--name-only", "-z", "--no-renames", "--"],
        ))
        unstaged_paths = self._split_paths(self._snapshot_command(
            "mutation_snapshot_unstaged_paths",
            ["diff", "--name-only", "-z", "--no-renames", "--"],
        ))
        tracked_paths = self._split_paths(self._snapshot_command(
            "mutation_snapshot_tracked", ["ls-files", "-z", "--"]
        ))
        untracked_paths = self._split_paths(self._snapshot_command(
            "mutation_snapshot_untracked",
            ["ls-files", "--others", "--exclude-standard", "-z", "--"],
        ))
        conflicts = self._snapshot_command(
            "mutation_snapshot_conflicts", ["ls-files", "-u", "-z", "--"]
        )
        head_tree = (
            self._snapshot_command("mutation_snapshot_head_tree", ["ls-tree", "-r", "-z", "HEAD"])
            if head else b""
        )

        index_path = self._git_path("index")
        lock_path = self._git_path("index.lock")
        if lock_path.exists():
            raise GitToolError("Git index.lock exists; another Git operation may be active")
        try:
            index_file = index_path.read_bytes()
        except FileNotFoundError:
            index_file = b""

        selected = set(parameters.get("paths", []))
        if isinstance(previous_stage_receipt, dict):
            selected.update(previous_stage_receipt.get("staged_paths", []))
        selected_files = {
            path: self._file_fingerprint(path) for path in sorted(selected)
        }
        filter_attributes = self._snapshot_command(
            "mutation_snapshot_filter_attributes",
            ["check-attr", "-z", "filter", "--", *sorted(selected)],
        ) if selected else b""

        return {
            "repository_root": self.repository_root,
            "common_git_dir": common_git_dir,
            "operation": operation,
            "branch": branch,
            "head": head,
            "status_porcelain_v2_hex": status.hex(),
            "status_entries_hex": status_entries,
            "index_entries_hex": index_entries.hex(),
            "head_tree_entries_hex": head_tree.hex(),
            "index_tree_fingerprint": hashlib.sha256(index_entries).hexdigest(),
            "index_file_fingerprint": hashlib.sha256(index_file).hexdigest(),
            "index_fingerprint": hashlib.sha256(index_file + b"\x00" + index_entries).hexdigest(),
            "staged_diff_fingerprint": hashlib.sha256(staged_diff).hexdigest(),
            "unstaged_diff_fingerprint": hashlib.sha256(unstaged_diff).hexdigest(),
            "staged_paths": staged_paths,
            "unstaged_paths": unstaged_paths,
            "tracked_paths": tracked_paths,
            "untracked_paths": untracked_paths,
            "conflicts_hex": conflicts.hex(),
            "selected_files": selected_files,
            "filter_attributes_hex": filter_attributes.hex(),
            "previous_stage_receipt": previous_stage_receipt,
            "hook_state": self._hook_snapshot(),
        }

    def _file_fingerprint(self, relative_path: str) -> dict:
        path = self._workspace.resolve_path(relative_path)
        try:
            before = path.lstat()
        except FileNotFoundError:
            return {"type": "absent"}
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise ValueError(f"Only regular files or tracked deletions may be staged: {relative_path!r}")
        content = path.read_bytes()
        after = path.lstat()
        if (
            before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ino != after.st_ino
            or before.st_mode != after.st_mode
        ):
            raise GitToolError(f"File changed while its snapshot was being captured: {relative_path}")
        return {
            "type": "file",
            "mode": stat.S_IMODE(after.st_mode),
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }

    def _git_path(self, name: str) -> Path:
        value = self._snapshot_command(f"git_path_{name}", ["rev-parse", "--git-path", name])
        path = Path(os.fsdecode(value).strip())
        return path if path.is_absolute() else self._workspace.root / path

    def _snapshot_command(self, operation: str, arguments: list[str]) -> bytes:
        result = self._execute(operation, arguments)
        if result.stdout_truncated or result.stderr_truncated:
            raise GitToolError(f"Git output was truncated while capturing {operation}")
        return result.stdout_bytes

    @staticmethod
    def _split_paths(value: bytes) -> list[str]:
        return sorted(os.fsdecode(item) for item in value.split(b"\x00") if item)

    @staticmethod
    def _parse_index_entries(value: bytes) -> dict[bytes, tuple[bytes, bytes, bytes]]:
        entries = {}
        for record in value.split(b"\x00"):
            if not record:
                continue
            metadata, separator, path = record.partition(b"\t")
            if not separator:
                raise GitToolError("Git returned malformed index data")
            parts = metadata.split()
            if len(parts) != 3:
                raise GitToolError("Git returned malformed index metadata")
            entries[path] = (parts[0], parts[1], parts[2])
        return entries

    @staticmethod
    def _parse_tree_entries(value: bytes) -> dict[bytes, tuple[bytes, bytes]]:
        entries = {}
        for record in value.split(b"\x00"):
            if not record:
                continue
            metadata, separator, path = record.partition(b"\t")
            if not separator:
                raise GitToolError("Git returned malformed tree data")
            parts = metadata.split()
            if len(parts) != 3:
                raise GitToolError("Git returned malformed tree metadata")
            entries[path] = (parts[0], parts[2])
        return entries

    def _index_matches_head(self, snapshot: dict) -> bool:
        index = self._parse_index_entries(bytes.fromhex(snapshot["index_entries_hex"]))
        tree = self._parse_tree_entries(bytes.fromhex(snapshot["head_tree_entries_hex"]))
        return all(stage == b"0" for _, _, stage in index.values()) and {
            path: (mode, oid) for path, (mode, oid, _) in index.items()
        } == tree

    def _verify_stage_receipt(
        self, snapshot: dict, receipt: dict | None, *, selected_paths: set[str]
    ) -> None:
        if not isinstance(receipt, dict):
            raise GitToolError("A successful approved git_stage action is required first")
        expected = {
            "repository_root": snapshot["repository_root"],
            "common_git_dir": snapshot["common_git_dir"],
            "head": snapshot["head"],
            "branch": snapshot["branch"],
            "index_fingerprint": snapshot["index_fingerprint"],
            "staged_diff_fingerprint": snapshot["staged_diff_fingerprint"],
            "staged_paths": snapshot["staged_paths"],
        }
        if any(receipt.get(key) != value for key, value in expected.items()):
            raise GitToolError("The current Git index no longer matches the previous approved git_stage result")
        current_files = snapshot["selected_files"]
        for path, fingerprint in receipt.get("file_fingerprints", {}).items():
            if path not in selected_paths and current_files.get(path) != fingerprint:
                raise GitToolError(f"Previously staged file changed after git_stage: {path}")

    @staticmethod
    def _validate_no_external_filters(snapshot: dict) -> None:
        fields = bytes.fromhex(snapshot["filter_attributes_hex"]).split(b"\\x00")
        for index in range(0, len(fields) - 2, 3):
            attribute = fields[index + 1]
            value = fields[index + 2]
            if attribute == b"filter" and value not in {b"unspecified", b"unset"}:
                path = os.fsdecode(fields[index])
                raise GitToolError(
                    f"Staging is blocked because {path!r} uses an external clean filter"
                )

    def _validate_mutation_preconditions(
        self, operation: str, parameters: dict, snapshot: dict,
        previous_stage_receipt: dict | None,
    ) -> None:
        if snapshot["head"] is None:
            raise GitToolError("Git mutation requires an existing HEAD commit")
        if operation in {"git_create_and_switch_branch", "git_commit"}:
            hook_state = snapshot["hook_state"]
            if hook_state["configured_path"] or hook_state["files"]:
                raise GitToolError(
                    "This Git mutation is blocked while active Git hooks are configured"
                )
        if operation == "git_create_and_switch_branch":
            if snapshot["branch"] is None:
                raise GitToolError("Cannot create a branch while HEAD is detached")
            if snapshot["status_entries_hex"]:
                raise GitToolError("Creating a branch requires a completely clean repository")
            return
        if snapshot["conflicts_hex"]:
            raise GitToolError("Git conflicts must be resolved before this operation")
        self._validate_no_external_filters(snapshot)
        selected_paths = set(parameters["paths"])
        if operation == "git_stage":
            if previous_stage_receipt is None:
                if not self._index_matches_head(snapshot):
                    raise GitToolError("git_stage requires a clean index or a matching previous approved git_stage result")
            else:
                self._verify_stage_receipt(
                    snapshot, previous_stage_receipt, selected_paths=selected_paths
                )
            return
        if operation == "git_commit":
            self._verify_stage_receipt(
                snapshot, previous_stage_receipt, selected_paths=selected_paths
            )
            for path, fingerprint in previous_stage_receipt.get(
                "file_fingerprints", {}
            ).items():
                if snapshot["selected_files"].get(path) != fingerprint:
                    raise GitToolError(
                        f"Staged file changed after the approved stage: {path}"
                    )
            if sorted(parameters["paths"]) != sorted(previous_stage_receipt.get("staged_paths", [])):
                raise GitToolError("git_commit paths must exactly match the previous approved git_stage result")
            if (
                snapshot["unstaged_paths"]
                or snapshot["untracked_paths"]
                or snapshot["conflicts_hex"]
                or snapshot["staged_paths"] != previous_stage_receipt.get("staged_paths")
            ):
                raise GitToolError("git_commit requires only the exact staged paths, with no unstaged, untracked, or conflicting changes")
            if not snapshot["staged_paths"]:
                raise GitToolError("An empty commit is not allowed")
            return
        raise ValueError(f"Unsupported Git mutation: {operation}")

    def _execute_mutation_command(
        self, operation: str, arguments: list[str]
    ) -> GitCommandResult:
        result = self._execute(operation, arguments)
        if result.stdout_truncated or result.stderr_truncated:
            raise GitToolError(
                f"Git output was truncated during mutating operation {operation!r}"
            )
        return result

    def _execute_approved_mutation(
        self,
        operation: str,
        parameters: dict,
        expected_snapshot: dict,
        previous_stage_receipt: dict | None = None,
    ) -> dict:
        normalized, current = self.capture_mutation_snapshot(
            operation, parameters, previous_stage_receipt
        )
        if current != expected_snapshot:
            raise GitSnapshotChangedError(current)
        self._validate_mutation_preconditions(
            operation, normalized, current, previous_stage_receipt
        )

        if operation == "git_create_and_switch_branch":
            branch_name = normalized["branch_name"]
            self._execute_mutation_command(
                "create_and_switch_branch", ["switch", "-c", branch_name]
            )
            after = self._capture_mutation_snapshot(operation, normalized, None)
            if after["branch"] != branch_name or after["head"] != current["head"] or after["status_entries_hex"]:
                raise GitToolError("Branch creation postcondition failed; repository state was not rolled back")
            return {"operation": operation, "branch": branch_name, "head": after["head"]}

        if operation == "git_stage":
            selected = set(normalized["paths"])
            before_index = self._parse_index_entries(bytes.fromhex(current["index_entries_hex"]))
            before_staged = set(current["staged_paths"])
            before_unstaged = set(current["unstaged_paths"])
            before_untracked = set(current["untracked_paths"])
            self._execute_mutation_command(
                "stage", ["add", "--", *normalized["paths"]]
            )
            after = self._capture_mutation_snapshot(operation, normalized, previous_stage_receipt)
            after_index = self._parse_index_entries(bytes.fromhex(after["index_entries_hex"]))
            changed = {
                os.fsdecode(path)
                for path in set(before_index) | set(after_index)
                if before_index.get(path) != after_index.get(path)
            }
            if not changed.issubset(selected):
                raise GitToolError(f"git add changed paths outside the approved list: {sorted(changed - selected)}")
            if (
                after["head"] != current["head"]
                or after["branch"] != current["branch"]
                or any(after["selected_files"].get(path) != current["selected_files"].get(path) for path in after["selected_files"])
                or set(after["staged_paths"]) - (before_staged | selected)
                or (set(after["unstaged_paths"]) - selected) != (before_unstaged - selected)
                or (set(after["untracked_paths"]) - selected) != (before_untracked - selected)
                or after["conflicts_hex"] != current["conflicts_hex"]
            ):
                raise GitToolError("git_stage postcondition failed; no automatic unstage or rollback was attempted")
            staged_paths = after["staged_paths"]
            if not staged_paths:
                raise GitToolError("The selected paths produced no staged changes")
            file_fingerprints = {
                path: after["selected_files"][path] for path in staged_paths
            }
            receipt = {
                "repository_root": after["repository_root"],
                "common_git_dir": after["common_git_dir"],
                "head": after["head"],
                "branch": after["branch"],
                "index_fingerprint": after["index_fingerprint"],
                "staged_diff_fingerprint": after["staged_diff_fingerprint"],
                "staged_paths": staged_paths,
                "file_fingerprints": file_fingerprints,
            }
            return {
                "operation": operation,
                "paths": normalized["paths"],
                "staged_paths": staged_paths,
                "_stage_receipt": receipt,
            }

        if operation == "git_commit":
            index_before = self._parse_index_entries(bytes.fromhex(current["index_entries_hex"]))
            old_head = current["head"]
            self._execute_mutation_command(
                "commit",
                [
                    "-c", "commit.gpgsign=false", "commit",
                    "--no-gpg-sign", "-m", normalized["message"],
                ],
            )
            new_head = self._snapshot_command("commit_verify_head", ["rev-parse", "--verify", "HEAD"]).decode().strip()
            parent = self._snapshot_command("commit_verify_parent", ["rev-parse", "--verify", "HEAD^"]).decode().strip()
            tree = self._snapshot_command("commit_verify_tree", ["ls-tree", "-r", "-z", "HEAD"])
            changed_paths = self._split_paths(self._snapshot_command(
                "commit_verify_changed_paths",
                ["diff-tree", "--no-commit-id", "--name-only", "-z", "--no-renames", "-r", "HEAD^", "HEAD", "--"],
            ))
            tree_entries = self._parse_tree_entries(tree)
            expected_tree = {
                path: (mode, oid)
                for path, (mode, oid, stage) in index_before.items()
                if stage == b"0"
            }
            if (
                new_head == old_head
                or parent != old_head
                or tree_entries != expected_tree
                or changed_paths != current["staged_paths"]
            ):
                raise GitToolError("git_commit postcondition failed; the created commit was not rolled back")
            return {
                "operation": operation,
                "commit": new_head,
                "parent": parent,
                "paths": changed_paths,
            }

        raise ValueError(f"Unsupported Git mutation: {operation}")

    def _hook_snapshot(self) -> dict:
        configured = self._invoke(
            "hooks_path_config", ["config", "--get", "core.hooksPath"], check=False
        )
        if configured.exit_code == 0:
            configured_path = configured.stdout_bytes.decode(
                "utf-8", errors="strict"
            ).strip()
        elif configured.exit_code == 1:
            configured_path = None
        else:
            raise GitCommandError(
                "hooks_path_config", configured.exit_code, configured.stderr
            )

        if configured_path:
            hooks_path = Path(configured_path)
            if not hooks_path.is_absolute():
                hooks_path = self._workspace.root / hooks_path
        else:
            hooks_path = self._git_path("hooks")

        files = {}
        if hooks_path.exists():
            if hooks_path.is_symlink() or not hooks_path.is_dir():
                files["<hooks-directory>"] = {"type": "unsafe"}
            else:
                for entry in sorted(hooks_path.iterdir(), key=lambda item: item.name):
                    if entry.name.endswith(".sample"):
                        continue
                    metadata = entry.lstat()
                    if stat.S_ISLNK(metadata.st_mode):
                        fingerprint = {
                            "type": "symlink",
                            "target": os.readlink(entry),
                        }
                    elif stat.S_ISREG(metadata.st_mode):
                        content = entry.read_bytes()
                        fingerprint = {
                            "type": "file",
                            "mode": stat.S_IMODE(metadata.st_mode),
                            "sha256": hashlib.sha256(content).hexdigest(),
                        }
                    else:
                        fingerprint = {"type": "other"}
                    files[entry.name] = fingerprint

        return {
            "configured_path": configured_path,
            "files": files,
        }

