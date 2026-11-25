from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
from ctypes import wintypes

import pytest

from local_agent.execution.tools._windows_runner import run_windows

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows Job Object integration test")


def test_runner_drains_both_streams_and_bounds_saved_output():
    script = "import sys; sys.stdout.write('o' * 2000000); sys.stderr.write('e' * 2000000)"
    result = run_windows(sys.executable, ("-c", script), os.getcwd(), 15)
    assert result.exit_code == 0
    assert len(result.stdout) == 1024 * 1024
    assert len(result.stderr) == 1024 * 1024
    assert result.stdout_truncated
    assert result.stderr_truncated


def test_timeout_terminates_process_tree(tmp_path):
    parent_info_file = tmp_path / "parent-job.json"
    child_info_file = tmp_path / "child-job.json"
    job_name = f"LocalAgent-Test-{os.getpid()}-{tmp_path.name}"

    job_info_code = """
import ctypes
import json
import os
import pathlib
import sys

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
open_job = kernel32.OpenJobObjectW
open_job.restype = ctypes.c_void_p
open_job.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_wchar_p]
open_process = kernel32.OpenProcess
open_process.restype = ctypes.c_void_p
open_process.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
get_pid = kernel32.GetCurrentProcessId
get_pid.restype = ctypes.c_ulong
get_pid.argtypes = []
is_in_job = kernel32.IsProcessInJob
is_in_job.restype = ctypes.c_int
is_in_job.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)
]
close = kernel32.CloseHandle
close.restype = ctypes.c_int
close.argtypes = [ctypes.c_void_p]

job = open_job(0x0004, False, sys.argv[-1])  # JOB_OBJECT_QUERY
open_error = 0 if job else ctypes.get_last_error()
pid = get_pid()
process = open_process(0x1000, False, pid) if job else None
process_error = 0 if process else ctypes.get_last_error()
membership = ctypes.c_int()
membership_ok = bool(process) and bool(
    is_in_job(process, job, ctypes.byref(membership))
)
membership_error = 0 if membership_ok else ctypes.get_last_error()
job_info = {
    "pid": pid,
    "open_job_ok": bool(job),
    "open_job_error": open_error,
    "open_process_ok": bool(process),
    "open_process_error": process_error,
    "membership_query_ok": membership_ok,
    "membership_error": membership_error,
    "is_in_runner_job": bool(membership.value) if membership_ok else None,
}
if process:
    close(process)
if job:
    close(job)
"""

    child_script = (
        job_info_code
        + """
pathlib.Path(sys.argv[1]).write_text(json.dumps(job_info))
import time
time.sleep(60)
"""
    )
    parent_script = (
        """
import subprocess
import sys

subprocess.Popen([sys.executable, "-c", sys.argv[1], sys.argv[3], sys.argv[4]])
"""
        + job_info_code
        + """
pathlib.Path(sys.argv[2]).write_text(json.dumps(job_info))
import time
deadline = time.monotonic() + 5
while not pathlib.Path(sys.argv[3]).exists() and time.monotonic() < deadline:
    time.sleep(0.01)
time.sleep(60)
"""
    )

    # On Windows, sys.executable inside a venv may be a redirector that
    # starts the base interpreter as a separate process. Launch the base
    # interpreter directly so the process under test is the process assigned
    # to the runner's Job Object.
    runner_python = getattr(sys, "_base_executable", sys.executable)
    result = run_windows(
        runner_python,
        (
            "-c", parent_script, child_script,
            str(parent_info_file), str(child_info_file), job_name,
        ),
        str(tmp_path),
        8,
        job_name=job_name,
    )

    assert result.timed_out, result.stderr.decode(errors="replace")
    assert parent_info_file.exists(), "parent process did not write its Job Object diagnostics"
    assert child_info_file.exists(), "child process did not write its Job Object diagnostics"
    parent_info = json.loads(parent_info_file.read_text())
    child_info = json.loads(child_info_file.read_text())

    assert parent_info["open_job_ok"] and parent_info["open_process_ok"], (
        f"parent could not open the runner Job Object/process: {parent_info}"
    )
    assert parent_info["membership_query_ok"] and parent_info["is_in_runner_job"], (
        f"parent is not a member of the runner Job Object: "
        f"parent={parent_info}"
    )
    assert child_info["open_job_ok"] and child_info["open_process_ok"], (
        f"child could not open the runner Job Object/process: {child_info}"
    )
    assert child_info["membership_query_ok"] and child_info["is_in_runner_job"], (
        f"child did not inherit membership in the runner Job Object: {child_info}; "
        f"parent_job={parent_info}"
    )

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.restype = ctypes.c_void_p
    open_process.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    wait = kernel32.WaitForSingleObject
    wait.restype = ctypes.c_ulong
    wait.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    close = kernel32.CloseHandle
    close.argtypes = [ctypes.c_void_p]

    handle = open_process(0x00100000, False, child_info["pid"])  # SYNCHRONIZE
    if not handle:
        error = ctypes.get_last_error()
        assert error == 87, (
            f"OpenProcess failed for child PID {child_info['pid']} "
            f"with unexpected error {error}"
        )
        return
    try:
        assert wait(handle, 0) == 0, (
            f"child process {child_info['pid']} survived the runner's timeout; "
            "TerminateJobObject did not terminate the complete process tree"
        )
    finally:
        close(handle)


