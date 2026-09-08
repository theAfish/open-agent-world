"""Read-only WFP audit utility; run by absolute path with Python -I and UAC.

The only request is a snapshot of one of <=8 profiles approved on the command
line at startup. Only its 17 deterministic keys are queried. No enumeration,
filter writes, process operations, file paths, or code are accepted over IPC.
All trusted modules are imported before opening the authenticated local pipe.
"""
import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
from pathlib import Path
import re
import sys
import uuid

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.sandbox.windows_wfp import _Guid, _Filter, _V4Mask, filter_keys
from backend.tests.windows_acceptance_pipe import serve


def guid_text(guid):
    return str(uuid.UUID(bytes_le=bytes(guid)))


def validate_profiles(profiles):
    if not 1 <= len(profiles) <= 8 or len(set(profiles)) != len(profiles):
        raise ValueError("Approve between one and eight distinct test profiles")
    if any(not isinstance(p, str) or not re.fullmatch(r"OpenAgentWorld\.[a-f0-9]{40}", p) for p in profiles):
        raise ValueError("Only canonical OAW profile identities may be audited")
    return frozenset(profiles)


def validate_request(payload, profiles):
    if (not isinstance(payload, dict) or set(payload) != {"version", "profile"}
        or type(payload["version"]) is not int or payload["version"] != 1
        or not isinstance(payload["profile"], str) or payload["profile"] not in profiles):
        raise ValueError("Only a startup-approved test profile snapshot is permitted")
    return payload["profile"]


class WfpReader:
    def __init__(self):
        self.api = ctypes.WinDLL("fwpuclnt", use_last_error=True)
        self.adv = ctypes.WinDLL("advapi32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.handle = wintypes.HANDLE()
        signatures = {
            "FwpmEngineOpen0": [wintypes.LPCWSTR, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(wintypes.HANDLE)],
            "FwpmEngineClose0": [wintypes.HANDLE],
            "FwpmFilterGetByKey0": [wintypes.HANDLE, ctypes.POINTER(_Guid), ctypes.POINTER(ctypes.POINTER(_Filter))],
        }
        for name, signature in signatures.items():
            fn = getattr(self.api, name)
            fn.argtypes, fn.restype = signature, ctypes.c_uint32
        self.api.FwpmFreeMemory0.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
        self.api.FwpmFreeMemory0.restype = None
        self.adv.IsValidSid.argtypes, self.adv.IsValidSid.restype = [ctypes.c_void_p], wintypes.BOOL
        self.adv.GetLengthSid.argtypes, self.adv.GetLengthSid.restype = [ctypes.c_void_p], wintypes.DWORD
        self.adv.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
        self.adv.ConvertSidToStringSidW.restype = wintypes.BOOL
        self.kernel.LocalFree.argtypes, self.kernel.LocalFree.restype = [ctypes.c_void_p], ctypes.c_void_p
        code = self.api.FwpmEngineOpen0(None, 10, None, None, ctypes.byref(self.handle))
        if code:
            raise OSError(f"FwpmEngineOpen0: 0x{code:08X}")

    def sid_text(self, pointer):
        if not pointer or not self.adv.IsValidSid(pointer) or self.adv.GetLengthSid(pointer) > 68:
            raise ValueError("Invalid installed SID")
        text = wintypes.LPWSTR()
        if not self.adv.ConvertSidToStringSidW(pointer, ctypes.byref(text)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return text.value
        finally:
            self.kernel.LocalFree(text)

    def inspect(self, key):
        guid, rule = _Guid.of(key), ctypes.POINTER(_Filter)()
        code = self.api.FwpmFilterGetByKey0(self.handle, ctypes.byref(guid), ctypes.byref(rule))
        status = {0: "present", 0x80320003: "missing", 5: "access_denied"}.get(code, "query_error")
        result = {"requested_key": str(key), "status": status, "code": code}
        if code:
            return result
        try:
            actual = rule.contents
            if actual.condition_count > 8:
                raise ValueError("Installed condition count exceeds audit bound")
            conditions = []
            for index in range(actual.condition_count):
                condition = actual.conditions[index]
                value = condition.value
                data = {"field": guid_text(condition.key), "match": condition.match, "type": value.type}
                if value.type == 13:
                    data["sid"] = self.sid_text(value.value.pointer)
                elif value.type == 3:
                    data["uint32"] = value.value.uint32
                elif value.type == 0x100 and value.value.pointer:
                    mask = ctypes.cast(value.value.pointer, ctypes.POINTER(_V4Mask)).contents
                    data.update(address=mask.address, mask=mask.mask)
                conditions.append(data)
            result.update(key=guid_text(actual.key), flags=actual.flags,
                layer=guid_text(actual.layer), sublayer=guid_text(actual.sublayer),
                action=actual.action.type, conditions=conditions, filter_id=actual.filter_id)
            return result
        finally:
            self.api.FwpmFreeMemory0(ctypes.cast(ctypes.byref(rule), ctypes.POINTER(ctypes.c_void_p)))

    def close(self):
        if self.handle:
            self.api.FwpmEngineClose0(self.handle)
            self.handle = wintypes.HANDLE()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", required=True)
    parser.add_argument("--profile", action="append", required=True)
    args = parser.parse_args()
    profiles = validate_profiles(args.profile)
    reader = WfpReader()
    closing = False
    print(json.dumps({"profiles": sorted(profiles), "sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}), flush=True)

    def snapshot(payload):
        nonlocal closing
        if payload == {"version": 1, "close": True} and type(payload.get("version")) is int:
            closing = True
            return {"closed": True}
        profile = validate_request(payload, profiles)
        result = {"profile": profile, "filters": [reader.inspect(key) for key in filter_keys(profile)]}
        print(json.dumps(result), flush=True)
        return result

    try:
        serve(args.session, "Audit", snapshot, stopped=lambda: closing)
    finally:
        reader.close()


if __name__ == "__main__":
    main()
