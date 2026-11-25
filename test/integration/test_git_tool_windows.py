from __future__ import annotations

import ctypes
import json
import os
import sys

from local_agent.execution.tools._windows_runner import run_windows

import pytest


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