def _git(repository, *arguments):
    executable = shutil.which("git")
    if executable is None:
        pytest.skip("Git for Windows is not installed")
    return subprocess.run(
        [executable, "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
    )


def _init_repository(repository):
    repository.mkdir(parents=True, exist_ok=True)
    executable = shutil.which("git")
    if executable is None:
        pytest.skip("Git for Windows is not installed")
    subprocess.run([executable, "init", "-q", "-b", "main", str(repository)], check=True)
    _git(repository, "config", "user.name", "Local Agent Test")
    _git(repository, "config", "user.email", "local-agent@example.invalid")


def _repository_snapshot(repository):
    head = _git(repository, "rev-parse", "HEAD").stdout
    index = _git(repository, "ls-files", "--stage", "-z").stdout
    tracked_paths = [
        os.fsdecode(path)
        for path in _git(repository, "ls-files", "-z").stdout.split(b"\0")
        if path
    ]
    tracked = tuple(
        (path, (repository / path).read_bytes() if (repository / path).is_file() else None)
        for path in tracked_paths
    )
    untracked_paths = [
        os.fsdecode(path)
        for path in _git(repository, "ls-files", "--others", "--exclude-standard", "-z").stdout.split(b"\0")
        if path
    ]
    untracked = tuple(
        (path, (repository / path).read_bytes())
        for path in untracked_paths
    )
    return head, index, tracked, untracked


def test_git_tool_covers_detached_head_all_operations_and_read_only_snapshot(
    tmp_path, monkeypatch
):
    from local_agent.execution.tools.git import GitTool
    from local_agent.execution.workspace import Workspace

    repository = tmp_path / "repository"
    _init_repository(repository)
    initial_files = {
        "tracked.txt": b"base\n",
        "literal[ab].txt": b"literal base\n",
        "literala.txt": b"glob base\n",
        "rename source.txt": b"rename payload\n",
        "space name-雪.txt": b"unicode base\n",
    }
    for name, content in initial_files.items():
        (repository / name).write_bytes(content)
    _git(repository, "add", "--all")
    _git(repository, "commit", "-qm", "initial")
    initial_head = _git(repository, "rev-parse", "HEAD").stdout.decode().strip()
    _git(repository, "checkout", "--detach", "HEAD")

    (repository / "tracked.txt").write_bytes(b"staged version\n")
    _git(repository, "add", "tracked.txt")
    (repository / "literal[ab].txt").write_bytes(b"literal changed\n")
    (repository / "literala.txt").write_bytes(b"glob candidate changed\n")
    (repository / "rename source.txt").rename(repository / "rename target.txt")
    (repository / "new file-雪.txt").write_bytes(b"untracked unicode\n")
    (repository / "copy target.txt").write_bytes(
        (repository / "space name-雪.txt").read_bytes()
    )
    _git(repository, "add", "--all")
    _git(
        repository,
        "reset",
        "-q",
        "HEAD",
        "--",
        "new file-雪.txt",
    )
    (repository / "tracked.txt").write_bytes(b"unstaged version\n")

    git_executable = shutil.which("git")
    assert git_executable is not None
    monkeypatch.setattr(
        "local_agent.execution.tools.git.shutil.which",
        lambda _: git_executable,
    )
    tool = GitTool(Workspace(repository))
    before = _repository_snapshot(repository)

    status = tool.status()
    assert status.branch is None
    assert status.head == initial_head
    assert "# branch.head (detached)" in status.raw
    assert any(entry.startswith("2 R.") for entry in status.entries)
    assert any(
        entry.startswith("1 A.") and "copy target.txt" in entry
        for entry in status.entries
    )
    assert all("rename source.txt" not in entry for entry in status.entries)

    assert tool.branch() is None
    assert tool.head() == initial_head
    branches = tool.branches()
    assert any(branch.name == "main" and not branch.current for branch in branches)
    assert tool.log(5)[0].sha == initial_head

    unstaged_diff = tool.diff()
    staged_diff = tool.staged_diff()
    assert "unstaged version" in unstaged_diff
    assert "staged version" in staged_diff
    assert "unstaged version" not in staged_diff
    literal_diff = tool.staged_diff(["literal[ab].txt"])
    assert "literal changed" in literal_diff
    assert "glob candidate changed" not in literal_diff

    changed = tool.changed_files()
    assert "tracked.txt" in changed
    assert "rename target.txt" in changed
    assert "copy target.txt" in changed
    assert "new file-雪.txt" in changed
    assert "space name-雪.txt" not in changed

    after = _repository_snapshot(repository)
    assert after == before


def test_git_tool_accepts_linked_worktree(tmp_path, monkeypatch):
    from local_agent.execution.tools.git import GitTool
    from local_agent.execution.workspace import Workspace

    repository = tmp_path / "main-repository"
    linked = tmp_path / "linked-worktree"
    _init_repository(repository)
    (repository / "base.txt").write_text("base\n", encoding="utf-8")
    _git(repository, "add", "base.txt")
    _git(repository, "commit", "-qm", "initial")
    _git(repository, "worktree", "add", "-q", "-b", "linked", str(linked), "HEAD")
    assert (linked / ".git").is_file()

    git_executable = shutil.which("git")
    assert git_executable is not None
    monkeypatch.setattr(
        "local_agent.execution.tools.git.shutil.which",
        lambda _: git_executable,
    )
    tool = GitTool(Workspace(linked))
    assert tool.branch() == "linked"
    assert tool.head() == _git(repository, "rev-parse", "HEAD").stdout.decode().strip()
    assert tool.status().branch == "linked"


def test_runner_does_not_inherit_unlisted_parent_handle(tmp_path):
    from local_agent.execution.tools._windows_runner import run_windows

    class SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", wintypes.LPVOID),
            ("bInheritHandle", wintypes.BOOL),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.restype = wintypes.HANDLE
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(SECURITY_ATTRIBUTES),
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    get_final_path = kernel32.GetFinalPathNameByHandleW
    get_final_path.restype = wintypes.DWORD
    get_final_path.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    close_handle = kernel32.CloseHandle
    close_handle.restype = wintypes.BOOL
    close_handle.argtypes = [wintypes.HANDLE]

    parent_only_file = tmp_path / "parent-only-handle.txt"
    parent_only_file.write_text("must not be inherited", encoding="utf-8")
    security = SECURITY_ATTRIBUTES(
        ctypes.sizeof(SECURITY_ATTRIBUTES), None, True
    )
    handle = create_file(
        str(parent_only_file),
        0x80000000,  # GENERIC_READ
        0x00000001 | 0x00000002,  # FILE_SHARE_READ | FILE_SHARE_WRITE
        ctypes.byref(security),
        3,  # OPEN_EXISTING
        0x00000080,  # FILE_ATTRIBUTE_NORMAL
        None,
    )
    assert handle not in (None, wintypes.HANDLE(-1).value)
    try:
        expected_buffer = ctypes.create_unicode_buffer(32768)
        length = get_final_path(handle, expected_buffer, len(expected_buffer), 0)
        assert length > 0
        expected_path = expected_buffer.value
        child_script = """
import ctypes
import sys
from ctypes import wintypes

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
get_final_path = kernel32.GetFinalPathNameByHandleW
get_final_path.restype = wintypes.DWORD
get_final_path.argtypes = [
    wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD
]
handle = wintypes.HANDLE(int(sys.argv[1]))
buffer = ctypes.create_unicode_buffer(32768)
length = get_final_path(handle, buffer, len(buffer), 0)
print("inherited" if length and buffer.value == sys.argv[2] else "not-inherited")
"""
        runner_python = getattr(sys, "_base_executable", sys.executable)
        result = run_windows(
            runner_python,
            ("-c", child_script, str(handle), expected_path),
            str(tmp_path),
            10,
        )
        assert result.exit_code == 0, result.stderr.decode(errors="replace")
        assert result.stdout.decode().strip() == "not-inherited"
    finally:
        assert close_handle(handle)
