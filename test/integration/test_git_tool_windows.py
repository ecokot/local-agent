from __future__ import annotations

import ctypes
import os
import sys
import uuid

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
    pid_file = tmp_path / "child.pid"
    child_script = "import time; time.sleep(60)"
    parent_script = (
        "import pathlib,subprocess,sys,time; "
        "p=subprocess.Popen([sys.executable,'-c',sys.argv[1]]); "
        "pathlib.Path(sys.argv[2]).write_text(str(p.pid)); "
        "time.sleep(60)"
    )
    result = run_windows(
        sys.executable,
        ("-c", parent_script, child_script, str(pid_file)),
        str(tmp_path),
        3,
    )

    assert result.timed_out, result.stderr.decode(errors="replace")
    assert pid_file.exists(), "parent did not start the child process"
    child_pid = int(pid_file.read_text())

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
