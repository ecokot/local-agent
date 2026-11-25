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
    membership_file = tmp_path / "child-job-membership.txt"
    child_script = (
        "import ctypes,os,sys,time; "
        "k=ctypes.WinDLL('kernel32',use_last_error=True); "
        "p=k.OpenProcess(0x1000,False,os.getpid()); "
        "assert p, 'child OpenProcess failed: '+str(ctypes.get_last_error()); "
        "f=k.IsProcessInJob; f.restype=ctypes.c_int; "
        "f.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.POINTER(ctypes.c_int)]; "
        "inside=ctypes.c_int(); "
        "ok=f(p,None,ctypes.byref(inside)); "
        "error=ctypes.get_last_error(); k.CloseHandle(p); "
        "open(sys.argv[1],'w').write(str((ok,inside.value,error))); "
        "time.sleep(60)"
    )
    parent_script = (
        "import pathlib,subprocess,sys,time; "
        "p=subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[3]], close_fds=False); "
        "pathlib.Path(sys.argv[2]).write_text(str(p.pid)); "
        "deadline=time.monotonic()+5; "
        "membership=pathlib.Path(sys.argv[3]); "
        "while not membership.exists() and time.monotonic()<deadline: time.sleep(0.01); "
        "assert membership.exists(), 'child did not report Job Object membership'; "
        "time.sleep(60)"
    )
    result = run_windows(
        sys.executable,
        ("-c", parent_script, child_script, str(pid_file), str(membership_file)),
        str(tmp_path),
        3,
    )
    assert result.timed_out, result.stderr.decode(errors="replace")
    assert pid_file.exists(), "parent did not start the child process"
    assert membership_file.exists(), "child did not report Job Object membership"
    membership = membership_file.read_text()
    assert membership == "(1, 1, 0)", (
        "child is not in any Job Object (or IsProcessInJob failed): " + membership
    )
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
            f"child process {child_pid} was in a Job Object but survived TerminateJobObject"
        )
    finally:
        close(handle)
