"""The broker's whole elevated operation surface stays fixed and data-only."""

import json
import os
from unittest.mock import Mock, call

import pytest

from backend.sandbox import windows_network_broker as broker
from backend.sandbox.models import SandboxNetworkError, SandboxSecurityError


PROFILE = "OpenAgentWorld." + "a" * 40


@pytest.mark.parametrize("operation", ["probe", "ensure", "release"])
def test_broker_only_dispatches_fixed_profile_policy(operation):
    filters = Mock()
    request = {"version": 1, "operation": operation}
    if operation != "probe":
        request["profile"] = PROFILE
    response = json.loads(broker._dispatch(filters, json.dumps(request).encode()))
    assert response == {"version": 1, "ok": True, "operation": operation}
    if operation == "probe":
        assert not filters.mock_calls
    else:
        assert filters.mock_calls == [getattr(call, operation)(PROFILE)]


@pytest.mark.parametrize("payload", [
    b"not-json", b"[]", b"null", b"\x80\x04pickle", b"[" * 1500,
    b" " * 4097,
    {"version": True, "operation": "probe"},
    {"version": 1, "operation": ["ensure"]},
    {"version": 1, "operation": "execute", "command": ["cmd.exe"]},
    {"version": 1, "operation": "ensure", "profile": PROFILE, "rules": "allow everything"},
    {"version": 1, "operation": "ensure", "profile": PROFILE, "path": "C:\\host-file"},
    {"version": 1, "operation": "ensure", "profile": "Microsoft.WindowsCalculator_8wekyb3d8bbwe"},
    {"version": 1, "operation": "ensure", "profile": "OpenAgentWorld." + "a" * 40 + "\n"},
    {"version": 1, "operation": "release", "profile": "S-1-15-2-1"},
    {"version": 1, "operation": "probe", "profile": PROFILE},
])
def test_malformed_or_arbitrary_requests_never_reach_privileged_filters(payload):
    filters = Mock()
    encoded = json.dumps(payload).encode() if isinstance(payload, dict) else payload
    response = json.loads(broker._dispatch(filters, encoded))
    assert response["ok"] is False
    assert not filters.mock_calls


def test_failed_policy_install_is_not_reported_ready():
    filters = Mock()
    filters.ensure.side_effect = SandboxSecurityError("WFP transaction failed")
    response = json.loads(broker._dispatch(filters, json.dumps({
        "version": 1, "operation": "ensure", "profile": PROFILE,
    }).encode()))
    assert response == {"version": 1, "ok": False, "error": "WFP transaction failed"}
    filters.release.assert_not_called()


@pytest.mark.parametrize("exception", [EOFError(), OSError("broken pipe"), ValueError("malformed response")])
def test_client_disconnect_or_bad_ack_fails_as_setup_error(monkeypatch, exception):
    monkeypatch.setattr(broker, "_request", Mock(side_effect=exception))
    with pytest.raises(SandboxNetworkError):
        broker.ensure_public_egress(PROFILE)
    with pytest.raises(SandboxNetworkError):
        broker.release_public_egress(PROFILE)


@pytest.mark.skipif(os.name != "nt", reason="Windows token API")
def test_current_host_token_and_pipe_identity_are_consistent():
    security = broker._PipeSecurity()
    assert security.owner.startswith("S-1-5-")
    assert security.name.startswith(r"\\.\pipe\OpenAgentWorld.NetworkPolicy.")
    assert security.process_identity(security.kernel.GetCurrentProcess()) == (security.owner, security.elevated, False)


@pytest.mark.skipif(os.name != "nt", reason="Windows pipe and token API")
def test_client_rejects_actual_unelevated_pipe_squatter(monkeypatch):
    import _winapi
    import uuid

    security = broker._PipeSecurity()
    if security.elevated:
        pytest.skip("requires an unelevated process to simulate pipe squatting")
    security.name += ".test." + uuid.uuid4().hex
    handle = security.create_server_pipe()
    pending = _winapi.ConnectNamedPipe(handle, overlapped=True)
    monkeypatch.setattr(broker, "_PipeSecurity", lambda: security)
    try:
        with pytest.raises(SandboxNetworkError, match="not the elevated host broker"):
            broker._request("probe")
    finally:
        pending.cancel()
        pending.GetOverlappedResult(True)
        security.kernel.CloseHandle(handle)
