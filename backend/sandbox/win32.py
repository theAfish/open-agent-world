"""Narrow ctypes binding for the Windows sandbox security primitives.

The implementation intentionally uses the stable AppContainer launch path
(``PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES``), rather than the experimental
``Experimental_CreateProcessInSandbox`` API.  The latter currently requires an
unpublished FlatBuffer schema/header and is explicitly subject to change.  This
module still provides the same properties: an AppContainer token with no network
capabilities, explicit NTFS grants, a scrubbed Unicode environment, and a Job
Object that owns the complete process tree.

There is no ordinary ``subprocess`` launch path in this module.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

from .environment import windows_environment_block
from .models import SandboxLimits, SandboxSecurityError, SandboxValidationError


# Access masks used for explicit AppContainer SID grants.
FILE_GENERIC_READ = 0x00120089
FILE_GENERIC_WRITE = 0x00120116
FILE_GENERIC_EXECUTE = 0x001200A0
DELETE = 0x00010000
FILE_DELETE_CHILD = 0x00000040
WRITE_DENIED_ACCESS = 0x00000116 | DELETE | FILE_DELETE_CHILD

READ_ACCESS = FILE_GENERIC_READ
MODIFY_ACCESS = (
    FILE_GENERIC_READ
    | FILE_GENERIC_WRITE
    | FILE_GENERIC_EXECUTE
    | DELETE
    | FILE_DELETE_CHILD
)


@dataclass(frozen=True, slots=True)
class AppContainerProfile:
    name: str
    sid: int


@dataclass(frozen=True, slots=True)
class NativeCommandResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool
    cancelled: bool


class _SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]


class _SECURITY_CAPABILITIES(ctypes.Structure):
    _fields_ = [
        ("AppContainerSid", ctypes.c_void_p),
        ("Capabilities", ctypes.POINTER(_SID_AND_ATTRIBUTES)),
        ("CapabilityCount", wintypes.DWORD),
        ("Reserved", wintypes.DWORD),
    ]


class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_ubyte)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [
        ("StartupInfo", _STARTUPINFOW),
        ("lpAttributeList", ctypes.c_void_p),
    ]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


class _SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", wintypes.BOOL),
    ]


class _TRUSTEE_W(ctypes.Structure):
    _fields_ = [
        ("pMultipleTrustee", ctypes.c_void_p),
        ("MultipleTrusteeOperation", wintypes.DWORD),
        ("TrusteeForm", wintypes.DWORD),
        ("TrusteeType", wintypes.DWORD),
        ("ptstrName", ctypes.c_void_p),
    ]


class _EXPLICIT_ACCESS_W(ctypes.Structure):
    _fields_ = [
        ("grfAccessPermissions", wintypes.DWORD),
        ("grfAccessMode", wintypes.DWORD),
        ("grfInheritance", wintypes.DWORD),
        ("Trustee", _TRUSTEE_W),
    ]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _JOBOBJECT_BASIC_UI_RESTRICTIONS(ctypes.Structure):
    _fields_ = [("UIRestrictionsClass", wintypes.DWORD)]


class WindowsNativeApi:
    """The concrete Win32 security implementation used in production."""

    # ProcThreadAttributeValue(number, thread=False, input=True, additive=False)
    _PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
    _PROC_THREAD_ATTRIBUTE_MITIGATION_POLICY = 0x00020007
    _PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES = 0x00020009

    _EXTENDED_STARTUPINFO_PRESENT = 0x00080000
    _CREATE_UNICODE_ENVIRONMENT = 0x00000400
    _CREATE_NO_WINDOW = 0x08000000
    _CREATE_SUSPENDED = 0x00000004
    _STARTF_USESTDHANDLES = 0x00000100

    _JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
    _JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
    _JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
    _JOB_OBJECT_BASIC_UI_RESTRICTIONS = 4
    _ALL_JOB_UI_RESTRICTIONS = 0x000000FF

    _PROCESS_CREATION_MITIGATION_POLICY_WIN32K_SYSTEM_CALL_DISABLE_ALWAYS_ON = (
        0x0000000010000000
    )

    _SE_FILE_OBJECT = 1
    _DACL_SECURITY_INFORMATION = 0x00000004
    _PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
    _UNPROTECTED_DACL_SECURITY_INFORMATION = 0x20000000
    _GRANT_ACCESS = 1
    _DENY_ACCESS = 3
    _REVOKE_ACCESS = 4
    _TRUSTEE_IS_SID = 0
    _TRUSTEE_IS_UNKNOWN = 0
    _SUB_CONTAINERS_AND_OBJECTS_INHERIT = 0x3

    _WAIT_OBJECT_0 = 0
    _WAIT_TIMEOUT = 258
    _ERROR_ALREADY_EXISTS = 183
    _ERROR_BROKEN_PIPE = 109
    _ERROR_NO_DATA = 232
    _HANDLE_FLAG_INHERIT = 0x1
    _STD_READ_BUFFER = 64 * 1024
    _MAX_OUTPUT_BYTES = 2 * 1024 * 1024
    _OUTPUT_TRUNCATED = "\n[output truncated at 2 MiB]\n"

    def __init__(self) -> None:
        if os.name != "nt":
            raise SandboxSecurityError(
                "WindowsSandboxBackend requires Windows; no fallback is available"
            )
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        self._userenv = ctypes.WinDLL("userenv", use_last_error=True)
        self._configure_functions()

    def _configure_functions(self) -> None:
        k32 = self._kernel32
        adv = self._advapi32
        user = self._userenv

        user.CreateAppContainerProfile.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            ctypes.POINTER(_SID_AND_ATTRIBUTES),
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        user.CreateAppContainerProfile.restype = ctypes.c_long
        user.DeriveAppContainerSidFromAppContainerName.argtypes = [
            wintypes.LPCWSTR,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        user.DeriveAppContainerSidFromAppContainerName.restype = ctypes.c_long
        user.DeleteAppContainerProfile.argtypes = [wintypes.LPCWSTR]
        user.DeleteAppContainerProfile.restype = ctypes.c_long

        adv.GetNamedSecurityInfoW.argtypes = [
            wintypes.LPWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        adv.GetNamedSecurityInfoW.restype = wintypes.DWORD
        adv.GetSecurityInfo.argtypes = [wintypes.HANDLE, *adv.GetNamedSecurityInfoW.argtypes[1:]]
        adv.GetSecurityInfo.restype = wintypes.DWORD
        adv.SetEntriesInAclW.argtypes = [
            wintypes.ULONG,
            ctypes.POINTER(_EXPLICIT_ACCESS_W),
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        adv.SetEntriesInAclW.restype = wintypes.DWORD
        adv.SetNamedSecurityInfoW.argtypes = [
            wintypes.LPWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        adv.SetNamedSecurityInfoW.restype = wintypes.DWORD
        adv.SetSecurityInfo.argtypes = [wintypes.HANDLE, *adv.SetNamedSecurityInfoW.argtypes[1:]]
        adv.SetSecurityInfo.restype = wintypes.DWORD
        adv.FreeSid.argtypes = [ctypes.c_void_p]
        adv.FreeSid.restype = ctypes.c_void_p

        k32.LocalFree.argtypes = [ctypes.c_void_p]
        k32.LocalFree.restype = ctypes.c_void_p
        k32.GetVolumePathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        k32.GetVolumePathNameW.restype = wintypes.BOOL
        k32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
        k32.GetDriveTypeW.restype = wintypes.UINT
        k32.GetVolumeInformationW.argtypes = [
            wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR, wintypes.DWORD,
        ]
        k32.GetVolumeInformationW.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.CloseHandle.restype = wintypes.BOOL
        k32.CreatePipe.argtypes = [
            ctypes.POINTER(wintypes.HANDLE),
            ctypes.POINTER(wintypes.HANDLE),
            ctypes.POINTER(_SECURITY_ATTRIBUTES),
            wintypes.DWORD,
        ]
        k32.CreatePipe.restype = wintypes.BOOL
        k32.SetHandleInformation.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        k32.SetHandleInformation.restype = wintypes.BOOL
        k32.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(_SECURITY_ATTRIBUTES),
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        k32.CreateFileW.restype = wintypes.HANDLE
        k32.InitializeProcThreadAttributeList.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        k32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
        k32.UpdateProcThreadAttribute.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        k32.UpdateProcThreadAttribute.restype = wintypes.BOOL
        k32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
        k32.CreateProcessW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.LPCWSTR,
            ctypes.POINTER(_STARTUPINFOW),
            ctypes.POINTER(_PROCESS_INFORMATION),
        ]
        k32.CreateProcessW.restype = wintypes.BOOL
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            wintypes.INT,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        k32.SetInformationJobObject.restype = wintypes.BOOL
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.AssignProcessToJobObject.restype = wintypes.BOOL
        k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k32.TerminateJobObject.restype = wintypes.BOOL
        k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k32.TerminateProcess.restype = wintypes.BOOL
        k32.ResumeThread.argtypes = [wintypes.HANDLE]
        k32.ResumeThread.restype = wintypes.DWORD
        k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k32.WaitForSingleObject.restype = wintypes.DWORD
        k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k32.GetExitCodeProcess.restype = wintypes.BOOL
        k32.ReadFile.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        ]
        k32.ReadFile.restype = wintypes.BOOL

    @staticmethod
    def _hresult(value: int) -> int:
        return value & 0xFFFFFFFF

    @staticmethod
    def _hresult_from_win32(error: int) -> int:
        return 0x80070000 | (error & 0xFFFF)

    def _raise_last_error(self, operation: str) -> None:
        error = ctypes.get_last_error()
        detail = ctypes.FormatError(error).strip()
        raise SandboxSecurityError(f"{operation} failed ({error}): {detail}")

    def _raise_hresult(self, operation: str, hresult: int) -> None:
        normalized = self._hresult(hresult)
        raise SandboxSecurityError(f"{operation} failed (HRESULT 0x{normalized:08X})")

    def ensure_appcontainer(self, identity: str) -> AppContainerProfile:
        sid = ctypes.c_void_p()
        hr = self._userenv.CreateAppContainerProfile(
            identity,
            identity,
            "Open Agent World sandbox",
            None,
            0,
            ctypes.byref(sid),
        )
        if self._hresult(hr) == self._hresult_from_win32(self._ERROR_ALREADY_EXISTS):
            hr = self._userenv.DeriveAppContainerSidFromAppContainerName(
                identity, ctypes.byref(sid)
            )
        if self._hresult(hr) != 0 or not sid.value:
            self._raise_hresult("Create/derive AppContainer profile", hr)
        return AppContainerProfile(name=identity, sid=int(sid.value))

    def free_appcontainer_sid(self, profile: AppContainerProfile) -> None:
        if profile.sid:
            self._advapi32.FreeSid(ctypes.c_void_p(profile.sid))

    def delete_appcontainer(self, identity: str) -> None:
        hr = self._userenv.DeleteAppContainerProfile(identity)
        # HRESULT_FROM_WIN32(ERROR_NOT_FOUND) and ERROR_FILE_NOT_FOUND are an
        # acceptable idempotent outcome during cleanup.
        if self._hresult(hr) not in {
            0,
            self._hresult_from_win32(2),
            self._hresult_from_win32(1168),
        }:
            self._raise_hresult("DeleteAppContainerProfile", hr)

    def grant_path(self, path: Path, sid: int, *, read_only: bool) -> None:
        access = READ_ACCESS if read_only else MODIFY_ACCESS
        self._change_path_acl(path, sid, self._GRANT_ACCESS, access)
        if read_only:
            # A hard link created beneath a writable directory can acquire an
            # inherited allow ACE on the shared file object.  An explicit deny
            # for this AppContainer SID keeps that inherited ACE from turning a
            # read-only mount writable while leaving the host user's ACEs alone.
            # Generic write includes READ_CONTROL and SYNCHRONIZE, both also
            # needed for ordinary reads. Deny only the specific write rights.
            self._change_path_acl(path, sid, self._DENY_ACCESS, WRITE_DENIED_ACCESS)

    def validate_workspace_volume(self, path: Path) -> None:
        volume = ctypes.create_unicode_buffer(32768)
        if not self._kernel32.GetVolumePathNameW(str(path), volume, len(volume)):
            self._raise_last_error("GetVolumePathNameW")
        filesystem = ctypes.create_unicode_buffer(64)
        flags = wintypes.DWORD()
        if not self._kernel32.GetVolumeInformationW(
            volume.value, None, 0, None, None, ctypes.byref(flags),
            filesystem, len(filesystem),
        ):
            self._raise_last_error("GetVolumeInformationW")
        # No remote shares, FAT/exFAT, WSL redirectors, or ACL emulation.
        if (
            self._kernel32.GetDriveTypeW(volume.value) != 3
            or filesystem.value.upper() != "NTFS"
            or not flags.value & 0x8  # FILE_PERSISTENT_ACLS
        ):
            raise SandboxValidationError("Windows workspaces require a local NTFS volume with persistent ACLs")

    @staticmethod
    def _workspace_paths(path: Path, *, granting: bool):
        pending = [path]
        while pending:
            item = pending.pop()
            try:
                metadata = item.lstat()
            except FileNotFoundError:
                if granting:
                    raise SandboxSecurityError(f"workspace changed during authorization: {item}")
                continue
            reparse = bool(getattr(metadata, "st_file_attributes", 0) & 0x400) or item.is_symlink()
            if reparse and granting:
                raise SandboxSecurityError(f"workspace contains a reparse point: {item}")
            yield item
            if not reparse and item.is_dir():
                pending.extend(item.iterdir())

    def open_workspace(self, path: Path) -> int:
        # Keep the selected directory identity stable while authority is live.
        # Without delete sharing the root cannot be renamed or replaced.
        handle = self._kernel32.CreateFileW(
            str(path), 0x02000000, 0x3, None, 3, 0x02200000, None,
        )
        if handle == ctypes.c_void_p(-1).value:
            self._raise_last_error(f"hold workspace directory: {path}")
        return int(handle)

    def close_workspace(self, handle: int) -> None:
        self._kernel32.CloseHandle(handle)

    def grant_workspace(
        self, path: Path, sid: int, *, read_only: bool, root_handle: int | None = None,
    ) -> None:
        # Apply only this SID, never replace a tree's ACLs or ownership. Explicit
        # per-object changes support protected child ACLs and avoid Windows'
        # automatic recursive propagation following newly introduced junctions.
        # Directory ACEs still inherit onto files created by the sandbox.
        try:
            for item in self._workspace_paths(path, granting=True):
                handle = root_handle if item == path else None
                self._change_path_acl(item, sid, self._REVOKE_ACCESS, 0, propagate=False, object_handle=handle)
                self._change_path_acl(
                    item, sid, self._GRANT_ACCESS,
                    READ_ACCESS if read_only else MODIFY_ACCESS,
                    propagate=False,
                    object_handle=handle,
                )
                if read_only:
                    self._change_path_acl(item, sid, self._DENY_ACCESS, WRITE_DENIED_ACCESS, propagate=False, object_handle=handle)
        except BaseException:
            self.revoke_workspace(path, sid, root_handle=root_handle)
            raise

    def revoke_workspace(self, path: Path, sid: int, *, root_handle: int | None = None) -> None:
        errors: list[str] = []
        for item in self._workspace_paths(path, granting=False):
            try:
                self._change_path_acl(
                    item, sid, self._REVOKE_ACCESS, 0, propagate=False,
                    object_handle=root_handle if item == path else None,
                )
            except SandboxSecurityError as exc:
                errors.append(str(exc))
        if errors:
            raise SandboxSecurityError("workspace ACL revocation failed: " + "; ".join(errors[:3]))

    def protect_path_acl(self, path: Path) -> None:
        """Stop a mount file inheriting the writable workspace package ACE.

        A newly-created hard link can carry the destination directory's
        inherited ACE into the file's shared security descriptor.  Marking the
        DACL protected converts the current entries to explicit entries, after
        which the package's inherited write grant can be removed and replaced
        with the requested access level.
        """

        self._set_path_acl_inheritance(path, protected=True)

    def unprotect_path_acl(self, path: Path) -> None:
        self._set_path_acl_inheritance(path, protected=False)

    def _set_path_acl_inheritance(self, path: Path, *, protected: bool) -> None:
        if not path.exists():
            raise SandboxSecurityError(f"cannot change ACL on missing path: {path}")

        old_dacl = ctypes.c_void_p()
        security_descriptor = ctypes.c_void_p()
        result = self._advapi32.GetNamedSecurityInfoW(
            str(path),
            self._SE_FILE_OBJECT,
            self._DACL_SECURITY_INFORMATION,
            None,
            None,
            ctypes.byref(old_dacl),
            None,
            ctypes.byref(security_descriptor),
        )
        if result != 0:
            raise SandboxSecurityError(
                f"GetNamedSecurityInfoW failed for {path} (Win32 {result})"
            )
        try:
            protection = (
                self._PROTECTED_DACL_SECURITY_INFORMATION
                if protected
                else self._UNPROTECTED_DACL_SECURITY_INFORMATION
            )
            result = self._advapi32.SetNamedSecurityInfoW(
                str(path),
                self._SE_FILE_OBJECT,
                self._DACL_SECURITY_INFORMATION | protection,
                None,
                None,
                old_dacl,
                None,
            )
            if result != 0:
                raise SandboxSecurityError(
                    f"SetNamedSecurityInfoW failed for {path} (Win32 {result})"
                )
        finally:
            if security_descriptor.value:
                self._kernel32.LocalFree(security_descriptor)

    def revoke_path(self, path: Path, sid: int) -> None:
        self._change_path_acl(path, sid, self._REVOKE_ACCESS, 0)

    def _change_path_acl(
        self, path: Path, sid: int, mode: int, access: int, *, propagate: bool = True,
        object_handle: int | None = None,
    ) -> None:
        if not os.path.lexists(path):
            raise SandboxSecurityError(f"cannot change ACL on missing path: {path}")

        handle = object_handle
        close_handle = False
        if not propagate and handle is None:
            # MAXIMUM_ALLOWED deliberately disables SetSecurityInfo's automatic
            # child propagation (documented Windows API contract). Opening the
            # reparse point itself keeps cleanup from granting/revoking its target.
            handle = self._kernel32.CreateFileW(
                str(path), 0x02000000, 0x7, None, 3, 0x02200000, None,
            )
            if handle == ctypes.c_void_p(-1).value:
                self._raise_last_error(f"open workspace ACL: {path}")
            close_handle = True

        old_dacl = ctypes.c_void_p()
        security_descriptor = ctypes.c_void_p()
        getter = self._advapi32.GetNamedSecurityInfoW if propagate else self._advapi32.GetSecurityInfo
        result = getter(
            str(path) if propagate else handle,
            self._SE_FILE_OBJECT,
            self._DACL_SECURITY_INFORMATION,
            None,
            None,
            ctypes.byref(old_dacl),
            None,
            ctypes.byref(security_descriptor),
        )
        if result != 0:
            if close_handle:
                self._kernel32.CloseHandle(handle)
            raise SandboxSecurityError(
                f"GetNamedSecurityInfoW failed for {path} (Win32 {result})"
            )

        new_dacl = ctypes.c_void_p()
        try:
            trustee = _TRUSTEE_W(
                pMultipleTrustee=None,
                MultipleTrusteeOperation=0,
                TrusteeForm=self._TRUSTEE_IS_SID,
                TrusteeType=self._TRUSTEE_IS_UNKNOWN,
                ptstrName=sid,
            )
            inheritance = (
                self._SUB_CONTAINERS_AND_OBJECTS_INHERIT if path.is_dir() else 0
            )
            entry = _EXPLICIT_ACCESS_W(
                grfAccessPermissions=access,
                grfAccessMode=mode,
                grfInheritance=inheritance,
                Trustee=trustee,
            )
            result = self._advapi32.SetEntriesInAclW(
                1, ctypes.byref(entry), old_dacl, ctypes.byref(new_dacl)
            )
            if result != 0:
                raise SandboxSecurityError(
                    f"SetEntriesInAclW failed for {path} (Win32 {result})"
                )
            setter = self._advapi32.SetNamedSecurityInfoW if propagate else self._advapi32.SetSecurityInfo
            result = setter(
                str(path) if propagate else handle,
                self._SE_FILE_OBJECT,
                self._DACL_SECURITY_INFORMATION,
                None,
                None,
                new_dacl,
                None,
            )
            if result != 0:
                raise SandboxSecurityError(
                    f"SetNamedSecurityInfoW failed for {path} (Win32 {result})"
                )
        finally:
            if new_dacl.value:
                self._kernel32.LocalFree(new_dacl)
            if security_descriptor.value:
                self._kernel32.LocalFree(security_descriptor)
            if close_handle:
                self._kernel32.CloseHandle(handle)

    def terminate_job(self, job_handle: int) -> None:
        if job_handle and not self._kernel32.TerminateJobObject(
            wintypes.HANDLE(job_handle), 0xC000013A
        ):
            error = ctypes.get_last_error()
            # A concurrently completed/closed job is already terminated.
            if error not in {6, 87}:
                self._raise_last_error("TerminateJobObject")

    def run_appcontainer(
        self,
        profile: AppContainerProfile,
        argv: Sequence[str],
        *,
        cwd: Path,
        environment: Mapping[str, str],
        limits: SandboxLimits,
        timeout_seconds: float,
        cancel_event: threading.Event,
        on_stdout: Callable[[str], None],
        on_stderr: Callable[[str], None],
        on_job_open: Callable[[int], None],
        on_job_close: Callable[[], None],
    ) -> NativeCommandResult:
        """Launch and wait for an AppContainer process under a kill-on-close job."""

        executable = self._resolve_executable(argv[0], cwd, environment)
        command_line = subprocess.list2cmdline([str(executable), *argv[1:]])
        env_block = ctypes.create_unicode_buffer(windows_environment_block(environment))

        stdout_read, stdout_write = self._pipe()
        stderr_read, stderr_write = self._pipe()
        stdin_handle = self._open_null_input()
        attribute_list: ctypes.Array[ctypes.c_char] | None = None
        initialized_attributes = False
        job = wintypes.HANDLE()
        process_info = _PROCESS_INFORMATION()

        try:
            handles = (wintypes.HANDLE * 3)(stdin_handle, stdout_write, stderr_write)
            security = _SECURITY_CAPABILITIES(
                AppContainerSid=profile.sid,
                Capabilities=None,
                CapabilityCount=0,
                Reserved=0,
            )
            mitigation = ctypes.c_ulonglong(
                self._PROCESS_CREATION_MITIGATION_POLICY_WIN32K_SYSTEM_CALL_DISABLE_ALWAYS_ON
            )

            attribute_size = ctypes.c_size_t()
            self._kernel32.InitializeProcThreadAttributeList(
                None, 3, 0, ctypes.byref(attribute_size)
            )
            attribute_list = ctypes.create_string_buffer(attribute_size.value)
            if not self._kernel32.InitializeProcThreadAttributeList(
                attribute_list, 3, 0, ctypes.byref(attribute_size)
            ):
                self._raise_last_error("InitializeProcThreadAttributeList")
            initialized_attributes = True

            self._update_attribute(
                attribute_list,
                self._PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
                ctypes.byref(security),
                ctypes.sizeof(security),
            )
            self._update_attribute(
                attribute_list,
                self._PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                handles,
                ctypes.sizeof(handles),
            )
            self._update_attribute(
                attribute_list,
                self._PROC_THREAD_ATTRIBUTE_MITIGATION_POLICY,
                ctypes.byref(mitigation),
                ctypes.sizeof(mitigation),
            )

            startup = _STARTUPINFOEXW()
            startup.StartupInfo.cb = ctypes.sizeof(startup)
            startup.StartupInfo.dwFlags = self._STARTF_USESTDHANDLES
            startup.StartupInfo.hStdInput = stdin_handle
            startup.StartupInfo.hStdOutput = stdout_write
            startup.StartupInfo.hStdError = stderr_write
            startup.lpAttributeList = ctypes.cast(attribute_list, ctypes.c_void_p)

            job = self._create_limited_job(limits)
            mutable_command = ctypes.create_unicode_buffer(command_line)
            creation_flags = (
                self._EXTENDED_STARTUPINFO_PRESENT
                | self._CREATE_UNICODE_ENVIRONMENT
                | self._CREATE_NO_WINDOW
                | self._CREATE_SUSPENDED
            )
            if not self._kernel32.CreateProcessW(
                str(executable),
                mutable_command,
                None,
                None,
                True,
                creation_flags,
                ctypes.cast(env_block, ctypes.c_void_p),
                str(cwd),
                ctypes.byref(startup.StartupInfo),
                ctypes.byref(process_info),
            ):
                self._raise_last_error("CreateProcessW(AppContainer)")

            # Parent copies must close before readers can observe EOF.
            self._close_handle(stdout_write)
            stdout_write = wintypes.HANDLE()
            self._close_handle(stderr_write)
            stderr_write = wintypes.HANDLE()
            self._close_handle(stdin_handle)
            stdin_handle = wintypes.HANDLE()

            if not self._kernel32.AssignProcessToJobObject(job, process_info.hProcess):
                self._kernel32.TerminateProcess(process_info.hProcess, 0xC0000022)
                self._raise_last_error("AssignProcessToJobObject")

            job_value = int(job) if isinstance(job, int) else int(job.value)
            on_job_open(job_value)
            if self._kernel32.ResumeThread(process_info.hThread) == 0xFFFFFFFF:
                self._kernel32.TerminateJobObject(job, 0xC0000022)
                self._raise_last_error("ResumeThread")

            output: list[str] = []
            errors: list[str] = []
            reader_failures: list[BaseException] = []
            readers = [
                threading.Thread(
                    target=self._read_pipe,
                    args=(stdout_read, output, on_stdout, reader_failures),
                    daemon=True,
                    name="oaw-sandbox-stdout",
                ),
                threading.Thread(
                    target=self._read_pipe,
                    args=(stderr_read, errors, on_stderr, reader_failures),
                    daemon=True,
                    name="oaw-sandbox-stderr",
                ),
            ]
            for reader in readers:
                reader.start()

            started = time.monotonic()
            timed_out = False
            cancelled = False
            while True:
                wait_result = self._kernel32.WaitForSingleObject(process_info.hProcess, 50)
                if wait_result == self._WAIT_OBJECT_0:
                    break
                if wait_result != self._WAIT_TIMEOUT:
                    self._raise_last_error("WaitForSingleObject")
                if cancel_event.is_set():
                    cancelled = True
                    self._kernel32.TerminateJobObject(job, 0xC000013A)
                elif time.monotonic() - started >= timeout_seconds:
                    timed_out = True
                    self._kernel32.TerminateJobObject(job, 0x00000102)
                else:
                    continue
                self._kernel32.WaitForSingleObject(process_info.hProcess, 5000)
                break

            exit_code = wintypes.DWORD()
            if not self._kernel32.GetExitCodeProcess(
                process_info.hProcess, ctypes.byref(exit_code)
            ):
                self._raise_last_error("GetExitCodeProcess")

            # KILL_ON_JOB_CLOSE guarantees descendants cannot retain pipe handles.
            self._close_handle(job)
            job = wintypes.HANDLE()
            on_job_close()
            for reader in readers:
                reader.join(timeout=5)
            if any(reader.is_alive() for reader in readers):
                raise SandboxSecurityError("sandbox output pipe did not close with its Job Object")
            if reader_failures:
                raise SandboxSecurityError(f"sandbox output capture failed: {reader_failures[0]}")

            return NativeCommandResult(
                exit_code=int(exit_code.value),
                stdout="".join(output),
                stderr="".join(errors),
                duration_seconds=time.monotonic() - started,
                timed_out=timed_out,
                cancelled=cancelled,
            )
        finally:
            if job:
                self._close_handle(job)
                on_job_close()
            self._close_handle(process_info.hThread)
            self._close_handle(process_info.hProcess)
            self._close_handle(stdin_handle)
            self._close_handle(stdout_write)
            self._close_handle(stderr_write)
            self._close_handle(stdout_read)
            self._close_handle(stderr_read)
            if initialized_attributes and attribute_list is not None:
                self._kernel32.DeleteProcThreadAttributeList(attribute_list)

    def _resolve_executable(
        self, raw: str, cwd: Path, environment: Mapping[str, str]
    ) -> Path:
        if not raw or "\x00" in raw:
            raise SandboxValidationError("argv[0] must be a non-empty executable")
        candidate = Path(raw)
        if candidate.is_absolute():
            resolved = candidate.resolve(strict=True)
            if not resolved.is_file():
                raise SandboxValidationError(f"executable is not a file: {raw}")
            return resolved
        if candidate.parent != Path("."):
            resolved = (cwd / candidate).resolve(strict=True)
            if not resolved.is_file():
                raise SandboxValidationError(f"executable is not a file: {raw}")
            return resolved

        suffixes = [""] if candidate.suffix else environment["PATHEXT"].split(";")
        for directory in environment["PATH"].split(";"):
            for suffix in suffixes:
                resolved = Path(directory) / f"{raw}{suffix.lower()}"
                if resolved.is_file():
                    return resolved.resolve()
                resolved = Path(directory) / f"{raw}{suffix.upper()}"
                if resolved.is_file():
                    return resolved.resolve()
        raise SandboxValidationError(
            f"executable {raw!r} is not present on the sandbox PATH"
        )

    def _update_attribute(
        self,
        attribute_list: ctypes.Array[ctypes.c_char],
        attribute: int,
        value: object,
        size: int,
    ) -> None:
        if not self._kernel32.UpdateProcThreadAttribute(
            attribute_list,
            0,
            attribute,
            value,
            size,
            None,
            None,
        ):
            self._raise_last_error("UpdateProcThreadAttribute")

    def _create_limited_job(self, limits: SandboxLimits) -> wintypes.HANDLE:
        job = self._kernel32.CreateJobObjectW(None, None)
        if not job:
            self._raise_last_error("CreateJobObjectW")
        try:
            info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = (
                self._JOB_OBJECT_LIMIT_ACTIVE_PROCESS
                | self._JOB_OBJECT_LIMIT_JOB_MEMORY
                | self._JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
                | self._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            )
            info.BasicLimitInformation.ActiveProcessLimit = limits.active_process_limit
            info.JobMemoryLimit = limits.memory_bytes
            if not self._kernel32.SetInformationJobObject(
                job,
                self._JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(info),
                ctypes.sizeof(info),
            ):
                self._raise_last_error("SetInformationJobObject(limits)")

            ui = _JOBOBJECT_BASIC_UI_RESTRICTIONS(
                UIRestrictionsClass=self._ALL_JOB_UI_RESTRICTIONS
            )
            if not self._kernel32.SetInformationJobObject(
                job,
                self._JOB_OBJECT_BASIC_UI_RESTRICTIONS,
                ctypes.byref(ui),
                ctypes.sizeof(ui),
            ):
                self._raise_last_error("SetInformationJobObject(UI restrictions)")
            return job
        except BaseException:
            self._close_handle(job)
            raise

    def _pipe(self) -> tuple[wintypes.HANDLE, wintypes.HANDLE]:
        security = _SECURITY_ATTRIBUTES(
            nLength=ctypes.sizeof(_SECURITY_ATTRIBUTES),
            lpSecurityDescriptor=None,
            bInheritHandle=True,
        )
        read_handle = wintypes.HANDLE()
        write_handle = wintypes.HANDLE()
        if not self._kernel32.CreatePipe(
            ctypes.byref(read_handle), ctypes.byref(write_handle), ctypes.byref(security), 0
        ):
            self._raise_last_error("CreatePipe")
        if not self._kernel32.SetHandleInformation(
            read_handle, self._HANDLE_FLAG_INHERIT, 0
        ):
            self._close_handle(read_handle)
            self._close_handle(write_handle)
            self._raise_last_error("SetHandleInformation")
        return read_handle, write_handle

    def _open_null_input(self) -> wintypes.HANDLE:
        security = _SECURITY_ATTRIBUTES(
            nLength=ctypes.sizeof(_SECURITY_ATTRIBUTES),
            lpSecurityDescriptor=None,
            bInheritHandle=True,
        )
        handle = self._kernel32.CreateFileW(
            "NUL",
            0x80000000,  # GENERIC_READ
            0x1 | 0x2,
            ctypes.byref(security),
            3,  # OPEN_EXISTING
            0x80,  # FILE_ATTRIBUTE_NORMAL
            None,
        )
        invalid = ctypes.c_void_p(-1).value
        value = int(handle) if isinstance(handle, int) else int(handle.value or 0)
        if value == invalid or value == 0:
            self._raise_last_error("CreateFileW(NUL)")
        return handle

    def _read_pipe(
        self,
        handle: wintypes.HANDLE,
        destination: list[str],
        callback: Callable[[str], None],
        failures: list[BaseException],
    ) -> None:
        import codecs

        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        buffer = ctypes.create_string_buffer(self._STD_READ_BUFFER)
        read = wintypes.DWORD()
        retained = 0
        truncated = False

        def publish(text: str) -> None:
            nonlocal retained, truncated
            encoded = text.encode("utf-8")
            remaining = self._MAX_OUTPUT_BYTES - retained
            if len(encoded) > remaining:
                text = encoded[:remaining].decode("utf-8", errors="ignore")
                truncated = True
            retained += len(text.encode("utf-8"))
            if text:
                destination.append(text)
                callback(text)
            if truncated:
                destination.append(self._OUTPUT_TRUNCATED)
                callback(self._OUTPUT_TRUNCATED)

        try:
            while True:
                ok = self._kernel32.ReadFile(
                    handle,
                    buffer,
                    len(buffer),
                    ctypes.byref(read),
                    None,
                )
                if not ok:
                    error = ctypes.get_last_error()
                    if error in {self._ERROR_BROKEN_PIPE, self._ERROR_NO_DATA}:
                        break
                    raise OSError(error, ctypes.FormatError(error))
                if read.value == 0:
                    break
                # Continue draining discarded output so the child's pipe can
                # never block after the retained and streamed budget is used.
                if not truncated:
                    publish(decoder.decode(buffer.raw[: read.value]))
            if not truncated:
                publish(decoder.decode(b"", final=True))
        except BaseException as exc:
            failures.append(exc)

    def _close_handle(self, handle: wintypes.HANDLE | int | None) -> None:
        if handle:
            self._kernel32.CloseHandle(handle)
