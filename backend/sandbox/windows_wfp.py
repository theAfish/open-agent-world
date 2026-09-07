"""Fixed per-AppContainer public IPv4 egress restrictions for the trusted broker.

internetClient alone is insufficient: Windows classifies networks by interface
profile rather than excluding private address ranges. These ALE filters add
explicit destination restrictions independent of that classification, including
complete IPv6 denial in this first policy. There are no permit filters.

Filters are deliberately NON-DYNAMIC. Closing or crashing the broker leaves
the deny policy enforced by BFE while an existing workload may still be alive.
The normal Sandbox destroy path removes its deterministic owned filters only
after Job termination. Persistent filters also survive BFE/system restart;
each new command reasserts the complete policy before receiving internetClient.
"""
from __future__ import annotations

import ctypes
import ipaddress
import re
import uuid
from ctypes import wintypes

from .models import SandboxSecurityError


BLOCKED_IPV4 = (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8",
    "169.254.0.0/16", "172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24",
    "192.88.99.0/24", "192.168.0.0/16", "198.18.0.0/15", "198.51.100.0/24",
    "203.0.113.0/24", "224.0.0.0/4", "240.0.0.0/4",
)
_PROFILE = re.compile(r"OpenAgentWorld\.[a-f0-9]{40}\Z", re.IGNORECASE)
_NAMESPACE = uuid.UUID("7d0a63b9-1c74-4bd3-88a3-30a3bb772b8f")
_CONNECT_V4 = "c38d57d1-05a7-4c33-904f-7fbceee60e82"
_CONNECT_V6 = "4a72393b-319f-44bc-84c3-ba54dcb3b6b4"
_PACKAGE_ID = "71bc78fa-f17c-4997-a602-6abb261f351c"
_REMOTE_ADDRESS = "b235ae9a-1d64-49b8-a44c-5ff3d9095045"
_FLAGS = "632ce23b-5167-435c-86d7-e903684aa80c"
_RULES = (*BLOCKED_IPV4, "ipv4-loopback-deny", "ipv6-deny")


class _Guid(ctypes.Structure):
    _fields_ = [("data1", ctypes.c_uint32), ("data2", ctypes.c_uint16),
                ("data3", ctypes.c_uint16), ("data4", ctypes.c_uint8 * 8)]

    @classmethod
    def of(cls, value):
        return cls.from_buffer_copy(uuid.UUID(str(value)).bytes_le)


class _Display(ctypes.Structure):
    _fields_ = [("name", wintypes.LPWSTR), ("description", wintypes.LPWSTR)]


class _Blob(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("data", ctypes.c_void_p)]


class _ValueUnion(ctypes.Union):
    _fields_ = [("uint8", ctypes.c_uint8), ("uint16", ctypes.c_uint16),
                ("uint32", ctypes.c_uint32), ("pointer", ctypes.c_void_p)]


class _Value(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("value", _ValueUnion)]


class _Session(ctypes.Structure):
    _fields_ = [("key", _Guid), ("display", _Display), ("flags", ctypes.c_uint32),
                ("transaction_timeout", ctypes.c_uint32), ("process_id", ctypes.c_uint32),
                ("sid", ctypes.c_void_p), ("username", wintypes.LPWSTR), ("kernel_mode", wintypes.BOOL)]


class _Sublayer(ctypes.Structure):
    _fields_ = [("key", _Guid), ("display", _Display), ("flags", ctypes.c_uint32),
                ("provider", ctypes.c_void_p), ("data", _Blob), ("weight", ctypes.c_uint16)]


class _Condition(ctypes.Structure):
    _fields_ = [("key", _Guid), ("match", ctypes.c_uint32), ("value", _Value)]


class _Action(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("key", _Guid)]


class _Context(ctypes.Union):
    _fields_ = [("raw", ctypes.c_uint64), ("provider", _Guid)]


class _Filter(ctypes.Structure):
    _fields_ = [("key", _Guid), ("display", _Display), ("flags", ctypes.c_uint32),
                ("provider", ctypes.c_void_p), ("data", _Blob), ("layer", _Guid),
                ("sublayer", _Guid), ("weight", _Value), ("condition_count", ctypes.c_uint32),
                ("conditions", ctypes.POINTER(_Condition)), ("action", _Action),
                ("context", _Context), ("reserved", ctypes.c_void_p),
                ("filter_id", ctypes.c_uint64), ("effective_weight", _Value)]


class _V4Mask(ctypes.Structure):
    _fields_ = [("address", ctypes.c_uint32), ("mask", ctypes.c_uint32)]


def validate_profile_name(profile: str) -> str:
    if not isinstance(profile, str) or not _PROFILE.fullmatch(profile):
        raise SandboxSecurityError("Network broker only accepts OAW AppContainer profile identities")
    return profile.lower()


def filter_keys(profile: str):
    profile = validate_profile_name(profile)
    return tuple(uuid.uuid5(_NAMESPACE, profile + ":" + rule) for rule in _RULES)


