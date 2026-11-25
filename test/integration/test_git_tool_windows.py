from __future__ import annotations

import ctypes
import json
import os
import sys

import pytest

from local_agent.execution.tools._windows_runner import run_windows


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows Job Object integration test")


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", ctypes.c_ulong),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_ulong),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_ulong),
        ("SchedulingClass", ctypes.c_ulong),
    ]


def _current_job_info() -> dict[str, int | bool]:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    query = kernel32.QueryInformationJobObject
    query.restype = ctypes.c_int
    query.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong,
        ctypes.c_void_p,
    ]
    info = _BasicLimitInformation()
    ok = bool(query(None, 2, ctypes.byref(info), ctypes.sizeof(info), None))
    return {
        "query_ok": ok,
        "error": 0 if ok else ctypes.get_last_error(),
        "limit_flags": int(info.LimitFlags) if ok else 0,
    }


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

    job_info_code = """
import ctypes
import json
import os
import pathlib
import sys

class Basic(ctypes.Structure):
    _fields_ = [
        ("a", ctypes.c_longlong), ("b", ctypes.c_longlong),
        ("flags", ctypes.c_ulong), ("c", ctypes.c_size_t),
        ("d", ctypes.c_size_t), ("e", ctypes.c_ulong),
        ("f", ctypes.c_size_t), ("g", ctypes.c_ulong),
        ("h", ctypes.c_ulong),
    ]

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
query = kernel32.QueryInformationJobObject
query.restype = ctypes.c_int
query.argtypes = [
    ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p,
    ctypes.c_ulong, ctypes.c_void_p,
]
info = Basic()
ok = bool(query(None, 2, ctypes.byref(info), ctypes.sizeof(info), None))
job_info = {
    "pid": os.getpid(),
    "query_ok": ok,
    "error": 0 if ok else ctypes.get_last_error(),
    "limit_flags": int(info.flags) if ok else 0,
}
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
import pathlib
import subprocess
import sys
import time

subprocess.Popen([sys.executable, "-c", sys.argv[1], sys.argv[4]])
"""
        + job_info_code
        + """
pathlib.Path(sys.argv[3]).write_text(json.dumps(job_info))
time.sleep(60)
"""
    )

    outer_job = _current_job_info()
    result = run_windows(
        sys.executable,
        (
            "-c", parent_script, child_script,
            str(parent_info_file), str(child_info_file),
        ),
        str(tmp_path),
        3,
    )

    assert result.timed_out, result.stderr.decode(errors="replace")
    assert parent_info_file.exists(), "parent process did not write its Job Object diagnostics"
    assert child_info_file.exists(), "child process did not write its Job Object diagnostics"
    parent_info = json.loads(parent_info_file.read_text())
    child_info = json.loads(child_info_file.read_text())
    child_pid = child_info["pid"]
    assert child_pid in result.job_process_ids, (
        f"child process {child_pid} is absent from the runner's Job Object snapshot: "
        f"{result.job_process_ids}; outer_job={outer_job}; "
        f"parent_job={parent_info}; child_job={child_info}"
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

    handle = open_process(0x00100000, False, child_pid)  # SYNCHRONIZE
    if not handle:
        error = ctypes.get_last_error()
        assert error == 87, (
            f"OpenProcess failed for child PID {child_pid} with unexpected error {error}"
        )
        return
    try:
        assert wait(handle, 3000) == 0, (
            f"child process {child_pid} survived the runner's timeout; "
            "TerminateJobObject did not terminate the complete process tree"
        )
    finally:
        close(handle)
