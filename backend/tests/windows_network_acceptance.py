"""Unprivileged clients/assertions for the approved Windows acceptance helpers."""
import ctypes
from ctypes import wintypes
import ipaddress
import json
import os
from pathlib import Path
import socket
import urllib.parse
import urllib.request

from backend.sandbox import windows_wfp as wfp
from backend.sandbox.windows_network_broker import _PipeSecurity
from backend.tests.windows_acceptance_pipe import request


def acceptance_root():
    raw = os.environ.get("OAW_TEST_WFP_ROOT")
    if not raw:
        raise RuntimeError("Set OAW_TEST_WFP_ROOT to the managed test root approved for the audit helper")
    return Path(raw).resolve()


def assert_unprivileged():
    security = _PipeSecurity()
    assert not security.elevated, "The pytest runner must remain unelevated"


def evidence(name, data):
    root = acceptance_root().parent / "evidence"
    root.mkdir(exist_ok=True)
    with (root / (name + ".jsonl")).open("a", encoding="utf-8") as output:
        output.write(json.dumps(data) + "\n")


class WfpAudit:
    def __init__(self, profile, sid):
        assert_unprivileged()
        self.profile = profile
        self.sid = sid
        self.session = os.environ["OAW_TEST_WFP_SESSION"]

    def snapshot(self, phase):
        result = request(self.session, "Audit", {"version": 1, "profile": self.profile})
        assert result["profile"] == self.profile
        evidence("wfp", {"phase": phase, **result})
        rules = result["filters"]
        assert len(rules) == 17
        assert {r["requested_key"] for r in rules} == {str(k) for k in wfp.filter_keys(self.profile)}
        assert all(r["status"] in {"missing", "present"} for r in rules), result
        return rules

    def absent(self, phase):
        assert all(r["status"] == "missing" and r["code"] == 0x80320003 for r in self.snapshot(phase))

    def installed(self, phase):
        rules = self.snapshot(phase)
        for rule, key, cidr in zip(rules, wfp.filter_keys(self.profile), (*wfp.BLOCKED_IPV4, "host", "ipv6")):
            assert rule["status"] == "present" and rule["code"] == 0, rule
            assert rule["key"] == str(key) and rule["flags"] & 0x9 == 0x9, rule
            assert rule["layer"] == (wfp._CONNECT_V6 if cidr == "ipv6" else wfp._CONNECT_V4), rule
            assert rule["sublayer"] == str(wfp._NAMESPACE) and rule["action"] == 0x1001, rule
            expected = [{"field": wfp._PACKAGE_ID, "match": 0, "type": 13, "sid": self.sid}]
            if cidr == "host":
                expected.append({"field": wfp._FLAGS, "match": 6, "type": 3, "uint32": 1})
            elif cidr != "ipv6":
                subnet = ipaddress.IPv4Network(cidr)
                expected.append({"field": wfp._REMOTE_ADDRESS, "match": 0, "type": 0x100,
                                 "address": int(subnet.network_address), "mask": int(subnet.netmask)})
            assert rule["conditions"] == expected, rule
        return rules


def profile_sid(record):
    security = _PipeSecurity()
    rendered = wintypes.LPWSTR()
    security.check(security.adv.ConvertSidToStringSidW(record.profile.sid, ctypes.byref(rendered)), "Read test profile SID")
    try:
        return rendered.value
    finally:
        security.kernel.LocalFree(rendered)


def broker_control(operation):
    result = request(os.environ["OAW_TEST_BROKER_SESSION"], "BrokerTest", {"version": 1, "operation": operation})
    evidence("broker", result)
    return result


def private_url():
    url = os.environ["OAW_TEST_PRIVATE_URL"]
    parsed = urllib.parse.urlsplit(url)
    address = ipaddress.IPv4Address(parsed.hostname)
    local = {a[4][0] for a in socket.getaddrinfo(socket.gethostname(), None)}
    if (parsed.scheme != "http" or parsed.username or parsed.password or parsed.fragment
        or not any(address in ipaddress.IPv4Network(c) for c in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
        or str(address) in local):
        raise ValueError("Use a controlled remote RFC1918 HTTP peer, not this host, a public endpoint or loopback")
    return url


def host_get(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=3) as response:
        assert response.status == 200
        return response.read(32768)
