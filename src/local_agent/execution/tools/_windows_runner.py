"""Bounded Windows process runner backed by Win32 Job Objects."""
from __future__ import annotations

import ctypes
import os
import subprocess
import threading
import time
import uuid
from ctypes import wintypes
from dataclasses import dataclass

MAX_OUTPUT_SIZE = 1024 * 1024
CLEANUP_TIMEOUT = 2.0
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 0x00000102
WAIT_FAILED = 0xFFFFFFFF
ERROR_BROKEN_PIPE = 109
ERROR_OPERATION_ABORTED = 995


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
    max_output_size: int = MAX_OUTPUT_SIZE,
    job_name: str | None = None,
) -> ProcessResult:
    if (
        not isinstance(max_output_size, int)
        or isinstance(max_output_size, bool)
        or not 1 <= max_output_size <= MAX_OUTPUT_SIZE
    ):
        raise ValueError(f"max_output_size must be between 1 and {MAX_OUTPUT_SIZE}")
    if os.name != "nt":
        raise WindowsRunnerError("The Git runner is supported on Windows only")
    if job_name is not None and (not isinstance(job_name, str) or not job_name):
        raise ValueError("job_name must be a non-empty string")

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    HANDLE = wintypes.HANDLE
    DWORD = wintypes.DWORD
    BOOL = wintypes.BOOL

    class SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("nLength", DWORD),
            ("lpSecurityDescriptor", wintypes.LPVOID),
            ("bInheritHandle", BOOL),
        ]

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
        _fields_ = [
            ("hProcess", HANDLE), ("hThread", HANDLE),
            ("dwProcessId", DWORD), ("dwThreadId", DWORD),
        ]

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
        _fields_ = [
            (name, ctypes.c_ulonglong)
            for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
            )
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS), ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    def api(name, restype, argtypes):
        function = getattr(kernel32, name)
        function.restype = restype
        function.argtypes = argtypes
        return function

    CreatePipe = api(
        "CreatePipe",
        BOOL,
        [ctypes.POINTER(HANDLE), ctypes.POINTER(HANDLE), ctypes.POINTER(SECURITY_ATTRIBUTES), DWORD],
    )
    SetHandleInformation = api("SetHandleInformation", BOOL, [HANDLE, DWORD, DWORD])
    CreateJobObjectW = api("CreateJobObjectW", HANDLE, [wintypes.LPVOID, wintypes.LPCWSTR])
    SetInformationJobObject = api(
        "SetInformationJobObject", BOOL, [HANDLE, ctypes.c_int, wintypes.LPVOID, DWORD]
    )
    CreateProcessW = api(
        "CreateProcessW",
        BOOL,
        [
            wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.LPVOID, wintypes.LPVOID,
            BOOL, DWORD, wintypes.LPVOID, wintypes.LPCWSTR,
            ctypes.POINTER(STARTUPINFOW), ctypes.POINTER(PROCESS_INFORMATION),
        ],
    )
    AssignProcessToJobObject = api("AssignProcessToJobObject", BOOL, [HANDLE, HANDLE])
    IsProcessInJob = api("IsProcessInJob", BOOL, [HANDLE, HANDLE, ctypes.POINTER(BOOL)])
    ResumeThread = api("ResumeThread", DWORD, [HANDLE])
    WaitForSingleObject = api("WaitForSingleObject", DWORD, [HANDLE, DWORD])
    GetExitCodeProcess = api("GetExitCodeProcess", BOOL, [HANDLE, ctypes.POINTER(DWORD)])
    TerminateJobObject = api("TerminateJobObject", BOOL, [HANDLE, DWORD])
    TerminateProcess = api("TerminateProcess", BOOL, [HANDLE, DWORD])
    ReadFile = api(
        "ReadFile", BOOL, [HANDLE, wintypes.LPVOID, DWORD, ctypes.POINTER(DWORD), wintypes.LPVOID]
    )
    CloseHandle = api("CloseHandle", BOOL, [HANDLE])
    CancelSynchronousIo = api("CancelSynchronousIo", BOOL, [HANDLE])
    CreateFileW = api(
        "CreateFileW",
        HANDLE,
        [wintypes.LPCWSTR, DWORD, DWORD, wintypes.LPVOID, DWORD, DWORD, HANDLE],
    )
    OpenThread = api("OpenThread", HANDLE, [DWORD, BOOL, DWORD])

    if job_name is None:
        job_name = f"LocalAgent-{os.getpid()}-{uuid.uuid4().hex}"

    job = None
    process = PROCESS_INFORMATION()
    stdin_handle = None
    read_handles: list[int] = []
    write_handles: list[int] = []
    readers: list[threading.Thread] = []
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    truncated = {"stdout": False, "stderr": False}
    reader_errors: list[BaseException] = []
    reader_failed = threading.Event()
    cleanup_errors: list[str] = []
    assigned_to_job = False
    job_termination_attempted = False
    timed_out = False
    owned_handles: dict[int, str] = {}
    closed_handles: set[int] = set()

    def own(handle, label):
        if handle not in (None, 0):
            value = handle if isinstance(handle, int) else handle.value
            if value in (None, 0, ctypes.c_void_p(-1).value):
                return None
            owned_handles[value] = label
            return value
        return None

    def check(ok, operation):
        if not ok:
            code = ctypes.get_last_error()
            raise WindowsRunnerError(
                f"{operation} failed: {ctypes.WinError(code)}"
            )

    def close_owned(handle, label):
        if handle in (None, 0) or handle in closed_handles:
            return
        try:
            if not CloseHandle(handle):
                code = ctypes.get_last_error()
                cleanup_errors.append(
                    f"CloseHandle({label}) failed: {ctypes.WinError(code)}"
                )
            else:
                closed_handles.add(handle)
        except Exception as error:  # noqa: BLE001
            cleanup_errors.append(f"CloseHandle({label}) raised: {error!r}")

    def terminate_job():
        nonlocal job_termination_attempted
        if job and not job_termination_attempted:
            job_termination_attempted = True
            try:
                if not TerminateJobObject(job, 1):
                    code = ctypes.get_last_error()
                    cleanup_errors.append(
                        f"TerminateJobObject during cleanup failed: {ctypes.WinError(code)}"
                    )
            except Exception as error:  # noqa: BLE001
                cleanup_errors.append(f"TerminateJobObject during cleanup raised: {error!r}")

    def wait_for_process(timeout_ms, label):
        if not process.hProcess:
            return
        try:
            result = WaitForSingleObject(process.hProcess, timeout_ms)
            if result != WAIT_OBJECT_0:
                if result == WAIT_TIMEOUT:
                    cleanup_errors.append(f"{label} timed out")
                else:
                    code = ctypes.get_last_error()
                    cleanup_errors.append(
                        f"{label} failed: {ctypes.WinError(code)}"
                    )
        except Exception as error:  # noqa: BLE001
            cleanup_errors.append(f"{label} raised: {error!r}")

    def reader(name, handle):
        chunk = ctypes.create_string_buffer(65536)
        count = DWORD()
        try:
            while True:
                ok = ReadFile(handle, chunk, len(chunk), ctypes.byref(count), None)
                if not ok:
                    code = ctypes.get_last_error()
                    if code in (ERROR_BROKEN_PIPE, ERROR_OPERATION_ABORTED):
                        return
                    raise WindowsRunnerError(
                        f"ReadFile({name}) failed: {ctypes.WinError(code)}"
                    )
                if count.value == 0:
                    return
                remaining = max_output_size - len(buffers[name])
                if remaining > 0:
                    buffers[name].extend(chunk.raw[: min(remaining, count.value)])
                if count.value > max(remaining, 0):
                    truncated[name] = True
        except Exception as error:  # noqa: BLE001
            reader_errors.append(error)
            reader_failed.set()

    def stop_readers():
        for thread in readers:
            try:
                thread.join(CLEANUP_TIMEOUT)
            except Exception as error:  # noqa: BLE001
                cleanup_errors.append(f"Reader join raised: {error!r}")
        for thread in readers:
            if not thread.is_alive():
                continue
            native_handle = None
            try:
                native_id = thread.native_id
                native = OpenThread(0x0001, False, native_id) if native_id else None
                if native:
                    native_handle = own(native, "reader thread handle")
                    if not CancelSynchronousIo(native_handle):
                        code = ctypes.get_last_error()
                        if code != 1168:  # ERROR_NOT_FOUND: no synchronous I/O remains pending
                            cleanup_errors.append(
                                f"CancelSynchronousIo failed: {ctypes.WinError(code)}"
                            )
                elif thread.is_alive():
                    code = ctypes.get_last_error()
                    cleanup_errors.append(
                        f"OpenThread for reader cancellation failed: {ctypes.WinError(code)}"
                    )
            except Exception as error:  # noqa: BLE001
                cleanup_errors.append(f"Reader cancellation raised: {error!r}")
            finally:
                close_owned(native_handle, "reader thread handle")
            try:
                thread.join(CLEANUP_TIMEOUT)
            except Exception as error:  # noqa: BLE001
                cleanup_errors.append(f"Reader join after cancellation raised: {error!r}")
        if any(thread.is_alive() for thread in readers):
            cleanup_errors.append("Reader thread did not stop within cleanup timeout")

    primary_error = None
    try:
        security = SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), None, True)
        for name in ("stdout", "stderr"):
            read_h, write_h = HANDLE(), HANDLE()
            check(
                CreatePipe(ctypes.byref(read_h), ctypes.byref(write_h), ctypes.byref(security), 0),
                f"CreatePipe({name})",
            )
            read_value = own(read_h.value, f"{name} read pipe")
            write_value = own(write_h.value, f"{name} write pipe")
            read_handles.append(read_value)
            write_handles.append(write_value)
            check(SetHandleInformation(read_h, 1, 0), f"SetHandleInformation({name} read pipe)")

        job = own(CreateJobObjectW(None, job_name), "Job Object")
        check(bool(job), "CreateJobObjectW")
        limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        limits.BasicLimitInformation.LimitFlags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        check(
            SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)),
            "SetInformationJobObject",
        )

        startup = STARTUPINFOW()
        startup.cb = ctypes.sizeof(startup)
        startup.dwFlags = 0x00000100  # STARTF_USESTDHANDLES
        stdin_handle = own(
            CreateFileW("NUL", 0x80000000, 0x00000001 | 0x00000002, None, 3, 0x00000080, None),
            "stdin",
        )
        check(stdin_handle is not None, "CreateFileW(NUL)")
        check(SetHandleInformation(stdin_handle, 1, 1), "SetHandleInformation(stdin)")
        startup.hStdInput = stdin_handle
        startup.hStdOutput = write_handles[0]
        startup.hStdError = write_handles[1]
        command_line = ctypes.create_unicode_buffer(
            subprocess.list2cmdline([executable, *arguments])
        )
        check(
            CreateProcessW(
                executable, command_line, None, None, True, 0x00000004,
                None, cwd, ctypes.byref(startup), ctypes.byref(process),
            ),
            "CreateProcessW(CREATE_SUSPENDED)",
        )
        own(process.hProcess, "process")
        own(process.hThread, "primary thread")

        check(
            AssignProcessToJobObject(job, process.hProcess),
            "AssignProcessToJobObject",
        )
        assigned_to_job = True

        in_job = BOOL()
        check(IsProcessInJob(process.hProcess, job, ctypes.byref(in_job)), "IsProcessInJob")
        if not in_job.value:
            raise WindowsRunnerError(
                f"Process {process.dwProcessId} is not in Job Object after successful assignment"
            )
        if ResumeThread(process.hThread) == WAIT_FAILED:
            check(False, "ResumeThread")

        for handle in write_handles:
            close_owned(handle, "child pipe writer")
        write_handles.clear()

        for name, handle in zip(("stdout", "stderr"), read_handles):
            thread = threading.Thread(target=reader, args=(name, handle), daemon=True)
            thread.start()
            readers.append(thread)

        deadline = time.monotonic() + timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            wait_ms = max(1, min(50, int(remaining * 1000)))
            wait_result = WaitForSingleObject(process.hProcess, wait_ms)
            if wait_result == WAIT_OBJECT_0:
                break
            if wait_result == WAIT_FAILED:
                check(False, "WaitForSingleObject")
            if wait_result != WAIT_TIMEOUT:
                raise WindowsRunnerError(
                    f"WaitForSingleObject returned unexpected status 0x{wait_result:08X}"
                )
            if reader_failed.is_set():
                raise WindowsRunnerError("A stdout/stderr reader failed; terminating process job")

        if timed_out:
            check(TerminateJobObject(job, 124), "TerminateJobObject")
            check(
                WaitForSingleObject(process.hProcess, int(CLEANUP_TIMEOUT * 1000)) == WAIT_OBJECT_0,
                "WaitForSingleObject after termination",
            )

        close_owned(job, "Job Object")
        if job not in closed_handles:
            terminate_job()
            close_owned(job, "Job Object")
        if job in closed_handles:
            job = None

        stop_readers()
        if any(thread.is_alive() for thread in readers):
            raise WindowsRunnerError("Reader thread did not stop within cleanup timeout")
        if reader_errors:
            raise WindowsRunnerError(f"Reader failed: {reader_errors[0]!r}")

        exit_code = DWORD()
        check(GetExitCodeProcess(process.hProcess, ctypes.byref(exit_code)), "GetExitCodeProcess")
        result = ProcessResult(
            bytes(buffers["stdout"]),
            bytes(buffers["stderr"]),
            None if timed_out else exit_code.value,
            timed_out,
            truncated["stdout"],
            truncated["stderr"],
        )
    # Catch BaseException here so KeyboardInterrupt/SystemExit still trigger resource cleanup.
    except BaseException as error:  # noqa: BLE001
        primary_error = error
        if process.hProcess:
            if not assigned_to_job:
                try:
                    process_state = WaitForSingleObject(process.hProcess, 0)
                except Exception as cleanup_error:  # noqa: BLE001
                    process_state = WAIT_FAILED
                    cleanup_errors.append(
                        f"WaitForSingleObject before TerminateProcess raised: {cleanup_error!r}"
                    )
                if process_state != WAIT_OBJECT_0:
                    if process_state not in (WAIT_TIMEOUT, WAIT_FAILED):
                        cleanup_errors.append(
                            f"WaitForSingleObject before TerminateProcess returned "
                            f"unexpected status 0x{process_state:08X}"
                        )
                    elif process_state == WAIT_FAILED:
                        code = ctypes.get_last_error()
                        cleanup_errors.append(
                            f"WaitForSingleObject before TerminateProcess failed: {ctypes.WinError(code)}"
                        )
                    try:
                        if not TerminateProcess(process.hProcess, 1):
                            code = ctypes.get_last_error()
                            cleanup_errors.append(
                                f"TerminateProcess during cleanup failed: {ctypes.WinError(code)}"
                            )
                    except Exception as cleanup_error:  # noqa: BLE001
                        cleanup_errors.append(
                            f"TerminateProcess during cleanup raised: {cleanup_error!r}"
                        )
                    wait_for_process(
                        int(CLEANUP_TIMEOUT * 1000),
                        "WaitForSingleObject after TerminateProcess",
                    )
            else:
                terminate_job()
                wait_for_process(int(CLEANUP_TIMEOUT * 1000), "WaitForSingleObject after TerminateJobObject")
        # Always attempt Job Object termination, even when process termination or wait failed.
        terminate_job()
        if job:
            if not job_termination_attempted:
                terminate_job()
            close_owned(job, "Job Object")
            if job in closed_handles:
                job = None
        stop_readers()
        cleanup_errors.extend(
            f"Reader error during cleanup: {error!r}"
            for error in reader_errors
        )
    finally:
        if job:
            close_owned(job, "Job Object")
        for handle, label in tuple(owned_handles.items()):
            close_owned(handle, label)
        # Retry individual CloseHandle failures once; continue through every resource.
        for handle, label in tuple(owned_handles.items()):
            if handle not in closed_handles:
                close_owned(handle, label)

    if primary_error is not None:
        if cleanup_errors:
            details = "; ".join(cleanup_errors)
            try:
                primary_error.add_note(f"Additional cleanup errors: {details}")
                primary_error.cleanup_errors = tuple(cleanup_errors)
            except (AttributeError, TypeError):
                pass
        raise primary_error

    if cleanup_errors:
        raise WindowsRunnerError("Cleanup errors: " + "; ".join(cleanup_errors))
    return result
