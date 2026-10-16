"""Best-effort Windows job limits for the script worker.

Static checks are not the security boundary. The worker is a separate process
with a wall-clock timeout. On Windows it is also placed in a job that caps
memory and CPU time and kills the worker when the job handle is closed.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_PROCESS_TIME = 0x00000002
_JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
_JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
_JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000


@dataclass
class JobLimit:
    handle: int
    applied: bool
    detail: str


def limit_process(process, *, memory_mb: int, cpu_seconds: int) -> JobLimit:
    """Attach `process` to a job. Returns applied=False when the OS refuses."""
    if sys.platform != "win32":
        return JobLimit(0, False, "Memory jobs are applied on Windows only.")
    import ctypes
    from ctypes import wintypes

    class BasicLimit(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", ctypes.c_uint32),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", ctypes.c_uint32),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", ctypes.c_uint32),
            ("SchedulingClass", ctypes.c_uint32),
        ]

    class IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class ExtendedLimit(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimit),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return JobLimit(0, False, "CreateJobObject failed.")

    memory_bytes = int(memory_mb) * 1024 * 1024
    info = ExtendedLimit()
    info.BasicLimitInformation.LimitFlags = (
        _JOB_OBJECT_LIMIT_PROCESS_TIME
        | _JOB_OBJECT_LIMIT_ACTIVE_PROCESS
        | _JOB_OBJECT_LIMIT_PROCESS_MEMORY
        | _JOB_OBJECT_LIMIT_JOB_MEMORY
        | _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    )
    # 100-nanosecond units.
    info.BasicLimitInformation.PerProcessUserTimeLimit = int(cpu_seconds) * 10_000_000
    # Allow the interpreter and one helper. This still blocks a process tree.
    info.BasicLimitInformation.ActiveProcessLimit = 2
    info.ProcessMemoryLimit = memory_bytes
    info.JobMemoryLimit = memory_bytes
    if not kernel32.SetInformationJobObject(
        job,
        _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        kernel32.CloseHandle(job)
        return JobLimit(0, False, f"SetInformationJobObject failed ({ctypes.get_last_error()}).")

    handle = int(getattr(process, "_handle", 0) or 0)
    if not handle or not kernel32.AssignProcessToJobObject(job, handle):
        kernel32.CloseHandle(job)
        return JobLimit(0, False, f"AssignProcessToJobObject failed ({ctypes.get_last_error()}).")
    return JobLimit(int(job), True, "Memory and CPU job limits applied.")


def terminate_job(job_handle: int) -> None:
    if sys.platform != "win32" or not job_handle:
        return
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    kernel32.TerminateJobObject(job_handle, 1)


def close_job(job_handle: int) -> None:
    if sys.platform != "win32" or not job_handle:
        return
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle(job_handle)
