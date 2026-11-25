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
    pid_file = tmp_path / "child.pid"
    parent_info_file = tmp_path / "parent-job.json"
    child_info_file = tmp_path / "child-job.json"
    child_script = (
        "import ctypes,json,pathlib,sys; "
        "class_code=''; "
        "class Basic(ctypes.Structure): "
        "_fields_=[('a',ctypes.c_longlong),('b',ctypes.c_longlong),"
        "('flags',ctypes.c_ulong),('c',ctypes.c_size_t),"
        "('d',ctypes.c_size_t),('e',ctypes.c_ulong),"
        "('f',ctypes.c_size_t),('g',ctypes.c_ulong),('h',ctypes.c_ulong)]; "
        "k=ctypes.WinDLL('kernel32',use_last_error=True); "
        "q=k.QueryInformationJobObject; q.restype=ctypes.c_int; "
        "q.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_void_p,"
        "ctypes.c_ulong,ctypes.c_void_p]; "
        "i=Basic(); ok=bool(q(None,2,ctypes.byref(i),ctypes.sizeof(i),None)); "
        "pathlib.Path(sys.argv[1]).write_text(json.dumps("
        "{'pid':__import__('os').getpid(),'query_ok':ok,"
        "'error':0 if ok else ctypes.get_last_error(),"
        "'limit_flags':int(i.flags) if ok else 0})); "
        "import time; time.sleep(60)"
    )
    parent_script = (
        "import ctypes,json,pathlib,subprocess,sys,time; "
        "p=subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[4]]); "
        "pathlib.Path(sys.argv[2]).write_text(str(p.pid)); "
        "class Basic(ctypes.Structure): "
        "_fields_=[('a',ctypes.c_longlong),('b',ctypes.c_longlong),"
        "('flags',ctypes.c_ulong),('c',ctypes.c_size_t),"
        "('d',ctypes.c_size_t),('e',ctypes.c_ulong),"
        "('f',ctypes.c_size_t),('g',ctypes.c_ulong),('h',ctypes.c_ulong)]; "
        "k=ctypes.WinDLL('kernel32',use_last_error=True); "
        "q=k.QueryInformationJobObject; q.restype=ctypes.c_int; "
        "q.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_void_p,"
        "ctypes.c_ulong,ctypes.c_void_p]; "
        "i=Basic(); ok=bool(q(None,2,ctypes.byref(i),ctypes.sizeof(i),None)); "
        "pathlib.Path(sys.argv[3]).write_text(json.dumps("
        "{'pid':__import__('os').getpid(),'query_ok':ok,"
        "'error':0 if ok else ctypes.get_last_error(),"
        "'limit_flags':int(i.flags) if ok else 0})); "
        "time.sleep(60)"
    )
    outer_job = _current_job_info()
    result = run_windows(
        sys.executable,
        (
            "-c", parent_script, child_script, str(pid_file),
            str(parent_info_file), str(child_info_file),
        ),
        str(tmp_path),
        3,
    )

    assert result.timed_out, result.stderr.decode(errors="replace")
    assert pid_file.exists(), "parent did not start the child process"
    child_pid = int(pid_file.read_text())
    parent_info = json.loads(parent_info_file.read_text())
    child_info = json.loads(child_info_file.read_text()) if child_info_file.exists() else None
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
