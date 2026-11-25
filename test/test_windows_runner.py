from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

import pytest

import local_agent.execution.tools._windows_runner as windows_runner


class FakeFunction:
    def __init__(self, kernel32, name):
        self.kernel32 = kernel32
        self.name = name
        self.restype = None
        self.argtypes = None

    def __call__(self, *args):
        return self.kernel32.call(self.name, *args)


class FakeKernel32:
    def __init__(self, failure=None):
        self.failure = failure
        self.calls = []
        self.last_error = 5
        self.pipe_handles = iter((101, 102, 103, 104))
        self.close_failures = 0
        self.read_release = threading.Event()
        self.wait_calls = 0
        self.terminate_calls = 0
        self.functions = {}
        self.attribute_handles = None
        self.attribute_update_size = None
        self.attribute_delete_calls = 0
        self.attribute_initialize_calls = 0
        self.create_process_flags = None
        self.create_process_inherit = None

    def __getattr__(self, name):
        function = self.functions.get(name)
        if function is None:
            function = FakeFunction(self, name)
            self.functions[name] = function
        return function

    def fail(self, name):
        return self.failure == name

    def call(self, name, *args):
        self.calls.append(name)
        if name == "CreatePipe":
            ctypes.cast(args[0], ctypes.POINTER(ctypes.c_void_p))[0] = next(self.pipe_handles)
            ctypes.cast(args[1], ctypes.POINTER(ctypes.c_void_p))[0] = next(self.pipe_handles)
            return 1
        if name == "SetHandleInformation":
            return 1
        if name == "InitializeProcThreadAttributeList":
            self.attribute_initialize_calls += 1
            size_pointer = ctypes.cast(args[3], ctypes.POINTER(ctypes.c_size_t))
            if args[0] is None:
                size_pointer[0] = 128
                self.last_error = 5 if self.fail("attribute_size_query") else 122
                return 0
            if self.fail(name):
                self.last_error = 5
                return 0
            return 1
        if name == "UpdateProcThreadAttribute":
            if self.fail(name):
                self.last_error = 5
                return 0
            self.attribute_update_size = args[4]
            handle_count = args[4] // ctypes.sizeof(ctypes.c_void_p)
            self.attribute_handles = [
                ctypes.cast(args[3], ctypes.POINTER(ctypes.c_void_p))[i]
                for i in range(handle_count)
            ]
            return 1
        if name == "DeleteProcThreadAttributeList":
            self.attribute_delete_calls += 1
            if self.fail(name):
                raise OSError("fake attribute-list cleanup failure")
            return None
        if name == "CreateJobObjectW":
            if self.fail(name):
                return 0
            return 500
        if name == "SetInformationJobObject":
            return 0 if self.fail(name) else 1
        if name == "CreateFileW":
            return 600
        if name == "CreateProcessW":
            self.create_process_inherit = bool(args[4])
            self.create_process_flags = args[5]
            if self.fail(name):
                return 0
            pointer = ctypes.cast(args[-1], ctypes.POINTER(ctypes.c_void_p))
            pointer[0] = 700
            pointer[1] = 701
            return 1
        if name == "AssignProcessToJobObject":
            return 0 if self.fail(name) else 1
        if name == "IsProcessInJob":
            ctypes.cast(args[2], ctypes.POINTER(wintypes.BOOL))[0] = 1
            return 1
        if name == "ResumeThread":
            return 0xFFFFFFFF if self.fail(name) else 1
        if name == "WaitForSingleObject":
            self.wait_calls += 1
            if self.fail(name) and self.wait_calls == 1:
                self.last_error = 6
                return windows_runner.WAIT_FAILED
            if self.failure == "ReadFile" and self.wait_calls == 1:
                self.read_release.wait(0.2)
                return windows_runner.WAIT_TIMEOUT
            if (
                self.failure == "AssignProcessToJobObject"
                and self.wait_calls == 1
            ):
                return windows_runner.WAIT_TIMEOUT
            if self.failure == "timeout":
                return windows_runner.WAIT_TIMEOUT
            return windows_runner.WAIT_OBJECT_0
        if name == "GetExitCodeProcess":
            ctypes.cast(args[1], ctypes.POINTER(wintypes.DWORD))[0] = 0
            return 1
        if name == "TerminateJobObject":
            self.terminate_calls += 1
            if self.fail(name) and self.terminate_calls == 1:
                return 0
            return 1
        if name == "TerminateProcess":
            return 0 if self.fail(name) else 1
        if name == "ReadFile":
            handle = args[0]
            if self.failure == "ReadFile" and handle == 101:
                self.last_error = 5
                self.read_release.set()
                return 0
            if self.failure == "reader_hang" and handle == 101:
                self.read_release.wait(1)
                self.last_error = 995
                return 0
            self.last_error = 109
            return 0
        if name == "OpenThread":
            return 800
        if name == "CancelSynchronousIo":
            self.read_release.set()
            return 0 if self.fail(name) else 1
        if name == "CloseHandle":
            if self.failure == "CloseHandle" and args[0] == 500 and self.close_failures == 0:
                self.close_failures += 1
                return 0
            return 1
        raise AssertionError(f"Unexpected Win32 API call: {name}")


