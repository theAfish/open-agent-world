"""Read-only prerequisites for the native AppContainer outbound capability.

Windows enforces internetClient in the filtering platform, for every socket
protocol, independently of proxy variables. No private-network or server
capability is granted. The DNS Client service and Windows certificate stores
remain the OS-provided resolver and trust sources; no user credentials or
privileged sockets are exported to the command.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from .models import SandboxSecurityError


class SidAndAttributes(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]


class _ServiceStatus(ctypes.Structure):
    _fields_ = [(name, wintypes.DWORD) for name in (
        "service_type", "current_state", "controls_accepted", "win32_exit_code",
        "service_specific_exit_code", "checkpoint", "wait_hint",
    )]


class WindowsNetworkIsolation:
    """Never changes machine firewall policy or loopback exemptions."""

    def __init__(self) -> None:
        self.adv = ctypes.WinDLL("advapi32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.firewall = ctypes.WinDLL("FirewallAPI", use_last_error=True)
        self.adv.CreateWellKnownSid.argtypes = [
            ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD),
        ]
        self.adv.CreateWellKnownSid.restype = wintypes.BOOL
        self.adv.EqualSid.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.adv.EqualSid.restype = wintypes.BOOL
        self.adv.OpenSCManagerW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
        self.adv.OpenSCManagerW.restype = wintypes.HANDLE
        self.adv.OpenServiceW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, wintypes.DWORD]
        self.adv.OpenServiceW.restype = wintypes.HANDLE
        self.adv.QueryServiceStatus.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ServiceStatus)]
        self.adv.QueryServiceStatus.restype = wintypes.BOOL
        self.adv.CloseServiceHandle.argtypes = [wintypes.HANDLE]
        self.adv.CloseServiceHandle.restype = wintypes.BOOL
        self.firewall.NetworkIsolationGetAppContainerConfig.argtypes = [
            ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(ctypes.POINTER(SidAndAttributes)),
        ]
        self.firewall.NetworkIsolationGetAppContainerConfig.restype = wintypes.DWORD
        self.kernel.GetProcessHeap.restype = wintypes.HANDLE
        self.kernel.HeapFree.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p]
        self.kernel.HeapFree.restype = wintypes.BOOL

    @staticmethod
    def _error(operation: str) -> SandboxSecurityError:
        code = ctypes.get_last_error()
        return SandboxSecurityError(f"{operation} failed ({code}): {ctypes.FormatError(code).strip()}")

    def internet_client_sid(self):
        # WinCapabilityInternetClientSid = 85; buffer ownership remains Python's.
        sid = ctypes.create_string_buffer(68)  # SECURITY_MAX_SID_SIZE
        size = wintypes.DWORD(len(sid))
        if not self.adv.CreateWellKnownSid(85, None, sid, ctypes.byref(size)):
            raise self._error("CreateWellKnownSid(internetClient)")
        return sid

    def require_services(self) -> None:
        manager = self.adv.OpenSCManagerW(None, None, 0x1)  # SC_MANAGER_CONNECT
        if not manager:
            raise self._error("OpenSCManagerW(network isolation)")
        try:
            for name in ("BFE", "MpsSvc", "Dnscache"):
                service = self.adv.OpenServiceW(manager, name, 0x4)  # SERVICE_QUERY_STATUS
                if not service:
                    raise self._error(f"OpenServiceW({name})")
                try:
                    status = _ServiceStatus()
                    if not self.adv.QueryServiceStatus(service, ctypes.byref(status)):
                        raise self._error(f"QueryServiceStatus({name})")
                    if status.current_state != 4:  # SERVICE_RUNNING
                        raise SandboxSecurityError(
                            f"Windows networking requires the {name} service; start it in Windows Services"
                        )
                finally:
                    self.adv.CloseServiceHandle(service)
        finally:
            self.adv.CloseServiceHandle(manager)

    def require_no_loopback_exemption(self, profile_sid: int | None = None) -> None:
        count = wintypes.DWORD()
        entries = ctypes.POINTER(SidAndAttributes)()
        code = self.firewall.NetworkIsolationGetAppContainerConfig(ctypes.byref(count), ctypes.byref(entries))
        if code:
            raise SandboxSecurityError(f"Cannot verify AppContainer loopback isolation ({code})")
        heap = self.kernel.GetProcessHeap()
        try:
            for index in range(count.value):
                if profile_sid is not None and self.adv.EqualSid(profile_sid, entries[index].Sid):
                    raise SandboxSecurityError(
                        "This AppContainer has a host loopback exemption. Remove its exemption "
                        "with CheckNetIsolation.exe before enabling networking"
                    )
        finally:
            # NetworkIsolationGetAppContainerConfig uses the process heap,
            # including each SID. It does not use LocalAlloc or FreeSid.
            for index in range(count.value):
                self.kernel.HeapFree(heap, 0, entries[index].Sid)
            if entries:
                self.kernel.HeapFree(heap, 0, entries)

    def probe(self) -> None:
        self.internet_client_sid()
        self.require_services()
        self.require_no_loopback_exemption()