class WindowsPublicEgressFilters:
    """Admin-only fixed filtering operations. No execution or arbitrary rules."""

    def __init__(self):
        self.api = ctypes.WinDLL("fwpuclnt", use_last_error=True)
        self.userenv = ctypes.WinDLL("userenv", use_last_error=True)
        self.advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        self.handle = wintypes.HANDLE()
        self._configure()
        session = _Session(display=_Display("OAW public egress broker", None),
                           flags=0, transaction_timeout=5000)
        # RPC_C_AUTHN_WINNT. No remote BFE, no supplied credentials.
        self._check(self.api.FwpmEngineOpen0(None, 10, None, ctypes.byref(session), ctypes.byref(self.handle)),
                    "Open Windows Filtering Platform (start the OAW network broker as Administrator)")

    def _configure(self):
        functions = {
            "FwpmEngineOpen0": [wintypes.LPCWSTR, ctypes.c_uint32, ctypes.c_void_p,
                                 ctypes.POINTER(_Session), ctypes.POINTER(wintypes.HANDLE)],
            "FwpmEngineClose0": [wintypes.HANDLE],
            "FwpmTransactionBegin0": [wintypes.HANDLE, ctypes.c_uint32],
            "FwpmTransactionCommit0": [wintypes.HANDLE],
            "FwpmTransactionAbort0": [wintypes.HANDLE],
            "FwpmSubLayerAdd0": [wintypes.HANDLE, ctypes.POINTER(_Sublayer), ctypes.c_void_p],
            "FwpmFilterAdd0": [wintypes.HANDLE, ctypes.POINTER(_Filter), ctypes.c_void_p,
                               ctypes.POINTER(ctypes.c_uint64)],
            "FwpmFilterDeleteByKey0": [wintypes.HANDLE, ctypes.POINTER(_Guid)],
        }
        for name, arguments in functions.items():
            function = getattr(self.api, name)
            function.argtypes = arguments
            function.restype = ctypes.c_uint32
        self.userenv.DeriveAppContainerSidFromAppContainerName.argtypes = [
            wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
        self.userenv.DeriveAppContainerSidFromAppContainerName.restype = ctypes.c_long
        self.advapi.FreeSid.argtypes = [ctypes.c_void_p]
        self.advapi.FreeSid.restype = ctypes.c_void_p

    @staticmethod
    def _check(code, operation, ignored=()):
        code = int(code) & 0xFFFFFFFF
        if code and code not in ignored:
            raise SandboxSecurityError(f"{operation} failed (0x{code:08X})")

    def _delete(self, key):
        guid = _Guid.of(key)
        self._check(self.api.FwpmFilterDeleteByKey0(self.handle, ctypes.byref(guid)),
                    "Remove owned OAW egress filter", ignored=(0x80320003,))

    def ensure(self, profile: str) -> None:
        profile = validate_profile_name(profile)
        sid = ctypes.c_void_p()
        self._check(self.userenv.DeriveAppContainerSidFromAppContainerName(profile, ctypes.byref(sid)),
                    "Derive OAW AppContainer SID")
        try:
            self._check(self.api.FwpmTransactionBegin0(self.handle, 0), "Begin egress policy transaction")
            try:
                sublayer = _Sublayer(key=_Guid.of(_NAMESPACE), flags=1, weight=65535,
                    display=_Display("OAW public IPv4 egress restrictions", "Fixed AppContainer deny policy"))
                self._check(self.api.FwpmSubLayerAdd0(self.handle, ctypes.byref(sublayer), None),
                            "Install OAW egress sublayer", ignored=(0x80320009,))
                # Atomic replacement both repairs orphaned state after a broker
                # restart and prevents a stale/partial policy from being trusted.
                for key, cidr in zip(filter_keys(profile), _RULES):
                    self._delete(key)
                    self._add(key, profile, sid.value, cidr)
                self._check(self.api.FwpmTransactionCommit0(self.handle), "Commit egress policy")
            except BaseException:
                self.api.FwpmTransactionAbort0(self.handle)
                raise
        finally:
            if sid:
                self.advapi.FreeSid(sid)

    def _add(self, key, profile, sid, cidr):
        ipv6 = cidr == "ipv6-deny"
        loopback = cidr == "ipv4-loopback-deny"
        conditions = (_Condition * (1 if ipv6 else 2))()
        conditions[0] = _Condition(key=_Guid.of(_PACKAGE_ID), match=0,
                                  value=_Value(type=13, value=_ValueUnion(pointer=sid)))  # FWP_SID
        if loopback:
            # WFP marks all traffic to this host, including globally assigned
            # interface addresses, with IS_LOOPBACK. Enforce that independently
            # of Windows' own AppContainer exemption and profile rules.
            conditions[1] = _Condition(key=_Guid.of(_FLAGS), match=6,
                value=_Value(type=3, value=_ValueUnion(uint32=1)))
        elif not ipv6:
            subnet = ipaddress.IPv4Network(cidr)
            mask = _V4Mask(int(subnet.network_address), int(subnet.netmask))  # host byte order
            conditions[1] = _Condition(key=_Guid.of(_REMOTE_ADDRESS), match=0,
                value=_Value(type=0x100, value=_ValueUnion(pointer=ctypes.addressof(mask))))
        weight = ctypes.c_uint64(0xFFFFFFFFFFFFFFFF)
        rule = _Filter(key=_Guid.of(key), display=_Display("OAW deny " + cidr, profile),
            flags=0x9, layer=_Guid.of(_CONNECT_V6 if ipv6 else _CONNECT_V4),
            sublayer=_Guid.of(_NAMESPACE), weight=_Value(type=4, value=_ValueUnion(pointer=ctypes.addressof(weight))),
            condition_count=len(conditions), conditions=conditions, action=_Action(type=0x1001))
        self._check(self.api.FwpmFilterAdd0(self.handle, ctypes.byref(rule), None, None), "Install fixed egress deny filter")

    def release(self, profile: str) -> None:
        keys = filter_keys(profile)
        self._check(self.api.FwpmTransactionBegin0(self.handle, 0), "Begin egress cleanup")
        try:
            for key in keys:
                self._delete(key)
            self._check(self.api.FwpmTransactionCommit0(self.handle), "Commit egress cleanup")
        except BaseException:
            self.api.FwpmTransactionAbort0(self.handle)
            raise

    def close(self) -> None:
        if self.handle:
            self.api.FwpmEngineClose0(self.handle)
            self.handle = wintypes.HANDLE()
