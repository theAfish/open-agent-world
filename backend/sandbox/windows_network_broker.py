"""Narrow elevated Windows public-egress policy broker.

Run explicitly from a trusted installation in an elevated host terminal:
``python -m backend.sandbox.windows_network_broker``. This process only installs
or removes fixed deny filters for OAW AppContainer identities. It has no command,
file, destination, or arbitrary WFP-rule operation. The management API and user
commands remain unelevated. All broker modules load before serving requests.

The authenticated channel is a Windows local named pipe, using its peer tokens
and an explicit DACL, not a secret available to Sandbox code. Clients verify
the server is elevated and belongs to their Windows account. The server rejects
AppContainer clients. No pickle or Python-object deserialization is used.

WFP deny filters are deliberately NOT dynamic: broker death keeps the policy in
place. The backend releases filters only after ending the AppContainer's Jobs.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import json
import os
import re
import time
from typing import Any

from .models import SandboxNetworkError, SandboxSecurityError


_PROFILE = re.compile(r"OpenAgentWorld\.[a-f0-9]{40}\Z")
_MAX_MESSAGE = 4096
_TIMEOUT = 5.0
_SETUP = (
    "Start the OAW Windows network-policy broker from a trusted installation "
    "in an elevated host terminal: python -m backend.sandbox.windows_network_broker. "
    "Keep the application itself unelevated. Offline execution remains available."
)


def _validate_profile(profile: Any) -> str:
    if not isinstance(profile, str) or not _PROFILE.fullmatch(profile):
        raise ValueError("A canonical OAW AppContainer profile is required")
    return profile


def _dispatch(filters: Any, payload: bytes) -> bytes:
    """The entire privilege-bearing protocol; inputs never become executable."""
    try:
        if len(payload) > _MAX_MESSAGE:
            raise ValueError("Network broker request is too large")
        request = json.loads(payload.decode("utf-8"))
        if not isinstance(request, dict) or type(request.get("version")) is not int or request["version"] != 1:
            raise ValueError("Unsupported network broker protocol")
        operation = request.get("operation")
        if not isinstance(operation, str):
            raise ValueError("Network broker operation must be a string")
        if operation == "probe" and set(request) == {"version", "operation"}:
            pass
        elif operation in {"ensure", "release"} and set(request) == {"version", "operation", "profile"}:
            profile = _validate_profile(request["profile"])
            if operation == "ensure":
                filters.ensure(profile)
            else:
                filters.release(profile)
        else:
            raise ValueError("Only fixed probe, ensure, and release operations are supported")
        response = {"version": 1, "ok": True, "operation": operation}
    except (ValueError, UnicodeError, RecursionError, OSError, SandboxSecurityError) as exc:
        # Never return caller-supplied payloads or stack traces to clients.
        response = {"version": 1, "ok": False, "error": str(exc)[:1200]}
    return json.dumps(response, separators=(",", ":")).encode("utf-8")


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [("length", wintypes.DWORD), ("descriptor", ctypes.c_void_p), ("inherit", wintypes.BOOL)]


class _PipeSecurity:
    """Windows token and pipe primitives; constructed only on Windows."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise SandboxNetworkError("The Windows network broker requires Windows")
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.adv = ctypes.WinDLL("advapi32", use_last_error=True)
        self.kernel.GetCurrentProcess.restype = wintypes.HANDLE
        self.kernel.GetCurrentThread.restype = wintypes.HANDLE
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.kernel.LocalFree.argtypes = [ctypes.c_void_p]
        self.kernel.LocalFree.restype = ctypes.c_void_p
        self.adv.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
        self.adv.OpenProcessToken.restype = wintypes.BOOL
        self.adv.OpenThreadToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, ctypes.POINTER(wintypes.HANDLE)]
        self.adv.OpenThreadToken.restype = wintypes.BOOL
        self.adv.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        self.adv.GetTokenInformation.restype = wintypes.BOOL
        self.adv.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
        self.adv.ConvertSidToStringSidW.restype = wintypes.BOOL
        self.adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD)]
        self.adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
        self.kernel.GetNamedPipeServerProcessId.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG)]
        self.kernel.GetNamedPipeServerProcessId.restype = wintypes.BOOL
        self.kernel.CreateNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(_SecurityAttributes)]
        self.kernel.CreateNamedPipeW.restype = wintypes.HANDLE
        self.kernel.DisconnectNamedPipe.argtypes = [wintypes.HANDLE]
        self.kernel.DisconnectNamedPipe.restype = wintypes.BOOL
        self.adv.ImpersonateNamedPipeClient.argtypes = [wintypes.HANDLE]
        self.adv.ImpersonateNamedPipeClient.restype = wintypes.BOOL
        self.adv.RevertToSelf.restype = wintypes.BOOL
        self.owner, self.elevated, container = self.process_identity(self.kernel.GetCurrentProcess())
        if container:
            raise SandboxNetworkError("AppContainer processes cannot use the host network-policy broker")
        suffix = hashlib.sha256(self.owner.encode("ascii")).hexdigest()[:32]
        self.name = rf"\\.\pipe\OpenAgentWorld.NetworkPolicy.{suffix}"

    @staticmethod
    def check(success: Any, operation: str) -> None:
        if not success:
            code = ctypes.get_last_error()
            raise SandboxNetworkError(f"{operation} failed (Win32 {code})")

    def token_info(self, token: Any, information: int) -> ctypes.Array:
        size = wintypes.DWORD()
        self.adv.GetTokenInformation(token, information, None, 0, ctypes.byref(size))
        if not 0 < size.value < 1024 * 1024:
            raise SandboxNetworkError("Cannot inspect network broker peer token")
        buffer = ctypes.create_string_buffer(size.value)
        self.check(self.adv.GetTokenInformation(token, information, buffer, size, ctypes.byref(size)), "GetTokenInformation")
        return buffer

    def token_identity(self, token: Any) -> tuple[str, bool, bool]:
        user = self.token_info(token, 1)  # TokenUser begins with SID_AND_ATTRIBUTES.
        sid = ctypes.cast(user, ctypes.POINTER(ctypes.c_void_p))[0]
        rendered = wintypes.LPWSTR()
        self.check(self.adv.ConvertSidToStringSidW(sid, ctypes.byref(rendered)), "ConvertSidToStringSidW")
        try:
            owner = rendered.value
        finally:
            self.kernel.LocalFree(rendered)
        elevation = wintypes.DWORD.from_buffer(self.token_info(token, 20)).value
        container = wintypes.DWORD.from_buffer(self.token_info(token, 29)).value
        return owner, bool(elevation), bool(container)

    def process_identity(self, process: Any) -> tuple[str, bool, bool]:
        token = wintypes.HANDLE()
        self.check(self.adv.OpenProcessToken(process, 0x8, ctypes.byref(token)), "OpenProcessToken")
        try:
            return self.token_identity(token)
        finally:
            self.kernel.CloseHandle(token)

    def verify_server(self, pipe: int) -> None:
        pid = wintypes.ULONG()
        self.check(self.kernel.GetNamedPipeServerProcessId(pipe, ctypes.byref(pid)), "GetNamedPipeServerProcessId")
        process = self.kernel.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFORMATION
        self.check(process, "OpenProcess(network broker)")
        try:
            owner, elevated, container = self.process_identity(process)
        finally:
            self.kernel.CloseHandle(process)
        if owner != self.owner or not elevated or container:
            raise SandboxNetworkError("Network-policy pipe server is not the elevated host broker")

    def verify_client(self, pipe: int) -> None:
        self.check(self.adv.ImpersonateNamedPipeClient(pipe), "ImpersonateNamedPipeClient")
        token = wintypes.HANDLE()
        try:
            self.check(self.adv.OpenThreadToken(self.kernel.GetCurrentThread(), 0x8, True, ctypes.byref(token)), "OpenThreadToken")
            owner, _, container = self.token_identity(token)
            if owner != self.owner or container:
                raise SandboxNetworkError("Network-policy broker rejects untrusted client tokens")
        finally:
            if token:
                self.kernel.CloseHandle(token)
            self.check(self.adv.RevertToSelf(), "RevertToSelf")

    def create_server_pipe(self) -> int:
        descriptor = ctypes.c_void_p()
        # No inherited ACEs; explicitly deny AppContainers, permit only this
        # host account, Administrators and SYSTEM, and deny low-integrity writes.
        sddl = ("D:P(D;;GA;;;AC)(A;;GA;;;SY)(A;;GA;;;BA)"
                f"(A;;GRGW;;;{self.owner})S:(ML;;NW;;;ME)")
        self.check(self.adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None), "ConvertStringSecurityDescriptorToSecurityDescriptorW")
        try:
            attributes = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), descriptor, False)
            # Duplex + overlapped + first instance; message mode, local clients only.
            handle = self.kernel.CreateNamedPipeW(self.name, 0x3 | 0x40000000 | 0x80000,
                0x4 | 0x2 | 0x8, 1, _MAX_MESSAGE, _MAX_MESSAGE, 1000, ctypes.byref(attributes))
            self.check(handle and handle != ctypes.c_void_p(-1).value, "CreateNamedPipeW(network broker)")
            return handle
        finally:
            self.kernel.LocalFree(descriptor)


