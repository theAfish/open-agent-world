"""Security contract for the test utilities, separate from native evidence."""
import pytest

from backend.tests.windows_wfp_audit import validate_profiles, validate_request
from backend.tests.windows_broker_test_control import BrokerControl

PROFILE = "OpenAgentWorld." + "a" * 40


@pytest.mark.parametrize("payload", [
    {"version": 1, "profile": "OpenAgentWorld." + "b" * 40},
    {"version": True, "profile": PROFILE},
    {"version": 1, "profile": [PROFILE]},
    {"version": 1, "profile": PROFILE, "key": "arbitrary"},
    {"version": 1, "operation": "ensure", "profile": PROFILE},
    {"version": 1, "operation": "execute", "argv": ["cmd.exe"]},
    [], None,
])
def test_audit_rejects_every_input_except_approved_profile_snapshot(payload):
    with pytest.raises(ValueError):
        validate_request(payload, validate_profiles([PROFILE]))


def test_audit_allowlist_is_frozen_and_bounded():
    approved = validate_profiles([PROFILE])
    assert isinstance(approved, frozenset)
    assert validate_request({"version": 1, "profile": PROFILE}, approved) == PROFILE
    for profiles in ([], [PROFILE] * 2, [PROFILE + "\n"], ["Microsoft.WindowsStore"],
                     ["OpenAgentWorld." + str(i) * 40 for i in range(9)]):
        with pytest.raises(ValueError):
            validate_profiles(profiles)


@pytest.mark.parametrize("payload", [
    {"version": 1, "operation": "abnormal", "pid": 123},
    {"version": 1, "operation": "start", "argv": ["cmd.exe"]},
    {"version": 1, "operation": "ensure", "profile": PROFILE},
    {"version": 1, "operation": []},
    {"version": True, "operation": "start"},
])
def test_controller_does_not_accept_arbitrary_process_or_filter_operations(payload):
    with pytest.raises(ValueError):
        BrokerControl().dispatch(payload)
