from __future__ import annotations

import ctypes
import os
import sys

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
    membership_file = tmp_path / "child-in-job.txt"
    child_script = (
        "import ctypes,sys,time; "
        "k=ctypes.WinDLL('kernel32',use_last_error=True); "
        "f=k.IsProcessInJob; "
        "f.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.POINTER(ctypes.c_int)]; "
        "f.restype=ctypes.c_int; "
        "v=ctypes.c_int(); "
        "assert f(ctypes.c_void_p(-1).value,None,ctypes.byref(v)); "
        "open(sys.argv[1],'w').write(str(v.value)); time.sleep(60)"
    )
    parent_script = (
        "import subprocess,sys,time,pathlib; "
        "p=subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[3]], close_fds=False); "
        "pathlib.Path(sys.argv[2]).write_text(str(p.pid)); "
        "time.sleep(60)"
    )
    result = run_windows(
        sys.executable,
        ("-c", parent_script, child_script, str(pid_file), str(membership_file)),
        str(tmp_path),
        1,
    )
    assert result.timed_out
    assert pid_file.exists()
    assert membership_file.exists()
    assert membership_file.read_text() == "1", "child process did not inherit Job Object membership"
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
    if handle:
        try:
            assert wait(handle, 3000) == 0, "child process survived Job Object termination"
        finally:
            close(handle)
