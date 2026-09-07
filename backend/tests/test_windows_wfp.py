"""Fixed-policy tests; real BFE acceptance requires an elevated broker."""
import ctypes
import ipaddress
import uuid
from unittest.mock import Mock

import pytest

from backend.sandbox.models import SandboxSecurityError
from backend.sandbox import windows_wfp as wfp


PROFILE = "OpenAgentWorld." + "a" * 40


def test_filter_keys_scope_rules_to_valid_oaw_identity():
    assert len(set(wfp.filter_keys(PROFILE))) == len(wfp.BLOCKED_IPV4) + 2
    assert wfp.filter_keys(PROFILE) == wfp.filter_keys(PROFILE.lower())
    assert set(wfp.filter_keys(PROFILE)).isdisjoint(wfp.filter_keys("OpenAgentWorld." + "b" * 40))
    for unsafe in ("Microsoft.WindowsStore", "", None, PROFILE + "\n", PROFILE + "\\x"):
        with pytest.raises(SandboxSecurityError):
            wfp.filter_keys(unsafe)


def test_public_ipv4_policy_blocks_private_special_and_ipv6_unconditionally():
    blocked = tuple(ipaddress.IPv4Network(cidr) for cidr in wfp.BLOCKED_IPV4)
    for address in ("0.1.2.3", "10.9.8.7", "100.64.0.1", "127.0.0.1", "169.254.1.1",
                    "172.16.61.254", "172.30.64.1", "192.168.1.1", "192.0.2.1",
                    "198.18.0.1", "198.51.100.1", "203.0.113.1", "224.1.1.1", "255.255.255.255"):
        assert any(ipaddress.IPv4Address(address) in cidr for cidr in blocked), address
    assert not any(ipaddress.IPv4Address("1.1.1.1") in cidr for cidr in blocked)
    adapter = wfp.WindowsPublicEgressFilters.__new__(wfp.WindowsPublicEgressFilters)
    adapter.handle = 1
    adapter.api = Mock()
    snapshots = []

    def add(handle, pointer, security, filter_id):
        rule = ctypes.cast(pointer, ctypes.POINTER(wfp._Filter)).contents
        conditions = [rule.conditions[index] for index in range(rule.condition_count)]
        snapshots.append((bytes(rule.layer), rule.action.type, rule.flags,
                          [(bytes(c.key), c.value.type, c.value.value.pointer) for c in conditions]))
        assert rule.condition_count in {1, 2}
        assert conditions[0].value.value.pointer == 42
        if len(conditions) == 2:
            mask = ctypes.cast(conditions[1].value.value.pointer, ctypes.POINTER(wfp._V4Mask)).contents
            assert mask.address == int(ipaddress.IPv4Address("10.0.0.0"))
            assert mask.mask == 0xFF000000
        return 0

    adapter.api.FwpmFilterAdd0.side_effect = add
    adapter._add(uuid.uuid4(), PROFILE, 42, "10.0.0.0/8")
    adapter._add(uuid.uuid4(), PROFILE, 42, "ipv6-deny")
    assert [item[0] for item in snapshots] == [bytes(wfp._Guid.of(wfp._CONNECT_V4)), bytes(wfp._Guid.of(wfp._CONNECT_V6))]
    assert all(item[1] == 0x1001 for item in snapshots)  # All rules block; never permit.
    assert all(item[2] == 0x9 for item in snapshots)  # Persistent hard blocks survive BFE restart.
    assert all(item[3][0][1] == 13 for item in snapshots)  # AppContainer SID, not executable path.
    assert len(snapshots[1][3]) == 1  # All IPv6 destinations, including mapped/private/host addresses.


def test_filter_failure_aborts_transaction_and_does_not_release_existing_policy():
    adapter = wfp.WindowsPublicEgressFilters.__new__(wfp.WindowsPublicEgressFilters)
    adapter.handle = 7
    adapter.userenv = Mock()
    adapter.userenv.DeriveAppContainerSidFromAppContainerName.return_value = 0
    adapter.advapi = Mock()
    adapter.api = Mock()
    adapter.api.FwpmTransactionBegin0.return_value = 0
    adapter.api.FwpmSubLayerAdd0.return_value = 0
    adapter._delete = Mock()
    adapter._add = Mock(side_effect=SandboxSecurityError("injected filter setup error"))
    with pytest.raises(SandboxSecurityError, match="injected"):
        adapter.ensure(PROFILE)
    adapter.api.FwpmTransactionAbort0.assert_called_once_with(7)
    adapter.api.FwpmTransactionCommit0.assert_not_called()


def test_loopback_rule_blocks_all_host_interfaces_without_address_snapshot():
    adapter = wfp.WindowsPublicEgressFilters.__new__(wfp.WindowsPublicEgressFilters)
    adapter.handle = 1
    adapter.api = Mock()

    def add(handle, pointer, security, filter_id):
        rule = ctypes.cast(pointer, ctypes.POINTER(wfp._Filter)).contents
        assert bytes(rule.layer) == bytes(wfp._Guid.of(wfp._CONNECT_V4))
        assert rule.condition_count == 2
        condition = rule.conditions[1]
        assert bytes(condition.key) == bytes(wfp._Guid.of(wfp._FLAGS))
        assert condition.match == 6 and condition.value.type == 3
        assert condition.value.value.uint32 == 1
        return 0

    adapter.api.FwpmFilterAdd0.side_effect = add
    adapter._add(uuid.uuid4(), PROFILE, 42, "ipv4-loopback-deny")


def test_broker_close_only_closes_session_and_retains_deny_filters():
    adapter = wfp.WindowsPublicEgressFilters.__new__(wfp.WindowsPublicEgressFilters)
    adapter.handle = 7
    adapter.api = Mock()
    adapter.close()
    adapter.api.FwpmEngineClose0.assert_called_once_with(7)
    adapter.api.FwpmFilterDeleteByKey0.assert_not_called()
    assert not adapter.handle