@pytest.fixture
def fake_win32(monkeypatch):
    fake = FakeKernel32()
    monkeypatch.setattr(windows_runner.os, "name", "nt")
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: fake, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: fake.last_error, raising=False)
    monkeypatch.setattr(
        ctypes, "WinError", lambda code: OSError(code, f"fake Win32 error {code}"), raising=False
    )
    monkeypatch.setattr(windows_runner, "CLEANUP_TIMEOUT", 0.01)
    return fake


def execute():
    return windows_runner.run_windows(
        "git.exe", ("status",), ".", 0.5, job_name="LocalAgent-Test-Job"
    )


def test_process_is_assigned_before_resume(fake_win32):
    execute()
    calls = fake_win32.calls
    assert calls.index("CreateProcessW") < calls.index("AssignProcessToJobObject")
    assert calls.index("AssignProcessToJobObject") < calls.index("ResumeThread")


def test_create_process_receives_only_standard_io_handle_whitelist(fake_win32):
    execute()
    assert fake_win32.attribute_handles == [600, 102, 104]
    assert fake_win32.attribute_update_size == 3 * ctypes.sizeof(ctypes.c_void_p)
    assert fake_win32.create_process_inherit is True
    assert fake_win32.create_process_flags == 0x00000004 | 0x00080000
    assert fake_win32.calls.index("InitializeProcThreadAttributeList") < (
        fake_win32.calls.index("UpdateProcThreadAttribute")
    )
    assert fake_win32.calls.index("UpdateProcThreadAttribute") < (
        fake_win32.calls.index("CreateProcessW")
    )
    assert fake_win32.attribute_delete_calls == 1


@pytest.mark.parametrize(
    "api_name",
    ["InitializeProcThreadAttributeList", "UpdateProcThreadAttribute", "CreateProcessW"],
)
def test_attribute_setup_and_process_creation_failures_release_resources(
    fake_win32, api_name
):
    fake_win32.failure = api_name
    with pytest.raises(windows_runner.WindowsRunnerError, match=api_name):
        execute()
    if api_name == "InitializeProcThreadAttributeList":
        assert fake_win32.attribute_delete_calls == 0
    else:
        assert fake_win32.attribute_delete_calls == 1
    assert fake_win32.calls.count("CloseHandle") >= 4


def test_attribute_size_query_failure_closes_prepared_handles(fake_win32):
    fake_win32.failure = "attribute_size_query"
    with pytest.raises(
        windows_runner.WindowsRunnerError,
        match="InitializeProcThreadAttributeList size query failed",
    ):
        execute()
    assert fake_win32.attribute_delete_calls == 0
    assert fake_win32.calls.count("CloseHandle") >= 4


def test_attribute_list_cleanup_error_does_not_skip_handle_cleanup(fake_win32):
    fake_win32.failure = "DeleteProcThreadAttributeList"
    with pytest.raises(windows_runner.WindowsRunnerError, match="DeleteProcThreadAttributeList"):
        execute()
    assert fake_win32.attribute_delete_calls == 1
    assert fake_win32.calls.count("CloseHandle") >= 6


@pytest.mark.parametrize(
    "api_name",
    ["CreateJobObjectW", "SetInformationJobObject", "CreateProcessW"],
)
def test_setup_api_failures_close_every_created_handle(fake_win32, api_name):
    fake_win32.failure = api_name
    with pytest.raises(windows_runner.WindowsRunnerError, match=api_name):
        execute()
    assert fake_win32.calls.count("CloseHandle") >= 4


def test_assign_failure_never_resumes_and_cleanup_continues(fake_win32):
    fake_win32.failure = "AssignProcessToJobObject"
    with pytest.raises(windows_runner.WindowsRunnerError, match="AssignProcessToJobObject"):
        execute()
    assert "ResumeThread" not in fake_win32.calls
    assert "TerminateProcess" in fake_win32.calls
    assert "TerminateJobObject" in fake_win32.calls
    assert fake_win32.calls.count("CloseHandle") >= 6


@pytest.mark.parametrize("api_name", ["ResumeThread", "WaitForSingleObject"])
def test_post_creation_api_failures_terminate_job_and_close_handles(fake_win32, api_name):
    fake_win32.failure = api_name
    with pytest.raises(windows_runner.WindowsRunnerError, match=api_name):
        execute()
    assert "TerminateJobObject" in fake_win32.calls
    assert fake_win32.calls.count("CloseHandle") >= 6


