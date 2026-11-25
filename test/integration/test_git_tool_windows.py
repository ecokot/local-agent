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
    child_membership_file = tmp_path / "child-in-job.txt"
    parent_membership_file = tmp_path / "parent-in-job.txt"
    job_name = "LocalAgent-Test-" + uuid.uuid4().hex
    child_script = (
        "import ctypes,os,sys,time; "
        "k=ctypes.WinDLL('kernel32',use_last_error=True); "
        "o=k.OpenJobObjectW; o.restype=ctypes.c_void_p; "
        "o.argtypes=[ctypes.c_ulong,ctypes.c_int,ctypes.c_wchar_p]; "
        "j=o(0x0004,False,sys.argv[2]); assert j, 'child OpenJobObjectW: '+str(ctypes.get_last_error()); "
        "q=k.QueryInformationJobObject; "
        "q.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_void_p,ctypes.c_ulong,ctypes.c_void_p]; "
        "q.restype=ctypes.c_int; "
        "b=ctypes.create_string_buffer(8+8*128); "
        "assert q(j,3,b,len(b),None), 'child QueryInformationJobObject: '+str(ctypes.get_last_error()); "
        "n=ctypes.c_uint32.from_buffer(b,4).value; "
        "ids=(ctypes.c_size_t*128).from_buffer(b,8); "
        "open(sys.argv[1],'w').write(str(os.getpid())+':'+','.join(map(str,ids[:n]))); "
        "time.sleep(60)"
    )
    parent_script = (
        "import ctypes,os,pathlib,subprocess,sys,time; "
        "k=ctypes.WinDLL('kernel32',use_last_error=True); "
        "o=k.OpenJobObjectW; o.restype=ctypes.c_void_p; "
        "o.argtypes=[ctypes.c_ulong,ctypes.c_int,ctypes.c_wchar_p]; "
        "j=o(0x0004,False,sys.argv[5]); assert j, 'parent OpenJobObjectW: '+str(ctypes.get_last_error()); "
        "q=k.QueryInformationJobObject; "
        "q.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_void_p,ctypes.c_ulong,ctypes.c_void_p]; "
        "q.restype=ctypes.c_int; "
        "b=ctypes.create_string_buffer(8+8*128); "
        "assert q(j,3,b,len(b),None), 'parent QueryInformationJobObject: '+str(ctypes.get_last_error()); "
        "n=ctypes.c_uint32.from_buffer(b,4).value; "
        "ids=(ctypes.c_size_t*128).from_buffer(b,8); "
        "pathlib.Path(sys.argv[4]).write_text(str(os.getpid())+':'+','.join(map(str,ids[:n]))); "
        "p=subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[3],sys.argv[5]], close_fds=False); "
        "pathlib.Path(sys.argv[2]).write_text(str(p.pid)); time.sleep(60)"
    )
    result = run_windows(
        sys.executable,
        ("-c", parent_script, child_script, str(pid_file), str(child_membership_file),
         str(parent_membership_file), job_name),
        str(tmp_path),
        1,
        job_name=job_name,
    )
    assert result.timed_out
    assert pid_file.exists()
    assert parent_membership_file.exists()
    assert child_membership_file.exists()
    parent_values = parent_membership_file.read_text().split(":")
    assert int(parent_values[0]) in {int(value) for value in parent_values[1].split(",") if value}, (
        "parent PID is absent from Job Object process list: " + parent_membership_file.read_text()
    )
    child_values = child_membership_file.read_text().split(":")
    assert int(child_values[0]) in {int(value) for value in child_values[1].split(",") if value}, (
        "child PID is absent from Job Object process list: " + child_membership_file.read_text()
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
    handle = open_process(0x00100000, False, child_pid)
    if handle:
        try:
            assert wait(handle, 3000) == 0, "child process survived Job Object termination"
        finally:
            close(handle)