def _request(operation: str, profile: str | None = None) -> None:
    import _winapi
    from multiprocessing.connection import PipeConnection

    security = _PipeSecurity()
    request = {"version": 1, "operation": operation}
    if profile is not None:
        request["profile"] = _validate_profile(profile)
    deadline = time.monotonic() + _TIMEOUT
    while True:
        try:
            _winapi.WaitNamedPipe(security.name, 200)
            handle = _winapi.CreateFile(security.name, _winapi.GENERIC_READ | _winapi.GENERIC_WRITE,
                0, 0, _winapi.OPEN_EXISTING, _winapi.FILE_FLAG_OVERLAPPED | 0x100000 | 0x10000, 0)
            break
        except OSError as exc:
            if getattr(exc, "winerror", None) not in {121, 231} or time.monotonic() >= deadline:
                raise SandboxNetworkError("Windows network-policy broker is unavailable. " + _SETUP) from exc
    with PipeConnection(handle) as connection:
        security.verify_server(handle)
        _winapi.SetNamedPipeHandleState(handle, _winapi.PIPE_READMODE_MESSAGE, None, None)
        connection.send_bytes(json.dumps(request, separators=(",", ":")).encode())
        if not connection.poll(_TIMEOUT):
            raise SandboxNetworkError("Windows network-policy broker did not confirm policy readiness")
        response_bytes = connection.recv_bytes(_MAX_MESSAGE)
        # DisconnectNamedPipe discards unread buffered replies. A bounded
        # acknowledgement lets the server know the client consumed readiness.
        connection.send_bytes(b"ack")
        response = json.loads(response_bytes.decode("utf-8"))
        if response != {"version": 1, "ok": True, "operation": operation}:
            detail = response.get("error", "Invalid broker response") if isinstance(response, dict) else "Invalid broker response"
            raise SandboxNetworkError("Windows public-egress setup failed: " + str(detail)[:1200])


