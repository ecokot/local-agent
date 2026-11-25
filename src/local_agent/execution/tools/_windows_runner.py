"""Bounded Windows process runner backed by Win32 Job Objects."""
from __future__ import annotations

import ctypes
import os
import threading
from dataclasses import dataclass
from ctypes import wintypes

MAX_OUTPUT_SIZE = 1024 * 1024
CLEANUP_TIMEOUT = 2.0

@dataclass(frozen=True, slots=True)
class ProcessResult:
    stdout: bytes
    stderr: bytes
    exit_code: int | None
    timed_out: bool
    stdout_truncated: bool
    stderr_truncated: bool

class WindowsRunnerError(RuntimeError):
    """Win32 process execution failed."""

def run_windows(
    executable: str,
    arguments: tuple[str, ...],
    cwd: str,
    timeout_seconds: float,
) -> ProcessResult:
    if os.name != "nt":
        raise WindowsRunnerError("The Git runner is supported on Windows only")

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    HANDLE = wintypes.HANDLE
    DWORD = wintypes.DWORD
    BOOL = wintypes.BOOL

    class SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("nLength", DWORD), ("lpSecurityDescriptor", wintypes.LPVOID),
                    ("bInheritHandle", BOOL)]

    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ("cb", DWORD), ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
            ("dwX", DWORD), ("dwY", DWORD), ("dwXSize", DWORD),
            ("dwYSize", DWORD), ("dwXCountChars", DWORD),
            ("dwYCountChars", DWORD), ("dwFillAttribute", DWORD),
            ("dwFlags", DWORD), ("wShowWindow", wintypes.WORD),
            ("cbReserved2", wintypes.WORD), ("lpReserved2", wintypes.LPBYTE),
            ("hStdInput", HANDLE), ("hStdOutput", HANDLE), ("hStdError", HANDLE),
        ]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [("hProcess", HANDLE), ("hThread", HANDLE),
                    ("dwProcessId", DWORD), ("dwThreadId", DWORD)]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", DWORD),
            ("Affinity", ctypes.c_size_t), ("PriorityClass", DWORD),
            ("SchedulingClass", DWORD),
        ]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS), ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    def api(name, restype, argtypes):
        fn = getattr(kernel32, name)
        fn.restype = restype
        fn.argtypes = argtypes
        return fn

    CreatePipe = api("CreatePipe", BOOL, [ctypes.POINTER(HANDLE), ctypes.POINTER(HANDLE),
                                          ctypes.POINTER(SECURITY_ATTRIBUTES), DWORD])
    SetHandleInformation = api("SetHandleInformation", BOOL, [HANDLE, DWORD, DWORD])
    CreateJobObjectW = api("CreateJobObjectW", HANDLE, [wintypes.LPVOID, wintypes.LPCWSTR])
    SetInformationJobObject = api("SetInformationJobObject", BOOL, [HANDLE, ctypes.c_int,
                                      wintypes.LPVOID, DWORD])
    CreateProcessW = api("CreateProcessW", BOOL, [wintypes.LPCWSTR, wintypes.LPWSTR,
        wintypes.LPVOID, wintypes.LPVOID, BOOL, DWORD, wintypes.LPVOID,
        wintypes.LPCWSTR, ctypes.POINTER(STARTUPINFOW), ctypes.POINTER(PROCESS_INFORMATION)])
    AssignProcessToJobObject = api("AssignProcessToJobObject", BOOL, [HANDLE, HANDLE])
    ResumeThread = api("ResumeThread", DWORD, [HANDLE])
    WaitForSingleObject = api("WaitForSingleObject", DWORD, [HANDLE, DWORD])
    GetExitCodeProcess = api("GetExitCodeProcess", BOOL, [HANDLE, ctypes.POINTER(DWORD)])
    TerminateJobObject = api("TerminateJobObject", BOOL, [HANDLE, DWORD])
    TerminateProcess = api("TerminateProcess", BOOL, [HANDLE, DWORD])
    ReadFile = api("ReadFile", BOOL, [HANDLE, wintypes.LPVOID, DWORD,
                                      ctypes.POINTER(DWORD), wintypes.LPVOID])
    CloseHandle = api("CloseHandle", BOOL, [HANDLE])
    CancelSynchronousIo = api("CancelSynchronousIo", BOOL, [HANDLE])
    CreateFileW = api("CreateFileW", HANDLE, [wintypes.LPCWSTR, DWORD, DWORD,
        wintypes.LPVOID, DWORD, DWORD, HANDLE])
    OpenThread = api("OpenThread", HANDLE, [DWORD, BOOL, DWORD])

    job = None
    process = PROCESS_INFORMATION()
    readers: list[threading.Thread] = []
    reader_errors: list[BaseException] = []
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    truncated = {"stdout": False, "stderr": False}
    read_handles: list[int] = []
    write_handles: list[int] = []
    stdin_handle = None
    started = False

    def check(ok, operation):
        if not ok:
            raise WindowsRunnerError(f"{operation} failed: {ctypes.WinError(ctypes.get_last_error())}")

    def close(handle):
        if handle:
            CloseHandle(handle)

    def reader(name, handle):
        chunk = ctypes.create_string_buffer(65536)
        count = DWORD()
        try:
            while True:
                ok = ReadFile(handle, chunk, len(chunk), ctypes.byref(count), None)
                if not ok:
                    code = ctypes.get_last_error()
                    if code == 109:  # ERROR_BROKEN_PIPE
                        return
                    if code == 995:  # ERROR_OPERATION_ABORTED during bounded cleanup
                        return
                    raise WindowsRunnerError(f"ReadFile({name}) failed: {ctypes.WinError(code)}")
                if count.value == 0:
                    return
                remaining = MAX_OUTPUT_SIZE - len(buffers[name])
                if remaining > 0:
                    buffers[name].extend(chunk.raw[:min(remaining, count.value)])
                if count.value > max(remaining, 0):
                    truncated[name] = True
        except BaseException as error:
            reader_errors.append(error)

    try:
        security = SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), None, True)
        for name in ("stdout", "stderr"):
            read_h, write_h = HANDLE(), HANDLE()
            check(CreatePipe(ctypes.byref(read_h), ctypes.byref(write_h),
                             ctypes.byref(security), 0), f"CreatePipe({name})")
            read_handles.append(read_h.value)
            write_handles.append(write_h.value)
            check(SetHandleInformation(read_h, 1, 0), "SetHandleInformation")

        job = CreateJobObjectW(None, None)
        check(bool(job), "CreateJobObjectW")
        limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        limits.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
        check(SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)),
              "SetInformationJobObject")

        startup = STARTUPINFOW()
        startup.cb = ctypes.sizeof(startup)
        startup.dwFlags = 0x00000100  # STARTF_USESTDHANDLES
        stdin_handle = CreateFileW("NUL", 0x80000000, 0x00000001 | 0x00000002,
                                   None, 3, 0x00000080, None)
        check(stdin_handle not in (None, ctypes.c_void_p(-1).value), "CreateFileW(NUL)")
        check(SetHandleInformation(stdin_handle, 1, 1), "SetHandleInformation(stdin)")
        startup.hStdInput = stdin_handle
        startup.hStdOutput = write_handles[0]
        startup.hStdError = write_handles[1]
        command_line = ctypes.create_unicode_buffer(
            subprocess_list2cmdline((executable, *arguments))
        )
        check(CreateProcessW(executable, command_line, None, None, True,
                             0x00000004, None, cwd, ctypes.byref(startup),
                             ctypes.byref(process)), "CreateProcessW(CREATE_SUSPENDED)")
        started = True
        check(AssignProcessToJobObject(job, process.hProcess), "AssignProcessToJobObject")
        if ResumeThread(process.hThread) == 0xFFFFFFFF:
            check(False, "ResumeThread")

        for handle in write_handles:
            close(handle)
        write_handles.clear()

        for name, handle in zip(("stdout", "stderr"), read_handles):
            thread = threading.Thread(target=reader, args=(name, handle), daemon=True)
            thread.start()
            readers.append(thread)

        wait_ms = max(1, int(timeout_seconds * 1000))
        wait = WaitForSingleObject(process.hProcess, wait_ms)
        timed_out = wait == 0x00000102  # WAIT_TIMEOUT
        if timed_out:
            check(TerminateJobObject(job, 124), "TerminateJobObject")
            check(WaitForSingleObject(process.hProcess, int(CLEANUP_TIMEOUT * 1000)) == 0,
                  "WaitForSingleObject after termination")
        elif wait != 0:
            check(False, "WaitForSingleObject")

        # Closing the job kills any descendants still holding inherited pipe handles.
        close(job)
        job = None
        for thread in readers:
            thread.join(CLEANUP_TIMEOUT)
        for thread in readers:
            if thread.is_alive():
                native = OpenThread(0x0001, False, thread.native_id)  # THREAD_TERMINATE
                if native:
                    CancelSynchronousIo(native)
                    close(native)
                thread.join(CLEANUP_TIMEOUT)
        if any(thread.is_alive() for thread in readers):
            raise WindowsRunnerError("Reader thread did not stop within cleanup timeout")
        if reader_errors:
            raise WindowsRunnerError(f"Reader failed: {reader_errors[0]}")

        exit_code = DWORD()
        check(GetExitCodeProcess(process.hProcess, ctypes.byref(exit_code)), "GetExitCodeProcess")
        return ProcessResult(bytes(buffers["stdout"]), bytes(buffers["stderr"]),
                             None if timed_out else exit_code.value, timed_out,
                             truncated["stdout"], truncated["stderr"])
    except BaseException:
        if job:
            # Assignment may have failed, leaving the suspended process outside the job.
            if process.hProcess:
                TerminateProcess(process.hProcess, 1)
                WaitForSingleObject(process.hProcess, int(CLEANUP_TIMEOUT * 1000))
            TerminateJobObject(job, 1)
            close(job)
            job = None
        elif started and process.hProcess:
            # The job was already closed, so KILL_ON_JOB_CLOSE handled descendants.
            pass
        raise
    finally:
        for handle in write_handles + read_handles:
            close(handle)
        if process.hThread:
            close(process.hThread)
        if process.hProcess:
            close(process.hProcess)
        if stdin_handle:
            close(stdin_handle)

def subprocess_list2cmdline(args: tuple[str, ...]) -> str:
    """Quote argv using the Windows CRT command-line convention."""
    import subprocess
    return subprocess.list2cmdline(list(args))
