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
    parent_membership_file = tmp_path / "parent-job-membership.txt"
    child_membership_file = tmp_path / "child-job-membership.txt"
    job_name = "LocalAgent-test-" + uuid.uuid4().hex
    membership_script = (
        "import ctypes,os,sys; "
        "k=ctypes.WinDLL('kernel32',use_last_error=True); "
        "p=k.OpenProcess(0x1000,False,os.getpid()); "
        "assert p, 'OpenProcess failed: '+str(ctypes.get_last_error()); "
        "open_job=k.OpenJobObjectW; open_job.restype=ctypes.c_void_p; "
        "open_job.argtypes=[ctypes.c_ulong,ctypes.c_int,ctypes.c_wchar_p]; "
        "j=open_job(0x0004,False,sys.argv[2]); "
        "assert j, 'OpenJobObjectW failed: '+str(ctypes.get_last_error()); "
        "f=k.IsProcessInJob; f.restype=ctypes.c_int; "
        "f.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.POINTER(ctypes.c_int)]; "
        "inside=ctypes.c_int(); "
        "ok=f(p,j,ctypes.byref(inside)); "
        "error=ctypes.get_last_error(); k.CloseHandle(j); k.CloseHandle(p); "
        "open(sys.argv[1],'w').write(str((ok,inside.value,error)))"
    )
    child_script = membership_script + "; import time; time.sleep(60)"
    parent_script = (
        "import ctypes,pathlib,subprocess,sys,time; "
        "p=subprocess.Popen([sys.executable,'-c',sys.argv[0],sys.argv[3],sys.argv[4]], close_fds=False); "
        "pathlib.Path(sys.argv[1]).write_text(str(p.pid)); "
        "k=ctypes.WinDLL('kernel32',use_last_error=True); "
        "h=k.OpenProcess(0x1000,False,os.getpid()) if False else None; "
        "import os; "
        "h=k.OpenProcess(0x1000,False,os.getpid()); "
        "assert h, 'parent OpenProcess failed: '+str(ctypes.get_last_error()); "
        "open_job=k.OpenJobObjectW; open_job.restype=ctypes.c_void_p; "
        "open_job.argtypes=[ctypes.c_ulong,ctypes.c_int,ctypes.c_wchar_p]; "
        "j=open_job(0x0004,False,sys.argv[4]); "
        "assert j, 'parent OpenJobObjectW failed: '+str(ctypes.get_last_error()); "
        "f=k.IsProcessInJob; f.restype=ctypes.c_int; "
        "f.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.POINTER(ctypes.c_int)]; "
        "inside=ctypes.c_int(); "
        "ok=f(h,j,ctypes.byref(inside)); "
        "error=ctypes.get_last_error(); k.CloseHandle(j); k.CloseHandle(h); "
        "open(sys.argv[2],'w').write(str((ok,inside.value,error))); "
        "time.sleep(60)"
    )
    result = run_windows(
        sys.executable,
        (
            "-c",
            parent_script,
            child_script,
            str(pid_file),
            str(parent_membership_file),
            str(child_membership_file),
            job_name,
        ),
        str(tmp_path),
        3,
        job_name=job_name,
    )
    assert result.timed_out, result.stderr.decode(errors="replace")
    assert pid_file.exists(), "parent did not start the child process"
    assert parent_membership_file.exists(), "parent did not report Job Object membership"
    assert child_membership_file.exists(), "child did not report Job Object membership"
    parent_membership = parent_membership_file.read_text()
    child_membership = child_membership_file.read_text()
    assert parent_membership == "(1, 1, 0)", (
        "parent is not in the runner's Job Object (or IsProcessInJob failed): "
        + parent_membership
    )
    assert child_membership == "(1, 1, 0)", (
        "child is not in the runner's Job Object (or IsProcessInJob failed): "
        + child_membership
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
            f"child process {child_pid} was in the runner's Job Object "
            "but survived TerminateJobObject"
        )
    finally:
        close(handle)