def ensure_public_egress(profile_name: str) -> None:
    try:
        _request("ensure", profile_name)
    except (OSError, ValueError, EOFError) as exc:
        raise SandboxNetworkError("Windows network-policy broker failed to establish egress restrictions") from exc


def release_public_egress(profile_name: str) -> None:
    try:
        _request("release", profile_name)
    except (OSError, ValueError, EOFError) as exc:
        raise SandboxNetworkError("Windows network-policy broker failed to confirm egress cleanup") from exc


def probe_network_broker() -> tuple[bool, str | None]:
    try:
        _request("probe")
    except (ImportError, OSError, ValueError, EOFError, SandboxSecurityError) as exc:
        return False, str(exc)
    return True, None


def run_broker() -> None:
    import _winapi
    from multiprocessing.connection import PipeConnection
    from .windows_wfp import WindowsPublicEgressFilters

    security = _PipeSecurity()
    if not security.elevated:
        raise SandboxNetworkError(_SETUP)
    # Freeze trusted policy code before opening the pipe or admitting workloads.
    filters = WindowsPublicEgressFilters()
    handle = None
    try:
        handle = security.create_server_pipe()
        print("OAW Windows network-policy broker ready; fixed public-egress filters only.", flush=True)
        while True:
            try:
                pending = _winapi.ConnectNamedPipe(handle, overlapped=True)
            except OSError as exc:
                if getattr(exc, "winerror", None) == 232:  # Client vanished before admission.
                    security.kernel.DisconnectNamedPipe(handle)
                    continue
                raise
            try:
                try:
                    while _winapi.WaitForSingleObject(pending.event, 1000) == _winapi.WAIT_TIMEOUT:
                        pass
                except BaseException:
                    pending.cancel()
                    raise
                _, error = pending.GetOverlappedResult(True)
                if error:
                    security.kernel.DisconnectNamedPipe(handle)
                    continue
            finally:
                del pending
            # Keep one owning server handle across clients, so another process
            # cannot squat the authenticated pipe name between requests.
            connection = PipeConnection(handle)
            try:
                if connection.poll(_TIMEOUT):
                    payload = connection.recv_bytes(_MAX_MESSAGE)
                    security.verify_client(handle)
                    connection.send_bytes(_dispatch(filters, payload))
                    if connection.poll(_TIMEOUT):
                        if connection.recv_bytes(8) != b"ack":
                            raise ValueError("Invalid network broker response acknowledgement")
            except (OSError, EOFError, ValueError, SandboxSecurityError):
                pass  # Disconnect malformed, vanished, or unauthorized clients.
            finally:
                connection._handle = None  # The server, not this wrapper, owns it.
                security.kernel.DisconnectNamedPipe(handle)
    finally:
        if handle is not None:
            security.kernel.CloseHandle(handle)
        filters.close()  # Does not remove deny filters from existing workloads.


if __name__ == "__main__":
    try:
        run_broker()
    except KeyboardInterrupt:
        pass