def test_terminate_job_failure_is_reported_and_job_handle_is_still_closed(fake_win32):
    fake_win32.failure = "timeout"
    original = fake_win32.call

    def fail_termination(name, *args):
        if name == "TerminateJobObject":
            fake_win32.calls.append(name)
            fake_win32.terminate_calls += 1
            if fake_win32.terminate_calls == 1:
                return 0
            return 1
        return original(name, *args)

    fake_win32.call = fail_termination
    with pytest.raises(windows_runner.WindowsRunnerError, match="TerminateJobObject"):
        execute()
    assert "CloseHandle" in fake_win32.calls


def test_reader_error_is_diagnostic_and_terminates_without_deadlock(fake_win32):
    fake_win32.failure = "ReadFile"
    with pytest.raises(windows_runner.WindowsRunnerError, match="reader failed") as caught:
        execute()
    assert fake_win32.calls.count("ReadFile") >= 1
    assert "TerminateJobObject" in fake_win32.calls
    assert any(
        "ReadFile(stdout)" in note
        for note in getattr(caught.value, "__notes__", [])
    )
    assert "CloseHandle" in fake_win32.calls


def test_reader_cancellation_is_bounded_and_uses_reader_thread(fake_win32):
    fake_win32.failure = "reader_hang"
    execute()
    assert "OpenThread" in fake_win32.calls
    assert "CancelSynchronousIo" in fake_win32.calls


def test_close_handle_failure_does_not_skip_other_handles(fake_win32):
    fake_win32.failure = "CloseHandle"
    with pytest.raises(windows_runner.WindowsRunnerError, match="CloseHandle"):
        execute()
    assert fake_win32.close_failures == 1
    assert fake_win32.calls.count("CloseHandle") >= 6


def test_terminate_process_failure_does_not_skip_job_termination(fake_win32):
    fake_win32.failure = "AssignProcessToJobObject"
    original = fake_win32.call

    def fail_process_termination(name, *args):
        if name == "TerminateProcess":
            fake_win32.calls.append(name)
            fake_win32.last_error = 5
            return 0
        return original(name, *args)

    fake_win32.call = fail_process_termination
    with pytest.raises(windows_runner.WindowsRunnerError) as caught:
        execute()
    assert "TerminateJobObject" in fake_win32.calls
    assert any(
        "TerminateProcess" in note
        for note in getattr(caught.value, "__notes__", [])
    )



def test_cleanup_failure_is_attached_without_masking_primary_error(fake_win32):
    fake_win32.failure = "AssignProcessToJobObject"
    original = fake_win32.call

    def fail_job_close(name, *args):
        if name == "CloseHandle" and args[0] == 500 and fake_win32.close_failures == 0:
            fake_win32.calls.append(name)
            fake_win32.close_failures += 1
            return 0
        return original(name, *args)

    fake_win32.call = fail_job_close
    with pytest.raises(windows_runner.WindowsRunnerError, match="AssignProcessToJobObject") as caught:
        execute()
    notes = getattr(caught.value, "__notes__", [])
    assert any("CloseHandle" in note for note in notes)
    assert "TerminateJobObject" in fake_win32.calls


def test_reader_cancellation_failure_is_reported(fake_win32):
    fake_win32.failure = "reader_hang"
    original = fake_win32.call

    def fail_cancellation(name, *args):
        if name == "CancelSynchronousIo":
            fake_win32.calls.append(name)
            fake_win32.read_release.set()
            return 0
        return original(name, *args)

    fake_win32.call = fail_cancellation
    with pytest.raises(windows_runner.WindowsRunnerError) as caught:
        execute()
    assert any(
        "CancelSynchronousIo" in note
        for note in getattr(caught.value, "__notes__", [])
    ) or "CancelSynchronousIo" in str(caught.value)



def test_unassigned_process_wait_failure_still_attempts_termination(fake_win32):
    fake_win32.failure = "AssignProcessToJobObject"
    original = fake_win32.call

    def fail_first_wait(name, *args):
        if name == "WaitForSingleObject" and fake_win32.wait_calls == 0:
            fake_win32.calls.append(name)
            fake_win32.wait_calls += 1
            fake_win32.last_error = 6
            return windows_runner.WAIT_FAILED
        return original(name, *args)

    fake_win32.call = fail_first_wait
    with pytest.raises(windows_runner.WindowsRunnerError, match="AssignProcessToJobObject") as caught:
        execute()
    assert "TerminateProcess" in fake_win32.calls
    assert "TerminateJobObject" in fake_win32.calls
    assert any(
        "WaitForSingleObject" in note
        for note in getattr(caught.value, "__notes__", [])
    )
