"""Thin ctypes wrappers around the Win32 calls EZWipe needs.

Only read-only IOCTLs live here in Phase 1. Nothing in this module writes to
a disk. Every handle is opened inside a context manager so it is always
closed.
"""

from __future__ import annotations

import ctypes
import sys
from contextlib import contextmanager
from ctypes import wintypes

IS_WINDOWS = sys.platform == "win32"

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x1
FILE_SHARE_WRITE = 0x2
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

ERROR_INSUFFICIENT_BUFFER = 122
ERROR_MORE_DATA = 234
ERROR_ACCESS_DENIED = 5
ERROR_FILE_NOT_FOUND = 2
ERROR_NOT_SUPPORTED = 50
ERROR_INVALID_FUNCTION = 1


class WinError(OSError):
    def __init__(self, op: str, code: int):
        self.op = op
        self.code = code
        super().__init__(code, f"{op} failed with Win32 error {code} (0x{code:X})")


def _kernel32():
    if not IS_WINDOWS:
        raise RuntimeError("Windows API is only available on Windows")
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                wintypes.HANDLE]
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p,
                                    wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                    ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    k32.DeviceIoControl.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL
    k32.GetLogicalDriveStringsW.argtypes = [wintypes.DWORD, wintypes.LPWSTR]
    k32.GetLogicalDriveStringsW.restype = wintypes.DWORD
    k32.GetWindowsDirectoryW.argtypes = [wintypes.LPWSTR, wintypes.UINT]
    k32.GetWindowsDirectoryW.restype = wintypes.UINT
    k32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    k32.GetDriveTypeW.restype = wintypes.UINT
    return k32


@contextmanager
def open_device(path: str, access: int = 0):
    """Open a device handle. access=0 allows most query IOCTLs without admin."""
    k32 = _kernel32()
    handle = k32.CreateFileW(path, access, FILE_SHARE_READ | FILE_SHARE_WRITE,
                             None, OPEN_EXISTING, 0, None)
    if handle in (None, INVALID_HANDLE_VALUE):
        raise WinError(f"CreateFile({path})", ctypes.get_last_error())
    try:
        yield handle
    finally:
        k32.CloseHandle(handle)


def device_io_control(handle, code: int, in_buf: bytes | ctypes.Array | None,
                      out_size: int) -> bytes:
    """Call DeviceIoControl and return the bytes written to the output buffer."""
    k32 = _kernel32()
    if in_buf is None:
        in_ptr, in_len = None, 0
    else:
        in_obj = in_buf if isinstance(in_buf, ctypes.Array) else ctypes.create_string_buffer(bytes(in_buf), len(in_buf))
        in_ptr, in_len = ctypes.addressof(in_obj), ctypes.sizeof(in_obj)
    out = ctypes.create_string_buffer(out_size) if out_size else None
    returned = wintypes.DWORD(0)
    ok = k32.DeviceIoControl(handle, code, in_ptr, in_len,
                             ctypes.addressof(out) if out is not None else None,
                             out_size, ctypes.byref(returned), None)
    if not ok:
        raise WinError(f"DeviceIoControl(0x{code:08X})", ctypes.get_last_error())
    return out.raw[: returned.value] if out is not None else b""


def logical_drives() -> list[str]:
    k32 = _kernel32()
    buf = ctypes.create_unicode_buffer(1024)
    n = k32.GetLogicalDriveStringsW(len(buf), buf)
    if n == 0:
        raise WinError("GetLogicalDriveStrings", ctypes.get_last_error())
    return [d for d in buf[:n].split("\x00") if d]


def drive_type(root: str) -> int:
    return _kernel32().GetDriveTypeW(root)


def windows_directory() -> str:
    k32 = _kernel32()
    buf = ctypes.create_unicode_buffer(260)
    k32.GetWindowsDirectoryW(buf, len(buf))
    return buf.value


def is_admin() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        return bool(ctypes.WinDLL("shell32").IsUserAnAdmin())
    except OSError:
        return False
